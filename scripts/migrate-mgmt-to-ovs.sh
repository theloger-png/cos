#!/usr/bin/env bash
#
# migrate-mgmt-to-ovs.sh - one-time infra migration for cos-node1: move the
# node's management IP off a VLAN sub-interface on a physical NIC and onto an
# OVS internal port on the "nos-br" bridge, so VMs tagged with the management
# VLAN can reach the node locally without going out through the physical
# switch.
#
# This is NOT part of the general install flow (see scripts/cos-install.sh)
# and does not touch libvirt, VMs, or any other part of the stack. It only
# prepares and applies a network change on the host.
#
# Usage:
#   scripts/migrate-mgmt-to-ovs.sh -v VLAN_ID -i IP/CIDR -g GATEWAY \
#       [-b OVS_BRIDGE] [-p OVS_PORT] [-e PHYS_IFACE] [-y]
#
# Required:
#   -v VLAN_ID    Management VLAN ID (1-4094)
#   -i IP/CIDR    Static IP address + prefix for the OVS port, e.g. 172.18.4.194/29
#   -g GATEWAY    Gateway IP for the default route, e.g. 172.18.4.193
#
# Optional:
#   -b BRIDGE     OVS bridge name (default: nos-br)
#   -p PORT       OVS internal port name (default: mgmt<VLAN_ID>)
#   -e IFACE      Physical interface currently carrying the old VLAN config
#                 (default: eno1np0)
#   -y, --yes     Skip the confirmation prompt before netplan apply
#   -h, --help    Show this help and exit
#
# Example:
#   scripts/migrate-mgmt-to-ovs.sh -v 350 -i 172.18.4.194/29 -g 172.18.4.193
#
# Notes:
#   - Applying this change will briefly interrupt SSH connectivity if you are
#     connected via the interface being reconfigured. Have console/IPMI
#     access ready before confirming.
#   - The old netplan VLAN file is backed up (renamed with a .bak-<timestamp>
#     suffix), never deleted, so the migration is reversible.
#   - On failure after netplan apply, this script does NOT attempt automatic
#     rollback (that could itself fail silently in a broken network state).
#     It prints the backup file path so you can manually revert.
#
set -euo pipefail

OVS_BRIDGE="nos-br"
PHYS_IFACE="eno1np0"
NEW_NETPLAN_FILE="/etc/netplan/61-cos-mgmt-ovs.yaml"

VLAN_ID=""
STATIC_IP=""
GATEWAY=""
OVS_PORT=""
ASSUME_YES=0

usage() {
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    echo "Error: $*" >&2
    exit 1
}

log() {
    echo ">> $*"
}

# Translate the long forms explicitly requested (--help, --yes) to their
# short-flag equivalents before getopts, to match create-test-vm.sh's
# getopts-based style while still accepting both spellings.
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --help) ARGS+=("-h") ;;
        --yes) ARGS+=("-y") ;;
        *) ARGS+=("$arg") ;;
    esac
done
set -- "${ARGS[@]}"

while getopts "v:i:g:b:p:e:yh" opt; do
    case "$opt" in
        v) VLAN_ID="$OPTARG" ;;
        i) STATIC_IP="$OPTARG" ;;
        g) GATEWAY="$OPTARG" ;;
        b) OVS_BRIDGE="$OPTARG" ;;
        p) OVS_PORT="$OPTARG" ;;
        e) PHYS_IFACE="$OPTARG" ;;
        y) ASSUME_YES=1 ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

[ -n "$VLAN_ID" ] || { usage; die "-v VLAN_ID is required"; }
[ -n "$STATIC_IP" ] || { usage; die "-i IP/CIDR is required"; }
[ -n "$GATEWAY" ] || { usage; die "-g GATEWAY is required"; }

[[ "$VLAN_ID" =~ ^[0-9]+$ ]] && [ "$VLAN_ID" -ge 1 ] && [ "$VLAN_ID" -le 4094 ] \
    || die "VLAN_ID must be a number between 1 and 4094"
