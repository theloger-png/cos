# COS - Cloud Operating System
## Architecture Document v0.2

---

## 1. Overview

COS is a lightweight cloud orchestrator designed to manage KVM virtual machines
and K3s containers across multiple physical nodes. It is designed to work with
NOS (Network Operating System) as the networking layer.

Target scale: 2-10 physical nodes, hundreds of VMs, single region.

---

## 2. General Architecture

```
+------------------+         +------------------+
|   cos-portal     |         |   cos-cli        |
|   React SPA      |         |   (future)       |
|   nginx :80      |         |                  |
+--------+---------+         +--------+---------+
         |  HTTPS (future)            |
         +------------+---------------+
                      | JWT Bearer / X-API-Key
             +--------v---------+
             |  cos-controller  |
             |  FastAPI :8090   |
             |  PostgreSQL      |
             +--------+---------+
                      |
          +-----------+-----------+
          | WebSocket :8091        | WebSocket :8091
+---------v--------+   +----------v-------+
|   cos-agent      |   |   cos-agent      |
|   node-1         |   |   node-2         |
|   KVM + libvirt  |   |   KVM + libvirt  |
|   NOS instance   |   |   NOS instance   |
+------------------+   +------------------+
         |                       |
         +-----------+-----------+
                     |
             +-------v--------+
             |  NOS REST API  |
             |  :8080         |
             +----------------+
```

---

## 3. Components

### 3.1 Controller
- Central orchestrator, runs as HA VM on each node
- Manages cluster state in PostgreSQL
- Exposes REST API on :8090 for portal and external automation
- Communicates with agents via WebSocket
- Communicates with NOS via REST API for network provisioning
- Background task monitors agent heartbeats (marks offline after 90s)
- Auto-generates admin user and API key on first start

### 3.2 Agent
- Runs on each physical node
- Registers with controller on startup (upsert by ip_address)
- Saves controller-assigned node_id to /opt/cos/node_id
- Executes commands from controller (VM create/start/stop/migrate)
- Sends heartbeat every 30s with resource usage and VM statuses
- Uses libvirt Python bindings for KVM management
- Uses NOS REST API for local network configuration
- WebSocket server on :8091

### 3.3 Controller HA (planned)
- One controller VM per physical node
- PostgreSQL streaming replication between nodes
- Keepalived with VIP on management network (1G interface)
- Active/standby - standby takes over if primary disappears

### 3.4 Portal
- React 19 SPA, Tailwind CSS, shadcn/ui components
- Vite build tool, TypeScript
- Served by nginx on :80 as static files
- JWT Bearer token authentication (login page)
- Dark/light theme toggle, persisted to localStorage
- Communicates only with controller REST API
- Pages: Dashboard, Nodes, Virtual Machines, Templates, Networks, Tenants
- Auto-refresh every 30s via React Query

---

## 4. Authentication

### 4.1 Portal Users (JWT)
- Users stored in PostgreSQL users table
- Roles: admin, operator, viewer
- Login via POST /api/v1/auth/login with username + password
- Returns JWT token (HS256, 8h expiry)
- Portal sends Authorization: Bearer <token> header
- Admin user auto-created on first start, password saved to /opt/cos/admin_password

### 4.2 API Keys (X-API-Key)
- For programmatic access (automation, agent-to-controller)
- Keys stored hashed (SHA-256) in api_keys table
- Admin API key auto-generated on first start, saved to /opt/cos/admin_api_key
- Agent uses X-API-Key for controller registration and heartbeat

### 4.3 Dual Auth
- Controller accepts both JWT Bearer and X-API-Key
- JWT Bearer tried first, falls back to X-API-Key
- tenant_id=None means admin access (sees all resources)

---

## 5. Project Structure

