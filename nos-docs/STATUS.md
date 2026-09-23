# NOS Project Status

## Completed - Phase 1

### Config Engine
- Store, schema, validator, diff, commit engine, serializer
- JunOS-style commit/rollback with 50 checkpoints
- Pydantic v2 models for all config sections
- Standardized config path to `/opt/nos/config/`

### CLI Engine
- Shell, parser, completer (operational + configure mode)
- Prefix matching: `sho int` expands to `show interfaces`
- Multi-line paste support
- `ens34.0` shorthand expands to unit subinterface notation
- Pipe support: `match`, `except`, `find`, `count`, `display set` (both modes)
- Pipe chaining: multiple `|` pipes in a single command
- Pipe completion after full pipe segments
- JunOS-style `ping` and `traceroute` options
- Space key: JunOS-style — completes unique prefix, shows options when ambiguous
- Ctrl+X: clears input line (optimized: cancel_completion + reset without history append)
- Interface aliasing: `set system interface-rename` maps physical interfaces to et0/et1 everywhere in nos-cli
- lo0 loopback: dummy interface support, units lo0.0/lo0.1/etc, excluded from interface-rename
- Autocompletion hints: dynamic hints for `<prefix>`, `<neighbor-ip>`, `<interface-name>`, `<vlan-name-or-id>`, `<ip-address>` in operational mode show commands and configure mode
- Multi-value set commands: JunOS-style `set interfaces et1 mtu 9000 unit 101 vlan-id 101` on a single line
- Tab completion for multi-value commands (navigates CONFIG_TREE correctly)
- FormattedText rendering bug fixed (completion hints show plain text, not rich markup)

### Show Commands
- `show interfaces` — live data via pyroute2, IPv6 addresses displayed
- `show interfaces terse` — IPv6 addresses, dotted subinterface names (irb.101)
- `show interfaces description`
- `show vlans` — live VLAN table with attached interfaces
- `show forwarding` — live data from PFE, correctly distinguishes xdp-native (link-based and legacy DRV_MODE) vs xdp-generic vs kernel
- `show arp` — with interface and hostname filters
- `show ipv6 neighbors` — with interface filter
- `show ethernet-switching table` — with interface/vlan/summary filters
- `show ethernet-switching interface` — per-interface switching info
- `show ethernet-switching statistics` — per-interface packet counters
- `show ethernet-switching flood` — per-VLAN flood group membership
- `show configuration` — tree format + `| display set` pipe
- `show route` - JunOS format, IPv4/IPv6, brief/detail/terse/hidden, protocol filter, prefix filter
- `show bgp summary` - JunOS format with peer states and prefix counts
- `show bgp neighbor [<ip>]` - detailed neighbor information
- `show isis` — adjacency, database [detail], interface [name], summary (FRR isisd via vtysh JSON)
- `show isis interface` — JunOS format with L/CirID/Level 1 DR/Level 2 DR/L1/L2 Metric columns
- `show isis adjacency` — JunOS format, kernel→NOS name translation
- `show isis database` — JunOS format (level 1/2, Sequence, Lifetime, A/P/OL/AT flags)
- `show isis route` — JunOS format, text parsing (FRR 8.4 no JSON support)
- `show security nat static/source/destination/pool` — NAT configuration display
- `show security nat translations` — active NAT session translations
- `show system login` — local user configuration display
- `show system services ssh` — SSH server configuration display

### Backend Drivers
- Kernel: interfaces, bridge, routes, VRF (pyroute2, never iproute2 CLI directly)
- FRR: client, renderer, IS-IS, BGP
- FRR daemons auto-enabled/disabled based on configured protocols (bgpd, isisd, ospfd)
- Subinterface deletion for physical parents (ens34.101 deletable)
- Physical interface detached from bridge on delete
- `nos-br` bridge deleted when last port is detached

### PFE (Packet Forwarding Engine)
- C process: `main.c`, `fib.c`, `ipc.c`
- XDP program: `xdp_prog.c`, `xdp_loader.c`, `maps.h`
- Python PFE manager: `manager.py`, `fib.py`, `stats.py`, `ipc.py`
- **XDP native on physical NICs (i40e/X710/X722 validated)**: three-tier attach strategy in `xdp_attach_iface()`:
  1. Tier 1: link-based native XDP via `bpf_program__attach_xdp()` (BPF_LINK_CREATE)
  2. Tier 2: legacy native XDP via `bpf_xdp_attach(XDP_FLAGS_DRV_MODE)` — same path as `ip link set ... xdpdrv`; i40e on kernel 6.8 returns EOPNOTSUPP from tier 1 (BPF_LINK_CREATE) but succeeds via tier 2
  3. Tier 3: generic/SKB mode fallback (`XDP_FLAGS_SKB_MODE`) — used for virtual interfaces (virtio-net, vmxnet3, bridges)
  - `main.c` calls `xdp_loader_attach_all(0)` (not forced SKB_MODE) so all three tiers are tried per interface
