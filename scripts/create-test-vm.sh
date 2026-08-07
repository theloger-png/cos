#!/usr/bin/env bash
#
# create-test-vm.sh - dev/test utility to manually create a KVM VM on
# cos-node1 with an OVS-tagged network interface on the "nos-br" bridge.
#
# This is NOT part of the install flow (see scripts/cos-install.sh). It is a
# throwaway helper for manually exercising the OVS VLAN tagging path added in
# the feature/ovs-networking branch.
#
# Usage:
#   scripts/create-test-vm.sh -n NAME -v VLAN_ID -i IP/CIDR -g GATEWAY \
#       [-r RAM_MB] [-c VCPUS] [-d DISK_GB]
#
# Required:
#   -n NAME       VM name (also used as the guest hostname)
#   -v VLAN_ID    OVS VLAN tag to apply to the VM's NIC (1-4094)
#   -i IP/CIDR    Static IP address + prefix for the guest, e.g. 10.0.20.5/24
#   -g GATEWAY    Gateway IP for the guest's default route, e.g. 10.0.20.1
#
# Optional:
#   -r RAM_MB     RAM in MB (default: 4096)
#   -c VCPUS      Number of vCPUs (default: 2)
#   -d DISK_GB    Disk size in GB (default: 40)
#   -h            Show this help and exit
#
# Example:
#   scripts/create-test-vm.sh -n vlan20-test -v 20 -i 10.0.20.5/24 -g 10.0.20.1
#
# Notes:
#   - The base cloud image is cached at
#     /var/lib/libvirt/images/noble-server-cloudimg-amd64.img and downloaded
#     automatically on first use. It is never deleted by this script.
#   - The guest login user is "super" with NOPASSWD sudo, your
#     ~/.ssh/id_ed25519.pub key, and a randomly generated password printed at
#     the end of the run.
#   - Verify OVS VLAN tagging after boot with: ovs-vsctl show
#
set -euo pipefail

IMAGES_DIR="/var/lib/libvirt/images"
BASE_IMAGE="${IMAGES_DIR}/noble-server-cloudimg-amd64.img"
BASE_IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
BRIDGE="nos-br"

RAM_MB=4096
VCPUS=2
DISK_GB=40

VM_NAME=""
VLAN_ID=""
STATIC_IP=""
GATEWAY=""

usage() {
    sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    echo "Error: $*" >&2
    exit 1
}

log() {
    echo ">> $*"
}

while getopts "n:v:i:g:r:c:d:h" opt; do
    case "$opt" in
        n) VM_NAME="$OPTARG" ;;
        v) VLAN_ID="$OPTARG" ;;
        i) STATIC_IP="$OPTARG" ;;
        g) GATEWAY="$OPTARG" ;;
        r) RAM_MB="$OPTARG" ;;
        c) VCPUS="$OPTARG" ;;
        d) DISK_GB="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

[ -n "$VM_NAME" ] || { usage; die "-n NAME is required"; }
[ -n "$VLAN_ID" ] || { usage; die "-v VLAN_ID is required"; }
[ -n "$STATIC_IP" ] || { usage; die "-i IP/CIDR is required"; }
[ -n "$GATEWAY" ] || { usage; die "-g GATEWAY is required"; }

