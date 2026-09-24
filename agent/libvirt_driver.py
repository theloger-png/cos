"""libvirt Python bindings wrapper for KVM virtual machine management."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import libvirt
import psutil

logger = logging.getLogger(__name__)

_DISK_BASE_DIR = "/var/lib/cos/vms"
_SEED_BASE_DIR = "/var/lib/cos/seeds"

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _mem_to_mib(value: int, unit: str) -> int:
    """Convert a libvirt memory value with its unit string to MiB."""
    u = unit.lower()
    if u in ("b",):
        return value // (1024 * 1024)
    if u in ("kib", "k", "kb"):
        return value // 1024
    if u in ("mib", "m", "mb"):
        return value
    if u in ("gib", "g", "gb"):
        return value * 1024
    return value // 1024  # libvirt default is KiB


def _disk_size_gb(path: str) -> float:
    """Return the virtual disk size in GB via qemu-img, or 0.0 on failure."""
    if not path or not os.path.exists(path):
        return 0.0
    try:
        result = subprocess.run(
            ["qemu-img", "info", "--output=json", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            return info.get("virtual-size", 0) / (1024 ** 3)
    except Exception as exc:
        logger.debug("qemu-img info failed for %s: %s", path, exc)
    return 0.0


_INTERFACE_VLAN_BLOCK = (
    "      <virtualport type='openvswitch'/>\n"
    "      <vlan>\n"
    "        <tag id='{vlan_id}'/>\n"
    "      </vlan>\n"
)

_SEED_DISK_BLOCK = (
    "    <disk type='file' device='cdrom'>\n"
    "      <driver name='qemu' type='raw'/>\n"
    "      <source file='{seed_path}'/>\n"
    "      <target dev='hda' bus='ide'/>\n"
    "      <readonly/>\n"
    "    </disk>\n"
)

_DOMAIN_XML_TEMPLATE = """\
<domain type='kvm'>
  <name>{name}</name>
  <uuid>{uuid}</uuid>
  <memory unit='MiB'>{ram_mb}</memory>
  <currentMemory unit='MiB'>{ram_mb}</currentMemory>
  <vcpu>{cpu_cores}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-i440fx-2.9'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-model'/>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
{seed_disk_block}    <interface type='bridge'>
      <mac address='{mac_address}'/>
      <source bridge='{bridge}'/>
      <model type='virtio'/>
{interface_vlan_block}    </interface>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <graphics type='vnc' port='-1' autoport='yes'/>
  </devices>
