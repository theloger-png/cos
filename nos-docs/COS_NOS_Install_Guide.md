# COS + NOS Installation Guide

Step-by-step guide for installing NOS on a bare-metal SuperMicro node and the
COS controller VM on top of it.  Validated end-to-end on a fresh Ubuntu 24.04
install with dual Intel i40e (X710/X722) NICs as of 2026-06-14.

---

## 1. Hardware Requirements

| Component | Specification |
|---|---|
| Server | SuperMicro Dual Gold 5218, 64 GB RAM |
| Storage | 2 × 960 GB SSD |
| NICs (data) | 2 × 10G fiber, Intel X710/X722 (i40e driver) |
| NICs (mgmt) | 1 × 1G copper (management), 1 × 1G copper (OOB/IPMI) |
| OS | Ubuntu 24.04 LTS (fresh minimal install) |

---

## 2. Ubuntu Base System

1. Install Ubuntu 24.04 LTS — minimal server variant, no extra packages.
2. Assign a static IP to the management NIC via netplan.
3. Ensure the management NIC is **not** the i40e port — NOS takes over i40e
   ports for data-plane use.

---

## 3. NOS Installation

### 3.1 Clone the Repository

```bash
cd ~
git clone https://github.com/theloger-png/nos nos-dev
cd nos-dev
```

### 3.2 Run the Install Script

The install script must be run as root.  It installs all system dependencies,
builds the PFE (XDP/eBPF) binary, creates the Python venv, installs systemd
services, generates the REST API key, and installs the libvirt hook.

```bash
sudo bash scripts/nos-install.sh
```

**After installation completes**, log out and log back in so that your user
inherits the `nos`, `frr`, and `frrvty` group memberships added during install.
A simple `newgrp nos` is not sufficient — a fresh SSH login is required.

### 3.3 Apply the Running Configuration

```bash
sudo systemctl start nos-pfe
sudo systemctl start nos-api
nos-cli
```

Inside nos-cli, apply the default empty config:

```
admin@nos01> configure
admin@nos01# commit
```

### 3.4 Residual IP Cleanup (if needed)

If netplan previously assigned an IP directly to a data NIC (e.g. `ens33`),
remove it before configuring NOS access ports:

```bash
sudo ip addr del 192.168.1.100/24 dev ens33
```

Then `commit` inside nos-cli to fully reapply the running config (this
restores any missing default routes).

---

## 4. Network Configuration

### 4.1 Interface Aliasing (Optional)

Map physical interface names to JunOS-style names:

```
admin@nos01# set system interface-rename ens33 et0
admin@nos01# set system interface-rename ens34 et1
admin@nos01# commit
```

### 4.2 VLANs

```
admin@nos01# set vlans mgmt vlan-id 115
admin@nos01# set vlans mgmt l3-interface irb.115
admin@nos01# set interfaces irb unit 115 family inet address 10.111.1.1/24
admin@nos01# commit
```

### 4.3 Trunk Port for VM Traffic

```
admin@nos01# set interfaces et1 unit 0 family ethernet-switching interface-mode trunk
admin@nos01# set interfaces et1 unit 0 family ethernet-switching vlan members all
admin@nos01# commit
```

### 4.4 Static Default Route

```
admin@nos01# set routing-options static route 0.0.0.0/0 next-hop <gateway-ip>
admin@nos01# commit
```

---

## 5. COS Controller VM

### 5.1 Install KVM / libvirt

```bash
sudo apt-get install -y qemu-kvm libvirt-daemon-system virtinst bridge-utils
sudo systemctl enable --now libvirtd
```

### 5.2 Create the nos-br Bridge

NOS uses a Linux bridge named `nos-br` as the attachment point for VM tap
interfaces.  NOS creates and manages this bridge automatically when the first
switching interface is committed.

Verify it exists after a `commit`:

```bash
bridge link show
```

### 5.3 Install the COS Controller VM

Use `virt-install` or `virsh` to create the VM.  Attach its primary NIC to
the `nos-br` bridge so it lands on the management VLAN.

