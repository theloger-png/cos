# COS Changelog

Historical record of changes. Current state lives in STATUS.md.

## Recent Changes (2026-10-02)

### Operations console

- Bottom console in the portal showing what is being done and what was done: time, user, operation, progress bar, status, duration; click a row for details and the full error. Collapsed bar shows the last 5 operations; expanded panel is resizable with filters and polls every 2 s.
- Backend: `operations` table (Alembic b7c8d9e0f1a2), recording ASGI middleware for all mutating API calls and logins, GET /api/v1/operations (admin sees all, tenants only their own), 90-day retention, interrupted operations failed at start-up.
- System events (`system` user): node offline/online, VM status changes from heartbeats.
- Real progress for template image fetch: agent sends interim progress frames over the existing /ws connection; AgentClient.send_command takes an optional on_progress callback and stays compatible with agents that send none.

## Recent Changes (2026-10-01)

### Portal confirmations and password reset

- Reusable ConfirmDialog (Cancel focused by default, close blocked while pending, errors shown inside the dialog) used for VM Stop, VM Delete (extra force-stop warning when the VM is not stopped), Network Delete and Template Delete. Previously these buttons acted on a single click.
- New "Reset password" action on running VMs: POST /api/v1/vms/{id}/reset-password generates a random password, sends only its SHA-512 hash to the agent (new vm_set_password command), which applies it live with libvirt setUserPassword (VIR_DOMAIN_PASSWORD_ENCRYPTED) through qemu-guest-agent. The plaintext is returned once and never stored or logged; the hash is scrubbed from error messages.
- CredentialsDialog extracted from VMCreate and shared with the reset flow.
- Username validation uses fullmatch (a "$" anchor would have accepted a trailing newline; caught by a unit test).
- cos-install.sh: fixed re-exec after a git pull losing its arguments (ORIG_ARGS); regression test updated accordingly.

## Recent Changes (2026-09-28)

All on branches feature/vm-console and feature/ovs-networking (merged into main).

### Web Serial Console
- Agent: serial pty + console elements in the domain XML for new VMs;
  ConsoleSession bridges the libvirt console stream to asyncio; new
  WS endpoint /console?uuid=<libvirt_uuid> on the agent (:8091).
- Root cause fixed along the way: a non-blocking libvirt virStream only
  delivers data when the libvirt event loop is running; the agent now
  registers virEventRegisterDefaultImpl() once, before the first
  libvirt.open(), and pumps virEventRunDefaultImpl() in a daemon thread.
- Controller: POST /api/v1/vms/{id}/console-ticket (one-time ticket,
  30 s, in-memory store) and WS /api/v1/vms/{id}/console?ticket=...
  relaying binary frames to the agent. Close codes: 4401 invalid or
  expired ticket, 4404 VM not found, 4409 VM not running, 4500 console
  unavailable, 4503 node offline, 1011 unexpected error.
- nginx: WebSocket upgrade map in /etc/nginx/conf.d/websocket-upgrade.conf,
  long read/send timeouts on /api/.
- Portal: /vms/:id/console page with xterm.js, Console button on the VMs
  page (running VMs only), manual reconnect, close-code messages.
- Validated end to end on real hardware: browser -> nginx -> controller
  -> agent -> libvirt -> VM, typing and output both directions.

### cos-install.sh Idempotent + Backup/Restore
- Now doubles as the update path: secrets and agent.env never regenerated,
  git update with checkout + pull --ff-only and a guarded re-exec, safe
  portal swap (portal.old rename, rollback on failure), nginx backup +
  nginx -t + automatic restore on failure, unit files rewritten only
  when changed, final OK/FAIL verification block.
- New: --role controller --backup [file] and --restore <file> [--yes]
  (pg_dump + secrets + config in a 0600 tar.gz).
- New: INSTALL.md (full bare-metal-to-cluster guide) and a rewritten,
  de-NOS'd README.md.
- requirements.txt now uses sqlalchemy[asyncio] (pulls in greenlet);
  pytest and pytest-asyncio moved to requirements-dev.txt.
