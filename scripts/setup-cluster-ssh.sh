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
    ssh -o StrictHostKeyChecking=accept-new "${ADMIN_USER}@${node}" "$@"
}

log "Nodes: ${NODES[*]}"
echo

# --- Step 1: ensure every node's cos user has a keypair, collect pubkeys ---
declare -A PUBKEYS
for node in "${NODES[@]}"; do
    log "Checking/creating cos SSH keypair on $node"
    ssh_to "$node" '
        set -e
        if [ ! -f /opt/cos/.ssh/id_ed25519 ]; then
            sudo -u cos mkdir -p /opt/cos/.ssh
            sudo -u cos ssh-keygen -t ed25519 -N "" -f /opt/cos/.ssh/id_ed25519 -q
            sudo chmod 700 /opt/cos/.ssh
            sudo chmod 600 /opt/cos/.ssh/id_ed25519
            sudo chmod 644 /opt/cos/.ssh/id_ed25519.pub
        fi
        sudo cat /opt/cos/.ssh/id_ed25519.pub
    ' > /tmp/pubkey-$$-"$node".txt
    PUBKEYS["$node"]="$(cat /tmp/pubkey-$$-"$node".txt)"
    rm -f /tmp/pubkey-$$-"$node".txt
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
        sudo -u cos ssh-keyscan -T 5 ${OTHER_NODES[*]} >> /tmp/cos-known-hosts-\$\$ 2>/dev/null || true
        if [ -s /tmp/cos-known-hosts-\$\$ ]; then
            sudo -u cos touch /opt/cos/.ssh/known_hosts
            sudo bash -c 'cat /tmp/cos-known-hosts-\$\$ >> /opt/cos/.ssh/known_hosts'
            sudo -u cos sort -u -o /opt/cos/.ssh/known_hosts /opt/cos/.ssh/known_hosts
            sudo chown cos:cos /opt/cos/.ssh/known_hosts
            sudo chmod 644 /opt/cos/.ssh/known_hosts
        fi
        rm -f /tmp/cos-known-hosts-\$\$
    "
done
echo

log "Done. Verifying cos-to-cos SSH between the first two nodes..."
FIRST="${NODES[0]}"
SECOND="${NODES[1]}"
if ssh_to "$FIRST" "sudo -u cos ssh -o BatchMode=yes -o ConnectTimeout=5 cos@${SECOND} 'echo ok'" 2>/dev/null | grep -q '^ok$'; then
    echo
    echo "==================================================================="
    echo "Success: cos@${FIRST} can SSH to cos@${SECOND} without a password."
    echo "Live migration (qemu+ssh://) should now work between these nodes."
    echo "==================================================================="
else
    echo
    echo "==================================================================="
    echo "WARNING: verification SSH from cos@${FIRST} to cos@${SECOND} did not"
    echo "succeed. Check the output above for errors, or re-run this script."
    echo "==================================================================="
    exit 1
fi
