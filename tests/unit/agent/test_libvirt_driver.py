"""Unit tests for agent/libvirt_driver.py with mocked libvirt connection."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch, call

import pytest
from agent.libvirt_driver import (
    LibvirtDriver,
    _make_cloud_init_user_data,
    _make_cloud_init_meta_data,
    _mem_to_mib,
    _disk_size_gb,
)


@pytest.fixture
def driver() -> LibvirtDriver:
    return LibvirtDriver(uri="qemu:///system", bridge="nos-br")


def _mock_conn() -> MagicMock:
    return MagicMock()


def _mock_domain(uuid_str: str = None, state: int = 1) -> MagicMock:
    domain = MagicMock()
    domain.UUIDString.return_value = uuid_str or str(uuid.uuid4())
    domain.name.return_value = "test-vm"
    domain.state.return_value = (state, 0)
    domain.XMLDesc.return_value = """
    <domain>
      <devices>
        <disk device='disk'>
          <source file='/var/lib/cos/vms/test.qcow2'/>
        </disk>
      </devices>
    </domain>
    """
    return domain


class TestCreateVM:
    def test_returns_uuid_string(self, driver):
        domain = _mock_domain()
        conn = _mock_conn()
        conn.defineXML.return_value = domain

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            result = driver.create_vm("vm-01", 2, 2048, 20, "")

        assert isinstance(result, str)
        assert len(result) == 36  # UUID format
        conn.defineXML.assert_called_once()
        domain.create.assert_called_once()

    def test_copies_image_when_exists(self, driver):
        domain = _mock_domain()
        conn = _mock_conn()
        conn.defineXML.return_value = domain

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("shutil.copy2") as mock_copy:
            driver.create_vm("vm-01", 2, 2048, 20, "/images/ubuntu.qcow2")

        mock_copy.assert_called_once()

    def test_xml_uses_bridge_interface(self, driver):
        domain = _mock_domain()
        conn = _mock_conn()
        conn.defineXML.return_value = domain
        captured_xml: list[str] = []

        def capture_define(xml: str):
            captured_xml.append(xml)
            return domain

        conn.defineXML.side_effect = capture_define

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            driver.create_vm("vm-01", 2, 2048, 20, "")

        assert captured_xml, "defineXML was not called"
        xml = captured_xml[0]
        assert "type='bridge'" in xml
        assert "<source bridge='nos-br'/>" in xml
        assert "type='network'" not in xml
        assert "network='default'" not in xml

    def test_xml_uses_custom_bridge(self):
        custom_driver = LibvirtDriver(uri="qemu:///system", bridge="custom-br0")
        domain = _mock_domain()
        conn = _mock_conn()
        conn.defineXML.return_value = domain
        captured_xml: list[str] = []

        def capture_define(xml: str):
            captured_xml.append(xml)
            return domain

        conn.defineXML.side_effect = capture_define

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            custom_driver.create_vm("vm-02", 1, 1024, 10, "")

        xml = captured_xml[0]
        assert "<source bridge='custom-br0'/>" in xml


class TestCreateVMVlan:
    def _captured_xml(self, driver, **kwargs) -> str:
        domain = _mock_domain()
        conn = _mock_conn()
        captured: list[str] = []

        def capture_define(xml: str):
            captured.append(xml)
            return domain

        conn.defineXML.side_effect = capture_define

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            driver.create_vm("vm-vlan", 2, 2048, 20, "", **kwargs)

        return captured[0]

    def test_with_vlan_id_adds_ovs_virtualport(self, driver):
        xml = self._captured_xml(driver, vlan_id=200)
        assert "<virtualport type='openvswitch'/>" in xml

    def test_with_vlan_id_adds_vlan_tag(self, driver):
        xml = self._captured_xml(driver, vlan_id=200)
        assert "<vlan>" in xml
        assert "<tag id='200'/>" in xml

    def test_vlan_id_value_is_correct(self, driver):
        xml = self._captured_xml(driver, vlan_id=42)
        assert "<tag id='42'/>" in xml

    def test_vlan_tag_nested_inside_interface_element(self, driver):
        """The virtualport/vlan tag must be nested inside the bridge interface."""
        xml = self._captured_xml(driver, vlan_id=200)
        iface_start = xml.index("<interface type='bridge'>")
        iface_end = xml.index("</interface>", iface_start)
        iface_block = xml[iface_start:iface_end]
        assert "<virtualport type='openvswitch'/>" in iface_block
        assert "<tag id='200'/>" in iface_block

    def test_without_vlan_id_no_virtualport_or_vlan_tag(self, driver):
        xml = self._captured_xml(driver)
        assert "<virtualport" not in xml
        assert "<vlan>" not in xml

    def test_vlan_id_none_no_virtualport_or_vlan_tag(self, driver):
        xml = self._captured_xml(driver, vlan_id=None)
        assert "<virtualport" not in xml
        assert "<vlan>" not in xml


class TestStartVM:
    def test_success(self, driver):
        conn = _mock_conn()
        domain = _mock_domain()
        conn.lookupByUUIDString.return_value = domain

        with patch("libvirt.open", return_value=conn):
            result = driver.start_vm("some-uuid")

        assert result is True
        domain.create.assert_called_once()

    def test_libvirt_error_returns_false(self, driver):
        import libvirt as _lv
        conn = _mock_conn()
        conn.lookupByUUIDString.side_effect = _lv.libvirtError("not found")

        with patch("libvirt.open", return_value=conn):
            result = driver.start_vm("bad-uuid")

        assert result is False


class TestStopVM:
    def test_success(self, driver):
        conn = _mock_conn()
        domain = _mock_domain()
        conn.lookupByUUIDString.return_value = domain

        with patch("libvirt.open", return_value=conn):
            result = driver.stop_vm("some-uuid")

        assert result is True
        domain.shutdown.assert_called_once()

    def test_error_returns_false(self, driver):
        import libvirt as _lv
        conn = _mock_conn()
        conn.lookupByUUIDString.side_effect = _lv.libvirtError("gone")

        with patch("libvirt.open", return_value=conn):
            result = driver.stop_vm("bad-uuid")

        assert result is False


class TestDestroyVM:
    def test_success_cleans_disk(self, driver):
        conn = _mock_conn()
        domain = _mock_domain()
        conn.lookupByUUIDString.return_value = domain

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove") as mock_rm:
            result = driver.destroy_vm("some-uuid")

        assert result is True
        domain.destroy.assert_called_once()
        domain.undefine.assert_called_once()
        mock_rm.assert_called_once_with("/var/lib/cos/vms/test.qcow2")

    def test_error_returns_false(self, driver):
        import libvirt as _lv
        conn = _mock_conn()
        conn.lookupByUUIDString.side_effect = _lv.libvirtError("gone")

        with patch("libvirt.open", return_value=conn):
            result = driver.destroy_vm("bad-uuid")

        assert result is False


class TestListVMs:
    def test_returns_list(self, driver):
        uuid1 = str(uuid.uuid4())
        uuid2 = str(uuid.uuid4())
        d1 = _mock_domain(uuid1, state=1)
        d2 = _mock_domain(uuid2, state=5)
        conn = _mock_conn()
        conn.listAllDomains.return_value = [d1, d2]

        with patch("libvirt.open", return_value=conn):
            result = driver.list_vms()

        assert len(result) == 2
        uuids = {r["uuid"] for r in result}
        assert uuid1 in uuids
        assert uuid2 in uuids

    def test_empty_when_no_domains(self, driver):
        conn = _mock_conn()
        conn.listAllDomains.return_value = []

        with patch("libvirt.open", return_value=conn):
            result = driver.list_vms()

        assert result == []


class TestGetNodeStats:
    def test_returns_expected_keys(self, driver):
        with patch("psutil.cpu_percent", return_value=25.0), \
             patch("psutil.virtual_memory") as mock_mem, \
             patch("psutil.disk_usage") as mock_disk:
            mock_mem.return_value = MagicMock(
                total=16 * 1024 ** 3,
                used=4 * 1024 ** 3,
            )
            mock_disk.return_value = MagicMock(
                total=500 * 1024 ** 3,
                used=100 * 1024 ** 3,
            )
            result = driver.get_node_stats()

        assert result["cpu_percent"] == 25.0
        assert result["ram_total_mb"] == 16384
        assert result["ram_used_mb"] == 4096
        assert abs(result["disk_total_gb"] - 500.0) < 1.0
        assert abs(result["disk_used_gb"] - 100.0) < 1.0


class TestMakeCloudInitUserData:
    def test_starts_with_cloud_config_header(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$salt$hash")
        assert ud.startswith("#cloud-config\n")

    def test_contains_chpasswd_block(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$salt$hash")
        assert "chpasswd:" in ud

    def test_contains_correct_username(self):
        ud = _make_cloud_init_user_data("centos", "$6$x$y")
        assert "name: centos" in ud

    def test_contains_password_hash(self):
        pw_hash = "$6$testsalt$testhash"
        ud = _make_cloud_init_user_data("ubuntu", pw_hash)
        assert pw_hash in ud

    def test_chpasswd_type_is_hash(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$s$h")
        assert "type: hash" in ud

    def test_expire_is_false(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$s$h")
        assert "expire: false" in ud

    def test_ssh_pwauth_enabled(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$s$h")
        assert "ssh_pwauth: true" in ud

    def test_custom_user_reflected(self):
        ud = _make_cloud_init_user_data("myuser", "$6$s$h")
        assert "name: myuser" in ud
        assert "name: ubuntu" not in ud


class TestMakeCloudInitMetaData:
    def test_contains_instance_id(self):
        instance_id = str(uuid.uuid4())
        md = _make_cloud_init_meta_data("my-vm", instance_id)
        assert f"instance-id: {instance_id}" in md

    def test_contains_local_hostname(self):
        md = _make_cloud_init_meta_data("my-vm", "some-id")
        assert "local-hostname: my-vm" in md

    def test_different_instance_ids_for_each_call(self):
        """Each direct call with a fresh uuid4 should differ — verified here via the caller."""
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())
        assert id1 != id2
        md1 = _make_cloud_init_meta_data("vm", id1)
        md2 = _make_cloud_init_meta_data("vm", id2)
        assert md1 != md2


class TestCreateVMWithCloudInit:
    def _captured_xml(self, driver, **kwargs) -> str:
        domain = _mock_domain()
        conn = _mock_conn()
        captured: list[str] = []

        def capture_define(xml: str):
            captured.append(xml)
            return domain

        conn.defineXML.side_effect = capture_define

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            driver.create_vm("vm-ci", 2, 2048, 20, "", **kwargs)

        return captured[0]

    def test_no_seed_disk_without_cloud_init_params(self, driver):
        xml = self._captured_xml(driver)
        assert "device='cdrom'" not in xml
        assert "cloud-localds" not in xml

    def test_seed_disk_present_when_cloud_init_provided(self, driver):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc), \
             patch("os.makedirs"), \
             patch("builtins.open", MagicMock()):
            xml = self._captured_xml(
                driver,
                cloud_init_user="ubuntu",
                cloud_init_password_hash="$6$salt$hash",
            )

        assert "device='cdrom'" in xml
        assert "bus='ide'" in xml

    def test_seed_disk_omitted_when_cloud_localds_fails(self, driver):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "cloud-localds not found"

        domain = _mock_domain()
        conn = _mock_conn()
        captured: list[str] = []

        conn.defineXML.side_effect = lambda xml: (captured.append(xml), domain)[1]

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"), \
             patch("subprocess.run", return_value=mock_proc), \
             patch("builtins.open", MagicMock()):
            driver.create_vm(
                "vm-fail",
                2, 2048, 20, "",
                cloud_init_user="ubuntu",
                cloud_init_password_hash="$6$s$h",
            )

        assert captured, "defineXML should have been called"
        assert "device='cdrom'" not in captured[0]


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestMemToMib:
    def test_kib(self):
        assert _mem_to_mib(2097152, "KiB") == 2048

    def test_mib(self):
        assert _mem_to_mib(2048, "MiB") == 2048

    def test_gib(self):
        assert _mem_to_mib(2, "GiB") == 2048

    def test_default_kib(self):
        # unknown unit falls back to KiB
        assert _mem_to_mib(1024, "unknown") == 1


class TestDiskSizeGb:
    def test_returns_virtual_size_in_gb(self):
        qemu_output = json.dumps({"virtual-size": 21474836480})  # 20 GiB in bytes
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = qemu_output

        with patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            size = _disk_size_gb("/some/path.qcow2")

        assert abs(size - 20.0) < 0.1

    def test_returns_zero_when_file_missing(self):
        with patch("os.path.exists", return_value=False):
            assert _disk_size_gb("/missing/path.qcow2") == 0.0

    def test_returns_zero_on_qemu_img_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert _disk_size_gb("/bad/path.qcow2") == 0.0


# ---------------------------------------------------------------------------
# get_vm_config tests
# ---------------------------------------------------------------------------

_SAMPLE_DOMAIN_XML = """\
<domain type='kvm'>
  <name>test-vm</name>
  <uuid>abc-123</uuid>
  <memory unit='KiB'>2097152</memory>
  <currentMemory unit='KiB'>2097152</currentMemory>
  <vcpu placement='static'>2</vcpu>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/var/lib/cos/vms/abc-123.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='/var/lib/cos/seeds/abc-123.iso'/>
      <target dev='hda' bus='ide'/>
      <readonly/>
    </disk>
    <interface type='bridge'>
      <mac address='52:54:00:11:22:33'/>
      <source bridge='nos-br'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
    </interface>
  </devices>