- Untracked __pycache__/*.pyc and ignored them.
- feature/ovs-networking and feature/vm-console merged into main.

### Validation Performed (Real Hardware)
- Re-run of --role controller on existing controller: secrets, portal,
  nginx, migrations all unchanged and OK.
- Re-run of --role agent on cos-node1 without prompts, VMs untouched.
- Backup, then restore on a separate test VM: old admin password and API
  key work, secret hashes identical, all 7 VMs and inventory restored.
- Fresh install on a clean Ubuntu 24.04 VM with no manual steps (after
  the greenlet fix), zero tracebacks.

### Bugs Found and Fixed by Validation
- Portal safe swap used "mv -T" over a non-empty directory.
- Script replaced itself with git pull mid-run (added guarded re-exec).
- Missing greenlet on a clean venv.

## Recent Changes (2026-09-24)

All on branch feature/ovs-networking (merged into main).

- **NOS networking dependency fully removed**:
  - agent/libvirt_driver.py refactored: VM NICs use native OVS VLAN tagging via libvirt domain XML (<virtualport type='openvswitch'/> and <vlan><tag id='X'/></vlan>), applied automatically by libvirt + OVS at NIC attach/detach
  - Deleted agent/nos_driver.py, controller/nos_client/, and agent/nos_api_client.py (the last was orphaned dead code found and removed at the end of the session)
  - Removed nos_api_key from the Node DB model (Alembic migration f6a7b8c0d1e2); removed NOS config fields from agent/config.py and controller/config.py
  - Removed configure_vlan/remove_vlan WebSocket commands and the controller-side broadcast logic in controller/api/routers/networks.py
  - scripts/cos-install.sh no longer adds the cos user to the nos group; installs openvswitch-switch as an agent-role dependency
  - Earlier entries below that describe NOS provisioning are historical and superseded by this change
- **New scripts**:
  - scripts/create-test-vm.sh: creates a KVM VM with OVS VLAN tagging for manual testing; auto-downloads the Ubuntu Noble cloud image if missing
  - scripts/migrate-mgmt-to-ovs.sh: one-time migration of a node's management IP from a VLAN sub-interface on a physical NIC to an OVS internal port on the nos-br bridge
- **New feature: optional static IP at VM creation** (single NIC only for now):
  - Operators can provide an IP/CIDR and gateway when creating a VM
  - agent/libvirt_driver.py generates a MAC address explicitly for each VM NIC and, when a static IP is given, writes a cloud-init network-config v2 file (matched by MAC) into the seed ISO via cloud-localds --network-config; with no IP the guest uses DHCP (unchanged default)
  - Portal (VMCreate.tsx): optional "Static IP (CIDR)" and "Gateway" fields, shown when a Network is selected
  - Validated end-to-end on cos-node1/cos-controller: static IP applied inside the guest via cloud-init
- **New feature: live IP addresses in the hardware editor**:
  - agent get_vm_config() adds ip_addresses (IPv4 only) to each NIC via domain.interfaceAddresses(): qemu-guest-agent only (ARP removed: VMs are commonly on routed subnets, so host ARP cannot see them), matched by MAC; empty list when the VM is stopped or the agent is unavailable
  - New VMs get a virtio-serial org.qemu.guest_agent.0 channel in the domain XML and cloud-init installs/enables qemu-guest-agent; VMs created before this change have neither and keep showing no IPs (not retrofitted)
  - Portal VMHardware.tsx shows them under each NIC's MAC
- **New feature: optional static IP when adding a NIC** (VM must be stopped):
  - AddNICRequest gains ip_cidr/gateway (both required, otherwise ignored); agent generates the new NIC's MAC explicitly and, if the domain is shut off, rebuilds its cloud-init seed ISO with a multi-NIC network-config
  - Every NIC in the domain is listed in the rebuilt network-config (recorded static entries kept, others DHCP) because a supplied network-config replaces cloud-init's DHCP fallback for unlisted interfaces
  - Seed inputs (user-data + static NICs) are now recorded in /var/lib/cos/seeds/<uuid>.seed.json (0600) at VM creation so the seed can be rebuilt without parsing the ISO; the rebuild uses a fresh instance-id, so cloud-init re-runs on next boot
  - Running/paused VMs: NIC is attached without static IP and a nic_failures entry says why
  - Seed ISOs and sidecars are not yet removed on VM destroy (pre-existing gap for the ISO)
- **Infrastructure migration validated on cos-node1** (fresh Ubuntu 24.04 install):
  - NOS uninstalled/unused; OVS installed via cos-install.sh
  - Topology: single OVS bridge "nos-br" carries both physical trunk uplinks (1G management-facing NIC and 10G data-facing NIC) plus an OVS internal port ("mgmtNNN", tagged with the management VLAN) for the node's own management IP, so VMs on the management VLAN reach the node locally over the bridge without traversing the physical switch
  - Management IP configured via netplan on the OVS internal port (not the physical NIC)
  - cos-controller VM recreated from scratch with scripts/create-test-vm.sh; controller role installed and validated (login, API key auth working)
  - cos-agent reinstalled on cos-node1 and re-registered with the new controller; heartbeat confirmed online
- **Fixed: static-IP NICs had no DNS** (commit 059d6c0; found via real-VM testing where apt failed with "Temporary failure resolving archive.ubuntu.com"):
  - The generated cloud-init network-config had the correct address and gateway/route but no nameservers entry, so static-IP VMs could not resolve names at all (affected both static IP at VM creation and static IP on add-NIC)
  - Static-IP NICs now include nameservers 1.1.1.1 and 8.8.8.8 (same servers as the project's netplan configs); DHCP NICs are unchanged and still get DNS via DHCP
- **Fixed** (pre-existing bugs surfaced by the first real UI walkthrough in a while):
  - Tenants create form was missing the required "email" field (form and type updated)
  - Templates create form was missing required "os_type" and "image_path" fields (form and type updated)
  - VM Credentials modal copy buttons did nothing over plain HTTP (Clipboard API fails silently in non-secure contexts); fixed with a secure-context check, an execCommand('copy') fallback, error logging, and visual copy-success feedback
- **Updating is done by re-running scripts/cos-install.sh** (see Recent Changes 2026-09-28): git pull, package reinstall, and service restart are now automated. The manual sequence (pip install --force-reinstall --no-cache-dir --no-deps + systemctl restart) is a debugging fallback only.

## Recent Changes (2026-06-15)

- **Validated milestone** (late afternoon): Cloud-init credentials and VM hardware editing features (13 commits, 216 tests)
  - **Cloud-init credentials** (commits 6a793c0, 88e5236, 0be52ca, e87db9d, 0333e99, 6ccb624, 579eec9):
    - VMTemplate now has cloud_init_user field (default "ubuntu"), editable in portal Templates page
    - On vm_create, controller generates random 16-char password + SHA-512 hash
    - Agent builds cloud-init seed ISO (cloud-localds) with chpasswd for template's cloud_init_user, ssh_pwauth enabled, unique instance-id per VM
    - Portal shows one-time credentials modal after VM creation
    - Validated end-to-end: login via virsh console with generated credentials works correctly
    - Fixed: ws_server.py wasn't passing cloud_init_user/cloud_init_password_hash to create_vm (commit 579eec9)
    - Fixed: seed ISO permissions - libvirt-qemu needs read access to /var/lib/cos/seeds/ (added cos group, dir mode 755)
  - **VM hardware editing** (commits ef4dc6f, c4e4477, 59f8623, 195d273, 57dc1eb, efffced):
    - New GET/PUT /api/v1/vms/{id}/hardware endpoints for CPU/RAM/disk/NIC configuration
    - Agent: get_vm_config (parses domain XML, resolves NIC VLANs via NOS REST API) and apply_vm_config
    - Portal: new VM hardware editor page - edit vCPU/RAM, add secondary disks, add/remove NICs with COS Network selector, pending-changes summary, apply confirmation (reboot warning only for CPU/RAM/disk)
    - NIC add/remove are live (no reboot); CPU/RAM/disk changes trigger graceful shutdown → reconfigure → restart
    - Validated end-to-end on cos-node1/test111: disk add (vdb 10GB), RAM 2048→4096, vCPU 2→3 (all via reboot), NIC add/remove (live, correct VLAN auto-provisioned)
    - Fixed: NIC detach now uses minimal XML (mac/source/model only) and single libvirt flag (LIVE or CONFIG, not combined); NOS cleanup only on successful detach
    - Fixed: per-NIC failures surfaced in API/portal; NOS config response parsing now correctly resolves vlan_id per vnetX
    - Fixed: add_nics now sends correct "interface-mode access" + numeric "vlan members <id>" (was invalid "vlan members vlan<id>")
- **Validated milestone** (afternoon): Portal UI end-to-end validation - Networks create/delete and VM Create form working through browser
  - Network create/delete: UI now has optional cidr/gateway fields (L2-only by default), Tenant selector added to Networks dialog (commits 7ae5411, beb561f)
  - VM Create form: Tenant + Network selectors added, resource fields auto-populated from template defaults, 422 validation error display fixed (commit 7812f89)
  - Full chain validated: created VLAN 101 via portal Networks page at http://188.213.242.235 → controller → agent → NOS, confirmed with `show vlans` on cos-node1
  - Network access: DNAT via 185.45.15.70 → 188.213.242.235 → 10.111.1.203; note http:// required (no TLS configured, browsers default to https on :443)
  - Architecture decision: Edge router deferred to future - dedicated NOS VM with trunk interface on nos-br, per-VLAN IRBs manual via nos-cli; Network.cidr/gateway remain informational only for now
- **Validated milestone** (early): Network create/delete via COS API provisions/removes VLANs in NOS end-to-end (commit 459a78d)
  - Flow: POST /api/v1/networks → controller WebSocket to all online agents → agent nos_driver.py → configure_vlan/remove_vlan → NOS commit
  - Tested on cos-node1: created network vlan_id=202, confirmed in `nos-cli show vlans`; deleted, confirmed removal
  - Required: `cos` system user must be in `nos` group (cos-install.sh agent role now does `usermod -aG nos cos`)
- **Fixed**: cos-agent crash-loop when NOS API key file is unreadable (PermissionError at import time)
  - Root cause: cos user not in `nos` group → `/opt/nos/api_key` (mode 640 root:nos) unreadable → load_nos_driver raised at module import → entire agent down (heartbeats, vm_create, all commands)
  - Primary fix: cos-install.sh --role agent now adds cos to nos group (step A2b)
  - Defensive fix: nos_driver.py load_nos_driver() now catches OSError, returns a driver that logs and returns False on VLAN calls instead of crashing the process

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
