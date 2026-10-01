#!/usr/bin/env bash
#
# setup-cluster-ssh.sh - one-time cluster bootstrap: gives the "cos" system
# user passwordless SSH access between every physical node, which live VM
# migration needs (agent/libvirt_driver.py's migrate_vm() opens
# qemu+ssh://<dest>/system as the cos user, with no username in the URI, so
# it SSHes as whichever user is running the cos-agent service).
#
# Run this from any machine with normal (password-prompted) SSH/sudo access
# to every node - it orchestrates the setup over plain ssh/scp, prompting for
# your login/sudo password per node exactly as a manual session would. No
# passwords are stored or hardcoded anywhere.
#
# What it does, for the full set of nodes given:
#   1. On each node: ensure the "cos" user has an ed25519 keypair under its
#      home (/opt/cos/.ssh), generating one if missing.
#   2. Collect every node's cos public key back to this machine.
#   3. On each node: write the UNION of all nodes' cos public keys into
#      /opt/cos/.ssh/authorized_keys (so any node can SSH as cos into any
#      other, including itself - harmless and simplifies the logic).
#   4. On each node: pre-populate /opt/cos/.ssh/known_hosts with every other
#      node's host key via ssh-keyscan, so the first real migration attempt
#      doesn't hang on an interactive "are you sure you want to continue
#      connecting?" prompt (the cos user has no login shell to answer it).
#
# Usage:
#   scripts/setup-cluster-ssh.sh -u ADMIN_USER NODE1 NODE2 [NODE3 ...]
#
# Required:
#   -u ADMIN_USER   The user you SSH in as on every node (needs sudo there).
#
# Arguments:
#   One or more node hostnames or IPs - every physical node running
#   cos-agent. List all of them, including the one you're running this from
#   if applicable; this script only ever connects outward over SSH, it
#   never assumes anything about the local machine's own role.
#
# Example:
#   scripts/setup-cluster-ssh.sh -u super cos-node1 172.18.9.6
#
# Notes:
#   - Safe to re-run: key generation and authorized_keys/known_hosts writes
#     are idempotent (existing keys are reused, duplicate entries are not
#     added twice).
#   - Requires the "cos" user to already exist on every node (i.e.
#     cos-install.sh --role agent has already been run there).
#
set -euo pipefail

ADMIN_USER=""
NODES=()

usage() {
    sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "u:h" opt; do
    case "$opt" in
        u) ADMIN_USER="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))
NODES=("$@")

[ -n "$ADMIN_USER" ] || { usage; echo "Error: -u ADMIN_USER is required" >&2; exit 1; }
[ "${#NODES[@]}" -ge 2 ] || { usage; echo "Error: give at least 2 node hostnames/IPs" >&2; exit 1; }

log() { echo ">> $*"; }
die() { echo "Error: $*" >&2; exit 1; }

ssh_to() {
    local node="$1"; shift
    # -t forces a pseudo-terminal so remote `sudo` can prompt for a password
    # interactively; without it, sudo on the remote end fails with
    # "a terminal is required to read the password".
    ssh -t -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${node}" "$@"
}

log "Nodes: ${NODES[*]}"
echo

# --- Step 1: ensure every node's cos user has a keypair, collect pubkeys ---
# Split into two ssh calls per node on purpose: the first needs `sudo` and
# its password prompts must go straight to the terminal (no local piping,
# or the prompt text gets swallowed into the pipe with no visible cue to
# type anything - the script just appears to hang); the second only reads a
# plain world-readable temp file with no sudo involved, so it's safe to
# pipe/capture normally.
declare -A PUBKEYS
for node in "${NODES[@]}"; do
    log "Checking/creating cos SSH keypair on $node (you may be prompted for the sudo password here)"
    ssh_to "$node" '
        set -e
        sudo rm -f /tmp/cos-pubkey-out.pub
        if ! sudo test -f /opt/cos/.ssh/id_ed25519; then
            sudo -u cos mkdir -p /opt/cos/.ssh
            sudo -u cos ssh-keygen -t ed25519 -N "" -f /opt/cos/.ssh/id_ed25519 -q
            sudo chmod 700 /opt/cos/.ssh
            sudo chmod 600 /opt/cos/.ssh/id_ed25519
            sudo chmod 644 /opt/cos/.ssh/id_ed25519.pub
        fi
        sudo cp /opt/cos/.ssh/id_ed25519.pub /tmp/cos-pubkey-out.pub
        sudo chmod 644 /tmp/cos-pubkey-out.pub
    ' || die "failed to prepare cos SSH key on $node"

    # The temp file above is root-owned (created via sudo cp), so the plain
    # admin user can read it but can't delete it under /tmp's sticky bit -
    # leave it in place; the next run's sudo rm at the top cleans it up.
    log "Reading back the public key from $node"
    PUBKEYS["$node"]="$(ssh -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${node}" \
        'cat /tmp/cos-pubkey-out.pub' | tr -d '\r')"
    [ -n "${PUBKEYS[$node]}" ] || die "failed to get cos public key from $node"