- XDP generic mode (virtio-net/vmxnet3 compatible), kernel fallback
- XDP VLAN tag push for access ports via `port_vlan_map`
- Fixed XDP tag-push MAC corruption (overlapping memcpy)
- Bridge MAC unique per VM (derived from physical port MAC)
- **Fixed: native XDP on i40e drops ARP/ICMP** — i40e disables hardware VLAN stripping when native XDP is attached, so frames arrive 802.1Q-tagged inside the XDP program (they arrive untagged in generic/SKB mode). `xdp_prog.c` now performs local-delivery checks (ARP passthrough, `local_ip4_map`/`local_ip6_map` lookups) immediately after parsing the VLAN header and *before* the `vlan_map` L2 redirect lookup, so ARP replies and locally-destined IP traffic reach the kernel instead of being redirected/dropped.
- `manager.py` `detect_forwarding_mode()`: fixed XDP attach-mode detection — kernel reports `XDP_ATTACHED="xdp"` (with `IFLA_XDP_DRV_PROG_ID`) for link-based native attach, `"xdpdrv"` for legacy native, `"xdpgeneric"` for generic. All three native variants now map to `XDP_NATIVE`.

### REST API
- Minimal HTTP API on 127.0.0.1:8080, `X-API-Key` auth via `/opt/nos/api_key` (mode 640, root:nos)
- Endpoints:
  - `GET /api/v1/config` — returns running or candidate config as JunOS-style set commands
  - `POST /api/v1/config` — applies set/delete commands to candidate config; Phase-1 validation; rollback on failure; persists to `candidate.json`
  - `POST /api/v1/commit` — full commit via shared ConfigApplier (applies to kernel/PFE/FRR, same behavior as CLI commit)
  - `POST /api/v1/commit-check` — Phase-2 dry-run validation (TODO)
- Shared driver/applier construction via `nos/config/applier_factory.py`, used by both `nos-cli` and `nos-api` to avoid configuration drift
- Systemd service `nos-api.service` (starts after `nos-pfe.service`)

### Integration / Config Apply
- ConfigApplier: integrated with CommitEngine, applies interfaces/VLANs/routing/protocols on every commit
- Unix socket JSON IPC between Python RE and C PFE
- `apply_svi`: IRB/SVI interfaces with IP addresses applied at commit
- `vlan_add_self` called automatically on SVI apply
- `nos-apply.service`: applies running config at boot
- Fixed: lo0.0 address lost after commit — `apply_interface` was calling `_sync_addresses` with no `family_inet`, wiping unit-managed addresses; now skipped when no address keys present at interface level

### Deployment
- Systemd services: `nos-pfe.service`, `nos-cli.service`, `nos-apply.service`
- Install script: `scripts/nos-install.sh` (Ubuntu 24.04) — validated end-to-end on fresh Ubuntu 24.04 install (bare metal, i40e NICs) with a single unmodified run
  - `setcap cap_net_admin,cap_net_raw,cap_sys_admin+eip` automated post-install
  - `traceroute` added to apt install list
  - **`build-essential` added to apt install list** (was missing — `make` not found, broke PFE build on fresh installs)
  - **`bpftool` replaced with `linux-tools-common linux-tools-$(uname -r)`** (no standalone `bpftool` package on Ubuntu 24.04)
  - Package installed non-editable (not `pip install -e`)
  - dnsmasq and isc-dhcp-client added to package list
  - nftables package added (for NAT backend)
  - `/etc/dnsmasq.d/` permissions fixed for nos group file writes
  - frr group membership added for human user (frr-reload.py access)
  - Sudoers rules added: nos-nft (nftables), nos-users (user management), nos-ssh (SSH config)
  - Ubuntu 24.04 systemd socket activation for SSH disabled automatically
  - **`/opt/nos/config/` permissions: 775 (group nos), was 750** — group could not write running.json/candidate.json/rollback files
  - **`running.json`/`candidate.json` explicitly chowned `root:nos`, chmod 664** after install
  - **Config initialization installs empty `{}` configs**, not the developer's working config from the repo (was leaking nos-dev's test interfaces ens33/ens34/irb.101 onto fresh installs)
  - **Config file copy step skips copy if source and destination resolve to the same file** (`realpath` check) — previously failed with "are the same file" on reinstall, aborting the script before the systemd units step
  - **`nos-pfe.service` `ExecStartPost`** now also creates `/run/nos/stats.json` and `/run/nos/stats.json.tmp` with `chmod 664 chown root:nos` (was causing a one-time `Permission denied` warning from `IfaceStatsWriter` at first boot — cosmetic, self-heals after first 30s cycle, but now mostly eliminated)
