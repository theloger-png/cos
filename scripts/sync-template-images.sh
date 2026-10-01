#!/usr/bin/env bash
#
# sync-template-images.sh - copy VM template image files from one node that
# already has them to one or more nodes that don't.
#
# Background: a VMTemplate's image_path (see portal Templates page) is a
# plain node-local filesystem path, e.g.
# /var/lib/libvirt/images/noble-server-cloudimg-amd64.img. It is never
# synced across nodes automatically - every node that should be able to
# create VMs from a given template needs the exact same file at the exact
# same path. See TODO.md, "Template Image Distribution Across Nodes" for
# the planned real fix; this script is the manual stopgap until then.
#
# Run this from any machine with normal (password-prompted) SSH/sudo access
# to both the source and target nodes.
#
# Usage:
#   scripts/sync-template-images.sh -u ADMIN_USER -s SOURCE_NODE TARGET1 [TARGET2 ...]
#
# Required:
#   -u ADMIN_USER     The user you SSH in as on every node (needs sudo there).
#   -s SOURCE_NODE     The node that already has the template images.
#
# Arguments:
#   One or more target node hostnames/IPs to copy images to.
#
# Example:
#   scripts/sync-template-images.sh -u super -s cos-node1 172.18.9.6
#
# Notes:
#   - Copies every *.img and *.qcow2 file directly under
#     /var/lib/libvirt/images/ on the source (non-recursive - this is where
#     base template images live per the install guide; VM disks live
#     separately under /var/lib/cos/vms/ and are never touched by this
#     script).
#   - Skips a file on a target if it already exists there with the same
#     size (cheap idempotency check, not a checksum) - re-running this
#     script after adding one new template only transfers the new file.
#   - Sets ownership to libvirt-qemu:kvm and mode 644 on every target,
#     matching what a fresh manual download would produce.
#   - Every privileged remote step runs as a direct (non-captured) SSH call
#     first, so any sudo password prompt is visible and answerable on your
#     terminal - sudo output is never silently swallowed into a captured
#     variable. Expect to be prompted for sudo once per node (source once,
#     then once per target) in addition to the SSH login password.
#   - Each remote step has a 180s timeout. If a step times out, it means a
#     prompt (SSH or sudo password) went unanswered for 3 minutes - check
#     the step name in the error message and retry.
#
set -euo pipefail

ADMIN_USER=""
SOURCE_NODE=""
TARGETS=()

usage() {
    sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "u:s:h" opt; do
    case "$opt" in
        u) ADMIN_USER="$OPTARG" ;;
        s) SOURCE_NODE="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))
TARGETS=("$@")

[ -n "$ADMIN_USER" ] || { usage; echo "Error: -u ADMIN_USER is required" >&2; exit 1; }
[ -n "$SOURCE_NODE" ] || { usage; echo "Error: -s SOURCE_NODE is required" >&2; exit 1; }
[ "${#TARGETS[@]}" -ge 1 ] || { usage; echo "Error: give at least 1 target node" >&2; exit 1; }

STEP_TIMEOUT=180

log() { echo ">> $*"; }
die() { echo "Error: $*" >&2; exit 1; }

# Runs a command on $node as a direct (non-captured, pty-attached) SSH call,
# so any SSH/sudo password prompt is shown on our real terminal and can be
# answered - never used for a call whose stdout we plan to capture.
ssh_interactive() {
    local node="$1"; shift
    timeout "$STEP_TIMEOUT" ssh -t -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${node}" "$@"
}

# Runs a command on $node as a plain (no -t) SSH call and prints its stdout -
# only ever used for commands that are already known not to prompt for
# anything (e.g. reading back a temp file that was just chmod'd world
# readable), so it's safe to capture with $().
ssh_capture() {
    local node="$1"; shift
    timeout "$STEP_TIMEOUT" ssh -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${node}" "$@"
}

log "Source: $SOURCE_NODE"
log "Targets: ${TARGETS[*]}"
echo

SOURCE_LIST_TMP="/tmp/cos-sync-list-$$"

log "Connecting to $SOURCE_NODE to list template images"
log "  (you may be asked for the SSH password for $ADMIN_USER, then for a sudo password - watch for both)"
ssh_interactive "$SOURCE_NODE" "
    sudo find /var/lib/libvirt/images -maxdepth 1 -type f \\( -name '*.img' -o -name '*.qcow2' \\) -printf '%f\t%s\n' > '$SOURCE_LIST_TMP' \
        && sudo chmod 644 '$SOURCE_LIST_TMP'
" || die "listing images on $SOURCE_NODE timed out or failed after ${STEP_TIMEOUT}s - this is the SSH login or sudo step on $SOURCE_NODE, see any prompt/error above. Verify the SSH password and that $ADMIN_USER has sudo on $SOURCE_NODE."

