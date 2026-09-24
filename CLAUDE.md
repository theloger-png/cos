# COS - Cloud Operating System

## Project Overview
COS is a cloud orchestrator that manages KVM virtual machines and K3s containers
across multiple physical nodes. It uses Open vSwitch (OVS) directly for VM
networking: VLAN tagging is applied natively via libvirt domain XML
(virtualport type='openvswitch' + vlan tag), with no external networking
daemon or API involved.

## Components
- controller/ - Central orchestrator (FastAPI, PostgreSQL)
- agent/      - Per-node daemon (libvirt, WebSocket server)
- common/     - Shared Pydantic v2 models
- portal/     - Web UI (React 19, Tailwind, shadcn/ui), served by nginx. Implemented and in use:
              VM creation, tenants, templates, networks, node management, hardware editor

## Stack
- Python 3.12
- FastAPI + uvicorn
- SQLAlchemy 2.0 async + asyncpg
- PostgreSQL
- libvirt-python for KVM management
- httpx for agent-to-controller HTTP calls (node registration, heartbeat)
- WebSockets for controller-agent communication

## Code Style
- Python: PEP8, type hints everywhere, pydantic v2 for data models
- Docstrings on all public methods
- Language: All code, comments, and documentation must be in English

## Networking
- VM NICs use OVS native VLAN tagging via libvirt domain XML - no external VLAN provisioning step is needed
- A Network in COS (name + vlan_id + optional cidr/gateway) is purely a database record used to pick a VLAN tag at VM NIC creation time

## Testing
- Framework: pytest
- All new code must have unit tests in tests/unit/
- Run tests before every commit

## Git
- Commit after each logical unit of work
- Use conventional commits: feat:, fix:, test:, docs:

## Hard Rules
- Never modify DB models directly - always use SQLAlchemy migrations (Alembic)
- Never call libvirt directly from controller - always go through agent via WebSocket
- All config via pydantic-settings and environment variables, never hardcoded

## Validated Milestones
- **2026-06-15**: End-to-end VM creation via COS API with automatic NOS networking
  - Tested on cos-node1/cos-controller with tenant "admin" and ubuntu-24.04-small template
  - Flow: POST /api/v1/vms → controller WebSocket → agent libvirt → nos-br attachment → nos-libvirt-hook triggers vnetX/VLAN provisioning → VM reaches running status with libvirt_uuid persisted
  - Fixed via commits 0d832f0 (admin JWT tenant context), da6713e (vm.status transitions), ac36cd3 (nos-br bridge attachment)

## Known Install Gaps
- **cos-install.sh --role agent**: Template image files must be readable by libvirt-qemu after manual copy:
  - After copying template images to `/var/lib/cos/images/`, run: `chmod o+r /var/lib/cos/images/*.qcow2`
  - Root cause: copied files inherit the operator's umask; cos-install.sh cannot pre-create them
  - Directories (`/var/lib/cos`, `/var/lib/cos/{images,vms,seeds}`) are now set to 755 and `libvirt-qemu` is added to the `cos` group automatically by the install script

## Workflow Notes (learned 2026-09-24)

The controller and agent run from `/opt/cos/venv` (an installed Python package), not directly from the git checkout. After a `git pull`, the source tree on disk is updated but the running systemd service continues executing the old installed code until the package is reinstalled into the venv.

The correct sequence after `git pull` on either `cos-node1` (agent) or the controller VM:

1. Reinstall the package: `sudo /opt/cos/venv/bin/pip install --force-reinstall --no-cache-dir --no-deps ~/cos/ -q`
2. Restart the service: `sudo systemctl restart cos-agent` (on nodes) or `sudo systemctl restart cos-controller` (on controller VM)

Both services require this sequence, not just one. Skipping the reinstall step silently leaves stale code running. The main symptom is new fields, parameters, or behavior appearing to have no effect even though `git log` on disk correctly shows the latest commit. This disconnect can cost significant debugging time since every other part of the chain looks correct.

## Do Not Implement Yet
- K3s/container management
- Billing system
- Multi-region support
- VXLAN tenant isolation (waiting for NOS VXLAN/EVPN)
