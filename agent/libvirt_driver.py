"""libvirt Python bindings wrapper for KVM virtual machine management."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid

import libvirt
import psutil

logger = logging.getLogger(__name__)

_DISK_BASE_DIR = "/var/lib/cos/vms"
_SEED_BASE_DIR = "/var/lib/cos/seeds"

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
