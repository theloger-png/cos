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
- POST /api/v1/vms/{id}/reset-password (resets the guest user's password; tries live via qemu-guest-agent if the VM is running, falls back to offline via libguestfs if stopped; returns the new password once, optional body {user}, default user = template cloud_init_user or ubuntu)
- GET /api/v1/operations: operation log for the portal console (newest first; query: limit<=500, before, status, user, action prefix). Admin callers (no tenant) see everything, tenant callers only their tenant's operations
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
- Commands: vm_create, vm_start, vm_stop, vm_reboot, vm_destroy, vm_migrate, vm_list, node_stats, vm_set_password (configure_vlan/remove_vlan removed 2026-09-24)
- libvirt_driver: KVM VM lifecycle via libvirt Python bindings
- NIC VLAN tagging handled in libvirt_driver via OVS domain XML (nos_driver removed)

### Operation log (portal bottom console)
- Every state-changing API call (POST/PUT/PATCH/DELETE, incl. endpoints added later) is recorded by an ASGI middleware (controller/api/operations_middleware.py) as a row in `operations`: user (JWT username or `api-key:<description>`), what, target, start/finish time, status running/success/failed, progress, error. Logins (success and failed) are recorded too; passwords/tokens are never stored. Unauthenticated requests, reads, node heartbeats and console tickets are not recorded
- System events under user `system`: node offline (heartbeat monitor), node back online, VM status changes reported by heartbeats
- Progress is real only for template image fetch (agent streams `{"progress": {"done", "total"}}` frames over the same /ws connection while curl downloads; the controller averages the per-node percentages). Everything else is indeterminate (progress NULL) until it finishes. A handler can override an HTTP-200 outcome with operations.set_outcome() (fetch-image does, when every node failed)
- Retention 90 days (daily purge); operations left "running" by a controller restart are marked failed at start-up
- Portal: OperationsConsole fixed at the bottom of every page, collapsible, last 5 operations visible when collapsed; expanded = resizable panel (height and open state remembered in localStorage), filters (type, user, status), click a row for details/error, "Load more" up to 500; polls every 2s

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
- Tables: nodes, vms, tenants, networks, vm_templates, api_keys, users, operations, alembic_version
- nos_api_key column removed from nodes (migration f6a7b8c0d1e2)
- All migrations tracked in alembic/versions/

## Known Limitations / Known Issues

### Limitations (By Design or Deferred)
- Static IP on add-NIC only takes effect for stopped VMs whose seed state was recorded by this version (VMs created earlier keep their seed unchanged and report a nic_failures entry); no live/hot-plug static IP.
- Live NIC IP display and static-IP-on-add-NIC only work for VMs created after these changes (they need the guest-agent channel, qemu-guest-agent and the seed sidecar); no retrofit of older VMs, by design and not planned.
- Guest OS support for cloud-init-based provisioning is scoped to Linux distributions with cloud-init and a Debian/RHEL-family package manager (Ubuntu, Debian, RHEL, Rocky, AlmaLinux, etc.). Windows guests are NOT supported - they require cloudbase-init or similar, not cloud-init #cloud-config.
- VM seed ISOs and their .seed.json sidecar files in /var/lib/cos/seeds/ are not cleaned up when a VM is destroyed (pre-existing ISO leak).
- No adoption of pre-existing libvirt domains by a new/rebuilt controller (they keep running but are invisible until restored from a backup or adopted); there is no adoption feature yet.
- Offline password reset requires libguestfs and a prebuilt appliance on the agent node (set up automatically by cos-install.sh); encrypted disks and unusual partition layouts are not supported.

### Known Issues
- **Agent WebSocket (/ws and /console) has no authentication** - relies entirely on network reachability; needs a shared secret (or similar) before the agent port is exposed to less trusted networks.
- Postgres password is hardcoded cos/cos.
- No HTTPS yet (portal and API on plain HTTP).
- **A leftover kernel 802.1Q VLAN subinterface silently blocks the OVS internal management port it's meant to be replaced by** - hit and fixed during cos-node2 onboarding (2026-10-01). When migrating a node's management IP off a kernel VLAN subinterface (e.g. eno1np0.350) onto an OVS internal port with scripts/migrate-mgmt-to-ovs.sh, the old interface must be deleted outright (`ip link delete`), not just have its IP removed - leaving it present (even with no IP) causes the kernel's own 8021q demux to keep silently claiming every 802.1Q frame for that VID ahead of the OVS bridge hook on the same physical NIC, so the new OVS port receives zero rx traffic forever with no error anywhere and every OVS-side setting (bridge, port, tag, flows) looking completely correct. The script now deletes this device itself (fixed alongside a second bug where its old-netplan-file detection regex didn't match cloud-init's quoted YAML values, `link: "eno1np0"` vs the unquoted form it expected). If this resurfaces on a future node migrated by an older checkout, diagnose it the same way: `ovs-vsctl get interface <mgmt-port> statistics` showing tx_packets non-zero/un-dropped but rx_packets stuck at 0 is the signature of a second consumer upstream of OVS claiming the tagged frames first.
- apt-get install on already installed packages upgrades them (seen with libvirt on a node with running VMs); install script should check with dpkg -s first and offer an explicit --upgrade-system flag.
- Re-exec after a git pull that changes the script itself: first real run (2026-10-01) exposed a bug (original arguments were lost after the parse loop's shift, so the script printed usage instead of installing); fixed by saving ORIG_ARGS before parsing. The fixed re-exec path still needs one real-machine validation (next update that changes cos-install.sh).
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
- 309 passed in the development sandbox (2026-09-28); 313 test functions exist in tests/unit, the difference is test_migration_cloud_init.py which does not collect in the sandbox. Run pytest tests/unit on a machine with the full requirements-dev.txt to get the authoritative number.

## Recent Changes

Latest updates:
- Operations console (2026-10-02): new `operations` table (migration b7c8d9e0f1a2), GET /api/v1/operations, recording middleware, system events and real image-download progress; portal bottom console with live 2s polling. 56 new tests (SQLite-backed middleware/API tests need aiosqlite, added to requirements-dev.txt). Not yet validated on a real controller/browser session: run scripts/cos-install.sh --role controller (applies the migration) and --role agent (needs the new agent code for progress frames; an old agent still works, the bar is just indeterminate).
- Template images downloadable from a URL and distributed to every node from the portal (POST /api/v1/templates/{id}/fetch-image): replaces manually scp-ing image files between nodes. Agent downloads directly via curl (skips if an identically-sized file is already present), runs off the event loop via asyncio.to_thread since a large image can take several minutes; controller fans the download out to every online node concurrently and persists image_path/image_url on any success. Needed a one-time permission fix in cos-install.sh (/var/lib/libvirt/images ships root:root 711, unwritable by the unprivileged cos user). 27 new tests (agent, controller, ws_server dispatch), full suite re-verified with zero regressions.
- Second physical node (cos-node2) onboarded, live migration infrastructure: OVS management migrated to cos-node2 matching cos-node1's topology (found and fixed a real bug in scripts/migrate-mgmt-to-ovs.sh - see Known Issues below); data NIC trunked on nos-br; VIR_MIGRATE_NON_SHARED_DISK added to migrate_vm() so migration works with per-node local storage; new scripts/setup-cluster-ssh.sh (passwordless cos-user SSH between nodes, required for qemu+ssh:// migration) and scripts/sync-template-images.sh (manual stopgap for the newly-documented gap where template images aren't synced across nodes - see TODO.md); create_vm() now fails loudly instead of silently producing a diskless VM when a template's image is missing on the node the scheduler picked.
- Offline password reset (VM stopped, via libguestfs): cos-pw-reset-helper C binary edits only the password hash and lastchg fields of /etc/shadow directly on the disk image - no cloud-init seed rebuild, so network config/SSH keys/hostname are untouched; controller tries live reset first and falls back to offline automatically when the VM isn't running; Reset password button now active for both running and stopped VMs. Requires libguestfs-dev + a prebuilt fixed appliance (cos-install.sh builds it with supermin and sets kvm group membership for acceleration). Validated end-to-end on cos-node1 (test1 VM): helper run, VM restarted, login with the new password confirmed working.
- VM actions in the portal: confirmation dialogs for Stop and Delete (VMs) and Delete (Networks, Templates); new Reset password button (live, through the guest agent) with a reusable one-time credentials dialog
- Web serial console for running VMs: xterm.js frontend, ticket-authenticated WebSocket relay through controller to agent libvirt stream
- cos-install.sh fully idempotent: re-run to update any machine (handles git pull, package reinstall, service restart with rollback on failure)
- Backup and restore: pg_dump + secrets + config in a single 0600 tar.gz file
- OVS networking: VM VLAN tagging via native libvirt domain XML, no external API needed
- Cloud-init provisioning: per-VM generated credentials, static IP or DHCP, qemu-guest-agent for live IP reporting

Full history: see [CHANGELOG.md](CHANGELOG.md)
