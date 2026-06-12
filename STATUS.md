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
- POST /api/v1/config/commit, POST /api/v1/config/rollback/{n}, GET /api/v1/config/compare (NOS passthrough)

### Scheduler
- Best-fit by free RAM ratio
- Filters online nodes that can fit cpu_cores + ram_mb + disk_gb
- Returns None if no node available

### NOS Client
- httpx async client for NOS REST API
- configure_vlan, delete_vlan, configure_interface, commit
- Graceful error handling (logs, returns False on failure)

### Agent
- Registers with controller on startup (upsert by ip_address)
- Saves controller-assigned node_id to /opt/cos/node_id
- Heartbeat every 30s: cpu_used, ram_used_mb, disk_used_gb, vm_statuses
- X-API-Key authentication to controller
- WebSocket server on :8091
- Commands: vm_create, vm_start, vm_stop, vm_reboot, vm_destroy, vm_migrate, vm_list, node_stats
- libvirt_driver: KVM VM lifecycle via libvirt Python bindings
- nos_driver: local NOS REST API wrapper for VLAN config

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
- Agent: installs KVM, libvirt, configures cos user
- Adds invoking user to cos group automatically
- Systemd services: cos-controller.service, cos-agent.service

### Database Schema (via Alembic)
- Tables: nodes, vms, tenants, networks, vm_templates, api_keys, users, alembic_version
- All migrations tracked in alembic/versions/

## Known Limitations / TODO

### Phase 1 Remaining
- Deploy on physical nodes (currently ESXi on bare metal)
- End-to-end VM creation test with real KVM + disk image
- Controller HA (PostgreSQL replication, Keepalived VIP)
- HTTPS/SSL for portal and API
- VLAN provisioning via NOS tested end-to-end

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
- NOS REST API for networking (same stack as NOS)

## Test Count
- Total: TBD - run pytest from project root

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
