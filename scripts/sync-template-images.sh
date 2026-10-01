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

log() { echo ">> $*"; }
die() { echo "Error: $*" >&2; exit 1; }

ssh_to() {
    local node="$1"; shift
    ssh -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${node}" "$@"
}

log "Source: $SOURCE_NODE"
log "Targets: ${TARGETS[*]}"
echo

log "Listing template images on $SOURCE_NODE"
IMAGE_LIST="$(ssh_to "$SOURCE_NODE" "sudo find /var/lib/libvirt/images -maxdepth 1 -type f \\( -name '*.img' -o -name '*.qcow2' \\) -printf '%f\t%s\n'")"

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
    while IFS=$'\t' read -r fname fsize; do
        [ -n "$fname" ] || continue

        TARGET_SIZE="$(ssh_to "$target" "sudo stat -c %s '/var/lib/libvirt/images/${fname}' 2>/dev/null || echo 0")"
        if [ "$TARGET_SIZE" = "$fsize" ]; then
            log "  $fname already present on $target with matching size, skipping"
            continue
        fi

        log "  Copying $fname ($fsize bytes) to $target ..."
        # Route the transfer through this machine (source -> local temp file
        # -> target) rather than node-to-node scp, so this script only ever
        # needs credentials for the nodes it was given, never needing the
        # source and target to trust each other.
        TMP_LOCAL="/tmp/cos-sync-image-$$-${fname}"
        scp -o StrictHostKeyChecking=accept-new -q "${ADMIN_USER}@${SOURCE_NODE}:/var/lib/libvirt/images/${fname}" "$TMP_LOCAL" \
            || { ssh_to "$SOURCE_NODE" "sudo chmod a+r '/var/lib/libvirt/images/${fname}'"; \
                 scp -o StrictHostKeyChecking=accept-new -q "${ADMIN_USER}@${SOURCE_NODE}:/var/lib/libvirt/images/${fname}" "$TMP_LOCAL"; }

        scp -o StrictHostKeyChecking=accept-new -q "$TMP_LOCAL" "${ADMIN_USER}@${target}:/tmp/${fname}"
        rm -f "$TMP_LOCAL"

        ssh_to "$target" "
            set -e
            sudo mv '/tmp/${fname}' '/var/lib/libvirt/images/${fname}'
            sudo chown libvirt-qemu:kvm '/var/lib/libvirt/images/${fname}'
            sudo chmod 644 '/var/lib/libvirt/images/${fname}'
        "
        log "  Done: $fname on $target"
    done <<< "$IMAGE_LIST"
done

echo
log "All targets synced."