</domain>
"""

_SAMPLE_DOMAIN_XML_WITH_VLAN = _SAMPLE_DOMAIN_XML.replace(
    "<model type='virtio'/>\n    </interface>",
    "<model type='virtio'/>\n      <vlan><tag id='101'/></vlan>\n    </interface>",
)


def _mock_domain_for_config(xml: str = _SAMPLE_DOMAIN_XML) -> MagicMock:
    d = MagicMock()
    d.XMLDesc.return_value = xml
    return d


class TestGetVmConfig:
    def _run(self, xml: str = _SAMPLE_DOMAIN_XML) -> dict:
        domain = _mock_domain_for_config(xml)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            return driver.get_vm_config("abc-123")

    def test_returns_vcpu(self):
        result = self._run()
        assert result["vcpu"] == 2

    def test_returns_memory_mb(self):
        result = self._run()
        assert result["memory_mb"] == 2048  # 2097152 KiB = 2048 MiB

    def test_returns_disks(self):
        result = self._run()
        assert any(d["target"] == "vda" and d["device"] == "disk" for d in result["disks"])
        assert any(d["target"] == "hda" and d["device"] == "cdrom" for d in result["disks"])

    def test_returns_nics(self):
        result = self._run()
        assert len(result["nics"]) == 1
        nic = result["nics"][0]
        assert nic["target"] == "vnet0"
        assert nic["mac"] == "52:54:00:11:22:33"
        assert nic["bridge"] == "nos-br"

    def test_nic_vlan_id_from_domain_xml(self):
        """get_vm_config reads vlan_id from the interface's own <vlan><tag id='X'/> element."""
        result = self._run(xml=_SAMPLE_DOMAIN_XML_WITH_VLAN)
        assert result["nics"][0]["vlan_id"] == 101

    def test_nic_vlan_id_none_when_no_vlan_tag(self):
        result = self._run()
        assert result["nics"][0]["vlan_id"] is None

    def test_disk_size_queried_via_qemu_img(self):
        qemu_output = json.dumps({"virtual-size": 21474836480})
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = qemu_output

        domain = _mock_domain_for_config()
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=mock_proc):
            result = driver.get_vm_config("abc-123")

        vda = next(d for d in result["disks"] if d["target"] == "vda")
        assert abs(vda["size_gb"] - 20.0) < 0.1


