# COS - Cloud Operating System

## Project Overview
COS is a cloud orchestrator that manages KVM virtual machines and K3s containers
across multiple physical nodes. It uses Open vSwitch (OVS) directly for VM
networking: VLAN tagging is applied natively via libvirt domain XML
(virtualport type='openvswitch' + vlan tag), with no external networking
daemon or API involved.

Always read STATUS.md before implementing any new module.

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

## Console
- Web-based serial console path: browser -> nginx -> controller WS relay -> agent WS /console -> libvirt openConsole stream
- The controller never touches libvirt directly; it relays raw bytes between the browser and the agent
- Authentication: one-time short-lived ticket issued by POST /api/v1/vms/{id}/console-ticket, validated at WS handshake

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
- **2026-06-15** (historical - used NOS networking and nos-libvirt-hook, both removed on 2026-09-24): End-to-end VM creation via COS API with automatic networking
  - Tested on cos-node1/cos-controller with tenant "admin" and ubuntu-24.04-small template
  - Prior flow: POST /api/v1/vms → controller WebSocket → agent libvirt → nos-br attachment → nos-libvirt-hook triggers vnetX/VLAN provisioning
  - Fixed via commits 0d832f0 (admin JWT tenant context), da6713e (vm.status transitions), ac36cd3 (nos-br bridge attachment)
- **2026-09-28**: Web serial console, idempotent installer, and backup/restore
  - Web serial console: browser → nginx → controller WS relay → agent /console → libvirt openConsole stream, xterm.js frontend, ticket-authenticated
  - cos-install.sh idempotent and doubles as update path: validated re-run on existing controller (secrets, portal, nginx unchanged), re-run on cos-node1 (VMs untouched)
  - Backup/restore: validated backup + restore on separate test VM (old credentials work, hashes match, 7 VMs recovered)
  - Fresh install on clean Ubuntu 24.04 VM with no manual steps (after greenlet fix), zero tracebacks

## Known Install Gaps
- **cos-install.sh --role agent**: Template image files must be readable by libvirt-qemu after manual copy:
  - After copying template images to `/var/lib/cos/images/`, run: `chmod o+r /var/lib/cos/images/*.qcow2`
  - Root cause: copied files inherit the operator's umask; cos-install.sh cannot pre-create them
  - Directories (`/var/lib/cos`, `/var/lib/cos/{images,vms,seeds}`) are now set to 755 and `libvirt-qemu` is added to the `cos` group automatically by the install script

## Workflow Notes

**Updating any machine (2026-09-28):** Just re-run `scripts/cos-install.sh`:
- On the controller VM: `sudo ~/cos/scripts/cos-install.sh --role controller`
- On each agent node: `sudo ~/cos/scripts/cos-install.sh --role agent`

The script handles git pull, package reinstall, and service restart as a single atomic operation with rollback on failure (portal and nginx). Never manually copy nginx or portal files.

**Why this is necessary:** The controller and agent run from `/opt/cos/venv` (an installed Python package), not directly from the git checkout. After a `git pull`, the source tree on disk is updated but the running systemd service continues executing the old installed code until the package is reinstalled into the venv. The main symptom is new fields, parameters, or behavior appearing to have no effect even though `git log` on disk correctly shows the latest commit.

**Debugging fallback (manual sequence if needed):**
1. Reinstall the package: `sudo /opt/cos/venv/bin/pip install --force-reinstall --no-cache-dir --no-deps ~/cos/ -q`
2. Restart the service: `sudo systemctl restart cos-agent` (on nodes) or `sudo systemctl restart cos-controller` (on controller VM)

**Security:** Never paste `admin_password` or `admin_api_key` into chats, logs, or external services - they are stored securely in `/opt/cos/` on disk and fetched when needed.

## Do Not Implement Yet
- K3s/container management
- Billing system
- Multi-region support
- VXLAN tenant isolation (decision pending: OVS static VXLAN vs Linux bridge + FRR EVPN)