</domain>
"""


def _generate_mac() -> str:
    """Generate a random MAC address under the libvirt-convention 52:54:00 OUI prefix."""
    octets = [0x52, 0x54, 0x00] + [random.randint(0x00, 0xFF) for _ in range(3)]
    return ":".join(f"{o:02x}" for o in octets)


def _make_cloud_init_user_data(cloud_init_user: str, cloud_init_password_hash: str) -> str:
    """Return cloud-init user-data YAML for chpasswd with a pre-hashed password."""
    return (
        "#cloud-config\n"
        "chpasswd:\n"
        "  users:\n"
        f"    - name: {cloud_init_user}\n"
        f"      password: '{cloud_init_password_hash}'\n"
        "      type: hash\n"
        "  expire: false\n"
        "ssh_pwauth: true\n"
    )


def _make_cloud_init_meta_data(vm_name: str, instance_id: str) -> str:
    """Return cloud-init meta-data YAML with the given instance-id and hostname."""
    return (
        f"instance-id: {instance_id}\n"
        f"local-hostname: {vm_name}\n"
    )


def _make_cloud_init_network_config_multi(nics: list[dict]) -> str:
    """Return cloud-init network-config v2 YAML for one or more interfaces.

    Each entry is ``{"mac": str, "ip_cidr": str | None, "gateway": str | None}``.
    Entries with both ip_cidr and gateway get a static address and default
    route; all others get DHCP. Interfaces are matched by MAC address rather
    than name, since the guest-visible interface name (ens3, enp1s0, ...)
    varies by OS and virtio driver, while the MAC is known and fixed at
    domain-definition time.

    Every NIC that should be configured must be listed: a supplied
    network-config replaces cloud-init's automatic DHCP fallback for
    interfaces it does not mention.
    """
    lines = ["network:", "  version: 2", "  ethernets:"]
    for idx, nic in enumerate(nics):
        lines += [f"    eth{idx}:", "      match:", f"        macaddress: '{nic['mac']}'"]
        ip_cidr = nic.get("ip_cidr")
        gateway = nic.get("gateway")
        if ip_cidr and gateway:
            lines += [
                "      addresses:",
                f"        - {ip_cidr}",
                "      routes:",
                "        - to: default",
                f"          via: {gateway}",
            ]
            if idx > 0:
                # A second default route with the same metric fails to install
                # on Linux; keep the first NIC's route preferred.
                lines.append(f"          metric: {1024 * (idx + 1)}")
        else:
            lines.append("      dhcp4: true")
    return "\n".join(lines) + "\n"


def _make_cloud_init_network_config(mac_address: str, ip_cidr: str, gateway: str) -> str:
    """Return cloud-init network-config v2 YAML for a single static-IP interface."""
    return _make_cloud_init_network_config_multi(
        [{"mac": mac_address, "ip_cidr": ip_cidr, "gateway": gateway}]
    )


def _seed_iso_path(vm_uuid: str) -> str:
    """Return the cloud-init seed ISO path for a domain."""
    return os.path.join(_SEED_BASE_DIR, f"{vm_uuid}.iso")


def _seed_state_path(vm_uuid: str) -> str:
    """Return the path of the JSON sidecar recording what went into a domain's seed ISO."""
    return os.path.join(_SEED_BASE_DIR, f"{vm_uuid}.seed.json")


def _load_seed_state(vm_uuid: str) -> dict | None:
    """Load the seed sidecar ({user_data, nics}) for a domain, or None if absent/unreadable."""
    try:
        with open(_seed_state_path(vm_uuid)) as f:
            state = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable seed state for %s: %s", vm_uuid, exc)
        return None
    if not isinstance(state, dict) or not isinstance(state.get("user_data"), str):
        return None
    if not isinstance(state.get("nics"), list):
        state["nics"] = []
    return state


def _save_seed_state(vm_uuid: str, user_data: str, nic_configs: list[dict]) -> None:
    """Record the seed's user-data and static NIC configs so the seed can be rebuilt later.

    The ISO itself is not parsed back because it cannot be read without extra
    tooling on the node. The file holds the password hash, so it is created 0600.
    """
    static = [
        {"mac": n["mac"], "ip_cidr": n["ip_cidr"], "gateway": n["gateway"]}
        for n in nic_configs
        if n.get("ip_cidr") and n.get("gateway")
    ]
    try:
        with open(
            _seed_state_path(vm_uuid), "w", opener=lambda p, flags: os.open(p, flags, 0o600)
        ) as f:
            json.dump({"user_data": user_data, "nics": static}, f)
    except OSError as exc:
        logger.warning("Failed to save seed state for %s: %s", vm_uuid, exc)


def _run_cloud_localds(
    vm_name: str, user_data: str, nic_configs: list[dict], output_path: str
) -> bool:
    """Build a cloud-init seed ISO at *output_path*. Returns True on success.

    Uses a random UUID as instance-id on every call to avoid cloud-init
    skipping re-configuration when an image is reused across VMs or a seed is
    rebuilt for an existing one. When *nic_configs* is non-empty a
    network-config v2 file is generated from it and passed via
    --network-config; when empty the guest keeps its default DHCP behavior.
    """
    meta_data = _make_cloud_init_meta_data(vm_name, str(uuid.uuid4()))
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            user_data_path = os.path.join(tmpdir, "user-data")
            meta_data_path = os.path.join(tmpdir, "meta-data")
            with open(user_data_path, "w") as f:
                f.write(user_data)
            with open(meta_data_path, "w") as f:
                f.write(meta_data)

            cmd = ["cloud-localds", output_path, user_data_path, meta_data_path]
            if nic_configs:
                network_config_path = os.path.join(tmpdir, "network-config")
                with open(network_config_path, "w") as f:
                    f.write(_make_cloud_init_network_config_multi(nic_configs))
                cmd += ["--network-config", network_config_path]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("cloud-localds failed for %s: %s", vm_name, result.stderr)
                return False
    except Exception as exc:
        logger.error("Failed to build cloud-init seed for %s: %s", vm_name, exc)
        return False
    return True