```
cos/
+-- controller/
|   +-- main.py              - FastAPI app, lifespan, background tasks
|   +-- config.py            - pydantic-settings, COS_ prefix
|   +-- db/
|   |   +-- base.py          - SQLAlchemy declarative base
|   |   +-- models.py        - SQLAlchemy 2.0 async models
|   |   +-- session.py       - async session factory
|   +-- api/
|   |   +-- app.py           - FastAPI app factory, CORS middleware
|   |   +-- auth.py          - X-API-Key authentication
|   |   +-- auth_users.py    - JWT + bcrypt password utilities
|   |   +-- deps.py          - shared dependencies (dual auth)
|   |   +-- routers/
|   |       +-- auth.py      - POST /auth/login, GET /auth/me
|   |       +-- nodes.py     - node management + heartbeat
|   |       +-- vms.py       - VM lifecycle
|   |       +-- networks.py  - network management
|   |       +-- tenants.py   - tenant management
|   |       +-- templates.py - VM templates
|   +-- scheduler/
|   |   +-- scheduler.py     - VM placement (best-fit by free RAM)
|   +-- nos_client/
|   |   +-- client.py        - httpx async client for NOS REST API
|   +-- agent_client/
|       +-- client.py        - WebSocket client to agents (30s timeout)
+-- agent/
|   +-- main.py              - entrypoint, heartbeat loop, registration
|   +-- config.py            - pydantic-settings, COS_AGENT_ prefix
|   +-- libvirt_driver.py    - KVM operations via libvirt Python
|   +-- nos_driver.py        - local NOS REST API wrapper
|   +-- ws_server.py         - WebSocket server :8091
+-- common/
|   +-- models.py            - shared Pydantic v2 models
|   +-- utils.py
+-- portal/                  - React SPA (Vite + TypeScript + Tailwind)
|   +-- src/
|   |   +-- api/             - axios client + per-resource API modules
|   |   +-- components/      - Layout, Sidebar, TopBar, StatusBadge, ResourceBar
|   |   +-- hooks/           - React Query hooks per resource
|   |   +-- pages/           - Dashboard, Nodes, VMs, Templates, Networks, Tenants, Login
|   |   +-- types/           - TypeScript types matching Pydantic models
|   |   +-- utils/           - format.ts (formatDate, formatGB)
|   +-- .env.production      - VITE_API_URL (generated by install script)
+-- nginx/
|   +-- cos-portal.conf      - nginx config, serves /opt/cos/portal/, proxies /api/ to :8090
+-- alembic/                 - Database migrations
|   +-- env.py               - async SQLAlchemy migration runner
|   +-- versions/            - migration files
+-- scripts/
|   +-- cos-install.sh       - deployment script (--role controller|agent)
+-- tests/
    +-- unit/
        +-- controller/
        +-- agent/
```

---

## 6. Data Models

### User
- id, username (unique), email (unique)
- hashed_password (bcrypt)
- role (admin/operator/viewer)
- tenant_id (FK nullable - NULL = admin)
- active, created_at

### Node
- id, hostname, ip_address (unique)
- cpu_total, cpu_used, ram_total_mb, ram_used_mb
- disk_total_gb, disk_used_gb
- status (online/offline/maintenance)
- last_heartbeat, created_at

### VM
- id, name, tenant_id (FK), node_id (FK)
- cpu_cores, ram_mb, disk_gb
- status (running/stopped/paused/migrating/error)
- libvirt_uuid, created_at

### Tenant
- id, name, email, hashed_password, active, created_at

### Network
- id, tenant_id (FK), name, vlan_id, cidr, gateway, created_at

### VMTemplate
- id, name, description, cpu_cores, ram_mb, disk_gb
- os_type, image_path, created_at

### APIKey
- id, tenant_id (FK nullable), key_hash (SHA-256)
- description, created_at, last_used

---

## 7. Communication Protocols

### Portal -> Controller
- REST API over HTTP (HTTPS planned)
- Authorization: Bearer <JWT token>
- Axios with 401 interceptor (redirects to /login on expired token)

### Controller -> Agent
- WebSocket ws://node_ip:8091/ws
- JSON commands: AgentCommand / AgentCommandResult
- Timeout: 30 seconds per command

### Agent -> Controller
- HTTP POST heartbeat every 30s to /api/v1/nodes/{node_id}/heartbeat
- X-API-Key authentication
- Payload: cpu_used, ram_used_mb, disk_used_gb, vm_statuses

### Agent Registration
- POST /api/v1/nodes on startup
- Upsert by ip_address (update if exists, create if new)
- Controller assigns node_id, agent saves to /opt/cos/node_id

### Controller -> NOS
- REST API http://node_ip:8080
- X-API-Key authentication
- Configure VLANs, interfaces, commit

### Agent -> NOS (local)
- REST API http://127.0.0.1:8080
- Local network configuration

---

## 8. Agent WebSocket Commands

| Command | Payload | Description |
|---------|---------|-------------|
| vm_create | name, cpu_cores, ram_mb, disk_gb, image_path | Create and define VM |
| vm_start | libvirt_uuid | Start stopped VM |
| vm_stop | libvirt_uuid | Graceful ACPI shutdown |
| vm_reboot | libvirt_uuid | Reboot VM |
| vm_destroy | libvirt_uuid | Force stop + undefine + delete disk |
| vm_migrate | libvirt_uuid, target_uri | Live migration |
| vm_list | - | List all VMs with status |
| node_stats | - | CPU/RAM/disk usage |

---

## 9. Scheduler

Simple best-fit scheduler:
- Filters nodes by status=online
- Filters nodes that can fit requested cpu_cores, ram_mb, disk_gb
- Sorts by (free_ram / total_ram) descending
- Returns first node that fits, or None if no node can accommodate

---

## 10. Networking Model

