# COS Project Status

## Completed - Phase 1

### Controller
- FastAPI app with uvicorn, CORS middleware
- Dual authentication: JWT Bearer (portal users) + X-API-Key (programmatic/agent)
- PostgreSQL with SQLAlchemy 2.0 async + asyncpg
- Alembic migrations for all schema changes
- Auto-generates admin API key (/opt/cos/admin_api_key) on first start
- Auto-generates admin user + password (/opt/cos/admin_password) on first start
- Background task: marks nodes offline after 90s without heartbeat

### Authentication
- User model: username, email, hashed_password (bcrypt), role, tenant_id
- Roles: admin, operator, viewer
- JWT tokens: HS256, 8h expiry
- POST /api/v1/auth/login, GET /api/v1/auth/me
- X-API-Key: SHA-256 hashed in api_keys table
- Dual auth: JWT tried first, fallback to X-API-Key
- Admin (tenant_id=None) sees all resources across tenants

### API Endpoints
- GET/POST/DELETE /api/v1/nodes + POST /api/v1/nodes/{id}/heartbeat
- GET/POST/DELETE /api/v1/vms + start/stop/reboot/migrate actions
- GET/POST/DELETE /api/v1/networks
- GET/POST/DELETE /api/v1/tenants + API key generation
- GET/POST/DELETE /api/v1/templates
- GET/PUT /api/v1/vms/{id}/hardware (CPU/RAM/disk/NIC editing)
- (Removed 2026-09-24: /api/v1/config/* NOS passthrough endpoints)

### Scheduler
- Best-fit by free RAM ratio
- Filters online nodes that can fit cpu_cores + ram_mb + disk_gb
- Returns None if no node available

### Networking (OVS)
- NOS integration fully removed (2026-09-24); controller/nos_client/, agent/nos_driver.py and agent/nos_api_client.py deleted
- VM NICs use native OVS VLAN tagging via libvirt domain XML (<virtualport type='openvswitch'/> + <vlan><tag id='X'/></vlan>), applied by libvirt + OVS at NIC attach/detach
- No external NOS API calls, hooks, or CLI commands are needed for VLAN provisioning
- A Network (name + vlan_id + optional cidr/gateway) is a DB record used to pick the VLAN tag at NIC creation

### Agent
- Registers with controller on startup (upsert by ip_address)
- Saves controller-assigned node_id to /opt/cos/node_id
- Heartbeat every 30s: cpu_used, ram_used_mb, disk_used_gb, vm_statuses
- X-API-Key authentication to controller
- WebSocket server on :8091
- Commands: vm_create, vm_start, vm_stop, vm_reboot, vm_destroy, vm_migrate, vm_list, node_stats (configure_vlan/remove_vlan removed 2026-09-24)
- libvirt_driver: KVM VM lifecycle via libvirt Python bindings
- NIC VLAN tagging handled in libvirt_driver via OVS domain XML (nos_driver removed)

### Portal
- React 19 + TypeScript + Vite + Tailwind CSS
- Login page with JWT authentication
- ProtectedRoute: redirects to /login if no token
- 401 interceptor: clears token and redirects to /login (except login endpoint)
- Dark/light theme toggle, default dark, persisted to localStorage
- Dashboard: stat cards (nodes, VMs, RAM, CPU), nodes online chart, recent VMs
- Nodes page: table with status badges, resource bars (CPU/RAM/disk), heartbeat time
- Node Detail: node info + VM list on that node
- VMs page: table with actions (start/stop/delete/migrate dialog)
- VM Create: form with template selector, optional node selector
- Templates page: table + create dialog
- Networks page: table + create dialog
- Tenants page: table + create dialog
- TopBar: username display, logout button
- Served by nginx on :80 as static files
- Proxies /api/ to controller :8090

### Deployment
- scripts/cos-install.sh --role controller|agent
- Controller: installs PostgreSQL, Node.js 20, nginx, builds portal, runs migrations
- Agent: installs KVM, libvirt, openvswitch-switch, configures cos user (no longer added to nos group)
- Adds invoking user to cos group automatically
- Systemd services: cos-controller.service, cos-agent.service

### Database Schema (via Alembic)
- Tables: nodes, vms, tenants, networks, vm_templates, api_keys, users, alembic_version
- nos_api_key column removed from nodes (migration f6a7b8c0d1e2)
- All migrations tracked in alembic/versions/

## Known Limitations / TODO

### Phase 1 Remaining
- feature/ovs-networking branch not yet merged to main
- Static IP on add-NIC only takes effect for stopped VMs whose seed state was recorded by this version (VMs created earlier keep their seed unchanged and report a nic_failures entry); no live/hot-plug static IP
- No automated script yet for the full nos-br + OVS internal port + netplan bootstrap from scratch (done manually on cos-node1; see scripts/migrate-mgmt-to-ovs.sh for the migration pattern) - would be a useful addition to scripts/
- Controller HA (PostgreSQL replication, Keepalived VIP)
- HTTPS/SSL for portal and API

### Known Issues
- node-1 (manually registered, no agent) shows "0s ago" heartbeat - cosmetic only
- Portal bundle size >500KB (no code splitting yet) - performance optimization deferred
- VITE_API_URL hardcoded at build time in .env.production - needs dynamic config for multi-env

## Phase 2 - Planned

### Infrastructure
- Controller HA: PostgreSQL streaming replication, Keepalived VIP
- HTTPS/SSL: Let's Encrypt or self-signed for internal use
- Deploy on physical nodes, remove ESXi

### Features
- K3s container support
- NOS VXLAN/EVPN integration (waiting for NOS Phase 2)
- Live migration tested end-to-end
- Advanced portal features (graphs, metrics, alerts)
- TACACS+ or LDAP integration
- Operator/viewer role enforcement in portal UI

## Architecture Decisions
- Python 3.12, FastAPI, SQLAlchemy 2.0 async
- PostgreSQL for cluster state
- WebSocket for controller-agent communication
- JWT + X-API-Key dual authentication
- bcrypt direct (not passlib - has bug with newer bcrypt versions)
- React + Vite SPA served by nginx
- Alembic for all DB schema changes
- libvirt-python for KVM management
- Open vSwitch for VM networking: native VLAN tagging via libvirt domain XML (replaced NOS REST API, 2026-09-24)

## Test Count
- Total: TBD - run pytest from project root

## Recent Changes (2026-09-24)
All on branch feature/ovs-networking (not yet merged to main).
- **NOS networking dependency fully removed**:
  - agent/libvirt_driver.py refactored: VM NICs use native OVS VLAN tagging via libvirt domain XML (<virtualport type='openvswitch'/> and <vlan><tag id='X'/></vlan>), applied automatically by libvirt + OVS at NIC attach/detach
  - Deleted agent/nos_driver.py, controller/nos_client/, and agent/nos_api_client.py (the last was orphaned dead code found and removed at the end of the session)
  - Removed nos_api_key from the Node DB model (Alembic migration f6a7b8c0d1e2); removed NOS config fields from agent/config.py and controller/config.py
  - Removed configure_vlan/remove_vlan WebSocket commands and the controller-side broadcast logic in controller/api/routers/networks.py
  - scripts/cos-install.sh no longer adds the cos user to the nos group; installs openvswitch-switch as an agent-role dependency
  - Earlier entries below that describe NOS provisioning are historical and superseded by this change
- **New scripts**:
  - scripts/create-test-vm.sh: creates a KVM VM with OVS VLAN tagging for manual testing; auto-downloads the Ubuntu Noble cloud image if missing
  - scripts/migrate-mgmt-to-ovs.sh: one-time migration of a node's management IP from a VLAN sub-interface on a physical NIC to an OVS internal port on the nos-br bridge
- **New feature: optional static IP at VM creation** (single NIC only for now):
  - Operators can provide an IP/CIDR and gateway when creating a VM
  - agent/libvirt_driver.py generates a MAC address explicitly for each VM NIC and, when a static IP is given, writes a cloud-init network-config v2 file (matched by MAC) into the seed ISO via cloud-localds --network-config; with no IP the guest uses DHCP (unchanged default)
  - Portal (VMCreate.tsx): optional "Static IP (CIDR)" and "Gateway" fields, shown when a Network is selected
  - Validated end-to-end on cos-node1/cos-controller: static IP applied inside the guest via cloud-init
- **New feature: live IP addresses in the hardware editor**:
  - agent get_vm_config() adds ip_addresses (IPv4 only) to each NIC via domain.interfaceAddresses(): qemu-guest-agent first, host ARP table as per-NIC fallback, matched by MAC; empty list for stopped VMs
  - Portal VMHardware.tsx shows them under each NIC's MAC
- **New feature: optional static IP when adding a NIC** (VM must be stopped):
  - AddNICRequest gains ip_cidr/gateway (both required, otherwise ignored); agent generates the new NIC's MAC explicitly and, if the domain is shut off, rebuilds its cloud-init seed ISO with a multi-NIC network-config
  - Every NIC in the domain is listed in the rebuilt network-config (recorded static entries kept, others DHCP) because a supplied network-config replaces cloud-init's DHCP fallback for unlisted interfaces
  - Seed inputs (user-data + static NICs) are now recorded in /var/lib/cos/seeds/<uuid>.seed.json (0600) at VM creation so the seed can be rebuilt without parsing the ISO; the rebuild uses a fresh instance-id, so cloud-init re-runs on next boot
  - Running/paused VMs: NIC is attached without static IP and a nic_failures entry says why
  - Seed ISOs and sidecars are not yet removed on VM destroy (pre-existing gap for the ISO)
- **Infrastructure migration validated on cos-node1** (fresh Ubuntu 24.04 install):
  - NOS uninstalled/unused; OVS installed via cos-install.sh
  - Topology: single OVS bridge "nos-br" carries both physical trunk uplinks (1G management-facing NIC and 10G data-facing NIC) plus an OVS internal port ("mgmtNNN", tagged with the management VLAN) for the node's own management IP, so VMs on the management VLAN reach the node locally over the bridge without traversing the physical switch
  - Management IP configured via netplan on the OVS internal port (not the physical NIC)
  - cos-controller VM recreated from scratch with scripts/create-test-vm.sh; controller role installed and validated (login, API key auth working)
  - cos-agent reinstalled on cos-node1 and re-registered with the new controller; heartbeat confirmed online
- **Fixed** (pre-existing bugs surfaced by the first real UI walkthrough in a while):
  - Tenants create form was missing the required "email" field (form and type updated)
  - Templates create form was missing required "os_type" and "image_path" fields (form and type updated)
  - VM Credentials modal copy buttons did nothing over plain HTTP (Clipboard API fails silently in non-secure contexts); fixed with a secure-context check, an execCommand('copy') fallback, error logging, and visual copy-success feedback
- **Operational note** (documented in CLAUDE.md): controller and agent run from an installed package in /opt/cos/venv, so git pull alone does not update running code; every deploy needs pip install --force-reinstall --no-cache-dir --no-deps into the venv followed by a service restart, for both controller and agent

## Recent Changes (2026-06-15)
- **Validated milestone** (late afternoon): Cloud-init credentials and VM hardware editing features (13 commits, 216 tests)
  - **Cloud-init credentials** (commits 6a793c0, 88e5236, 0be52ca, e87db9d, 0333e99, 6ccb624, 579eec9):
    - VMTemplate now has cloud_init_user field (default "ubuntu"), editable in portal Templates page
    - On vm_create, controller generates random 16-char password + SHA-512 hash
    - Agent builds cloud-init seed ISO (cloud-localds) with chpasswd for template's cloud_init_user, ssh_pwauth enabled, unique instance-id per VM
    - Portal shows one-time credentials modal after VM creation
    - Validated end-to-end: login via virsh console with generated credentials works correctly
    - Fixed: ws_server.py wasn't passing cloud_init_user/cloud_init_password_hash to create_vm (commit 579eec9)
    - Fixed: seed ISO permissions - libvirt-qemu needs read access to /var/lib/cos/seeds/ (added cos group, dir mode 755)
  - **VM hardware editing** (commits ef4dc6f, c4e4477, 59f8623, 195d273, 57dc1eb, efffced):
    - New GET/PUT /api/v1/vms/{id}/hardware endpoints for CPU/RAM/disk/NIC configuration
    - Agent: get_vm_config (parses domain XML, resolves NIC VLANs via NOS REST API) and apply_vm_config
    - Portal: new VM hardware editor page - edit vCPU/RAM, add secondary disks, add/remove NICs with COS Network selector, pending-changes summary, apply confirmation (reboot warning only for CPU/RAM/disk)
    - NIC add/remove are live (no reboot); CPU/RAM/disk changes trigger graceful shutdown → reconfigure → restart
    - Validated end-to-end on cos-node1/test111: disk add (vdb 10GB), RAM 2048→4096, vCPU 2→3 (all via reboot), NIC add/remove (live, correct VLAN auto-provisioned)
    - Fixed: NIC detach now uses minimal XML (mac/source/model only) and single libvirt flag (LIVE or CONFIG, not combined); NOS cleanup only on successful detach
    - Fixed: per-NIC failures surfaced in API/portal; NOS config response parsing now correctly resolves vlan_id per vnetX
    - Fixed: add_nics now sends correct "interface-mode access" + numeric "vlan members <id>" (was invalid "vlan members vlan<id>")
- **Validated milestone** (afternoon): Portal UI end-to-end validation - Networks create/delete and VM Create form working through browser
  - Network create/delete: UI now has optional cidr/gateway fields (L2-only by default), Tenant selector added to Networks dialog (commits 7ae5411, beb561f)
  - VM Create form: Tenant + Network selectors added, resource fields auto-populated from template defaults, 422 validation error display fixed (commit 7812f89)
  - Full chain validated: created VLAN 101 via portal Networks page at http://188.213.242.235 → controller → agent → NOS, confirmed with `show vlans` on cos-node1
  - Network access: DNAT via 185.45.15.70 → 188.213.242.235 → 10.111.1.203; note http:// required (no TLS configured, browsers default to https on :443)
  - Architecture decision: Edge router deferred to future - dedicated NOS VM with trunk interface on nos-br, per-VLAN IRBs manual via nos-cli; Network.cidr/gateway remain informational only for now
- **Validated milestone** (early): Network create/delete via COS API provisions/removes VLANs in NOS end-to-end (commit 459a78d)
  - Flow: POST /api/v1/networks → controller WebSocket to all online agents → agent nos_driver.py → configure_vlan/remove_vlan → NOS commit
  - Tested on cos-node1: created network vlan_id=202, confirmed in `nos-cli show vlans`; deleted, confirmed removal
  - Required: `cos` system user must be in `nos` group (cos-install.sh agent role now does `usermod -aG nos cos`)
- **Fixed**: cos-agent crash-loop when NOS API key file is unreadable (PermissionError at import time)
  - Root cause: cos user not in `nos` group → `/opt/nos/api_key` (mode 640 root:nos) unreadable → load_nos_driver raised at module import → entire agent down (heartbeats, vm_create, all commands)
  - Primary fix: cos-install.sh --role agent now adds cos to nos group (step A2b)
  - Defensive fix: nos_driver.py load_nos_driver() now catches OSError, returns a driver that logs and returns False on VLAN calls instead of crashing the process

## Recent Changes (2026-06-10)
- Implemented: COS initial structure - controller, agent, common, portal
- Implemented: PostgreSQL with SQLAlchemy 2.0 async + Alembic migrations
- Implemented: Dual authentication - JWT Bearer + X-API-Key
- Implemented: User model with roles (admin/operator/viewer)
- Implemented: Login page with JWT, ProtectedRoute, logout
- Implemented: Portal - React 19, Tailwind, dark/light theme, all pages
- Implemented: Agent heartbeat, node registration upsert, node_id persistence
- Implemented: cos-install.sh --role controller|agent
- Implemented: Portal served by nginx, built and deployed via install script
- Fixed: passlib replaced with bcrypt direct (detect_wrap_bug issue)
- Fixed: Admin users can list all VMs/networks (tenant_id=None check)
- Fixed: Node registration upsert by ip_address (no duplicate key errors)
- Fixed: Heartbeat endpoint 404 (endpoint was missing)
- Fixed: CORS middleware added to controller
- Fixed: created_at field missing from templates and networks API responses
- Fixed: Date formatting across all portal pages (formatDate helper)
- Fixed: GB values rounded to 2 decimal places in Nodes page