Example `virt-install` invocation:

```bash
virt-install \
  --name cos-ctrl \
  --ram 4096 --vcpus 4 \
  --disk path=/var/lib/libvirt/images/cos-ctrl.qcow2,size=60 \
  --cdrom /path/to/ubuntu-24.04-server.iso \
  --network bridge=nos-br,model=virtio \
  --os-variant ubuntu24.04
```

### 5.4 COS Agent Installation

After the controller VM is running, SSH in and install the COS agent following
the COS repository README.

### 5.5 Verify Connectivity

From the NOS node:

```bash
ping 10.111.1.X   # COS controller IP on management VLAN
```

From inside the COS controller VM:

```bash
ping 10.111.1.1   # NOS IRB.115 gateway
```

---

## 5.6 Configurare VLAN pentru interfata VM in NOS

> **Note:** As of nos-install.sh revision 2026-06-14, this step is automated
> by the libvirt hook described in section 5.7.  Manual configuration is only
> needed when the hook is not installed or when you need a non-default VLAN
> and cannot edit the domain XML.

When a KVM VM is started and libvirt creates a `vnetX` tap interface attached
to `nos-br`, NOS must know which VLAN to assign to that tap port before
Ethernet frames can be switched correctly.

**Manual procedure:**

```
admin@nos01# set interfaces vnet0 unit 0 family ethernet-switching interface-mode access
admin@nos01# set interfaces vnet0 unit 0 family ethernet-switching vlan members 115
admin@nos01# commit
```

Replace `vnet0` with the actual tap interface name shown by `ip link` or
`virsh domiflist <vm-name>`.

When the VM is destroyed or shut down, clean up the now-stale config:

```
admin@nos01# delete interfaces vnet0
admin@nos01# commit
```

---

## 5.7 Automatic vnetX Provisioning via the libvirt Hook

`nos-install.sh` installs a libvirt QEMU hook at `/etc/libvirt/hooks/qemu`
that automates the manual steps in section 5.6.

### How It Works

Libvirt calls the hook script on every domain lifecycle event.  The hook acts
on two events only:

| Event | Sub-operation | Action |
|---|---|---|
| `started` | `begin` | Adds each `vnetX` attached to `nos-br` as an access port on the configured VLAN; commits. |
| `stopped` or `release` | `end` | Deletes each such `vnetX` from NOS config; commits. |

All other events are ignored; the hook always exits 0 so it never blocks a VM
from starting or stopping.

### VLAN Selection

The hook reads the target VLAN from a custom `<nos:vlan>` element in the
domain's `<metadata>` block:

```xml
<domain type='kvm'>
  <name>my-vm</name>
  <metadata>
    <nos:vlan xmlns:nos="https://github.com/theloger-png/nos">200</nos:vlan>
  </metadata>
  ...
</domain>
```

If the element is absent or contains an invalid value, the hook defaults to
**VLAN 115** (the management VLAN).

### Setting a Non-default VLAN

Edit the domain XML with:

```bash
sudo virsh edit my-vm
```

Add (or update) the `<metadata>` block above the `<devices>` element.  The
`xmlns:nos` attribute must be exactly `https://github.com/theloger-png/nos`.

Changes take effect the next time the VM is started.

### Automatic Ghost-Interface Cleanup

On every VM start (`started/begin`), the hook performs a best-effort cleanup
pass before provisioning the new `vnetX`:

1. **Query NOS running config** — `GET /api/v1/config?source=running` to find
   all configured `vnetX` interfaces with `ethernet-switching` config.
2. **Query the kernel** — check `/sys/class/net/vnetN` for each candidate to
   determine which tap devices currently exist.
3. **Remove ghosts** — for any `vnetX` present in NOS config but absent from
   the kernel (left over from a previous VM lifecycle that was force-destroyed),
   a `delete interfaces vnetX` command is added to the same config batch as the
   new provisioning commands.  A single commit covers both the cleanup and the
   new interface.