IMAGE_LIST="$(ssh_capture "$SOURCE_NODE" "cat '$SOURCE_LIST_TMP'; rm -f '$SOURCE_LIST_TMP'")" \
    || die "reading the image list back from $SOURCE_NODE failed after the listing step succeeded"

if [ -z "$IMAGE_LIST" ]; then
    die "no .img/.qcow2 files found directly under /var/lib/libvirt/images on $SOURCE_NODE"
fi

log "Found images on $SOURCE_NODE:"
echo "$IMAGE_LIST" | while IFS=$'\t' read -r fname fsize; do
    echo "   $fname (${fsize} bytes)"
done
echo

for target in "${TARGETS[@]}"; do
    log "Syncing to $target"

    TARGET_SIZES_TMP="/tmp/cos-sync-sizes-$$"
    STAT_SCRIPT=": > '$TARGET_SIZES_TMP'"
    while IFS=$'\t' read -r fname _; do
        [ -n "$fname" ] || continue
        STAT_SCRIPT+="; printf '%s\t%s\n' '$fname' \"\$(stat -c %s '/var/lib/libvirt/images/${fname}' 2>/dev/null || echo 0)\" >> '$TARGET_SIZES_TMP'"
    done <<< "$IMAGE_LIST"
    STAT_SCRIPT+="; chmod 644 '$TARGET_SIZES_TMP'"

    log "  Checking which images already exist on $target"
    log "  (you may be asked for the SSH password for $ADMIN_USER, then for a sudo password - watch for both)"
    ssh_interactive "$target" "sudo bash -c \"$STAT_SCRIPT\"" \
        || die "checking existing images on $target timed out or failed after ${STEP_TIMEOUT}s - this is the SSH login or sudo step on $target, see any prompt/error above. Verify the SSH password and that $ADMIN_USER has sudo on $target."

    TARGET_SIZES="$(ssh_capture "$target" "cat '$TARGET_SIZES_TMP'; rm -f '$TARGET_SIZES_TMP'")" \
        || die "reading existing image sizes back from $target failed after the check step succeeded"

    while IFS=$'\t' read -r fname fsize; do
        [ -n "$fname" ] || continue

        TARGET_SIZE="$(echo "$TARGET_SIZES" | awk -F'\t' -v f="$fname" '$1 == f { print $2 }')"
        [ -n "$TARGET_SIZE" ] || TARGET_SIZE=0

        if [ "$TARGET_SIZE" = "$fsize" ]; then
            log "  $fname already present on $target with matching size, skipping"
            continue
        fi

        log "  Copying $fname ($fsize bytes) to $target via this machine ..."
        # Route the transfer through this machine (source -> local temp file
        # -> target) rather than node-to-node scp, so this script only ever
        # needs credentials for the nodes it was given, never needing the
        # source and target to trust each other.
        TMP_LOCAL="/tmp/cos-sync-image-$$-${fname}"
        log "    Fetching $fname from $SOURCE_NODE (you may be asked for the SSH password for $ADMIN_USER)"
        if ! timeout "$STEP_TIMEOUT" scp -o StrictHostKeyChecking=accept-new -q "${ADMIN_USER}@${SOURCE_NODE}:/var/lib/libvirt/images/${fname}" "$TMP_LOCAL"; then
            log "    Direct fetch failed - likely a permissions issue on $SOURCE_NODE, making $fname world-readable there and retrying"
            ssh_interactive "$SOURCE_NODE" "sudo chmod a+r '/var/lib/libvirt/images/${fname}'" \
                || die "could not chmod $fname readable on $SOURCE_NODE (SSH or sudo step timed out or failed after ${STEP_TIMEOUT}s)"
            timeout "$STEP_TIMEOUT" scp -o StrictHostKeyChecking=accept-new -q "${ADMIN_USER}@${SOURCE_NODE}:/var/lib/libvirt/images/${fname}" "$TMP_LOCAL" \
                || die "fetching $fname from $SOURCE_NODE failed even after making it world-readable"
        fi

        log "    Uploading $fname to $target:/tmp (you may be asked for the SSH password for $ADMIN_USER)"
        timeout "$STEP_TIMEOUT" scp -o StrictHostKeyChecking=accept-new -q "$TMP_LOCAL" "${ADMIN_USER}@${target}:/tmp/${fname}" \
            || die "uploading $fname to $target failed"
        rm -f "$TMP_LOCAL"

        log "    Moving $fname into place on $target and fixing ownership (you may be asked for a sudo password)"
        ssh_interactive "$target" "
            set -e
            sudo mv '/tmp/${fname}' '/var/lib/libvirt/images/${fname}'
            sudo chown libvirt-qemu:kvm '/var/lib/libvirt/images/${fname}'
            sudo chmod 644 '/var/lib/libvirt/images/${fname}'
        " || die "moving $fname into place on $target timed out or failed after ${STEP_TIMEOUT}s - see any prompt/error above"

        log "  Done: $fname on $target"
    done <<< "$IMAGE_LIST"
done

echo
log "All targets synced."
