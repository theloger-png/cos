# Installing COS

This guide walks through installing COS from bare metal: preparing a
physical node, creating the controller VM, deploying the controller and
agents, and day-to-day operations (updates, backup/restore). It assumes no
prior knowledge of COS.

## 1. What is COS

COS (Cloud Operating System) is a small cloud orchestrator: a central
**controller** exposes a REST API and a web **portal** for creating and
managing KVM virtual machines across one or more physical **nodes**. Each
node runs a lightweight **agent** that talks to libvirt on the controller's
behalf and to Open vSwitch (OVS) for VM networking (VLAN tagging is applied
natively via libvirt domain XML - there is no external networking daemon or
API involved).

```
                         Operator's browser
                                 │
                                 │ HTTP :80
                                 ▼
                    ┌────────────────────────┐
                    │      Controller VM      │
                    │  nginx :80 (portal)     │
                    │   └─ proxies /api/ to → │
                    │  FastAPI API :8090      │
                    │  PostgreSQL (local)     │
                    └───────────┬─────────────┘
                                │ WebSocket :8091
                    ┌───────────┼───────────────┐
                    ▼           ▼               ▼
               Agent (Node 1) Agent (Node 2) Agent (Node N)
               libvirt + OVS  libvirt + OVS   libvirt + OVS
               "nos-br" bridge, VM VLAN tagging
```

The controller VM itself runs as a KVM guest on one of the physical nodes,
attached to the same OVS bridge ("nos-br" by convention) as the VMs it
manages.

## 2. Requirements

- **Ubuntu Server 24.04 LTS** on every machine (physical nodes and the
  controller VM) - `cos-install.sh` checks `/etc/os-release` and refuses to
  run on anything else.
- Hardware with virtualization enabled (Intel VT-x / AMD-V) on every
  physical node that will run VMs.
- Internet access from every machine (controller and nodes) during
  install/update - packages, the Node.js 20 apt repo, and the Ubuntu cloud
  image are downloaded from the internet.
- **Static IPs on the management network.** The COS management network
  has no DHCP - every physical node and the controller VM must have a
  static IP. Assign it during the Ubuntu OS installer (see §3) by selecting
  the management NIC and entering an IP address, prefix, gateway, and DNS.
  Once configured there, the IP persists across reboots.
- Network reachability between machines on the management network:

  | From | To | Port | Purpose |
  |---|---|---|---|
  | Operator's browser | Controller | TCP 80 | Portal (proxies `/api/` and the console WebSocket to the API internally) |
  | Each agent node | Controller | TCP 8090 | Agent registration, heartbeat, VM commands (HTTP, direct, not via nginx) |
  | Controller | Each agent node | TCP 8091 | WebSocket command channel and VM console relay |

  The agent's WebSocket port (8091) has no authentication of its own yet
  (see §11) - it must not be reachable from anything less trusted than the
  controller's own network.
- A management network the physical nodes' NICs are on, either as a plain
  untagged (access) port or as one VLAN on a trunk port - both are
  supported (see §4).

## 3. Installing Ubuntu on a new physical node

If the node is remote, mount the standard Ubuntu Server 24.04 LTS ISO as
IPMI virtual media and watch the install through the IPMI console (SSH is
not available yet at this point).

In the installer:

- **Network**: on the management NIC, set a static IP, gateway and DNS
  (disable DHCP). If the management port is tagged on the switch, the
  installer's network screen also lets you add a VLAN on that NIC and
  configure the static IP on the VLAN sub-interface instead.
- **Hostname**: pick something recognizable (e.g. `cos-node1`).
- **User**: create a regular user; enable **OpenSSH server** so you can
  manage the node remotely afterwards.
- **Storage**: use the guided disk layout unless you have a specific
  partitioning requirement.

After the first reboot, confirm the node has internet access and is
reachable over SSH from your workstation. Keep IPMI as your safety net for
the rest of this guide - any step that touches host networking can cut SSH,
and IPMI is the only way back in if that happens.

## 4. Node preparation from a clean Ubuntu 24.04

Do this on every physical node that will run VMs (including the node that
will host the controller VM).

### 4.1 Base packages and the repo

```bash
sudo apt-get update
sudo apt-get install -y git openvswitch-switch
git clone <your-cos-repo-url> ~/cos
```

`scripts/cos-install.sh` (run in §6/§7) installs the rest (KVM/libvirt,
Python, etc.) itself - `openvswitch-switch` is installed here first because
the bridge below needs it.

### 4.2 KVM/libvirt sanity check

The install script installs `qemu-kvm` and `libvirt-daemon-system`, but you
can confirm virtualization is usable at any point after that with:

```bash
virsh list --all
```

An empty table (no error) means libvirtd is running and reachable.

### 4.3 Open vSwitch bridge ("nos-br")

