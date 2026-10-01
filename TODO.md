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
- [ ] Password reset for running VM (regenerate cloud-init seed ISO + graceful reboot)
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
- [ ] VM live migration between nodes: libvirt supports it natively, agent already has vm_migrate command, but untested
  - Requires a second physical node
  - First version: live migration with disk copy over the dedicated migration VLAN
  - Later: shared storage (NFS first, Ceph when 3+ nodes)
  - Requirements to document: same OVS bridge and VLANs on destination, compatible CPU, seed ISOs and backing files copied over, node_id updated in database, rollback on failure

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
