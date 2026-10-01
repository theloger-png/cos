# COS Project TODO

## Completed

### Web Console
- [x] Web serial console for running VMs (xterm.js, ticket-authenticated WebSocket relay through the controller)
- [x] Web console endpoint behind portal authentication (short-lived single-use ticket, JWT/X-API-Key + tenant ownership check)

### Deployment / Install
- [x] Idempotent cos-install.sh that doubles as the update path
- [x] Backup/restore (--backup / --restore)
- [x] INSTALL.md (full bare-metal-to-cluster guide)
- [x] README.md rewritten (de-NOS'd)

## Next Up

### Console Validation and Edge Cases
- [ ] Validate console edge cases: window resize, VM stopped while console is open, same console in two tabs, stopped-VM button state
- [ ] Re-validate re-exec path on a real machine after the ORIG_ARGS fix (first run lost the --role argument and printed usage; re-running the same command worked)

### Template Image Distribution Across Nodes
- [x] Immediate fix (2026-10-01): create_vm() now raises instead of silently falling back to a blank disk when a template's image_path doesn't exist on the node the scheduler picked - found via a real bug onboarding cos-node2: a VM was created with an empty disk, booted straight to "no bootable device" with ~0.8s total CPU time ever consumed, and gave zero indication anything was wrong (no error anywhere, console just showed nothing) until someone opened a serial console and investigated why. The underlying gap itself is still unfixed, see below.
- [ ] The actual problem: VMTemplate.image_path is a plain node-local filesystem path (e.g. /var/lib/libvirt/images/noble-server-cloudimg-amd64.img) entered by hand when the template is created in the portal. It is never copied or synced to other nodes, and the scheduler (best-fit by free RAM) has no awareness of which nodes actually have a given template's image file present. With one node this never mattered; with 2+ nodes any template only usable on the node it was originally placed on fails (now loudly, thanks to the fix above, but still fails) on every other node.
- [x] Manual stopgap (2026-10-01): scripts/sync-template-images.sh - run once per new node, copies every *.img/*.qcow2 under /var/lib/libvirt/images/ from a source node that has them to one or more targets over plain SSH/scp (password-prompted, no stored credentials), fixing ownership/permissions on the way. Acceptable with 2-3 nodes, doesn't scale past that.
- [ ] Real fix options, needs a decision: (a) scheduler checks template image presence per-node before placing a VM (needs the agent to report which image paths exist, or the controller to track it), (b) agent auto-fetches the missing image from the controller or another node on demand before creating the VM (needs an image transfer path - could reuse the same qemu+ssh/scp-style approach as migration's NON_SHARED_DISK copy), (c) keep the manual script above as the permanent answer if the cluster never grows past a handful of nodes
- [ ] Once a real fix is chosen, remove the manual-copy requirement from node onboarding docs and retire sync-template-images.sh

### Deployment / Security / Reliability
- [ ] Install script: dpkg -s check before apt install, --upgrade-system flag
- [ ] Shared secret between controller and agent (protects /ws and /console)
- [ ] PostgreSQL password: generate at first install, store in a root-only file, use it in systemd unit and alembic URL; migration path for existing installs
- [ ] HTTPS for portal and API
- [ ] Scheduled automatic database backups, and a documented way to keep them off the controller VM
- [ ] VM adoption: import pre-existing libvirt domains (use the agent's vm_list command) into the controller database
- [ ] cos-update wrapper or documented one-liner for updating all nodes

### Portal CRUD for Templates and Networks
- [ ] Edit form for Templates (update cloud_init_user, resource defaults)
- [ ] Edit form for Networks (update vlan_id, cidr, gateway, tenant)
- [ ] Delete confirmations with resource count (N VMs use this template, etc.)

### VM Password and ISO Management
- [x] Password reset for running VM, live through qemu-guest-agent (no reboot)
- [x] Offline password reset fallback for stopped VMs (2026-10-01) - implemented via libguestfs instead of the originally planned seed-rebuild approach, since a seed rebuild would have rerun every cloud-init per-instance module (SSH host key regen, network config overwrite, hostname reset) rather than just the password. cos-pw-reset-helper edits only the password hash and lastchg fields of /etc/shadow directly on the disk image. Validated end-to-end on cos-node1.
- [ ] Bake qemu-guest-agent into the base images used by templates, so isolated VMs (no network at first boot) still get the agent
- [ ] CD/ISO management (upload, attach to VM, set boot order)
- [ ] Create VM without template (blank disk + boot from attached ISO)

### Web Console - Advanced (Graphical)
- [ ] noVNC web console for VM access (graphical, separate from the serial console)
- [ ] Design: how to integrate the graphical console with the existing hardware editor flow

## Planned - Larger Initiatives

### Unified First-Node Installer
- [ ] "--role node": unified first-node install for a clean Ubuntu 24.04 node
  - Run after the standard Ubuntu installer (static IP set there)
  - whiptail menu: hostname, role, management IP/prefix, gateway, DNS, management VLAN (empty=untagged or a number=tagged)
  - Open vSwitch bridge with automatic rollback if connectivity is lost (like "commit confirmed")
  - Creation of the controller VM from the Ubuntu cloud image
  - Controller install inside it
  - Local agent install with the credentials passed automatically
  - Must be validated on real hardware
- [ ] Optional customized installer ISO (autoinstall) only if many nodes need installing; not needed now

### VM Live Migration
- [x] cos-node2 onboarded (2026-10-01): second physical node joined, OVS management migrated, data NIC trunked - see STATUS.md Known Issues for a real bug hit during onboarding (OVS internal port receiving zero traffic)
- [x] VIR_MIGRATE_NON_SHARED_DISK added to migrate_vm() (2026-10-01) - storage is per-node local, not shared, so this flag is required to copy the disk over the migration connection; untested against a real second node until cos-node2 existed
- [x] scripts/setup-cluster-ssh.sh (2026-10-01) - one-time bootstrap giving the cos user passwordless SSH between all nodes (qemu+ssh:// migration SSHes as whichever user runs cos-agent, i.e. cos, with no explicit user in the URI)
- [ ] Actual live migration test between cos-node1 and cos-node2 - pending, blocked on the template image gap (see "Template Image Distribution Across Nodes" above) needing to be worked around first so there's a real bootable VM to migrate
- [ ] Later: shared storage (NFS first, Ceph when 3+ nodes) as an alternative to NON_SHARED_DISK's wire copy, worth revisiting for larger disks where a live wire copy is slow
- [ ] Requirements to document once validated: same OVS bridge and VLANs on destination (now true for cos-node1/cos-node2), compatible CPU, node_id updated in database, rollback on failure

### Multi-POP Design
- [ ] Multi-POP design notes (treat each POP as a failure and storage domain, live migration only inside a POP, regional controllers with thin global layer instead of one controller for 100 nodes; L2 between POPs via VXLAN with one VNI per tenant network)
  - VTEP options: (A) OVS static VXLAN (does not scale), (C) Linux bridge + kernel VXLAN + FRR EVPN (industry standard but departs from OVS), (D) COS controller as control plane
  - Constraints: MTU (VXLAN adds ~50 bytes), encryption over public links (IPsec/WireGuard), ARP suppression, BUM with ingress replication, loop protection, witness vote for 2-node cluster (Postgres/etcd)
- [ ] Networking model decision pending: keep OVS with static VXLAN for a few sites versus moving tenant networking to Linux bridge + FRR EVPN

## Deferred - Lower Priority

### Edge Router (Future)
- [ ] Design: dedicated router VM for tenant VLAN routing (L3 gateway)
  - Possible implementation: NOS VM with trunk interface on nos-br, per-VLAN IRBs configured manually
  - COS Network.cidr/gateway remain informational only for now
  - No automatic IRB provisioning until router is set up
  - To be designed later; NOS is one possible option, not a dependency

### AWS-Style Routing/Firewall/NAT Menu (Future)
- [ ] Once edge router exists, add COS UI section for route tables, security groups, NAT rules (via edge NOS REST API)

### Portal Bundle Size Optimization
- [ ] Portal bundle is over 1 MB (no code splitting)
  - Consider code-splitting (dynamic imports per route)
  - npm reports audit warnings (2 moderate, 7 high)