- Managed addresses persisted across restarts: `/opt/nos/managed_addresses.json`
- Managed users persisted across restarts: `/opt/nos/managed_users.json`
- `/run/nos` permissions fixed via systemd `RuntimeDirectoryMode`/`Group` and `CAP_CHOWN`
- `nos-apply` permissions: `UMask=0002`, `/opt/nos` group-writable
- Rollback directory permissions: **775** (was 770 — group could not create rollback checkpoint files on fresh install)
- **libvirt QEMU hook**: `/etc/libvirt/hooks/qemu` installed by `nos-install.sh` (requires `libxml2-utils`, restarts libvirtd)
  - On VM "started/begin": parses domain XML for `vnetX` interfaces attached to `nos-br`, provisions each as an access port on VLAN from `<nos:vlan>` domain metadata (default 115), via NOS REST API
  - Proactive ghost-interface cleanup on every VM start: compares NOS running config `vnetX` entries against `/sys/class/net/vnetN`, deletes any `vnetX` no longer present in the kernel, in the same commit as new provisioning
  - Validated end-to-end on cos-node1: destroy/start and full destroy/undefine/recreate cycles, including a 5-ghost cleanup scenario

### Interface Statistics
- Per-interface counters collected every 30 seconds by IfaceStatsWriter background thread (nos/pfe/stats.py)
- Stats written atomically to /run/nos/stats.json (mode 0664, nos group) using IF-MIB naming for future SNMP compatibility
- Interface names in stats.json match NOS-cli names (et0/et1 or hardware name if no alias)
- bps/pps calculated as 30-second moving averages, last_flap tracking
- `show interfaces`: Traffic statistics section with bytes, packets, bps, pps, errors, drops
- `show interfaces extensive`: adds last flap timestamp and moving average annotation
- `show interfaces <name>`: filters output to a single interface
- `show interfaces <name> extensive/terse/detail/description`: all variants work correctly
- Tab completion for format keywords after interface name

### DHCP Server and Client
- DHCP server via dnsmasq: per-interface pool configuration with range, gateway, optional dns-server
- dnsmasq config files generated in /etc/dnsmasq.d/nos-<iface>-<pool>.conf
- Interface name translation: NOS names (et1.101) translated to kernel names (ens34.101) in dnsmasq config
- dnsmasq DNS listener disabled (port=0) to avoid conflict with systemd-resolved
- DHCP client: `set interfaces <name> family inet dhcp` starts dhclient via sudo
- Duplicate dhclient prevention via pgrep fallback check
- `show dhcp server leases`, `show dhcp server statistics`, `show dhcp client leases`
- sudoers rules for dnsmasq and dhclient management without password prompt

### NAT (Network Address Translation)
- Static NAT (1:1): `set security nat static rule <name> source <prefix> translated <ip>`
- Source NAT with pool (many-to-few): `set security nat pool <name> address <prefix>`
- Destination NAT (port forwarding): `set security nat destination rule <name> destination port <port> forward-to <ip> port <port>`
- nftables backend via sudo (no password prompt)
- Config serializer: round-trip support for all NAT types
- `show security nat static/source/destination/pool` — display NAT configurations
- `show security nat translations` — active session tracking

### User Management
- Local users: `set system login user <name> class [super-user|operator|read-only]`
- Password hashing: SHA512, never stores plaintext
- Linux user creation/deletion via sudo: useradd/usermod/userdel/chpasswd (no password prompt)
- Managed users tracked in `/opt/nos/managed_users.json` for persistence across restarts
- User class enforcement in CLI permissions (super-user=full, operator=limited, read-only=show only)
- `show system login` — display local user configuration

### SSH Server
- Configuration: `set system services ssh port <1-65535>`, `set system services ssh protocol-version v2`, `set system services ssh root-login [allow|deny|deny-password]`
- SSH config written to `/etc/ssh/sshd_config.d/nos.conf` via sudo
- Ubuntu 24.04 systemd socket activation disabled automatically (prevents conflicts)
- `sshd` reloaded after config changes
- `show system services ssh` — display SSH server configuration