done
echo

# --- Step 2: build the combined authorized_keys content ---------------------
ALL_KEYS=""
for node in "${NODES[@]}"; do
    ALL_KEYS="${ALL_KEYS}${PUBKEYS[$node]}
"
done

# --- Step 3: push authorized_keys + known_hosts to every node --------------
for node in "${NODES[@]}"; do
    log "Writing authorized_keys and known_hosts on $node"

    KEYS_TMP="/tmp/cos-authorized-keys-$$"
    printf '%s' "$ALL_KEYS" > "$KEYS_TMP"
    scp -o StrictHostKeyChecking=accept-new -q "$KEYS_TMP" "${ADMIN_USER}@${node}:/tmp/cos-authorized-keys"
    rm -f "$KEYS_TMP"

    # Collect every OTHER node's host key for known_hosts (skip self; a node
    # scanning its own SSH host key is harmless but pointless).
    OTHER_NODES=()
    for n in "${NODES[@]}"; do
        [ "$n" != "$node" ] && OTHER_NODES+=("$n")
    done

    ssh_to "$node" "
        set -e
        sudo install -o cos -g cos -m 600 /tmp/cos-authorized-keys /opt/cos/.ssh/authorized_keys
        rm -f /tmp/cos-authorized-keys
        KH_TMP=/tmp/cos-known-hosts-\$\$
        ssh-keyscan -T 5 ${OTHER_NODES[*]} > \"\$KH_TMP\" 2>/dev/null || true
        if [ -s \"\$KH_TMP\" ]; then
            sudo -u cos touch /opt/cos/.ssh/known_hosts
            cat \"\$KH_TMP\" | sudo -u cos tee -a /opt/cos/.ssh/known_hosts > /dev/null
            sudo -u cos sort -u -o /opt/cos/.ssh/known_hosts /opt/cos/.ssh/known_hosts
            sudo chown cos:cos /opt/cos/.ssh/known_hosts
            sudo chmod 644 /opt/cos/.ssh/known_hosts
        fi
        rm -f \"\$KH_TMP\"
    "
done
echo

log "Done. Verifying cos-to-cos SSH between the first two nodes..."
log "(you may be prompted for the sudo password here too)"
FIRST="${NODES[0]}"
SECOND="${NODES[1]}"

ssh_to "$FIRST" "sudo -u cos ssh -o BatchMode=yes -o ConnectTimeout=5 cos@${SECOND} 'echo ok' > /tmp/cos-verify-result 2>&1" \
    || true  # the result file (or lack of it) is the real signal, not this call's own exit code

VERIFY_OUTPUT="$(ssh -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${FIRST}" \
    'cat /tmp/cos-verify-result 2>/dev/null; rm -f /tmp/cos-verify-result' | tr -d '\r')"

if [ "$VERIFY_OUTPUT" = "ok" ]; then
    echo
    echo "==================================================================="
    echo "Success: cos@${FIRST} can SSH to cos@${SECOND} without a password."
    echo "Live migration (qemu+ssh://) should now work between these nodes."
    echo "==================================================================="
else
    echo
    echo "==================================================================="
    echo "WARNING: verification SSH from cos@${FIRST} to cos@${SECOND} did not"
    echo "succeed. Output was: ${VERIFY_OUTPUT:-<empty>}"
    echo "Check for errors above, or re-run this script."
    echo "==================================================================="
    exit 1
fi
