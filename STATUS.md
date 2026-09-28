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

### VM Console
- Web-based serial console for running VMs, no external viewer needed
- Flow: browser opens a WebSocket to the controller -> controller relays raw bytes to the owning agent's own /console WebSocket -> agent attaches to the VM's libvirt pty serial stream (controller never touches libvirt directly, per the hard rule)
- Auth: portal requests a short-lived, single-use ticket via POST /api/v1/vms/{id}/console-ticket (normal JWT/X-API-Key auth + tenant ownership check), then opens the console WebSocket with that ticket as a query param, since the browser cannot attach a bearer credential to a WS handshake
- Requires the guest to have a serial getty on the attached device (ttyS0/isa-serial) - default on Ubuntu cloud images, no extra guest config needed
- Portal: VMConsole.tsx renders the stream with xterm.js; "Console" button on the VMs page, enabled only while the VM is running
- Requires the agent's libvirt event loop to actually run (fixed in this session - see Recent Changes) for the console stream to deliver data at all

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
- scripts/cos-install.sh --role controller|agent - fully idempotent, doubles as the update path with zero manual steps (root/OS check before git pull, tracked-file changes discarded via "git checkout -- ." before "git pull --ff-only", venv/secrets only created if missing, portal rebuilt and atomically swapped in, nginx config validated with "nginx -t" and rolled back on failure, systemd units only rewritten when changed, both services always restarted)
- Never regenerates or overwrites secrets/credentials/user data on re-run: admin_api_key, admin_password, agent.env and node_id are all left untouched once they exist
- scripts/cos-install.sh --role controller --backup [FILE] / --restore FILE: pg_dump + secrets + config into a single 0600 tar.gz, and restore onto a fresh controller (drops/recreates the DB, reloads the dump, restores secrets/config, re-runs migrations)
- Controller: installs PostgreSQL, Node.js 20, nginx, builds portal, runs migrations
- Agent: installs KVM, libvirt, openvswitch-switch, configures cos user (no longer added to nos group)
- Adds invoking user to cos group automatically
- Systemd services: cos-controller.service, cos-agent.service
- See INSTALL.md for the full bare-metal-to-cluster install guide

### Database Schema (via Alembic)
- Tables: nodes, vms, tenants, networks, vm_templates, api_keys, users, alembic_version
- nos_api_key column removed from nodes (migration f6a7b8c0d1e2)
- All migrations tracked in alembic/versions/

## Known Limitations / Known Issues

### Limitations (By Design or Deferred)
- Static IP on add-NIC only takes effect for stopped VMs whose seed state was recorded by this version (VMs created earlier keep their seed unchanged and report a nic_failures entry); no live/hot-plug static IP.
- Live NIC IP display and static-IP-on-add-NIC only work for VMs created after these changes (they need the guest-agent channel, qemu-guest-agent and the seed sidecar); no retrofit of older VMs, by design and not planned.
- Guest OS support for cloud-init-based provisioning is scoped to Linux distributions with cloud-init and a Debian/RHEL-family package manager (Ubuntu, Debian, RHEL, Rocky, AlmaLinux, etc.). Windows guests are NOT supported - they require cloudbase-init or similar, not cloud-init #cloud-config.
- VM seed ISOs and their .seed.json sidecar files in /var/lib/cos/seeds/ are not cleaned up when a VM is destroyed (pre-existing ISO leak).
- No adoption of pre-existing libvirt domains by a new/rebuilt controller (they keep running but are invisible until restored from a backup or adopted); there is no adoption feature yet.

### Known Issues
- **Agent WebSocket (/ws and /console) has no authentication** - relies entirely on network reachability; needs a shared secret (or similar) before the agent port is exposed to less trusted networks.
- Postgres password is hardcoded cos/cos.
- No HTTPS yet (portal and API on plain HTTP).
- apt-get install on already installed packages upgrades them (seen with libvirt on a node with running VMs); install script should check with dpkg -s first and offer an explicit --upgrade-system flag.
- Re-exec after a git pull that changes the script itself has not been exercised on a real machine yet.
- Console browser edge cases not yet validated: window resize, VM stopped while console is open, same console in two tabs, stopped-VM button state.
- Portal bundle is over 1 MB (no code splitting) and npm reports audit warnings (2 moderate, 7 high).
- VITE_API_URL hardcoded at build time in .env.production - needs dynamic config for multi-env.
- node-1 (manually registered, no agent) shows "0s ago" heartbeat - cosmetic only.

## Phase 2 - Planned

### Infrastructure
- Controller HA: PostgreSQL streaming replication, Keepalived VIP
- HTTPS/SSL: Let's Encrypt or self-signed for internal use
- Unified first-node installer ("--role node"): whiptail menu, OVS bridge setup with automatic rollback, controller VM creation, local agent install (see TODO.md, Unified First-Node Installer)
- Shared secret between controller and agent: protects /ws and /console WebSocket endpoints (see TODO.md, Deployment / Security / Reliability)

### Features
- K3s container support
- Tenant L2 overlay (VXLAN): decision pending between OVS static VXLAN and Linux bridge + FRR EVPN (see TODO.md, Multi-POP Design)
- Live migration tested end-to-end
- VM adoption: import pre-existing libvirt domains into COS database (see TODO.md, Deployment / Security / Reliability)
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
- Total: 309 passed (2026-09-28) - run `pytest tests/unit` from project root
- Note: test_migration_cloud_init.py fails to collect (alembic install issue in this sandbox; passes on real machines)

## Recent Changes

Latest updates:
- Web serial console for running VMs: xterm.js frontend, ticket-authenticated WebSocket relay through controller to agent libvirt stream
- cos-install.sh fully idempotent: re-run to update any machine (handles git pull, package reinstall, service restart with rollback on failure)
- Backup and restore: pg_dump + secrets + config in a single 0600 tar.gz file
- OVS networking: VM VLAN tagging via native libvirt domain XML, no external API needed
- Cloud-init provisioning: per-VM generated credentials, static IP or DHCP, qemu-guest-agent for live IP reporting

Full history: see [CHANGELOG.md](CHANGELOG.md)