# ---------------------------------------------------------------------------
# apply_vm_config tests
# ---------------------------------------------------------------------------


_SAMPLE_DOMAIN_XML_WITH_PCI = _SAMPLE_DOMAIN_XML.replace(
    "<model type='virtio'/>",
    "<model type='virtio'/>"
    "<alias name='net0'/>"
    "<address type='pci' domain='0x0000' bus='0x00' slot='0x06' function='0x0'/>",
)


class TestApplyVmConfigNicRemoval:
    """NIC removal - live detach only; OVS releases the port automatically, no reboot."""

    def _run_remove(self, target="vnet0", domain_xml=_SAMPLE_DOMAIN_XML):
        domain = _mock_domain_for_config(domain_xml)
        domain.state.return_value = (1, 0)  # VIR_DOMAIN_RUNNING = 1
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            # get_vm_config is called at the end too; avoid real libvirt there
            driver.get_vm_config = MagicMock(return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []})
            result = driver.apply_vm_config("abc-123", {"remove_nics": [{"target": target}]})

        return domain, result

    def test_detach_called(self):
        domain, _ = self._run_remove()
        domain.detachDeviceFlags.assert_called_once()

    def test_detach_uses_live_flag_only_when_running(self):
        """Running domain: detach must use VIR_DOMAIN_AFFECT_LIVE alone, not combined."""
        import libvirt as _lv
        domain, _ = self._run_remove()  # domain.state returns VIR_DOMAIN_RUNNING
        _, flags = domain.detachDeviceFlags.call_args[0]
        assert flags == _lv.VIR_DOMAIN_AFFECT_LIVE

    def test_detach_uses_config_flag_only_when_stopped(self):
        """Stopped domain: detach must use VIR_DOMAIN_AFFECT_CONFIG alone."""
        import libvirt as _lv
        domain = _mock_domain_for_config(_SAMPLE_DOMAIN_XML)
        domain.state.return_value = (5, 0)  # VIR_DOMAIN_SHUTOFF = 5
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []})

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            driver.apply_vm_config("abc-123", {"remove_nics": [{"target": "vnet0"}]})

        _, flags = domain.detachDeviceFlags.call_args[0]
        assert flags == _lv.VIR_DOMAIN_AFFECT_CONFIG

    def test_no_shutdown_for_nic_only_removal(self):
        domain, _ = self._run_remove()
        domain.shutdown.assert_not_called()

    def test_detach_xml_has_no_address_or_alias(self):
        """Detach XML must omit <address> and <alias> to avoid PCI mismatch."""
        domain, _ = self._run_remove(domain_xml=_SAMPLE_DOMAIN_XML_WITH_PCI)
        captured_xml = domain.detachDeviceFlags.call_args[0][0]
        assert "<address" not in captured_xml
        assert "<alias" not in captured_xml

    def test_detach_xml_contains_mac_and_source(self):
        """Detach XML must include the NIC's MAC and bridge source."""
        domain, _ = self._run_remove()
        captured_xml = domain.detachDeviceFlags.call_args[0][0]
        assert "52:54:00:11:22:33" in captured_xml
        assert "nos-br" in captured_xml
        assert "virtio" in captured_xml

    def test_result_reports_nic_failure_on_detach_error(self):
        """apply_vm_config result must include nic_failures when detach fails."""
        import libvirt as _lv
        domain = _mock_domain_for_config()
        domain.state.return_value = (1, 0)
        domain.detachDeviceFlags.side_effect = _lv.libvirtError("no device found at address")

        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []})

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = driver.apply_vm_config("abc-123", {"remove_nics": [{"target": "vnet0"}]})

        assert len(result["nic_failures"]) == 1
        assert result["nic_failures"][0]["target"] == "vnet0"
        assert "no device found at address" in result["nic_failures"][0]["reason"]

    def test_result_nic_failures_empty_on_success(self):
        """apply_vm_config result has empty nic_failures when detach succeeds."""
        _, result = self._run_remove()
        assert result["nic_failures"] == []


