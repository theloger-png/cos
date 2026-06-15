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
- [ ] noVNC web console for VM access (investigated NIC hotplug feasibility this session - confirmed working)
- [ ] Web console endpoint behind portal authentication
- [ ] Design: how to integrate with existing hardware editor flow

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
