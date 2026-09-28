# COS Project TODO

## Next Up

### Portal CRUD for Templates and Networks
- [ ] Edit form for Templates (update cloud_init_user, resource defaults)
- [ ] Edit form for Networks (update vlan_id, cidr, gateway, tenant)
- [ ] Delete confirmations with resource count (N VMs use this template, etc.)

### VM Password and ISO Management
- [ ] Password reset for running VM (regenerate cloud-init seed ISO + graceful reboot)
- [ ] CD/ISO management (upload, attach to VM, set boot order)
- [ ] Create VM without template (blank disk + boot from attached ISO)

### Web Console
- [x] Web serial console for running VMs (xterm.js, ticket-authenticated WebSocket relay through the controller) - done, see STATUS.md 2026-09-28
- [x] Web console endpoint behind portal authentication - done (short-lived single-use ticket, same JWT/X-API-Key + tenant ownership check as the rest of /vms)
- [ ] noVNC web console for VM access (graphical, separate from the serial console above; investigated NIC hotplug feasibility - confirmed working)
- [ ] Design: how to integrate the graphical console with the existing hardware editor flow

### Deployment / Install
- [x] Idempotent cos-install.sh that doubles as the update path - done, see STATUS.md 2026-09-28
- [x] Backup/restore (--backup / --restore) - done, see STATUS.md 2026-09-28
- [ ] PostgreSQL "cos" role/database password is hardcoded to "cos" - move to a generated secret stored under /opt/cos (see the TODO comment in scripts/cos-install.sh)
- [ ] Agent WebSocket (port 8091) has no authentication of its own - add a shared secret (or similar) between controller and agent
- [ ] HTTPS for the portal and the controller API
- [ ] Adoption of pre-existing libvirt domains by a new/rebuilt controller (so a lost-and-restored controller, or a fresh one, can discover VMs already running on nodes instead of only what --restore brought back)
- [ ] Automatic scheduled database backups (today's --backup is manual/on-demand only)
- [ ] "--role node": unified first-node install (whiptail menu, OVS bridge setup with automatic rollback if connectivity is lost, controller VM creation, local agent install) in a single run
- [ ] Optional customized installer ISO (Ubuntu 24.04 preseeded with the COS repo + install script) to skip the manual git-clone-and-run step in INSTALL.md §3-4

## Deferred Features

### Edge Router (Future)
- [ ] Dedicated VM running NOS will serve as L3 edge/gateway for tenant VLANs
  - Single trunk interface on nos-br carrying all tenant VLANs
  - Per-VLAN IRBs configured manually via nos-cli inside edge VM
  - COS Network.cidr/gateway fields remain informational only for now
  - No automatic IRB provisioning until edge node/VM is set up
  - Revisit when dedicated edge node/VM is in place

### AWS-Style Routing/Firewall/NAT Menu (Future)
- [ ] Once edge router exists, add COS UI section for:
  - Route tables
  - Security groups
  - NAT rules
  - Configured against edge NOS via REST API (similar pattern to VLAN broadcast)

### Portal Bundle Size Optimization
- [ ] Portal bundle is 811KB (gzip 250KB) - vite warns about chunk size
  - Consider code-splitting (dynamic imports per route)
  - Not urgent, deferred after edge router work