def _collect_interface_ipv4(domain: libvirt.virDomain, source: int) -> dict[str, list[str]]:
    """Return {lower-case MAC: [IPv4 addresses]} from one interfaceAddresses() source.

    Returns an empty dict when the source is unavailable (domain not running,
    guest agent not responding, ...). Loopback and link-local addresses are skipped.
    """
    try:
        ifaces = domain.interfaceAddresses(source)
    except libvirt.libvirtError as exc:
        logger.debug("interfaceAddresses(source=%s) unavailable: %s", source, exc)
        return {}

    result: dict[str, list[str]] = {}
    for iface in ifaces.values():
        hwaddr = iface.get("hwaddr")
        if not hwaddr:
            continue
        for addr in iface.get("addrs") or []:
            if addr.get("type") != libvirt.VIR_IP_ADDR_TYPE_IPV4:
                continue
            ip = addr.get("addr")
            try:
                parsed = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if parsed.is_loopback or parsed.is_link_local:
                continue
            ips = result.setdefault(hwaddr.lower(), [])
            if ip not in ips:
                ips.append(ip)
    return result


def _lookup_nic_ips(domain: libvirt.virDomain, macs: list[str]) -> dict[str, list[str]]:
    """Return {lower-case MAC: [IPv4]} for *macs*, preferring the guest agent over ARP.

    The ARP source (host neighbour table) is only queried when the guest agent
    is unavailable or knows no address for at least one of the NICs.
    """
    ips = _collect_interface_ipv4(domain, libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT)
    if any(not ips.get(m) for m in macs):
        arp_ips = _collect_interface_ipv4(domain, libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP)
        for m in macs:
            if not ips.get(m) and arp_ips.get(m):
                ips[m] = arp_ips[m]
    return ips