[[ "$VM_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || die "VM name '$VM_NAME' has invalid characters"
[[ "$VLAN_ID" =~ ^[0-9]+$ ]] && [ "$VLAN_ID" -ge 1 ] && [ "$VLAN_ID" -le 4094 ] \
    || die "VLAN_ID must be a number between 1 and 4094"
[[ "$STATIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] \
    || die "IP/CIDR '$STATIC_IP' must look like e.g. 10.0.20.5/24"
[[ "$GATEWAY" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "GATEWAY '$GATEWAY' must look like e.g. 10.0.20.1"
[[ "$RAM_MB" =~ ^[0-9]+$ ]] || die "RAM_MB must be a number"
[[ "$VCPUS" =~ ^[0-9]+$ ]] || die "VCPUS must be a number"
[[ "$DISK_GB" =~ ^[0-9]+$ ]] || die "DISK_GB must be a number"

SSH_PUBKEY_FILE="${HOME}/.ssh/id_ed25519.pub"
[ -f "$SSH_PUBKEY_FILE" ] || die "SSH public key not found at $SSH_PUBKEY_FILE"
SSH_PUBKEY="$(cat "$SSH_PUBKEY_FILE")"

VM_DISK="${IMAGES_DIR}/${VM_NAME}.qcow2"
SEED_ISO="${IMAGES_DIR}/${VM_NAME}-seed.iso"

DISK_CREATED=0
SEED_CREATED=0
DOMAIN_DEFINED=0
CLOUD_INIT_DIR=""

cleanup() {
    local exit_code=$?
    if [ "$exit_code" -eq 0 ]; then
        return
    fi
    echo "Failure detected, cleaning up partially-created resources..." >&2
    if [ -n "$CLOUD_INIT_DIR" ] && [ -d "$CLOUD_INIT_DIR" ]; then
        rm -rf "$CLOUD_INIT_DIR"
    fi
    if [ "$DOMAIN_DEFINED" -eq 1 ]; then
        virsh destroy "$VM_NAME" >/dev/null 2>&1 || true
        virsh undefine "$VM_NAME" >/dev/null 2>&1 || true
    fi
    if [ "$SEED_CREATED" -eq 1 ]; then
        rm -f "$SEED_ISO"
    fi
    if [ "$DISK_CREATED" -eq 1 ]; then
        rm -f "$VM_DISK"
    fi
}
trap cleanup EXIT

# --- Step 1: base cloud image -------------------------------------------
mkdir -p "$IMAGES_DIR"
if [ ! -f "$BASE_IMAGE" ]; then
    log "Base image not found at $BASE_IMAGE"
    log "Downloading Ubuntu Noble cloud image (this is a large file, please wait)..."
    if ! curl -fL --progress-bar -o "$BASE_IMAGE" "$BASE_IMAGE_URL"; then
        rm -f "$BASE_IMAGE"
        die "download of base cloud image failed"
    fi
    if [ ! -s "$BASE_IMAGE" ]; then
        rm -f "$BASE_IMAGE"
        die "downloaded base cloud image is empty"
    fi
    log "Base image downloaded successfully"
else
    log "Using existing base image at $BASE_IMAGE"
fi

# --- Step 2: per-VM disk ---------------------------------------------------
log "Creating ${DISK_GB}G qcow2 disk at $VM_DISK (backed by base image)"
qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$VM_DISK" "${DISK_GB}G" >/dev/null
DISK_CREATED=1

# --- Step 3: cloud-init seed ISO -------------------------------------------
log "Generating cloud-init seed ISO"

PASSWORD="$(openssl rand -base64 12)"
SALT="$(openssl rand -hex 8)"
PASSWORD_HASH="$(openssl passwd -6 -salt "$SALT" "$PASSWORD")"
INSTANCE_ID="${VM_NAME}-$(date +%s)-$RANDOM"

CLOUD_INIT_DIR="$(mktemp -d)"

USER_DATA_TEMPLATE="$(cat <<'EOF'
#cloud-config
hostname: __VM_NAME__
users:
  - name: super
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - __SSH_PUBKEY__
chpasswd:
  users:
    - name: super
      password: '__PASSWORD_HASH__'
      type: hash
  expire: false
ssh_pwauth: true
write_files:
  - path: /etc/netplan/99-cos.yaml
    content: |
      network:
        version: 2
        ethernets:
          enp1s0:
            addresses: [__STATIC_IP__]
            routes:
              - to: default
                via: __GATEWAY__
            nameservers:
              addresses: [1.1.1.1, 8.8.8.8]
runcmd:
  - netplan apply
EOF
)"

# Placeholder substitution below is plain bash string replacement (immune to
# shell/variable re-expansion), which keeps the '$' characters in
# PASSWORD_HASH from ever being interpreted.
USER_DATA="${USER_DATA_TEMPLATE//__VM_NAME__/$VM_NAME}"
USER_DATA="${USER_DATA//__SSH_PUBKEY__/$SSH_PUBKEY}"
USER_DATA="${USER_DATA//__PASSWORD_HASH__/$PASSWORD_HASH}"
USER_DATA="${USER_DATA//__STATIC_IP__/$STATIC_IP}"
USER_DATA="${USER_DATA//__GATEWAY__/$GATEWAY}"

printf '%s\n' "$USER_DATA" > "${CLOUD_INIT_DIR}/user-data"
printf 'instance-id: %s\nlocal-hostname: %s\n' "$INSTANCE_ID" "$VM_NAME" > "${CLOUD_INIT_DIR}/meta-data"

cloud-localds "$SEED_ISO" "${CLOUD_INIT_DIR}/user-data" "${CLOUD_INIT_DIR}/meta-data"
SEED_CREATED=1
rm -rf "$CLOUD_INIT_DIR"

# --- Step 4: define the domain (without starting it) -----------------------
# virt-install normally boots the guest as part of "install". To keep the VM
# fully defined-but-stopped until OVS VLAN tagging is injected below, we use
# --dry-run --print-xml to have virt-install build the domain XML without any
# libvirt side effects, then register it ourselves via `virsh define`.
log "Building domain XML with virt-install"
DOMAIN_XML="$(mktemp)"

virt-install \
    --connect qemu:///system \
    --name "$VM_NAME" \
    --memory "$RAM_MB" \
    --vcpus "$VCPUS" \
    --os-variant ubuntu24.04 \
    --disk path="$VM_DISK",bus=virtio \
    --disk path="$SEED_ISO",device=cdrom \
    --network bridge="$BRIDGE",model=virtio \
    --graphics none \
    --console pty,target_type=serial \
    --import \
    --boot hd,cdrom \
    --noreboot \
    --noautoconsole \
    --dry-run \
    --print-xml > "$DOMAIN_XML"

virsh define "$DOMAIN_XML" >/dev/null
rm -f "$DOMAIN_XML"
DOMAIN_DEFINED=1
log "Domain '$VM_NAME' defined (not yet started)"

# --- Step 5: inject OVS VLAN tagging into the interface XML ----------------
log "Adding OVS VLAN tag $VLAN_ID to the '$BRIDGE' interface"
INACTIVE_XML="$(mktemp)"
virsh dumpxml --inactive "$VM_NAME" > "$INACTIVE_XML"

VM_XML_PATH="$INACTIVE_XML" VM_VLAN_ID="$VLAN_ID" VM_BRIDGE="$BRIDGE" python3 <<'PYEOF'
import os
import xml.etree.ElementTree as ET

xml_path = os.environ["VM_XML_PATH"]
vlan_id = os.environ["VM_VLAN_ID"]
bridge = os.environ["VM_BRIDGE"]

tree = ET.parse(xml_path)
root = tree.getroot()

interface = None
for iface in root.findall("./devices/interface[@type='bridge']"):
    source = iface.find("source")
    if source is not None and source.get("bridge") == bridge:
        interface = iface
        break

if interface is None:
    raise SystemExit(f"could not find a bridge interface for '{bridge}' in {xml_path}")

source = interface.find("source")
insert_at = list(interface).index(source) + 1

virtualport = ET.Element("virtualport", {"type": "openvswitch"})
vlan = ET.Element("vlan")
ET.SubElement(vlan, "tag", {"id": vlan_id})

interface.insert(insert_at, virtualport)
interface.insert(insert_at + 1, vlan)

ET.indent(tree)
tree.write(xml_path)
PYEOF

virsh define "$INACTIVE_XML" >/dev/null
rm -f "$INACTIVE_XML"

# --- Step 6: start the VM ---------------------------------------------------
log "Starting VM '$VM_NAME'"
virsh start "$VM_NAME" >/dev/null

trap - EXIT

echo
echo "==================================================================="
echo "VM created and started successfully"
echo "  Name:      $VM_NAME"
echo "  VLAN:      $VLAN_ID"
echo "  IP:        $STATIC_IP"
echo "  Gateway:   $GATEWAY"
echo "  Login:     super"
echo "  Password:  $PASSWORD"
echo
echo "Verify OVS port VLAN tagging with: ovs-vsctl show"
echo "==================================================================="