class TestApplyVmConfigNicAddition:
    """NIC addition - live attach with OVS VLAN tag applied natively, no reboot."""

    def _build_domain(self):
        domain = MagicMock()
        domain.state.return_value = (1, 0)  # running
        domain.XMLDesc.return_value = _SAMPLE_DOMAIN_XML
        return domain

    def _run_add(self, vlan_id=111):
        domain = self._build_domain()
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(
            return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []}
        )

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            driver.apply_vm_config("abc-123", {"add_nics": [{"vlan_id": vlan_id}]})

        return domain

    def test_attach_called(self):
        domain = self._run_add(vlan_id=101)
        domain.attachDeviceFlags.assert_called_once()

    def test_attach_xml_contains_ovs_virtualport(self):
        domain = self._run_add(vlan_id=101)
        xml_arg = domain.attachDeviceFlags.call_args[0][0]
        assert "<virtualport type='openvswitch'/>" in xml_arg

    def test_attach_xml_contains_vlan_tag(self):
        domain = self._run_add(vlan_id=101)
        xml_arg = domain.attachDeviceFlags.call_args[0][0]
        assert "<vlan>" in xml_arg
        assert "<tag id='101'/>" in xml_arg

    def test_attach_xml_vlan_id_value_correct(self):
        domain = self._run_add(vlan_id=222)
        xml_arg = domain.attachDeviceFlags.call_args[0][0]
        assert "<tag id='222'/>" in xml_arg

    def test_attach_xml_uses_configured_bridge(self):
        domain = self._run_add(vlan_id=111)
        xml_arg = domain.attachDeviceFlags.call_args[0][0]
        assert "<source bridge='nos-br'/>" in xml_arg

    def test_attach_xml_no_vlan_tag_when_vlan_id_none(self):
        domain = self._run_add(vlan_id=None)
        xml_arg = domain.attachDeviceFlags.call_args[0][0]
        assert "<virtualport" not in xml_arg
        assert "<vlan>" not in xml_arg

    def test_attach_uses_live_and_config_flags_when_running(self):
        """Running domain: attach must combine LIVE and CONFIG flags."""
        import libvirt as _lv
        domain = self._run_add(vlan_id=111)
        _, flags = domain.attachDeviceFlags.call_args[0]
        assert flags == (_lv.VIR_DOMAIN_AFFECT_CONFIG | _lv.VIR_DOMAIN_AFFECT_LIVE)

    def test_no_shutdown_for_nic_only_addition(self):
        domain = self._run_add(vlan_id=101)
        domain.shutdown.assert_not_called()