[[ "$STATIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] \
    || die "IP/CIDR '$STATIC_IP' must look like e.g. 172.18.4.194/29"
[[ "$GATEWAY" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "GATEWAY '$GATEWAY' must look like e.g. 172.18.4.193"

if [ -z "$OVS_PORT" ]; then
    OVS_PORT="mgmt${VLAN_ID}"
fi

print_revert_instructions() {
    if [ -n "${OLD_NETPLAN_BACKUP:-}" ]; then
        echo "To revert manually:" >&2
        echo "  sudo mv '$OLD_NETPLAN_BACKUP' '$OLD_NETPLAN_FILE_ORIG'" >&2
        echo "  sudo rm -f '$NEW_NETPLAN_FILE'" >&2
        echo "  sudo netplan apply" >&2
    else
        echo "No old netplan file was backed up; remove '$NEW_NETPLAN_FILE' and" >&2
        echo "restore your previous management network config manually." >&2
    fi
}

# --- Step 1: verify the OVS bridge exists -----------------------------------
command -v ovs-vsctl >/dev/null 2>&1 || die "ovs-vsctl not found; is openvswitch-switch installed?"
ovs-vsctl br-exists "$OVS_BRIDGE" || die "OVS bridge '$OVS_BRIDGE' does not exist"

# --- Step 2: create or fix the OVS internal port (idempotent) --------------
if ovs-vsctl list-ports "$OVS_BRIDGE" | grep -qx "$OVS_PORT"; then
    CURRENT_TAG="$(ovs-vsctl get port "$OVS_PORT" tag 2>/dev/null | tr -d '[]')"
    if [ "$CURRENT_TAG" = "$VLAN_ID" ]; then
        log "OVS port '$OVS_PORT' already exists on '$OVS_BRIDGE' with tag $VLAN_ID"
    else
        log "OVS port '$OVS_PORT' exists with tag '$CURRENT_TAG', fixing to $VLAN_ID"
        ovs-vsctl set port "$OVS_PORT" tag="$VLAN_ID"
    fi
else
    log "Creating OVS internal port '$OVS_PORT' on '$OVS_BRIDGE' with tag $VLAN_ID"
    ovs-vsctl add-port "$OVS_BRIDGE" "$OVS_PORT" tag="$VLAN_ID" \
        -- set interface "$OVS_PORT" type=internal
fi

# --- Step 3: find and back up the old netplan VLAN file ---------------------
OLD_NETPLAN_FILE_ORIG=""
OLD_NETPLAN_BACKUP=""
for f in /etc/netplan/*.yaml; do
    [ -f "$f" ] || continue
    if grep -q '^[[:space:]]*vlans:' "$f" \
        && grep -qE "^[[:space:]]*id:[[:space:]]*${VLAN_ID}[[:space:]]*\$" "$f" \
        && grep -qE "^[[:space:]]*link:[[:space:]]*${PHYS_IFACE}[[:space:]]*\$" "$f"; then
        OLD_NETPLAN_FILE_ORIG="$f"
        break
    fi
done

if [ -n "$OLD_NETPLAN_FILE_ORIG" ]; then
    OLD_NETPLAN_BACKUP="${OLD_NETPLAN_FILE_ORIG}.bak-$(date +%Y%m%d%H%M%S)"
    log "Found old VLAN netplan config at $OLD_NETPLAN_FILE_ORIG"
    log "Backing it up to $OLD_NETPLAN_BACKUP"
    mv "$OLD_NETPLAN_FILE_ORIG" "$OLD_NETPLAN_BACKUP"
else
    log "WARNING: no existing netplan VLAN file found for VLAN $VLAN_ID on $PHYS_IFACE"
    log "Continuing - this may be a fresh setup with nothing to migrate"
fi

# --- Step 4: write the new netplan file -------------------------------------
log "Writing new netplan config to $NEW_NETPLAN_FILE"
cat > "$NEW_NETPLAN_FILE" <<EOF
network:
  version: 2
  ethernets:
    ${OVS_PORT}:
      dhcp4: false
      addresses: [${STATIC_IP}]
      routes:
        - to: default
          via: ${GATEWAY}
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
EOF
chmod 600 "$NEW_NETPLAN_FILE"

# --- Step 5: warn and confirm before applying -------------------------------
echo
echo "==================================================================="
echo "WARNING: applying this change will briefly interrupt network"
echo "connectivity on '$PHYS_IFACE' / VLAN $VLAN_ID. If you are connected"
echo "over SSH via that path, your session may drop."
echo "Make sure you have console or IPMI access ready before continuing."
echo "==================================================================="
echo

if [ "$ASSUME_YES" -ne 1 ]; then
    read -r -p "Type 'yes' to proceed with netplan apply: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        die "aborted by operator (confirmation not given)"
    fi
fi

# --- Step 6: bring the OVS internal port up ---------------------------------
log "Bringing up interface '$OVS_PORT'"
ip link set "$OVS_PORT" up

# --- Step 7: apply netplan ---------------------------------------------------
log "Applying netplan"
if ! netplan apply; then
    echo "netplan apply failed." >&2
    print_revert_instructions
    exit 1
fi

# --- Step 8: verify connectivity ---------------------------------------------
log "Verifying connectivity to gateway $GATEWAY"
PING_OK=0
for attempt in 1 2 3 4 5; do
    if ping -c 1 -W 2 "$GATEWAY" >/dev/null 2>&1; then
        PING_OK=1
        break
    fi
    log "Ping attempt $attempt failed, retrying..."
    sleep 2
done

if [ "$PING_OK" -eq 1 ]; then
    echo
    echo "==================================================================="
    echo "Success: management IP migrated to OVS port '$OVS_PORT'"
    echo "  Bridge:   $OVS_BRIDGE"
    echo "  VLAN:     $VLAN_ID"
    echo "  IP:       $STATIC_IP"
    echo "  Gateway:  $GATEWAY (reachable)"
    echo "==================================================================="
else
    echo
    echo "==================================================================="
    echo "FAILURE: gateway $GATEWAY did not respond after netplan apply."
    echo "==================================================================="
    print_revert_instructions
    exit 1
fi
