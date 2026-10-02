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
- [x] Immediate fix (2026-10-01): create_vm() now raises instead of silently falling back to a blank disk when a template's image_path doesn't exist on the node the scheduler picked - found via a real bug onboarding cos-node2: a VM was created with an empty disk, booted straight to "no bootable device" with ~0.8s total CPU time ever consumed, and gave zero indication anything was wrong (no error anywhere, console just showed nothing) until someone opened a serial console and investigated why.
- [x] Manual stopgap (2026-10-01, superseded by the real fix below): scripts/sync-template-images.sh - copies image files between nodes over plain SSH/scp. Kept in the repo as a fallback for scp-ing something that isn't fetchable by URL (a locally-built custom image, say), but no longer the primary path.
- [x] Real fix (2026-10-01): templates now have an image_url (portal: Templates page, Image URL field); POST /api/v1/templates/{id}/fetch-image fans the download out to every currently online node directly (each node's agent runs its own curl, no controller-side proxying of potentially large files), persists image_path/image_url on any success. Existing templates created before this feature (image_path only, no image_url) are migrated by opening the Download dialog and supplying a URL once. /var/lib/libvirt/images needed a one-time permission fix (cos-install.sh now chgrp/chmod's it) since it ships root:root 711 and the unprivileged cos user couldn't write there at all.
- [ ] Not yet covered: a template whose image genuinely can't be fetched by URL (hand-built custom image only present locally) still needs scripts/sync-template-images.sh or manual scp - not a regression, just outside this feature's scope.
- [ ] Scheduler still has no awareness of per-node image presence before placing a VM - creating a VM still assumes whoever set up the template already ran the download/fetch step on every node that might get scheduled to. Not a practical problem day-to-day (downloading once covers all current and future nodes going forward), but worth tightening eventually (e.g. scheduler prefers/requires nodes with the image present).

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
- [x] Fixed: cos user's shell changed from /usr/sbin/nologin to /bin/bash in cos-install.sh (2026-10-01) - found via the cluster-ssh verification test failing with "This account is currently not available": nologin refuses to run ANY command over SSH, not just interactive login, which would have broken qemu+ssh:// migration identically. Security boundary is unaffected: cos has no password set, so direct/password login stays impossible regardless of shell - only SSH key auth works, which is exactly what's being set up. Re-running cos-install.sh on already-provisioned nodes fixes their existing cos user too (not just fresh installs).
- [x] Migration disk handling (2026-10-02): sparse pre-create on destination, source/destination cleanup, seed cleanup on VM delete - found via cos-node1/node2 disk usage (destination disks were fully allocated, sources never deleted); needs validation on the real nodes after cos-install.sh --role agent on both
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