class TestApplyVmConfigVcpuMemory:
    """vcpu/memory changes trigger shutdown + redefine + start."""

    def _run_vcpu_memory(self, new_vcpu=4, new_mem=4096):
        domain = _mock_domain_for_config()
        domain.state.side_effect = [
            (1, 0),   # initial state check → running
            (5, 0),   # shutdown poll → shutoff
        ]
        domain.XMLDesc.return_value = _SAMPLE_DOMAIN_XML

        new_domain = MagicMock()
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        conn.defineXML.return_value = new_domain

        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(return_value={"vcpu": new_vcpu, "memory_mb": new_mem, "disks": [], "nics": []})

        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)), \
             patch("time.sleep"):
            driver.apply_vm_config("abc-123", {"vcpu": new_vcpu, "memory_mb": new_mem})

        return conn, domain, new_domain

    def test_shutdown_called(self):
        _, domain, _ = self._run_vcpu_memory()
        domain.shutdown.assert_called_once()

    def test_define_xml_called(self):
        conn, _, _ = self._run_vcpu_memory()
        conn.defineXML.assert_called_once()

    def test_new_xml_has_updated_vcpu(self):
        conn, _, _ = self._run_vcpu_memory(new_vcpu=4)
        xml_arg = conn.defineXML.call_args[0][0]
        assert "<vcpu" in xml_arg and ">4<" in xml_arg

    def test_new_xml_has_updated_memory_kib(self):
        conn, _, _ = self._run_vcpu_memory(new_mem=4096)
        xml_arg = conn.defineXML.call_args[0][0]
        # 4096 MiB × 1024 = 4194304 KiB
        assert "4194304" in xml_arg

    def test_domain_started_after_redefine(self):
        _, _, new_domain = self._run_vcpu_memory()
        new_domain.create.assert_called_once()


