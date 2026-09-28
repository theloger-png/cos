# COS — Cloud Operating System

COS is a cloud orchestrator that manages KVM virtual machines across
multiple physical nodes. VM networking uses Open vSwitch (OVS) directly:
VLAN tagging is applied natively via libvirt domain XML, with no external
networking daemon or API involved.

## Features

- Multi-node KVM VM lifecycle: create, start, stop, reboot, destroy, migrate
- Native OVS VLAN tagging per VM NIC (no external network controller)
- Web portal (React + Tailwind + shadcn/ui): dashboard, nodes, VMs,
  templates, networks, tenants, hardware editor
- Web-based serial console for running VMs, streamed over WebSocket
- Cloud-init based provisioning: per-VM generated credentials, static IP or
  DHCP, qemu-guest-agent for live IP reporting
- Dual authentication: JWT (portal users) and X-API-Key (agents/scripts)
- Multi-tenant resource scoping
- Idempotent installer that doubles as the update path, plus backup/restore

See [INSTALL.md](INSTALL.md) for the full install guide (from a bare
Ubuntu 24.04 node to a running cluster), including updates, backup/restore
and troubleshooting.

## Project Structure

| Path | Role |
|------|------|
| `controller/` | Central orchestrator: FastAPI REST API, scheduler, PostgreSQL |
| `agent/` | Per-node daemon: libvirt management, OVS networking, WebSocket server |
| `common/` | Shared Pydantic v2 models |
| `portal/` | Web UI (React 19, Tailwind, shadcn/ui), served by nginx |
| `scripts/` | Install/update/backup script and node-networking helpers |
| `nginx/` | nginx config installed by `scripts/cos-install.sh` |
| `alembic/` | Database migrations |
| `tests/` | Unit tests |

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## License

TBD.
