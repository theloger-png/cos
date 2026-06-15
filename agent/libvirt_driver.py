"""libvirt Python bindings wrapper for KVM virtual machine management."""

from __future__ import annotations

import logging
import os
import shutil
import uuid

import libvirt
import psutil

logger = logging.getLogger(__name__)

_DISK_BASE_DIR = "/var/lib/cos/vms"

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
    <interface type='bridge'>
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

    def create_vm(
        self,
        name: str,
        cpu_cores: int,
        ram_mb: int,
        disk_gb: int,
        image_path: str,
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

        xml = _DOMAIN_XML_TEMPLATE.format(
            name=name,
            uuid=domain_uuid,
            ram_mb=ram_mb,
            cpu_cores=cpu_cores,
            disk_path=disk_path,
            bridge=self._bridge,
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
            import xml.etree.ElementTree as ET
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