class LibvirtDriver:
    """Manages KVM virtual machines via libvirt."""

    def __init__(self, uri: str = "qemu:///system", bridge: str = "nos-br") -> None:
        self._uri = uri
        self._bridge = bridge

    def _connect(self) -> libvirt.virConnect:
        conn = libvirt.open(self._uri)
        if conn is None:
            raise RuntimeError(f"Failed to connect to libvirt at {self._uri}")
        return conn

    def _build_cloud_init_seed(
        self,
        vm_name: str,
        vm_uuid: str,
        cloud_init_user: str,
        cloud_init_password_hash: str,
        nic_configs: list[dict] | None = None,
    ) -> str | None:
        """Build a cloud-init seed ISO and return its path, or None on failure.

        When *nic_configs* is non-empty ([{mac, ip_cidr, gateway}, ...]), a
        network-config v2 file is generated from it and passed to
        cloud-localds, configuring static IPs on the guest's NICs. When
        omitted, no network-config file is added and the guest falls back to
        its default DHCP behavior.

        On success the seed's inputs are recorded in a JSON sidecar so the
        seed can later be rebuilt (see _apply_static_ip_to_seed).
        """
        os.makedirs(_SEED_BASE_DIR, exist_ok=True)
        seed_path = _seed_iso_path(vm_uuid)
        nic_configs = nic_configs or []
        user_data = _make_cloud_init_user_data(cloud_init_user, cloud_init_password_hash)

        if not _run_cloud_localds(vm_name, user_data, nic_configs, seed_path):
            return None
        _save_seed_state(vm_uuid, user_data, nic_configs)
        return seed_path

    def _apply_static_ip_to_seed(
        self,
        domain: libvirt.virDomain,
        vm_uuid: str,
        mac: str,
        ip_cidr: str,
        gateway: str,
    ) -> str | None:
        """Rebuild a stopped domain's cloud-init seed so NIC *mac* gets a static IP.

        Returns None on success, or a human-readable failure reason. Static
        entries already recorded for other NICs are kept; every other NIC in
        the domain is listed with DHCP, because a network-config replaces
        cloud-init's automatic DHCP fallback for interfaces it omits. If the
        domain has no seed yet, a new one with empty user-data is created and
        attached. If a seed ISO exists but its inputs were never recorded
        (created before the sidecar existed), it is left untouched because its
        user-data cannot be recovered.
        """
        seed_path = _seed_iso_path(vm_uuid)
        state = _load_seed_state(vm_uuid)
        if state is None:
            if os.path.exists(seed_path):
                return (
                    "existing cloud-init seed has no recorded state to merge with "
                    "(created before static IP support); left unchanged"
                )
            state = {"user_data": "#cloud-config\n", "nics": []}

        root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        macs = [m.get("address", "") for m in root.findall(".//interface/mac") if m.get("address")]
        if mac.lower() not in {m.lower() for m in macs}:
            macs.append(mac)

        static = {n["mac"].lower(): n for n in state["nics"]}
        static[mac.lower()] = {"ip_cidr": ip_cidr, "gateway": gateway}
        nic_configs = [
            {
                "mac": m,
                "ip_cidr": static.get(m.lower(), {}).get("ip_cidr"),
                "gateway": static.get(m.lower(), {}).get("gateway"),
            }
            for m in macs
        ]

        os.makedirs(_SEED_BASE_DIR, exist_ok=True)
        tmp_path = f"{seed_path}.tmp"
        if not _run_cloud_localds(domain.name(), state["user_data"], nic_configs, tmp_path):
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            return "cloud-localds failed while rebuilding the cloud-init seed"
        try:
            os.replace(tmp_path, seed_path)
        except OSError as exc:
            return f"could not install rebuilt cloud-init seed: {exc}"
        _save_seed_state(vm_uuid, state["user_data"], nic_configs)

        attached = any(
            src.get("file") == seed_path
            for src in root.findall(".//disk[@device='cdrom']/source")
        )
        if not attached:
            try:
                domain.attachDeviceFlags(
                    _SEED_DISK_BLOCK.format(seed_path=seed_path),
                    libvirt.VIR_DOMAIN_AFFECT_CONFIG,
                )
            except libvirt.libvirtError as exc:
                return f"rebuilt cloud-init seed could not be attached: {exc}"
        return None

    def create_vm(
        self,
        name: str,
        cpu_cores: int,
        ram_mb: int,
        disk_gb: int,
        image_path: str,
        vlan_id: int | None = None,
        cloud_init_user: str | None = None,
        cloud_init_password_hash: str | None = None,
        ip_cidr: str | None = None,
        gateway: str | None = None,
    ) -> str:
        """Define and start a new KVM domain, returning its libvirt UUID."""
        domain_uuid = str(uuid.uuid4())
        mac_address = _generate_mac()
        os.makedirs(_DISK_BASE_DIR, exist_ok=True)
        disk_path = os.path.join(_DISK_BASE_DIR, f"{domain_uuid}.qcow2")

        if image_path and os.path.exists(image_path):
            shutil.copy2(image_path, disk_path)
            result = subprocess.run(
                ["qemu-img", "resize", disk_path, f"{disk_gb}G"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error("qemu-img resize failed for %s: %s", disk_path, result.stderr)
        else:
            # Create a blank qcow2 disk using qemu-img
            os.system(f"qemu-img create -f qcow2 {disk_path} {disk_gb}G")

        seed_disk_block = ""
        if cloud_init_user and cloud_init_password_hash:
            nic_configs = []
            if ip_cidr and gateway:
                nic_configs = [{"mac": mac_address, "ip_cidr": ip_cidr, "gateway": gateway}]
            seed_path = self._build_cloud_init_seed(
                vm_name=name,
                vm_uuid=domain_uuid,
                cloud_init_user=cloud_init_user,
                cloud_init_password_hash=cloud_init_password_hash,
                nic_configs=nic_configs,
            )
            if seed_path:
                seed_disk_block = _SEED_DISK_BLOCK.format(seed_path=seed_path)

        interface_vlan_block = (
            _INTERFACE_VLAN_BLOCK.format(vlan_id=vlan_id) if vlan_id is not None else ""
        )
        xml = _DOMAIN_XML_TEMPLATE.format(
            name=name,
            uuid=domain_uuid,
            ram_mb=ram_mb,
            cpu_cores=cpu_cores,
            disk_path=disk_path,
            bridge=self._bridge,
            mac_address=mac_address,
            interface_vlan_block=interface_vlan_block,
            seed_disk_block=seed_disk_block,
        )

        conn = self._connect()
        try:
            domain = conn.defineXML(xml)
            domain.create()
            logger.info("Created VM %s with UUID %s", name, domain_uuid)
            return domain_uuid
        finally:
            conn.close()

    def start_vm(self, libvirt_uuid: str) -> bool:
        """Start a stopped domain. Returns True on success."""
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            domain.create()
            return True
        except libvirt.libvirtError as exc:
            logger.error("start_vm %s failed: %s", libvirt_uuid, exc)
            return False
        finally:
            conn.close()

    def stop_vm(self, libvirt_uuid: str) -> bool:
        """Gracefully shut down a domain via ACPI. Returns True on success."""
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            domain.shutdown()
            return True
        except libvirt.libvirtError as exc:
            logger.error("stop_vm %s failed: %s", libvirt_uuid, exc)
            return False
        finally:
            conn.close()

    def reboot_vm(self, libvirt_uuid: str) -> bool:
        """Reboot a running domain. Returns True on success."""
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            domain.reboot()
            return True
        except libvirt.libvirtError as exc:
            logger.error("reboot_vm %s failed: %s", libvirt_uuid, exc)
            return False
        finally:
            conn.close()

    def destroy_vm(self, libvirt_uuid: str) -> bool:
        """Force-stop, undefine, and delete the disk of a domain."""
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            xml = domain.XMLDesc()
            try:
                domain.destroy()
            except libvirt.libvirtError:
                pass

            disk_path: str | None = None
            root = ET.fromstring(xml)
            for source in root.findall(".//disk[@device='disk']/source"):
                disk_path = source.get("file")
                break

            domain.undefine()

            if disk_path and os.path.exists(disk_path):
                os.remove(disk_path)
                logger.info("Deleted disk %s for VM %s", disk_path, libvirt_uuid)

            return True
        except libvirt.libvirtError as exc:
            logger.error("destroy_vm %s failed: %s", libvirt_uuid, exc)
            return False
        finally:
            conn.close()


    def migrate_vm(self, libvirt_uuid: str, target_uri: str) -> bool:
        """Live-migrate a domain to *target_uri*. Returns True on success."""
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            dest_conn = libvirt.open(target_uri)
            try:
                domain.migrate(
                    dest_conn,
                    libvirt.VIR_MIGRATE_LIVE | libvirt.VIR_MIGRATE_PERSIST_DEST,
                    None,
                    None,
                    0,
                )
                logger.info("Migrated VM %s to %s", libvirt_uuid, target_uri)
                return True
            finally:
                dest_conn.close()
        except libvirt.libvirtError as exc:
            logger.error("migrate_vm %s to %s failed: %s", libvirt_uuid, target_uri, exc)
            return False
        finally:
            conn.close()

    def list_vms(self) -> list[dict]:
        """Return all defined domains with their uuid, name, and state."""
        conn = self._connect()
        try:
            domains = conn.listAllDomains()
            result = []
            for d in domains:
                state, _ = d.state()
                result.append({
                    "uuid": d.UUIDString(),
                    "name": d.name(),
                    "state": state,
                })
            return result
        finally:
            conn.close()

    def get_node_stats(self) -> dict:
        """Return current CPU, RAM, and disk utilisation for this node."""
        cpu_percent = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": cpu_percent,
            "ram_total_mb": mem.total // (1024 * 1024),
            "ram_used_mb": mem.used // (1024 * 1024),
            "disk_total_gb": disk.total / (1024 ** 3),
            "disk_used_gb": disk.used / (1024 ** 3),
        }

    def get_vm_config(self, libvirt_uuid: str) -> dict:
        """Return the current hardware configuration of a domain as a plain dict.

        Shape: {vcpu, memory_mb, disks: [{target, size_gb, path, device}],
                nics: [{target, mac, bridge, vlan_id, ip_addresses}]}

        Each NIC's vlan_id is read directly from its own <vlan><tag id='X'/></vlan>
        element in the domain XML, applied natively by libvirt+OVS.

        ip_addresses holds the NIC's live IPv4 addresses, matched by MAC: the
        qemu-guest-agent is asked first, falling back to the host ARP table.
        It is empty for stopped domains.
        """
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            xml_str = domain.XMLDesc(0)
            root = ET.fromstring(xml_str)
            nic_macs = [
                m.get("address", "").lower()
                for m in root.findall(".//interface[@type='bridge']/mac")
                if m.get("address")
            ]
            ip_map = _lookup_nic_ips(domain, nic_macs)
        finally:
            conn.close()

        # --- vCPU ---
        vcpu_elem = root.find("vcpu")
        vcpu = int(vcpu_elem.text) if vcpu_elem is not None and vcpu_elem.text else 1

        # --- Memory (libvirt default unit is KiB) ---
        mem_elem = root.find("memory")
        mem_val = int(mem_elem.text) if mem_elem is not None and mem_elem.text else 0
        mem_unit = mem_elem.get("unit", "KiB") if mem_elem is not None else "KiB"
        memory_mb = _mem_to_mib(mem_val, mem_unit)

        # --- Disks ---
        disks: list[dict] = []
        for disk_elem in root.findall(".//disk"):
            device = disk_elem.get("device", "disk")
            source = disk_elem.find("source")
            target = disk_elem.find("target")
            if source is None or target is None:
                continue
            path = source.get("file", "")
            dev = target.get("dev", "")
            size_gb = _disk_size_gb(path)
            disks.append({"target": dev, "size_gb": size_gb, "path": path, "device": device})

        # --- NICs ---
        nics: list[dict] = []
        for iface_elem in root.findall(".//interface[@type='bridge']"):
            mac_elem = iface_elem.find("mac")
            source = iface_elem.find("source")
            target = iface_elem.find("target")
            tag_elem = iface_elem.find("vlan/tag")
            mac = mac_elem.get("address", "") if mac_elem is not None else ""
            bridge = source.get("bridge", "") if source is not None else ""
            vnet = target.get("dev", "") if target is not None else ""
            vlan_id = int(tag_elem.get("id")) if tag_elem is not None and tag_elem.get("id") else None
            nics.append({
                "target": vnet,
                "mac": mac,
                "bridge": bridge,
                "vlan_id": vlan_id,
                "ip_addresses": ip_map.get(mac.lower(), []),
            })

        return {"vcpu": vcpu, "memory_mb": memory_mb, "disks": disks, "nics": nics}

    # ------------------------------------------------------------------
    # Hardware editing
    # ------------------------------------------------------------------

    def apply_vm_config(
        self,
        libvirt_uuid: str,
        changes: dict,
    ) -> dict:
        """Apply hardware changes to a domain and return the resulting config.

        *changes* keys (all optional):
            vcpu (int): new vCPU count
            memory_mb (int): new RAM in MiB
            add_disks (list[{size_gb}]): new secondary disks to create and attach
            add_nics (list[{vlan_id, ip_cidr?, gateway?}]): new NICs to attach on
                *bridge*, VLAN-tagged natively by libvirt+OVS. When ip_cidr and
                gateway are both given and the domain is shut off, its cloud-init
                seed is rebuilt to configure the static IP on next boot; on any
                other state the NIC is attached without it and a nic_failures
                entry explains why.
            remove_nics (list[{target}]): existing NIC target names to detach

        Apply order:
        1. NIC removals (live detach; OVS releases the port automatically) - no reboot
        2. NIC additions (live attach with OVS VLAN tag) - no reboot
        3. If vcpu/memory/disk changes: graceful shutdown → modify XML → redefine → start

        Returns the updated hardware config dict (same shape as get_vm_config).
        """
        add_disks: list[dict] = changes.get("add_disks", [])
        add_nics: list[dict] = changes.get("add_nics", [])
        remove_nics: list[dict] = changes.get("remove_nics", [])
        new_vcpu: int | None = changes.get("vcpu")
        new_memory_mb: int | None = changes.get("memory_mb")
        needs_reboot = bool(new_vcpu or new_memory_mb or add_disks)

        nic_failures: list[dict] = []

        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)

            # ── Step 1: NIC removals ──────────────────────────────────────
            for nic in remove_nics:
                target_name = nic.get("target", "")
                if not target_name:
                    continue

                # Extract MAC from live XML to build a minimal detach fragment.
                xml_str = domain.XMLDesc(0)
                root = ET.fromstring(xml_str)
                mac_address: str | None = None
                for iface_elem in root.findall(".//interface"):
                    t = iface_elem.find("target")
                    if t is None or t.get("dev") != target_name:
                        continue
                    mac_elem = iface_elem.find("mac")
                    if mac_elem is not None:
                        mac_address = mac_elem.get("address", "")
                    break

                if mac_address is None:
                    reason = f"NIC {target_name} not found in domain XML"
                    logger.warning(reason)
                    nic_failures.append({"target": target_name, "reason": reason})
                    continue

                # Minimal XML avoids <address>/<alias> PCI mismatch on detach.
                detach_xml = (
                    f"<interface type='bridge'>"
                    f"<mac address='{mac_address}'/>"
                    f"<source bridge='{self._bridge}'/>"
                    f"<model type='virtio'/>"
                    f"</interface>"
                )

                state, _ = domain.state()
                if state == libvirt.VIR_DOMAIN_RUNNING:
                    flags = libvirt.VIR_DOMAIN_AFFECT_LIVE
                else:
                    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG

                try:
                    domain.detachDeviceFlags(detach_xml, flags)
                except libvirt.libvirtError as exc:
                    reason = str(exc)
                    logger.warning("detach NIC %s failed: %s", target_name, reason)
                    nic_failures.append({"target": target_name, "reason": reason})

            # ── Step 2: NIC additions ─────────────────────────────────────
            for nic in add_nics:
                vlan_id = nic.get("vlan_id")
                ip_cidr = nic.get("ip_cidr")
                gateway = nic.get("gateway")
                # Explicit MAC so the guest network-config can match this NIC.
                new_mac = _generate_mac()
                vlan_fragment = (
                    _INTERFACE_VLAN_BLOCK.format(vlan_id=vlan_id) if vlan_id is not None else ""
                )
                nic_xml = (
                    f"<interface type='bridge'>"
                    f"<mac address='{new_mac}'/>"
                    f"<source bridge='{self._bridge}'/>"
                    f"<model type='virtio'/>"
                    f"{vlan_fragment}"
                    f"</interface>"
                )

                state, _ = domain.state()
                is_running = state == libvirt.VIR_DOMAIN_RUNNING

                flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
                if is_running:
                    flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

                try:
                    domain.attachDeviceFlags(nic_xml, flags)
                except libvirt.libvirtError as exc:
                    reason = str(exc)
                    logger.error("attach NIC (vlan=%s) failed: %s", vlan_id, reason)
                    nic_failures.append({
                        "target": f"new-nic (vlan {vlan_id})",
                        "reason": reason,
                    })
                    continue

                if ip_cidr and gateway:
                    nic_label = f"new-nic (vlan {vlan_id})"
                    if state != libvirt.VIR_DOMAIN_SHUTOFF:
                        reason = (
                            "static IP requires the VM to be stopped - NIC attached "
                            "without static IP configuration, guest will need manual "
                            "network configuration or a reboot after adding a "
                            "network-config"
                        )
                        logger.warning("%s (%s)", reason, nic_label)
                        nic_failures.append({"target": nic_label, "reason": reason})
                    else:
                        seed_failure = self._apply_static_ip_to_seed(
                            domain, libvirt_uuid, new_mac, ip_cidr, gateway
                        )
                        if seed_failure:
                            logger.error("static IP for %s failed: %s", nic_label, seed_failure)
                            nic_failures.append({
                                "target": nic_label,
                                "reason": (
                                    "NIC attached but static IP not configured: "
                                    f"{seed_failure}"
                                ),
                            })

            # ── Step 3: Shutdown → modify XML → redefine → start ─────────
            if needs_reboot:
                state, _ = domain.state()
                was_running = state == libvirt.VIR_DOMAIN_RUNNING

                if was_running:
                    try:
                        domain.shutdown()
                    except libvirt.libvirtError as exc:
                        logger.warning("graceful shutdown failed, will force: %s", exc)

                    # Wait up to 60 s for clean shutdown before forcing
                    for _ in range(60):
                        time.sleep(1)
                        state, _ = domain.state()
                        if state == libvirt.VIR_DOMAIN_SHUTOFF:
                            break
                    else:
                        try:
                            domain.destroy()
                        except libvirt.libvirtError as exc:
                            logger.warning("force destroy failed: %s", exc)

                # Use the inactive (persistent) XML for clean redefinition
                xml_str = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
                root = ET.fromstring(xml_str)

                if new_vcpu is not None:
                    vcpu_elem = root.find("vcpu")
                    if vcpu_elem is not None:
                        vcpu_elem.text = str(new_vcpu)

                if new_memory_mb is not None:
                    for tag in ("memory", "currentMemory"):
                        elem = root.find(tag)
                        if elem is not None:
                            elem.set("unit", "KiB")
                            elem.text = str(new_memory_mb * 1024)

                for disk_change in add_disks:
                    size_gb = disk_change.get("size_gb", 0)
                    if not size_gb:
                        continue

                    # Determine next available virtio disk target (vda, vdb, …)
                    used_letters = {
                        e.find("target").get("dev")[2:]
                        for e in root.findall(".//disk[@device='disk']")
                        if e.find("target") is not None
                        and len(e.find("target").get("dev", "")) == 3
                        and e.find("target").get("dev", "").startswith("vd")
                    }
                    next_letter = next(
                        (c for c in "abcdefghijklmnopqrstuvwxyz" if c not in used_letters),
                        None,
                    )
                    if next_letter is None:
                        logger.error("No available disk targets for VM %s", libvirt_uuid)
                        continue

                    next_dev = f"vd{next_letter}"
                    os.makedirs(_DISK_BASE_DIR, exist_ok=True)
                    disk_path = os.path.join(_DISK_BASE_DIR, f"{libvirt_uuid}-{next_dev}.qcow2")

                    result = subprocess.run(
                        ["qemu-img", "create", "-f", "qcow2", disk_path, f"{size_gb}G"],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        logger.error(
                            "qemu-img create failed for %s: %s", disk_path, result.stderr
                        )
                        continue

                    disk_xml = (
                        f"<disk type='file' device='disk'>"
                        f"<driver name='qemu' type='qcow2'/>"
                        f"<source file='{disk_path}'/>"
                        f"<target dev='{next_dev}' bus='virtio'/>"
                        f"</disk>"
                    )
                    devices_elem = root.find("devices")
                    if devices_elem is not None:
                        devices_elem.append(ET.fromstring(disk_xml))

                new_xml = ET.tostring(root, encoding="unicode")
                domain = conn.defineXML(new_xml)
                if domain is None:
                    raise RuntimeError(f"defineXML failed for VM {libvirt_uuid}")

                if was_running:
                    domain.create()
        finally:
            conn.close()

        result = self.get_vm_config(libvirt_uuid)
        result["nic_failures"] = nic_failures
        return result
