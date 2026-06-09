# COS - Cloud Operating System
## Architecture Document v0.1

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
|   (React UI)     |         |   (future)       |
+--------+---------+         +--------+---------+
         |                            |
         +------------+---------------+
                      |
             +--------v---------+
             |  cos-controller  |
             |  FastAPI :8090   |
             |  PostgreSQL      |
             +--------+---------+
                      |
          +-----------+-----------+
          |                       |
+---------v--------+   +----------v-------+
|   cos-agent      |   |   cos-agent      |
|   node-1 :8091   |   |   node-2 :8091   |
|   KVM + libvirt  |   |   KVM + libvirt  |
|   NOS instance   |   |   NOS instance   |
+------------------+   +------------------+
```

---

## 3. Components

### 3.1 Controller
- Central orchestrator, runs as HA VM on each node
- Manages cluster state in PostgreSQL
- Exposes REST API for portal and external automation
- Communicates with agents via WebSocket
- Communicates with NOS via REST API for network provisioning
- Background task monitors agent heartbeats

### 3.2 Agent
- Runs on each physical node
- Executes commands from controller (VM create/start/stop/migrate)
- Sends heartbeat every 30s with resource usage and VM statuses
- Uses libvirt Python bindings for KVM management
- Uses NOS REST API for local network configuration

### 3.3 Controller HA
- One controller VM per physical node
- PostgreSQL streaming replication between nodes
- Keepalived with VIP on management network (1G interface)
- Active/standby - standby takes over if primary disappears

### 3.4 Portal
- React SPA, Tailwind CSS, shadcn/ui
- Communicates only with controller REST API
- Not yet implemented

---

## 4. Project Structure

```
cos/
+-- controller/
|   +-- main.py              - FastAPI app, lifespan, background tasks
|   +-- config.py            - pydantic-settings, COS_ prefix
|   +-- db/
|   |   +-- models.py        - SQLAlchemy 2.0 async models
|   |   +-- session.py       - async session factory
|   +-- api/
|   |   +-- auth.py          - X-API-Key authentication
|   |   +-- deps.py          - shared dependencies
|   |   +-- routers/
|   |       +-- nodes.py
|   |       +-- vms.py
|   |       +-- networks.py
|   |       +-- tenants.py
|   |       +-- templates.py
|   +-- scheduler/
|   |   +-- scheduler.py     - VM placement (best-fit by free RAM)
|   +-- nos_client/
|   |   +-- client.py        - httpx async client for NOS REST API
|   +-- agent_client/
|       +-- client.py        - WebSocket client to agents
+-- agent/
|   +-- main.py              - entrypoint, heartbeat loop, registration
|   +-- config.py            - pydantic-settings, COS_AGENT_ prefix
|   +-- libvirt_driver.py    - KVM operations via libvirt Python
|   +-- nos_driver.py        - local NOS REST API wrapper
|   +-- ws_server.py         - WebSocket server :8091
+-- common/
|   +-- models.py            - shared Pydantic v2 models
|   +-- utils.py
+-- portal/                  - React UI (not yet implemented)
+-- tests/
    +-- unit/
```

---

## 5. Data Models

### Node
- id, hostname, ip_address
- cpu_total, ram_total_mb, disk_total_gb
- status (online/offline/maintenance)
- last_heartbeat, nos_api_key

### VM
- id, name, tenant_id, node_id
- cpu_cores, ram_mb, disk_gb
- status (running/stopped/paused/migrating/error)
- libvirt_uuid

### Tenant
- id, name, email, hashed_password, active

### Network
- id, tenant_id, name, vlan_id, cidr, gateway

### VMTemplate
- id, name, description, cpu_cores, ram_mb, disk_gb, os_type, image_path

### APIKey
- id, tenant_id, key_hash, description

---

## 6. Communication Protocols

### Portal -> Controller
- REST API over HTTPS (HTTP in dev)
- X-API-Key authentication

### Controller -> Agent
- WebSocket ws://node_ip:8091/ws
- JSON commands: AgentCommand / AgentCommandResult
- Timeout: 30 seconds per command

### Agent -> Controller
- HTTP POST heartbeat every 30s
- Payload: node stats + VM statuses

### Controller -> NOS
- REST API http://node_ip:8080
- X-API-Key authentication
- Configure VLANs, interfaces, commit

### Agent -> NOS (local)
- REST API http://127.0.0.1:8080
- Local network configuration

---

## 7. Networking Model (Phase 1)

Phase 1 uses simple VLAN-based tenant isolation:
- Each tenant network = one VLAN
- NOS configures VLAN on the physical node
- VMs attach to Linux bridge for that VLAN
- No inter-node L2 (VMs on different nodes cannot communicate at L2)

Phase 2 (when NOS VXLAN/EVPN is ready):
- VXLAN tunnels between nodes
- Full L2 tenant isolation across nodes
- Live migration with network continuity

---

## 8. Physical Infrastructure

### Nodes
- SuperMicro Dual Gold 5218, 64GB RAM, 2x960GB SSD, 2x10G
- Ubuntu 24.04 LTS + KVM + NOS

### Network
- 1G management (controller HA, heartbeat, SSH)
- 10G trunk to NCS57C3:
  - VLAN 20: live migration
  - VLAN 100: tenant overlay (VXLAN future)

### Controller HA
- VM on each node, management network
- PostgreSQL replication over management network
- Keepalived VIP on management network

---

## 9. Development Phases

### Phase 1 (current)
- Controller core: nodes, VMs, tenants, networks, templates
- Agent: libvirt, heartbeat, WebSocket
- NOS integration: VLAN provisioning
- Simple scheduler: best-fit by free RAM
- No portal yet

### Phase 2
- React portal
- K3s container support
- NOS VXLAN/EVPN integration
- Live migration with network continuity
- Alembic migrations

### Phase 3
- Billing
- Multi-tenancy portal (customer self-service)
- Advanced scheduler (affinity/anti-affinity)
- Metrics and monitoring
- Backups

---

## 10. Technology Decisions

| Decision | Alternative | Reason |
|----------|-------------|--------|
| Python 3.12 | Go | Same stack as NOS, faster development |
| FastAPI | Django/Flask | Async native, automatic OpenAPI docs |
| SQLAlchemy 2.0 async | Tortoise ORM | Mature, flexible, good async support |
| PostgreSQL | SQLite | HA replication, production ready |
| WebSocket agent | gRPC | Simpler, no proto files, debuggable |
| libvirt-python | direct QEMU | Standard API, stable, well documented |
| Pydantic v2 | dataclasses | Validation, serialization, same as NOS |

---

*Document version 0.1 - Phase 1*
*Updated as architecture evolves*
