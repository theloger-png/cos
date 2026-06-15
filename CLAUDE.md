# COS - Cloud Operating System

## Project Overview
COS is a cloud orchestrator that manages KVM virtual machines and K3s containers
across multiple physical nodes. It integrates with NOS (Network Operating System)
via REST API for networking configuration.

## Components
- controller/ - Central orchestrator (FastAPI, PostgreSQL)
- agent/      - Per-node daemon (libvirt, WebSocket server)
- common/     - Shared Pydantic v2 models
- portal/     - Web UI (React, Tailwind, shadcn/ui) - not yet implemented

## Stack
- Python 3.12
- FastAPI + uvicorn
- SQLAlchemy 2.0 async + asyncpg
- PostgreSQL
- libvirt-python for KVM management
- httpx for NOS REST API client
- WebSockets for controller-agent communication

## Code Style
- Python: PEP8, type hints everywhere, pydantic v2 for data models
- Docstrings on all public methods
- Language: All code, comments, and documentation must be in English

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
- Never call NOS API directly from agent routers - use nos_driver.py
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

## Do Not Implement Yet
- Portal (React UI)
- K3s/container management
- Billing system
- Multi-region support
- VXLAN tenant isolation (waiting for NOS VXLAN/EVPN)