## Known Limitations / TODO
- Production mode: NOS full control of interfaces (disable netplan) — not yet
- SNMP server: planned for future phase
- **DNS**: NOS does not apply configured nameservers to `systemd-resolved`. After bringing up `irb.<vlan>` as the management interface, DNS resolution fails until manually fixed with `sudo resolvectl dns irb.<vlan> 1.1.1.1 8.8.8.8`. Not yet implemented (nameservers aren't even in the config schema yet).
- **port_vlan_map cleanup**: when a `vnetX`/interface is deleted from NOS config (e.g. VM destroyed), its entry in the XDP `port_vlan_map` BPF map is not removed. Stale entries (keyed by ifindex) accumulate over time. Harmless functionally (stale ifindexes never match real traffic) but should be cleaned up on interface detach.
- **libvirt vnetX lifecycle**: automated via `scripts/nos-libvirt-hook.sh` installed to `/etc/libvirt/hooks/qemu`. Hook provisions each new `vnetX` (attached to `nos-br`) as an access port on the configured VLAN via the NOS REST API on VM start; removes it on VM stop. VLAN defaults to 115; override per-domain via `<nos:vlan xmlns:nos="https://github.com/theloger-png/nos">200</nos:vlan>` in domain XML metadata. See `COS_NOS_Install_Guide.md` section 5.7.
- **Bridge FDB stale/permanent entries**: manual `bridge fdb add ... permanent` entries used during earlier port_vlan_map experiments can persist in the `nos-br` FDB after the underlying `vnetX` is destroyed and recreated with a different MAC, causing L2 traffic (including ARP) to be delivered to the wrong/non-existent MAC. Not a NOS bug per se (manual kernel state), but worth a `nos-cli` diagnostic/cleanup command in the future (e.g. `clear ethernet-switching table`).
- **Residual IP on physical interface after access-port + IRB conversion**: when converting a physical interface from "IP directly on the interface" (netplan default) to "access-port + IRB", the old IP can remain double-configured on the physical interface in the kernel even though it's removed from NOS config. Requires manual `ip addr del <ip>/<prefix> dev <iface>` + `commit` (to reapply default route/bridge state). See `COS_NOS_Install_Guide.md` section 3.

## Known Bugs
- `frr-reload.py` fails with rc=1 when all protocols are deleted (frr.conf retains stale config)

## Phase 2 — In Progress

## Phase 2 — Planned Features
- DHCP relay: forward to external server
- NAT masquerade: remaining masquerade feature implementation
- ACL / firewall filters: JunOS firewall filter syntax, applied per-interface inbound/outbound
- TACACS+ authentication: set system tacacs-server, authentication-order
- REST API: HTTP API for automation and integration (needed for COS<->NOS integration per COS_Architecture.md section 7, and for the libvirt vnetX automation above)
- OSPF routing protocol
- LAG/LACP: link aggregation
- DNS/nameserver config schema + systemd-resolved integration (see Known Limitations above)
- port_vlan_map cleanup on interface detach (see Known Limitations above)

## Architecture Decisions
- JunOS-like CLI identical syntax
- Python 3.12 control plane, C for PFE/XDP
- FRR as routing engine (zebra, isisd, bgpd, staticd)
- XDP native on physical NICs (i40e validated) with three-tier fallback (link-based native -> legacy DRV_MODE native -> generic), generic for VMs (virtio-net, vmxnet3), kernel as final fallback
- Unix socket JSON IPC between Python RE and C PFE
- Pydantic v2 for config schema validation
- pyroute2 for all kernel operations (never iproute2 CLI directly)
- commit/rollback stateful JunOS-style (50 checkpoints)
- Management VLAN access-port + IRB (not trunk) when only one VLAN is needed — simpler, no switch reconfiguration required beyond access/untagged on the physical port

## Test Count
- Total: 1976 tests collected (1849 passing, 0 failures) - 2026-06-15

## Recent Changes (2026-06-15)
- **Implemented: REST API** — minimal HTTP API on 127.0.0.1:8080 with `X-API-Key` auth via `/opt/nos/api_key`
  - Endpoints: `GET /api/v1/config`, `POST /api/v1/config` (set/delete), `POST /api/v1/commit` (full ConfigApplier commit), `POST /api/v1/commit-check` (TODO)
  - Shared `applier_factory.py` construction pattern between `nos-cli` and `nos-api` to prevent configuration drift
  - systemd service `nos-api.service` (after `nos-pfe.service`)
  - Tested: `tests/api/` (53 tests, all passing)
- **Implemented: libvirt QEMU hook for vnetX provisioning** — `/etc/libvirt/hooks/qemu` installed by `nos-install.sh`
  - On VM start: parses domain XML, provisions `vnetX` interfaces as access ports on configured VLAN (default 115, overridable per-domain via XML metadata) via NOS REST API
  - Proactive ghost cleanup: compares running config `vnetX` against `/sys/class/net/vnetN`, deletes orphaned entries before provisioning new ones, all in single commit
  - Tested: `tests/integration/test_libvirt_hook.py` (23 tests, all passing)
  - Validated end-to-end on cos-node1: multiple destroy/start and destroy/undefine/recreate cycles, including 5-ghost cleanup scenario
- **Validated: nos-libvirt-hook fires correctly for VMs created via COS agent** — confirmed on cos-node1: COS agent-created VM (not manual virt-install) triggered libvirt hook successfully, vnetX auto-provisioned as VLAN 115 access port on first VM creation through COS API
- Config applier refactor: `nos/config/applier_factory.py` creates ConfigApplier with all necessary drivers (kernel, FRR, PFE, NAT, DHCP, SSH, users); used by both CLI and API to ensure identical config behavior
- **Fixed: nos-api commit no longer crashes (500) if a rollback file in `/opt/nos/config/rollback/` has incorrect ownership** — `_rotate_rollbacks()` now recovers via `os.remove` + retry, or skips the slot with a logged error rather than raising. `nos-install.sh` now chowns existing `rollback/*.json` to `nos:nos` on every install (commit 709fa1a). Discovered while testing COS network/VLAN provisioning end-to-end through the portal UI — a stale `rollback.20.json` owned by `super:super` (from earlier manual `nos-cli` testing) caused commit failures for VLAN provisioning requests coming from `cos-agent` via `nos-api`.

## Recent Changes (2026-06-14)
- **Implemented: libvirt QEMU hook** — `scripts/nos-libvirt-hook.sh` installed to `/etc/libvirt/hooks/qemu` by `nos-install.sh`; automatically provisions `vnetX` access ports on VM start (via POST /api/v1/config + commit) and removes them on VM stop; VLAN selectable per-domain via XML metadata (`<nos:vlan>` element); falls back to VLAN 115. Added `libxml2-utils` to apt deps. Tested: `tests/integration/test_libvirt_hook.py` (20 tests).
- **Fixed: native XDP on i40e physical NICs** — three-tier attach strategy (link-based BPF_LINK_CREATE -> legacy XDP_FLAGS_DRV_MODE -> generic SKB_MODE) in `pfe/xdp/xdp_loader.c`; `main.c` no longer forces SKB_MODE on attach_all
- **Fixed: native XDP on i40e dropped ARP/ICMP** — local-delivery checks (ARP, local_ip4_map/local_ip6_map) moved before vlan_map redirect in `xdp_prog.c`, to account for i40e disabling HW VLAN stripping in native mode
- **Fixed: `show forwarding` misreported native XDP as kernel** — `manager.py` detect_forwarding_mode() now recognizes `XDP_ATTACHED="xdp"` (link-based) as XDP_NATIVE in addition to `"xdpdrv"` and `"xdpgeneric"`
- **Fixed: nos-install.sh fresh-install failures** — added build-essential, fixed bpftool->linux-tools package name, fixed /opt/nos/config and rollback/ permissions (775), fixed config file "same file" copy error on reinstall, install empty configs instead of dev's working config, pre-create /run/nos/stats.json(.tmp) with correct perms
- Validated: full fresh-install end-to-end on bare-metal Ubuntu 24.04 (i40e dual X710/X722), single unmodified `nos-install.sh` run, NOS + COS controller VM + COS agent all functional from scratch
- Created `COS_NOS_Install_Guide.md` — step-by-step physical node + controller VM install guide, validated against the fresh install above

## Recent Changes (2026-06-09)
- Implemented: NAT engine — static, source, destination with nftables backend and show commands
- Implemented: User management — local users with class-based access control (super-user/operator/read-only)
- Implemented: SSH server configuration (`set system services ssh`) with automatic socket activation disable on Ubuntu 24.04
- Implemented: Enhanced IS-IS show commands — interface, adjacency, database, route with JunOS format and text parsing
- Implemented: Multi-value set commands — JunOS-style `set interfaces et1 mtu 9000 unit 101 vlan-id 101` on single line
- Fixed: FormattedText rendering bug (completion hints now show plain text)
- Fixed: Tab completion for multi-value commands (correct CONFIG_TREE navigation)
- Updated: Install script — setcap extended (cap_net_raw, cap_sys_admin), nftables package, sudoers rules for NAT/users/SSH