### Phase 1 (current) - VLAN-based
- Each tenant network = one VLAN
- NOS configures VLAN on physical node via REST API
- VMs attach to Linux bridge for that VLAN
- No inter-node L2 (VMs on different nodes cannot communicate at L2)

### Phase 2 (when NOS VXLAN/EVPN is ready)
- VXLAN tunnels between nodes
- Full L2 tenant isolation across nodes
- Live migration with network continuity

---

## 11. Physical Infrastructure

### Nodes
- SuperMicro Dual Gold 5218, 64GB RAM, 2x960GB SSD, 2x10G
- Ubuntu 24.04 LTS + KVM + NOS

### Network
- 1G management (controller HA, heartbeat, SSH, OOB IPMI)
- 10G trunk to NCS57C3 (Cisco IOS XR):
  - VLAN 20: live migration
  - VLAN 100: tenant overlay (VXLAN future)

### Controller HA (planned)
- VM on each node, connected via 1G management network
- PostgreSQL streaming replication
- Keepalived VIP on management network

---

## 12. Deployment

### Install Script
```
sudo bash scripts/cos-install.sh --role controller
sudo bash scripts/cos-install.sh --role agent
```

### Controller Install Steps
1. Install system packages (Python 3.12, PostgreSQL, Node.js 20, nginx, pkg-config, libvirt-dev)
2. Create cos user/group, add invoking user to cos group
3. Create /opt/cos/ directory structure
4. Install Python package in venv
5. Setup PostgreSQL (user + database)
6. Run Alembic migrations (alembic upgrade head)
7. Generate admin API key -> /opt/cos/admin_api_key (mode 640)
8. Delete stale admin_password to force regeneration
9. Build portal (npm install + npm run build)
10. Deploy portal to /opt/cos/portal/ (mode 755)
11. Install and enable nginx with cos-portal.conf
12. Install and start cos-controller.service
13. Print admin API key and admin password

### Agent Install Steps
1. Install system packages (Python 3.12, KVM, libvirt, pkg-config, libvirt-dev)
2. Create cos user/group, add cos to libvirt group
3. Create /opt/cos/ directory structure, /var/lib/cos/images/
4. Install Python package in venv
5. Generate node_id -> /opt/cos/node_id
6. Install and start cos-agent.service

### Configuration Files
- /opt/cos/config/controller.env - COS_NOS_API_URL, etc.
- /opt/cos/config/agent.env - COS_AGENT_CONTROLLER_URL, COS_AGENT_CONTROLLER_API_KEY, etc.
- /opt/cos/admin_api_key - X-API-Key for programmatic access (mode 640)
- /opt/cos/admin_password - portal admin user password (mode 640)
- /opt/cos/node_id - agent node UUID assigned by controller

---

## 13. Development Phases

### Phase 1 - Core COS (IN PROGRESS)
- Controller core: nodes, VMs, tenants, networks, templates
- Agent: libvirt, heartbeat, WebSocket
- NOS integration: VLAN provisioning via REST API
- Simple scheduler: best-fit by free RAM
- Portal: login, dashboard, all resource pages
- Deploy script: --role controller|agent
- Alembic DB migrations
- JWT + X-API-Key dual authentication
- Remaining: deploy on physical nodes, end-to-end VM creation, controller HA, HTTPS

### Phase 2
- React portal advanced features
- K3s container support
- NOS VXLAN/EVPN integration
- Live migration with network continuity
- HTTPS/SSL
- Controller HA (PostgreSQL replication, Keepalived)

### Phase 3
- Billing
- Multi-tenancy portal (customer self-service)
- Advanced scheduler (affinity/anti-affinity)
- Metrics and monitoring
- Backups

---

## 14. Technology Decisions

| Decision | Alternative | Reason |
|----------|-------------|--------|
| Python 3.12 | Go | Same stack as NOS, faster development |
| FastAPI | Django/Flask | Async native, automatic OpenAPI docs |
| SQLAlchemy 2.0 async | Tortoise ORM | Mature, flexible, good async support |
| PostgreSQL | SQLite | HA replication, production ready |
| WebSocket agent | gRPC | Simpler, no proto files, debuggable |
| libvirt-python | direct QEMU | Standard API, stable, well documented |
| Pydantic v2 | dataclasses | Validation, serialization, same as NOS |
| bcrypt direct | passlib | passlib has bug with newer bcrypt versions |
| JWT + X-API-Key | API key only | Portal needs user-scoped sessions |
| React + Vite | Next.js | SPA sufficient, simpler deployment |
| nginx | FastAPI static | More efficient for static files, easy SSL |
| Alembic | manual SQL | Safe schema migrations, version controlled |

---

*Document version 0.2 - Phase 1 in progress*
*Updated as architecture evolves*
