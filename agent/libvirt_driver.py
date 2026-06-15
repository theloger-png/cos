"""libvirt Python bindings wrapper for KVM virtual machine management."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import libvirt
import psutil

# Preserve the NOS namespace prefix when round-tripping domain XML through ElementTree.
ET.register_namespace("nos", "https://github.com/theloger-png/nos")

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


def _vlan_for_iface(nos_config: dict | None, target: str) -> int | None:
    """Parse a NOS GET /api/v1/config response to find the VLAN for *target*.

    Handles both scalar members ("vlan101") and list members (["vlan101"]).
    Returns None when the interface or VLAN cannot be determined.
    """
    if not nos_config or not target:
        return None
    try:
        iface = nos_config.get("interfaces", {}).get(target, {})
        members = (
            iface.get("unit", {})
            .get("0", {})
            .get("family", {})
            .get("ethernet-switching", {})
            .get("vlan", {})
            .get("members")
        )
        if isinstance(members, list) and members:
            members = members[0]
        if isinstance(members, str) and members.startswith("vlan"):
            return int(members[4:])
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    return None

_NOS_METADATA_BLOCK = (
    "  <metadata>\n"
    "    <nos:vlan xmlns:nos=\"https://github.com/theloger-png/nos\">{vlan_id}</nos:vlan>\n"
    "  </metadata>\n"
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
{metadata_block}  <memory unit='MiB'>{ram_mb}</memory>
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
      <source bridge='{bridge}'/>
      <model type='virtio'/>
    </interface>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <graphics type='vnc' port='-1' autoport='yes'/>
  </devices>
</domain>
"""


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
    ) -> str | None:
        """Build a cloud-init seed ISO and return its path, or None on failure.

        Uses a random UUID as instance-id on every call to avoid cloud-init
        skipping re-configuration when an image is reused across VMs.
        """
        os.makedirs(_SEED_BASE_DIR, exist_ok=True)
        seed_path = os.path.join(_SEED_BASE_DIR, f"{vm_uuid}.iso")
        instance_id = str(uuid.uuid4())

        user_data = _make_cloud_init_user_data(cloud_init_user, cloud_init_password_hash)
        meta_data = _make_cloud_init_meta_data(vm_name, instance_id)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                user_data_path = os.path.join(tmpdir, "user-data")
                meta_data_path = os.path.join(tmpdir, "meta-data")
                with open(user_data_path, "w") as f:
                    f.write(user_data)
                with open(meta_data_path, "w") as f:
                    f.write(meta_data)
                result = subprocess.run(
                    ["cloud-localds", seed_path, user_data_path, meta_data_path],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.error("cloud-localds failed for %s: %s", vm_name, result.stderr)
                    return None
        except Exception as exc:
            logger.error("Failed to build cloud-init seed for %s: %s", vm_name, exc)
            return None

        return seed_path

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
    ) -> str:
        """Define and start a new KVM domain, returning its libvirt UUID."""
        domain_uuid = str(uuid.uuid4())
        os.makedirs(_DISK_BASE_DIR, exist_ok=True)
        disk_path = os.path.join(_DISK_BASE_DIR, f"{domain_uuid}.qcow2")

        if image_path and os.path.exists(image_path):
            shutil.copy2(image_path, disk_path)
        else:
            # Create a blank qcow2 disk using qemu-img
            os.system(f"qemu-img create -f qcow2 {disk_path} {disk_gb}G")

        seed_disk_block = ""
        if cloud_init_user and cloud_init_password_hash:
            seed_path = self._build_cloud_init_seed(
                vm_name=name,
                vm_uuid=domain_uuid,
                cloud_init_user=cloud_init_user,
                cloud_init_password_hash=cloud_init_password_hash,
            )
            if seed_path:
                seed_disk_block = _SEED_DISK_BLOCK.format(seed_path=seed_path)

        metadata_block = (
            _NOS_METADATA_BLOCK.format(vlan_id=vlan_id) if vlan_id is not None else ""
        )
        xml = _DOMAIN_XML_TEMPLATE.format(
            name=name,
            uuid=domain_uuid,
            ram_mb=ram_mb,
            cpu_cores=cpu_cores,
            disk_path=disk_path,
            bridge=self._bridge,
            metadata_block=metadata_block,
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

    def get_vm_config(self, libvirt_uuid: str, nos_client: object | None = None) -> dict:
        """Return the current hardware configuration of a domain as a plain dict.

        Shape: {vcpu, memory_mb, disks: [{target, size_gb, path, device}],
                nics: [{target, mac, bridge, vlan_id}]}

        *nos_client* should be a NOSApiClient instance. When provided, each NIC's
        vlan_id is resolved by querying GET /api/v1/config on the local NOS instance.
        """
        conn = self._connect()
        try:
            domain = conn.lookupByUUIDString(libvirt_uuid)
            xml_str = domain.XMLDesc(0)
        finally:
            conn.close()

        root = ET.fromstring(xml_str)

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
        nos_config = nos_client.get_config() if nos_client is not None else None
        nics: list[dict] = []
        for iface_elem in root.findall(".//interface[@type='bridge']"):
            mac_elem = iface_elem.find("mac")
            source = iface_elem.find("source")
            target = iface_elem.find("target")
            mac = mac_elem.get("address", "") if mac_elem is not None else ""
            bridge = source.get("bridge", "") if source is not None else ""
            vnet = target.get("dev", "") if target is not None else ""
            vlan_id = _vlan_for_iface(nos_config, vnet)
            nics.append({"target": vnet, "mac": mac, "bridge": bridge, "vlan_id": vlan_id})

        return {"vcpu": vcpu, "memory_mb": memory_mb, "disks": disks, "nics": nics}

    # ------------------------------------------------------------------
    # Hardware editing
    # ------------------------------------------------------------------

    def apply_vm_config(
        self,
        libvirt_uuid: str,
        changes: dict,
        nos_client: object | None = None,
    ) -> dict:
        """Apply hardware changes to a domain and return the resulting config.

        *changes* keys (all optional):
            vcpu (int): new vCPU count
            memory_mb (int): new RAM in MiB
            add_disks (list[{size_gb}]): new secondary disks to create and attach
            add_nics (list[{vlan_id}]): new NICs to attach on *bridge* and provision in NOS
            remove_nics (list[{target}]): existing NIC target names to detach and remove from NOS

        Apply order:
        1. NIC removals (live detach + NOS cleanup) — no reboot
        2. NIC additions (live attach + NOS VLAN provisioning) — no reboot
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
                flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
                if state == libvirt.VIR_DOMAIN_RUNNING:
                    flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

                detach_ok = False
                try:
                    domain.detachDeviceFlags(detach_xml, flags)
                    detach_ok = True
                except libvirt.libvirtError as exc:
                    reason = str(exc)
                    logger.warning("detach NIC %s failed: %s", target_name, reason)
                    nic_failures.append({"target": target_name, "reason": reason})

                # NOS cleanup only when the detach actually succeeded.
                if detach_ok and nos_client is not None:
                    nos_client.post_config([f"delete interfaces {target_name}"])
                    nos_client.commit()

            # ── Step 2: NIC additions ─────────────────────────────────────
            for nic in add_nics:
                vlan_id = nic.get("vlan_id")
                nic_xml = (
                    f"<interface type='bridge'>"
                    f"<source bridge='{self._bridge}'/>"
                    f"<model type='virtio'/>"
                    f"</interface>"
                )

                state, _ = domain.state()
                is_running = state == libvirt.VIR_DOMAIN_RUNNING

                # Capture interface targets before attachment so we can diff
                before_xml = domain.XMLDesc(0)
                before_targets = {
                    e.find("target").get("dev")
                    for e in ET.fromstring(before_xml).findall(".//interface[@type='bridge']")
                    if e.find("target") is not None
                }

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

                # Provision NOS only when the VM is running and vnetX is assigned
                if is_running and nos_client is not None and vlan_id is not None:
                    after_xml = domain.XMLDesc(0)
                    after_targets = {
                        e.find("target").get("dev")
                        for e in ET.fromstring(after_xml).findall(".//interface[@type='bridge']")
                        if e.find("target") is not None
                    }
                    for new_target in after_targets - before_targets:
                        nos_client.post_config([
                            f"set interfaces {new_target} unit 0 family "
                            f"ethernet-switching vlan members vlan{vlan_id}"
                        ])
                        nos_client.commit()

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

        result = self.get_vm_config(libvirt_uuid, nos_client)
        result["nic_failures"] = nic_failures
        return result
