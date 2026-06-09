# COS — Cloud Operating System

COS is a cloud orchestrator that manages KVM virtual machines and K3s containers across multiple physical nodes. It integrates with NOS (Network Operating System) via REST API for VLAN and network configuration.

## Architecture

```
┌──────────────────────────────────────────────┐
│                  Controller                  │
│  FastAPI REST API  │  Scheduler  │  DB (PG)  │
│       ↕ NOS REST API (httpx)                 │
└─────────────────────┬────────────────────────┘
                      │ WebSocket (port 8091)
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       Agent 1     Agent 2     Agent N
     (KVM node)  (KVM node)  (KVM node)
```

### Components

| Path | Role |
|------|------|
| `controller/` | Central API server, scheduler, DB, NOS integration |
| `agent/` | Per-node daemon: libvirt management + heartbeat |
| `common/` | Shared Pydantic models and utilities |
| `tests/` | Unit tests |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your database URL, NOS API details, etc.
```

### 3. Start the controller

```bash
# From the cos/ directory
python -m controller.main
```

The first run creates the database tables and writes the master admin API key to `/opt/cos/admin_api_key`.

### 4. Start an agent (on each physical node)

```bash
python -m agent.main
```

The agent registers itself with the controller, then starts the WebSocket command server and heartbeat loop.

## API Overview

All endpoints require `X-API-Key` header.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/nodes` | List nodes |
| POST | `/api/v1/nodes` | Register node |
| GET | `/api/v1/vms` | List VMs (tenant-scoped) |
| POST | `/api/v1/vms` | Create VM |
| POST | `/api/v1/vms/{id}/start` | Start VM |
| POST | `/api/v1/vms/{id}/stop` | Stop VM |
| POST | `/api/v1/vms/{id}/migrate` | Migrate VM |
| GET | `/api/v1/networks` | List networks |
| POST | `/api/v1/networks` | Create network (configures NOS VLAN) |
| GET | `/api/v1/tenants` | List tenants (admin) |
| POST | `/api/v1/tenants` | Create tenant (admin) |
| POST | `/api/v1/tenants/{id}/apikeys` | Generate API key (admin) |
| GET | `/api/v1/templates` | List VM templates |

## Scheduler

Node selection uses a best-fit strategy: nodes are ranked by available RAM ratio (`free_ram / total_ram`) descending. The first node that satisfies CPU, RAM, and disk requirements is selected.

## NOS Integration

Network creation calls `POST /api/v1/vlans` on NOS, then `POST /api/v1/commit`. Network deletion calls `DELETE /api/v1/vlans/{vlan_id}` then commits.

## Running Tests

```bash
pytest tests/ -v
```

## Configuration Reference

### Controller (`COS_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `COS_DATABASE_URL` | `postgresql+asyncpg://cos:cos@localhost/cos` | Async PostgreSQL DSN |
| `COS_API_HOST` | `0.0.0.0` | Listen address |
| `COS_API_PORT` | `8090` | Listen port |
| `COS_NOS_API_URL` | `http://127.0.0.1:8080` | NOS controller URL |
| `COS_NOS_API_KEY` | `` | NOS API key |
| `COS_AGENT_HEARTBEAT_TIMEOUT_SECONDS` | `90` | Mark node offline after this many seconds |

### Agent (`COS_AGENT_` prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `COS_AGENT_CONTROLLER_URL` | `http://127.0.0.1:8090` | Controller URL |
| `COS_AGENT_WS_PORT` | `8091` | WebSocket listen port |
| `COS_AGENT_HEARTBEAT_INTERVAL_SECONDS` | `30` | Heartbeat frequency |
| `COS_AGENT_LIBVIRT_URI` | `qemu:///system` | libvirt connection URI |
| `COS_AGENT_NOS_API_URL` | `http://127.0.0.1:8080` | Local NOS URL |