class TestApplyVmConfigDiskAdd:
    """Disk addition creates file, adds to XML, triggers reboot."""

    def test_qemu_img_create_called(self):
        domain = _mock_domain_for_config()
        domain.state.side_effect = [(1, 0), (5, 0)]
        domain.XMLDesc.return_value = _SAMPLE_DOMAIN_XML

        new_domain = MagicMock()
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        conn.defineXML.return_value = new_domain

        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []})

        qemu_mock = MagicMock()
        qemu_mock.returncode = 0

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=qemu_mock) as mock_run, \
             patch("time.sleep"):
            driver.apply_vm_config("abc-123", {"add_disks": [{"size_gb": 20}]})

        # subprocess.run should have been called at least for qemu-img create
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("qemu-img" in c and "create" in c for c in calls)

    def test_new_xml_contains_new_disk_target(self):
        domain = _mock_domain_for_config()
        domain.state.side_effect = [(1, 0), (5, 0)]
        domain.XMLDesc.return_value = _SAMPLE_DOMAIN_XML

        new_domain = MagicMock()
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        conn.defineXML.return_value = new_domain

        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []})

        qemu_mock = MagicMock()
        qemu_mock.returncode = 0

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=qemu_mock), \
             patch("time.sleep"):
            driver.apply_vm_config("abc-123", {"add_disks": [{"size_gb": 20}]})

        xml_arg = conn.defineXML.call_args[0][0]
        # vda already exists in sample XML, next should be vdb
        assert "vdb" in xml_arg