The new `vnetX` being provisioned is always present in the kernel by the time
the hook runs (`started/begin` fires after libvirt creates the tap), so it is
never incorrectly flagged as a ghost.

Cleanup failures (API unreachable, parse errors) are logged to syslog and
skipped — they never block VM startup or the provisioning of the new interface.

Example syslog output when ghosts are removed:

```
nos-libvirt-hook[...]: [my-vm] INFO: removing ghost interface: vnet0 (no longer in kernel)
nos-libvirt-hook[...]: [my-vm] INFO: removing ghost interface: vnet1 (no longer in kernel)
nos-libvirt-hook[...]: [my-vm] INFO: provisioning VLAN 115 for: vnet3
nos-libvirt-hook[...]: [my-vm] INFO: committed — VLAN 115 access port(s): vnet3
```

### Verifying the Hook

Start a VM and check syslog:

```bash
sudo journalctl -t nos-libvirt-hook --since "1 minute ago"
```

Expected log lines:

```
nos-libvirt-hook[...]: [my-vm] INFO: add for devices: vnet0 (started/begin)
nos-libvirt-hook[...]: [my-vm] INFO: provisioning VLAN 115 for: vnet0
nos-libvirt-hook[...]: [my-vm] INFO: committed — VLAN 115 access port(s): vnet0
```

Verify the interface appeared in NOS config:

```
admin@nos01> show configuration interfaces vnet0
unit 0 {
    family ethernet-switching {
        interface-mode access;
        vlan {
            members 115;
        }
    }
}
```

### Dependencies

The hook requires:

- `curl` — for REST API calls
- `xmllint` (package `libxml2-utils`) — for domain XML parsing
- `logger` — for syslog output (part of `util-linux`, always present)

All dependencies are installed by `nos-install.sh`.  If `xmllint` is
unavailable, the hook falls back to Python 3 (`xml.etree.ElementTree`), which
is always present on NOS nodes.

### Troubleshooting

**Hook not triggering:**
- Confirm `/etc/libvirt/hooks/qemu` exists and is executable (`ls -la /etc/libvirt/hooks/`).
- Confirm libvirtd was restarted after install: `sudo systemctl restart libvirtd`.

**API authentication errors:**
- Confirm `/opt/nos/api_key` exists and is readable by root: `sudo cat /opt/nos/api_key`.
- Confirm `nos-api` service is running: `systemctl status nos-api`.

**VLAN not applied:**
- Check `journalctl -t nos-libvirt-hook` for error lines.
- Verify the VLAN ID exists in NOS config: `show vlans`.

---

## 6. XDP / PFE Notes

### 6.1 i40e Native XDP

Intel X710/X722 NICs support native XDP via the legacy `XDP_FLAGS_DRV_MODE`
path (tier 2 in NOS's three-tier attach strategy).  BPF_LINK_CREATE (tier 1)
returns EOPNOTSUPP on kernel 6.8 for i40e — this is expected and handled
automatically.

### 6.2 Hardware VLAN Stripping

i40e disables hardware VLAN stripping when native XDP is attached.  NOS's XDP
program (`xdp_prog.c`) handles 802.1Q-tagged frames correctly by performing
local-delivery checks before the `vlan_map` redirect lookup.

### 6.3 PFE Restart Recovery

If SSH connectivity is lost after a network reconfiguration, use OOB/IPMI:

```bash
sudo systemctl restart nos-pfe
nos-cli
admin@nos01# commit
```

---

## 7. Post-Install Checklist

- [ ] `show interfaces` shows all physical NICs in correct forwarding mode
- [ ] `show forwarding` shows `xdp-native` for i40e ports
- [ ] Management VLAN (irb.115) has correct IP, reachable from network
- [ ] COS controller VM starts and gets IP via DHCP (or static config)
- [ ] `journalctl -t nos-libvirt-hook` shows clean provisioning on VM start
- [ ] `show vlans` shows `mgmt (115)` with correct member interfaces