COS's agent attaches every VM NIC to an OVS bridge named **`nos-br`**
(`agent/config.py`'s `vm_bridge` default) and tags VLANs natively via
libvirt domain XML - there is no separate VLAN-provisioning step. The
bridge itself, however, is **not** created automatically; you create it
once per node. There are two cases depending on how your switch presents
the management port:

**Case A - management port untagged (access port).** The simplest case:
leave the node's existing management IP exactly as the installer configured
it, and dedicate a separate physical NIC (the data-facing interface) to
carrying VM traffic:

```bash
sudo ovs-vsctl add-br nos-br
sudo ovs-vsctl add-port nos-br <data-facing-iface>
```

No changes to the management interface are needed.

**Case B - management port tagged (trunk carrying the management VLAN
too).** Create the bridge and add the trunk NIC(s), then move the node's
own management IP off the VLAN sub-interface and onto an OVS internal port
using the helper script:

```bash
sudo ovs-vsctl add-br nos-br
sudo ovs-vsctl add-port nos-br <trunk-iface>

sudo ~/cos/scripts/migrate-mgmt-to-ovs.sh \
    -v <MGMT_VLAN_ID> -i <STATIC_IP>/<PREFIX> -g <GATEWAY>
```

**Run this from the IPMI console, not over SSH** - it briefly interrupts
connectivity on the interface it reconfigures. The script backs up the old
netplan VLAN file (never deletes it) and prints revert instructions if the
new config doesn't come up; see `scripts/migrate-mgmt-to-ovs.sh -h` for all
flags (bridge/port names default to `nos-br` / `mgmt<VLAN_ID>`).

Verify the bridge afterwards with `sudo ovs-vsctl show`.

## 5. Creating the controller VM on a node

Pick one physical node (prepared per §4) to host the controller VM. The
repo includes `scripts/create-test-vm.sh`, which downloads the Ubuntu Noble
cloud image, creates a per-VM disk, builds a cloud-init seed with a static
IP, and defines+starts a libvirt domain with its NIC tagged for the right
VLAN on `nos-br` - exactly what the controller VM needs:

```bash
cd ~/cos
./scripts/create-test-vm.sh \
    -n cos-controller -v <MGMT_VLAN_ID> \
    -i <CONTROLLER_STATIC_IP>/<PREFIX> -g <GATEWAY> \
    -r 4096 -c 4 -d 60
```

This logs you in as user `super` (NOPASSWD sudo, your own
`~/.ssh/id_ed25519.pub` key, and a randomly generated password printed at
the end of the run). If your management port is untagged (Case A in §4.3),
the controller VM's NIC still needs to land on the right network without an
OVS VLAN tag; either point `-v` at the bridge's native/access VLAN, or
adapt the `virt-install`/domain-XML steps inside the script (drop the
`<virtualport>`/`<vlan>` injection) for a plain access-port attachment.

Verify the VM is up and reachable before continuing:

```bash
ping <CONTROLLER_STATIC_IP>
ssh super@<CONTROLLER_STATIC_IP>
```

**Known cloud-init pitfall:** if you ever rebuild a VM's disk from the same
base image while reusing an old seed ISO (same `instance-id`), cloud-init
will recognize the instance as "already provisioned" and skip re-running
network configuration - the guest keeps whatever `/etc/netplan/
50-cloud-init.yaml` was baked in from the previous run, which can leave it
on the wrong IP or unreachable. Always let the script generate a fresh
seed (it does, by default) rather than copying one between VMs.

## 6. Deploying the controller

Inside the controller VM:

```bash
git clone <your-cos-repo-url> ~/cos
sudo ~/cos/scripts/cos-install.sh --role controller
```

This installs PostgreSQL, Node.js 20 and nginx, runs the database
migrations, builds and deploys the portal, installs the nginx and systemd
configs, and starts `cos-controller`. On first run it prints:

```
Admin password: <generated>
Admin API key: <generated>
Portal: http://<controller-ip>
```

- Admin password is stored at `/opt/cos/admin_password` (mode 640).
- Admin API key is stored at `/opt/cos/admin_api_key` (mode 640).

Re-running this same command later (see §9) never regenerates these - it
only reprints "unchanged (see ...)".

Log in to the portal at `http://<controller-ip>` with username `admin` and
the printed password. Verify the API directly with:

```bash
curl -H "X-API-Key: $(sudo cat /opt/cos/admin_api_key)" http://<controller-ip>:8090/api/v1/nodes
```

## 7. Deploying an agent on each node

On every physical node that will run VMs (prepared per §4), including the
node hosting the controller VM if it should also run tenant VMs:

```bash
git clone <your-cos-repo-url> ~/cos
sudo ~/cos/scripts/cos-install.sh --role agent \
    --controller-url http://<controller-ip>:8090 \
    --controller-api-key <admin-api-key-from-step-6>
```

(Omit the two flags to be prompted interactively instead.) This installs
KVM/libvirt/OVS, generates a node ID, writes `/opt/cos/config/agent.env`,
and starts `cos-agent`. The node should show up **online** in the portal's
Nodes page within about 35 seconds (heartbeat interval plus a little
slack).

## 8. First steps after install

1. **Tenant** - portal → Tenants → create one (e.g. "admin" already exists
   by default for the initial admin user).
2. **Network** - portal → Networks → create one with a VLAN ID (and
   optionally a CIDR/gateway, informational only for now).
3. **Template** - portal → Templates → create one pointing at a base image
   path already present on the node(s) (e.g. an Ubuntu 24.04 cloud image).
4. **VM** - portal → VMs → Create, pick the template/network/node.
5. **Console** - once the VM is `running`, click **Console** on the VMs
   page to open its serial console over the browser. This requires the
   guest to have a serial getty on the console device COS attaches
   (`ttyS0`/`isa-serial`) - this is the default on Ubuntu cloud images, no
   extra guest configuration needed.

## 9. Updating an existing installation

`git pull` is handled by the script itself. To update either machine, just
re-run the exact same command you used to install it:

```bash
sudo ~/cos/scripts/cos-install.sh --role controller   # on the controller VM
sudo ~/cos/scripts/cos-install.sh --role agent         # on each node
```

**Preserved, never touched:** the admin password and API key
(`/opt/cos/admin_password`, `/opt/cos/admin_api_key`), the PostgreSQL
database, `/opt/cos/config/agent.env` (controller URL/API key), and
`/opt/cos/node_id`.

**Refreshed on every run:** the COS Python package and its dependencies,
the portal build (safely swapped in - the old portal stays live if the
build fails), the nginx config (validated with `nginx -t` before reload;
rolled back automatically if it fails), the systemd units (only rewritten
if their content actually changed), and the database schema (`alembic
upgrade head`). Both services are always restarted at the end so the new
code actually takes effect.

## 10. Backup and restore

Back up the controller (database + secrets + config) into a single file:

```bash
sudo ~/cos/scripts/cos-install.sh --role controller --backup
# or: --backup /path/to/file.tar.gz
```

This writes a 0600 `.tar.gz` (default `~/cos-backup-<timestamp>.tar.gz`)
and exits without installing or changing anything.

Restore onto a fresh controller VM (built per §5-§6, but skip the manual
`cos-install.sh --role controller` run - `--restore` does it for you):

```bash
sudo ~/cos/scripts/cos-install.sh --role controller --restore /path/to/backup.tar.gz
```

This runs the normal controller install first (so the schema and services
exist), then stops `cos-controller`, drops and recreates the `cos`
database, loads the dump, restores the admin secrets and `/opt/cos/config`
from the backup, re-runs migrations, and starts the controller again. It
asks for confirmation before dropping the database - pass `--yes` to skip
the prompt for scripted use.

**Warning:** deleting the controller VM without a backup loses all COS
inventory (nodes, VMs, tenants, networks, templates - everything in the
`cos` database). The VMs themselves keep running on their nodes (libvirt
doesn't need the controller), but a new controller has no record of them
and cannot manage them until they are adopted (not implemented yet, see
§11).

## 11. Troubleshooting

- **Controller logs:** `journalctl -u cos-controller -f`
- **Agent logs:** `journalctl -u cos-agent -f`
- **nginx config problems:** `sudo nginx -t` (the install script already
  refuses to reload a broken config, but this is the same check by hand)
- **VM image permission errors** ("Could not open
  `/var/lib/cos/images/*.qcow2`"): the file must be readable by
  `libvirt-qemu` after a manual copy - `sudo chmod o+r
  /var/lib/cos/images/*.qcow2`.
- **CORS or "Network Error" in the portal after an update:** the portal is
  always built with `VITE_API_URL` forced empty (it calls `/api/...`
  relative to whatever host served it) - if you see cross-origin errors,
  something is loading the portal from one host while the API is on
  another; check `nginx/cos-portal.conf`'s `proxy_pass` target.
- **Agent never shows "online":** check `journalctl -u cos-agent` on the
  node for a connection error to the controller URL in
  `/opt/cos/config/agent.env`, and confirm TCP 8090 is reachable from the
  node to the controller.

**Known gaps** (tracked in `TODO.md` and detailed in `STATUS.md` Known Limitations / Known Issues):
- No HTTPS yet - the portal and API are plain HTTP.
- The agent's WebSocket port (8091) has no authentication of its own; it
  relies entirely on network reachability from the controller - needs a
  shared secret before the agent port is exposed to less trusted networks.
- No controller HA (single PostgreSQL instance, no failover).
- No adoption of pre-existing libvirt domains by a new/rebuilt controller.
- PostgreSQL password is hardcoded "cos" - should be generated and stored
  securely before production use.
