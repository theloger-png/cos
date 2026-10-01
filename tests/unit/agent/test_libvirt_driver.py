"""Unit tests for agent/libvirt_driver.py with mocked libvirt connection."""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch, call

import pytest
from agent.libvirt_driver import (
    ConsoleSession,
    ConsoleUnavailableError,
    DomainNotFoundError,
    DomainNotRunningError,
    LibvirtDriver,
    _make_cloud_init_user_data,
    _make_cloud_init_meta_data,
    _make_cloud_init_network_config,
    _make_cloud_init_network_config_multi,
    _generate_mac,
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

    def test_xml_has_guest_agent_channel(self, driver):
        domain = _mock_domain()
        conn = _mock_conn()
        captured_xml: list[str] = []
        conn.defineXML.side_effect = lambda xml: (captured_xml.append(xml), domain)[1]

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            driver.create_vm("vm-01", 2, 2048, 20, "")

        import xml.etree.ElementTree as _ET
        root = _ET.fromstring(captured_xml[0])
        channels = root.findall("./devices/channel[@type='unix']")
        assert len(channels) == 1
        target = channels[0].find("target")
        assert target.get("type") == "virtio"
        assert target.get("name") == "org.qemu.guest_agent.0"
        assert channels[0].find("source") is None

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

    def test_xml_has_mac_address(self, driver):
        domain = _mock_domain()
        conn = _mock_conn()
        captured_xml: list[str] = []
        conn.defineXML.side_effect = lambda xml: (captured_xml.append(xml), domain)[1]

        with patch("libvirt.open", return_value=conn), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("os.system"):
            driver.create_vm("vm-01", 2, 2048, 20, "")

        assert "<mac address='52:54:00:" in captured_xml[0]


class TestGenerateMac:
    def test_uses_libvirt_oui_prefix(self):
        assert _generate_mac().startswith("52:54:00:")

    def test_format_is_six_colon_separated_octets(self):
        parts = _generate_mac().split(":")
        assert len(parts) == 6
        assert all(len(p) == 2 for p in parts)

    def test_different_calls_produce_different_macs(self):
        macs = {_generate_mac() for _ in range(20)}
        assert len(macs) > 1


class TestMakeCloudInitNetworkConfig:
    def test_contains_version_2(self):
        cfg = _make_cloud_init_network_config("52:54:00:ab:cd:ef", "192.168.1.50/24", "192.168.1.1")
        assert "version: 2" in cfg

    def test_matches_by_mac_address(self):
        cfg = _make_cloud_init_network_config("52:54:00:ab:cd:ef", "192.168.1.50/24", "192.168.1.1")
        assert "macaddress: '52:54:00:ab:cd:ef'" in cfg

    def test_contains_ip_cidr(self):
        cfg = _make_cloud_init_network_config("52:54:00:ab:cd:ef", "192.168.1.50/24", "192.168.1.1")
        assert "- 192.168.1.50/24" in cfg

    def test_contains_default_route_via_gateway(self):
        cfg = _make_cloud_init_network_config("52:54:00:ab:cd:ef", "192.168.1.50/24", "192.168.1.1")
        assert "to: default" in cfg
        assert "via: 192.168.1.1" in cfg


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

    def test_installs_guest_agent_package(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$s$h")
        assert "packages:\n  - qemu-guest-agent\n" in ud

    def test_enables_guest_agent_service(self):
        ud = _make_cloud_init_user_data("ubuntu", "$6$s$h")
        assert "runcmd:\n  - systemctl enable --now qemu-guest-agent\n" in ud

    def test_user_data_is_valid_yaml(self):
        import yaml
        doc = yaml.safe_load(_make_cloud_init_user_data("ubuntu", "$6$s$h"))
        assert doc["packages"] == ["qemu-guest-agent"]
        assert doc["runcmd"] == ["systemctl enable --now qemu-guest-agent"]

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

    def test_network_config_passed_when_ip_cidr_and_gateway_given(self, driver):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.makedirs"), \
             patch("builtins.open", MagicMock()):
            self._captured_xml(
                driver,
                cloud_init_user="ubuntu",
                cloud_init_password_hash="$6$salt$hash",
                ip_cidr="192.168.1.50/24",
                gateway="192.168.1.1",
            )

        cmd = mock_run.call_args[0][0]
        assert "--network-config" in cmd

    def test_no_network_config_when_ip_cidr_or_gateway_missing(self, driver):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.makedirs"), \
             patch("builtins.open", MagicMock()):
            self._captured_xml(
                driver,
                cloud_init_user="ubuntu",
                cloud_init_password_hash="$6$salt$hash",
                ip_cidr="192.168.1.50/24",
            )

        cmd = mock_run.call_args[0][0]
        assert "--network-config" not in cmd


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


# ---------------------------------------------------------------------------
# get_vm_config: live IP addresses
# ---------------------------------------------------------------------------

import libvirt as _libvirt  # noqa: E402

_SRC_AGENT = _libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT
_IPV4 = _libvirt.VIR_IP_ADDR_TYPE_IPV4
_IPV6 = _libvirt.VIR_IP_ADDR_TYPE_IPV6

_TWO_NIC_DOMAIN_XML = _SAMPLE_DOMAIN_XML.replace(
    "  </devices>",
    "    <interface type='bridge'>\n"
    "      <mac address='52:54:00:aa:bb:cc'/>\n"
    "      <source bridge='nos-br'/>\n"
    "      <target dev='vnet1'/>\n"
    "      <model type='virtio'/>\n"
    "    </interface>\n"
    "  </devices>",
)


def _iface(mac: str, *addrs: tuple[int, str]) -> dict:
    return {
        "hwaddr": mac,
        "addrs": [{"type": t, "addr": a, "prefix": 24} for t, a in addrs],
    }


class TestGetVmConfigIpAddresses:
    def _run(self, sources: dict, xml: str = _SAMPLE_DOMAIN_XML):
        """*sources* maps a libvirt source constant to a dict result or an exception."""
        domain = _mock_domain_for_config(xml)

        def interface_addresses(source, flags=0):
            result = sources.get(source, {})
            if isinstance(result, Exception):
                raise result
            return result

        domain.interfaceAddresses.side_effect = interface_addresses
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        with patch("libvirt.open", return_value=conn), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = driver.get_vm_config("abc-123")
        return domain, result

    def test_guest_agent_ips_used(self):
        domain, result = self._run({
            _SRC_AGENT: {"ens3": _iface("52:54:00:11:22:33", (_IPV4, "10.0.20.5"))},
        })
        assert result["nics"][0]["ip_addresses"] == ["10.0.20.5"]
        sources = [c[0][0] for c in domain.interfaceAddresses.call_args_list]
        assert sources == [_SRC_AGENT]

    def test_agent_exception_yields_empty_list_without_arp_fallback(self):
        domain, result = self._run({
            _SRC_AGENT: _libvirt.libvirtError("QEMU guest agent is not configured"),
        })
        assert result["nics"][0]["ip_addresses"] == []
        assert result["vcpu"] == 2
        sources = [c[0][0] for c in domain.interfaceAddresses.call_args_list]
        assert sources == [_SRC_AGENT]

    def test_agent_without_address_for_mac_yields_empty_list(self):
        _, result = self._run({
            _SRC_AGENT: {"ens9": _iface("52:54:00:99:99:99", (_IPV4, "10.9.9.9"))},
        })
        assert result["nics"][0]["ip_addresses"] == []

    def test_stopped_domain_returns_empty_ip_list(self):
        not_running = _libvirt.libvirtError("Requested operation is not valid: domain is not running")
        _, result = self._run({_SRC_AGENT: not_running})
        assert result["nics"][0]["ip_addresses"] == []
        assert result["vcpu"] == 2

    def test_only_global_ipv4_addresses_are_returned(self):
        _, result = self._run({
            _SRC_AGENT: {
                "lo": _iface("52:54:00:11:22:33", (_IPV4, "127.0.0.1")),
                "ens3": _iface(
                    "52:54:00:11:22:33",
                    (_IPV4, "10.0.20.5"),
                    (_IPV4, "169.254.10.3"),
                    (_IPV6, "fe80::5054:ff:fe11:2233"),
                    (_IPV4, "10.0.20.5"),
                ),
            },
        })
        assert result["nics"][0]["ip_addresses"] == ["10.0.20.5"]

    def test_interfaces_without_hwaddr_are_ignored(self):
        _, result = self._run({
            _SRC_AGENT: {"lo": {"hwaddr": None, "addrs": [{"type": _IPV4, "addr": "10.1.1.1", "prefix": 8}]}},
        })
        assert result["nics"][0]["ip_addresses"] == []

    def test_multi_nic_matched_by_mac(self):
        """NIC 1 resolved by the agent, NIC 2 unknown to it gets an empty list."""
        _, result = self._run(
            {_SRC_AGENT: {"ens3": _iface("52:54:00:11:22:33", (_IPV4, "10.0.20.5"))}},
            xml=_TWO_NIC_DOMAIN_XML,
        )
        by_target = {n["target"]: n["ip_addresses"] for n in result["nics"]}
        assert by_target == {"vnet0": ["10.0.20.5"], "vnet1": []}


# ---------------------------------------------------------------------------
# Multi-NIC cloud-init network-config
# ---------------------------------------------------------------------------


class TestMakeCloudInitNetworkConfigMulti:
    def test_single_static_entry_matches_single_nic_helper(self):
        nic = {"mac": "52:54:00:ab:cd:ef", "ip_cidr": "192.168.1.50/24", "gateway": "192.168.1.1"}
        assert _make_cloud_init_network_config_multi([nic]) == _make_cloud_init_network_config(
            "52:54:00:ab:cd:ef", "192.168.1.50/24", "192.168.1.1"
        )

    def test_entry_without_static_ip_uses_dhcp(self):
        cfg = _make_cloud_init_network_config_multi([{"mac": "52:54:00:11:22:33"}])
        assert "macaddress: '52:54:00:11:22:33'" in cfg
        assert "dhcp4: true" in cfg
        assert "addresses:" not in cfg

    def test_entry_with_only_one_of_ip_or_gateway_uses_dhcp(self):
        cfg = _make_cloud_init_network_config_multi(
            [{"mac": "52:54:00:11:22:33", "ip_cidr": "10.0.0.5/24", "gateway": None}]
        )
        assert "dhcp4: true" in cfg
        assert "10.0.0.5/24" not in cfg

    def test_multiple_nics_get_unique_ids_and_macs(self):
        cfg = _make_cloud_init_network_config_multi([
            {"mac": "52:54:00:11:22:33"},
            {"mac": "52:54:00:aa:bb:cc", "ip_cidr": "10.0.30.6/24", "gateway": "10.0.30.1"},
        ])
        assert "    eth0:" in cfg and "    eth1:" in cfg
        assert "macaddress: '52:54:00:11:22:33'" in cfg
        assert "macaddress: '52:54:00:aa:bb:cc'" in cfg
        assert "- 10.0.30.6/24" in cfg

    def test_secondary_static_default_route_gets_higher_metric(self):
        cfg = _make_cloud_init_network_config_multi([
            {"mac": "52:54:00:11:22:33", "ip_cidr": "10.0.20.5/24", "gateway": "10.0.20.1"},
            {"mac": "52:54:00:aa:bb:cc", "ip_cidr": "10.0.30.6/24", "gateway": "10.0.30.1"},
        ])
        assert cfg.count("metric:") == 1
        assert "metric: 2048" in cfg

    def test_static_nic_gets_nameservers_block(self):
        cfg = _make_cloud_init_network_config_multi(
            [{"mac": "52:54:00:11:22:33", "ip_cidr": "10.0.20.5/24", "gateway": "10.0.20.1"}]
        )
        assert (
            "      nameservers:\n"
            "        addresses:\n"
            "          - 1.1.1.1\n"
            "          - 8.8.8.8\n"
        ) in cfg

    def test_dhcp_nic_has_no_nameservers(self):
        cfg = _make_cloud_init_network_config_multi([{"mac": "52:54:00:11:22:33"}])
        assert "nameservers" not in cfg

    def test_nameservers_only_on_static_nics_in_mixed_config(self):
        cfg = _make_cloud_init_network_config_multi([
            {"mac": "52:54:00:11:22:33"},
            {"mac": "52:54:00:aa:bb:cc", "ip_cidr": "10.0.30.6/24", "gateway": "10.0.30.1"},
        ])
        assert cfg.count("nameservers:") == 1
        assert cfg.index("nameservers:") > cfg.index("52:54:00:aa:bb:cc")

    def test_generated_yaml_is_valid_and_nested_correctly(self):
        yaml = pytest.importorskip("yaml")
        cfg = yaml.safe_load(_make_cloud_init_network_config_multi([
            {"mac": "52:54:00:11:22:33", "ip_cidr": "10.0.20.5/24", "gateway": "10.0.20.1"},
            {"mac": "52:54:00:aa:bb:cc", "ip_cidr": "10.0.30.6/24", "gateway": "10.0.30.1"},
        ]))
        for eth in cfg["network"]["ethernets"].values():
            assert eth["nameservers"]["addresses"] == ["1.1.1.1", "8.8.8.8"]


# ---------------------------------------------------------------------------
# Seed sidecar written at VM creation
# ---------------------------------------------------------------------------


def _fake_cloud_localds(captured: list[dict] | None = None, returncode: int = 0):
    """subprocess.run stand-in that creates the ISO and records the inputs it was given."""

    def run(cmd, **kwargs):
        if captured is not None:
            record = {"cmd": cmd, "user_data": open(cmd[2]).read()}
            if "--network-config" in cmd:
                record["network_config"] = open(cmd[cmd.index("--network-config") + 1]).read()
            captured.append(record)
        if returncode == 0:
            with open(cmd[1], "w") as f:
                f.write("iso")
        return MagicMock(returncode=returncode, stderr="boom" if returncode else "")

    return run


class TestSeedStateSidecar:
    def test_sidecar_records_user_data_and_static_nic(self, driver, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.libvirt_driver._SEED_BASE_DIR", str(tmp_path))
        nic = {"mac": "52:54:00:11:22:33", "ip_cidr": "10.0.20.5/24", "gateway": "10.0.20.1"}
        with patch("subprocess.run", side_effect=_fake_cloud_localds()):
            path = driver._build_cloud_init_seed("vm", "u-1", "ubuntu", "$6$s$h", nic_configs=[nic])

        assert path == str(tmp_path / "u-1.iso")
        state = json.loads((tmp_path / "u-1.seed.json").read_text())
        assert state["nics"] == [nic]
        assert "$6$s$h" in state["user_data"]

    def test_sidecar_has_no_nics_for_dhcp_vm_and_is_private(self, driver, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.libvirt_driver._SEED_BASE_DIR", str(tmp_path))
        with patch("subprocess.run", side_effect=_fake_cloud_localds()):
            driver._build_cloud_init_seed("vm", "u-2", "ubuntu", "$6$s$h")

        sidecar = tmp_path / "u-2.seed.json"
        assert json.loads(sidecar.read_text())["nics"] == []
        assert (sidecar.stat().st_mode & 0o777) == 0o600

    def test_no_sidecar_when_cloud_localds_fails(self, driver, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.libvirt_driver._SEED_BASE_DIR", str(tmp_path))
        with patch("subprocess.run", side_effect=_fake_cloud_localds(returncode=1)):
            assert driver._build_cloud_init_seed("vm", "u-3", "ubuntu", "$6$s$h") is None
        assert not (tmp_path / "u-3.seed.json").exists()


# ---------------------------------------------------------------------------
# apply_vm_config: static IP on NIC addition
# ---------------------------------------------------------------------------

_VM_UUID = "abc-123"
_RUNNING = 1
_SHUTOFF = 5
_STATIC_NIC = {"vlan_id": 101, "ip_cidr": "10.0.30.6/24", "gateway": "10.0.30.1"}


def _seed_xml(seed_path: str | None) -> str:
    """Sample domain XML whose cdrom points at *seed_path* (or with no cdrom at all)."""
    if seed_path is None:
        start = _SAMPLE_DOMAIN_XML.index("    <disk type='file' device='cdrom'>")
        end = _SAMPLE_DOMAIN_XML.index("</disk>", start) + len("</disk>\n")
        return _SAMPLE_DOMAIN_XML[:start] + _SAMPLE_DOMAIN_XML[end:]
    return _SAMPLE_DOMAIN_XML.replace("/var/lib/cos/seeds/abc-123.iso", seed_path)


class TestApplyVmConfigStaticIp:
    def _run(self, tmp_path, monkeypatch, *, state, add_nics=None, xml=None,
             sidecar: dict | None = None, existing_iso: bool = False, rc: int = 0):
        seeds = tmp_path / "seeds"
        seeds.mkdir()
        monkeypatch.setattr("agent.libvirt_driver._SEED_BASE_DIR", str(seeds))
        seed_path = str(seeds / f"{_VM_UUID}.iso")
        if sidecar is not None:
            (seeds / f"{_VM_UUID}.seed.json").write_text(json.dumps(sidecar))
        if existing_iso:
            (seeds / f"{_VM_UUID}.iso").write_text("old-iso")

        domain = MagicMock()
        domain.state.return_value = (state, 0)
        domain.name.return_value = "test-vm"
        domain.XMLDesc.return_value = xml if xml is not None else _seed_xml(seed_path)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(
            return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []}
        )

        calls: list[dict] = []
        with patch("libvirt.open", return_value=conn), \
             patch("subprocess.run", side_effect=_fake_cloud_localds(calls, returncode=rc)):
            result = driver.apply_vm_config(
                _VM_UUID, {"add_nics": add_nics if add_nics is not None else [_STATIC_NIC]}
            )
        return domain, result, calls, seeds

    def test_running_vm_skips_seed_rebuild_and_reports_failure(self, tmp_path, monkeypatch):
        domain, result, calls, seeds = self._run(tmp_path, monkeypatch, state=_RUNNING)

        assert calls == []
        domain.attachDeviceFlags.assert_called_once()  # NIC still attached, no seed attach
        assert not (seeds / f"{_VM_UUID}.iso").exists()
        assert len(result["nic_failures"]) == 1
        failure = result["nic_failures"][0]
        assert failure["target"] == "new-nic (vlan 101)"
        assert "static IP requires the VM to be stopped" in failure["reason"]
        assert "NIC attached without static IP configuration" in failure["reason"]

    def test_running_vm_without_static_ip_has_no_failure(self, tmp_path, monkeypatch):
        _, result, calls, _ = self._run(
            tmp_path, monkeypatch, state=_RUNNING, add_nics=[{"vlan_id": 101}]
        )
        assert calls == []
        assert result["nic_failures"] == []

    def test_stopped_vm_with_only_ip_or_only_gateway_is_left_alone(self, tmp_path, monkeypatch):
        _, result, calls, _ = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF,
            add_nics=[{"vlan_id": 101, "ip_cidr": "10.0.30.6/24"}],
        )
        assert calls == []
        assert result["nic_failures"] == []

    def test_new_nic_attach_xml_carries_the_mac_used_in_network_config(self, tmp_path, monkeypatch):
        domain, _, calls, _ = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF, sidecar={"user_data": "#cloud-config\n", "nics": []},
            existing_iso=True,
        )
        attach_xml = domain.attachDeviceFlags.call_args_list[0][0][0]
        new_mac = ET.fromstring(attach_xml).find("mac").get("address")
        assert f"macaddress: '{new_mac}'" in calls[0]["network_config"]

    def test_stopped_vm_rebuilds_seed_merging_existing_configuration(self, tmp_path, monkeypatch):
        import libvirt as _lv
        recorded = {
            "user_data": "#cloud-config\nchpasswd: kept\n",
            "nics": [{"mac": "52:54:00:11:22:33", "ip_cidr": "10.0.20.5/24", "gateway": "10.0.20.1"}],
        }
        domain, result, calls, seeds = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF, sidecar=recorded, existing_iso=True
        )

        assert result["nic_failures"] == []
        assert len(calls) == 1
        # Original user-data preserved; not regenerated.
        assert calls[0]["user_data"] == recorded["user_data"]
        cfg = calls[0]["network_config"]
        # Existing primary NIC keeps its static IP, new NIC gets its own.
        assert "macaddress: '52:54:00:11:22:33'" in cfg
        assert "- 10.0.20.5/24" in cfg
        assert "- 10.0.30.6/24" in cfg and "via: 10.0.30.1" in cfg
        # Rebuilt ISO replaced the original in place, temp file gone.
        assert (seeds / f"{_VM_UUID}.iso").read_text() == "iso"
        assert not (seeds / f"{_VM_UUID}.iso.tmp").exists()
        # Sidecar now records both static NICs.
        state = json.loads((seeds / f"{_VM_UUID}.seed.json").read_text())
        assert {n["ip_cidr"] for n in state["nics"]} == {"10.0.20.5/24", "10.0.30.6/24"}
        # NIC attached persistently only (domain is off); seed already attached.
        domain.attachDeviceFlags.assert_called_once()
        assert domain.attachDeviceFlags.call_args[0][1] == _lv.VIR_DOMAIN_AFFECT_CONFIG

    def test_existing_dhcp_nic_stays_dhcp_in_rebuilt_network_config(self, tmp_path, monkeypatch):
        """A network-config replaces cloud-init's DHCP fallback, so unlisted NICs would go dark."""
        _, _, calls, _ = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF,
            sidecar={"user_data": "#cloud-config\n", "nics": []}, existing_iso=True,
        )
        cfg = calls[0]["network_config"]
        primary_block = cfg.split("eth1:")[0]
        assert "macaddress: '52:54:00:11:22:33'" in primary_block
        assert "dhcp4: true" in primary_block

    def test_creates_and_attaches_new_seed_when_vm_has_none(self, tmp_path, monkeypatch):
        import libvirt as _lv
        domain, result, calls, seeds = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF, xml=_seed_xml(None)
        )

        assert result["nic_failures"] == []
        assert calls[0]["user_data"] == "#cloud-config\n"
        assert (seeds / f"{_VM_UUID}.iso").exists()
        # NIC attach + seed cdrom attach, both config-only.
        assert domain.attachDeviceFlags.call_count == 2
        seed_xml, flags = domain.attachDeviceFlags.call_args_list[1][0]
        assert "device='cdrom'" in seed_xml and str(seeds / f"{_VM_UUID}.iso") in seed_xml
        assert flags == _lv.VIR_DOMAIN_AFFECT_CONFIG

    def test_legacy_seed_without_recorded_state_is_left_untouched(self, tmp_path, monkeypatch):
        domain, result, calls, seeds = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF, existing_iso=True
        )

        assert calls == []
        assert (seeds / f"{_VM_UUID}.iso").read_text() == "old-iso"
        assert len(result["nic_failures"]) == 1
        assert "NIC attached but static IP not configured" in result["nic_failures"][0]["reason"]
        assert "no recorded state" in result["nic_failures"][0]["reason"]

    def test_cloud_localds_failure_keeps_old_seed_and_reports_failure(self, tmp_path, monkeypatch):
        _, result, _, seeds = self._run(
            tmp_path, monkeypatch, state=_SHUTOFF, rc=1,
            sidecar={"user_data": "#cloud-config\n", "nics": []}, existing_iso=True,
        )
        assert (seeds / f"{_VM_UUID}.iso").read_text() == "old-iso"
        assert not (seeds / f"{_VM_UUID}.iso.tmp").exists()
        assert len(result["nic_failures"]) == 1
        assert "cloud-localds failed" in result["nic_failures"][0]["reason"]

    def test_failed_nic_attach_skips_static_ip_handling(self, tmp_path, monkeypatch):
        import libvirt as _lv
        seeds = tmp_path / "seeds"
        seeds.mkdir()
        monkeypatch.setattr("agent.libvirt_driver._SEED_BASE_DIR", str(seeds))
        domain = MagicMock()
        domain.state.return_value = (_SHUTOFF, 0)
        domain.attachDeviceFlags.side_effect = _lv.libvirtError("attach boom")
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        driver.get_vm_config = MagicMock(return_value={"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []})

        with patch("libvirt.open", return_value=conn), patch("subprocess.run") as run:
            result = driver.apply_vm_config(_VM_UUID, {"add_nics": [_STATIC_NIC]})

        run.assert_not_called()
        assert len(result["nic_failures"]) == 1
        assert "attach boom" in result["nic_failures"][0]["reason"]


# ---------------------------------------------------------------------------
# ConsoleSession
# ---------------------------------------------------------------------------


class TestConsoleSessionRecv:
    """The background thread's read side: stream -> inbound_queue."""

    @pytest.mark.asyncio
    async def test_forwards_data_then_none_sentinel_on_eof(self):
        stream = MagicMock()
        stream.recv.side_effect = [b"hello", b""]
        conn = MagicMock()
        session = ConsoleSession(conn, stream, asyncio.get_running_loop())

        await asyncio.to_thread(session._run)

        assert await session.inbound_queue.get() == b"hello"
        assert await session.inbound_queue.get() is None

    @pytest.mark.asyncio
    async def test_retries_on_would_block_then_returns_data(self):
        stream = MagicMock()
        stream.recv.side_effect = [-2, b"data", b""]
        conn = MagicMock()
        session = ConsoleSession(conn, stream, asyncio.get_running_loop())

        with patch("agent.libvirt_driver.time.sleep") as mock_sleep:
            await asyncio.to_thread(session._run)

        mock_sleep.assert_called_once()
        assert await session.inbound_queue.get() == b"data"
        assert await session.inbound_queue.get() is None

    @pytest.mark.asyncio
    async def test_stops_on_recv_error_without_raising(self):
        import libvirt as _lv

        stream = MagicMock()
        stream.recv.side_effect = _lv.libvirtError("stream broken")
        conn = MagicMock()
        session = ConsoleSession(conn, stream, asyncio.get_running_loop())

        await asyncio.to_thread(session._run)  # must not raise

        assert await session.inbound_queue.get() is None

    @pytest.mark.asyncio
    async def test_stops_on_negative_error_code_other_than_would_block(self):
        stream = MagicMock()
        stream.recv.side_effect = [-1]
        conn = MagicMock()
        session = ConsoleSession(conn, stream, asyncio.get_running_loop())

        await asyncio.to_thread(session._run)

        assert await session.inbound_queue.get() is None

    @pytest.mark.asyncio
    async def test_cleans_up_stream_and_connection_on_exit(self):
        stream = MagicMock()
        stream.recv.side_effect = [b""]
        conn = MagicMock()
        session = ConsoleSession(conn, stream, asyncio.get_running_loop())

        await asyncio.to_thread(session._run)

        stream.finish.assert_called_once()
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_runs_even_if_finish_raises(self):
        """A broken stream.finish() must not prevent conn.close() from running."""
        stream = MagicMock()
        stream.recv.side_effect = [b""]
        stream.finish.side_effect = RuntimeError("boom")
        conn = MagicMock()
        session = ConsoleSession(conn, stream, asyncio.get_running_loop())

        await asyncio.to_thread(session._run)  # must not raise

        conn.close.assert_called_once()


class TestConsoleSessionSend:
    """The write side: write() -> outbound queue -> stream.send()."""

    def _session(self, stream=None) -> ConsoleSession:
        return ConsoleSession(MagicMock(), stream or MagicMock(), MagicMock())

    def test_write_queues_bytes(self):
        session = self._session()
        session.write(b"abc")
        assert session._outbound_queue.get_nowait() == b"abc"

    def test_flush_outbound_sends_queued_data(self):
        stream = MagicMock()
        stream.send.return_value = 3
        session = self._session(stream)
        session.write(b"abc")

        session._flush_outbound()

        stream.send.assert_called_once_with(b"abc")

    def test_flush_outbound_noop_when_empty(self):
        stream = MagicMock()
        session = self._session(stream)

        session._flush_outbound()

        stream.send.assert_not_called()

    def test_send_all_retries_on_would_block(self):
        stream = MagicMock()
        stream.send.side_effect = [-2, 5]
        session = self._session(stream)

        with patch("agent.libvirt_driver.time.sleep") as mock_sleep:
            session._send_all(b"abcde")

        mock_sleep.assert_called_once()
        assert stream.send.call_count == 2

    def test_send_all_continues_on_partial_send(self):
        stream = MagicMock()
        stream.send.side_effect = [3, 3]
        session = self._session(stream)

        session._send_all(b"abcdef")

        assert stream.send.call_count == 2
        assert stream.send.call_args_list[0] == call(b"abcdef")
        assert stream.send.call_args_list[1] == call(b"def")

    def test_send_all_stops_without_raising_on_error(self):
        import libvirt as _lv

        stream = MagicMock()
        stream.send.side_effect = _lv.libvirtError("boom")
        session = self._session(stream)

        session._send_all(b"abc")  # must not raise

        stream.send.assert_called_once()


class TestConsoleSessionClose:
    def test_close_without_start_is_safe(self):
        session = ConsoleSession(MagicMock(), MagicMock(), MagicMock())
        session.close()  # must not raise (thread was never started)
        assert session._stop_event.is_set()

    def test_close_is_idempotent(self):
        session = ConsoleSession(MagicMock(), MagicMock(), MagicMock())
        session.close()
        session.close()  # must not raise the second time either


# ---------------------------------------------------------------------------
# libvirt event loop registration (required for non-blocking stream I/O)
# ---------------------------------------------------------------------------


class TestEnsureLibvirtEventLoop:
    def _reset(self, monkeypatch):
        """Isolate from whatever real/prior state other tests left behind."""
        import agent.libvirt_driver as mod

        monkeypatch.setattr(mod, "_event_loop_started", False)
        return mod

    def test_first_call_registers_impl_and_starts_a_daemon_thread(self, monkeypatch):
        mod = self._reset(monkeypatch)

        with patch.object(mod.libvirt, "virEventRegisterDefaultImpl") as mock_register, \
             patch.object(mod.threading, "Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            mod._ensure_libvirt_event_loop()

        mock_register.assert_called_once_with()
        mock_thread_cls.assert_called_once_with(
            target=mod._pump_libvirt_events_forever,
            name="libvirt-event-loop",
            daemon=True,
        )
        mock_thread.start.assert_called_once_with()
        assert mod._event_loop_started is True

    def test_second_call_does_nothing(self, monkeypatch):
        mod = self._reset(monkeypatch)

        with patch.object(mod.libvirt, "virEventRegisterDefaultImpl") as mock_register, \
             patch.object(mod.threading, "Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            mod._ensure_libvirt_event_loop()
            mod._ensure_libvirt_event_loop()
            mod._ensure_libvirt_event_loop()

        mock_register.assert_called_once_with()
        mock_thread_cls.assert_called_once()

    def test_connect_registers_the_event_loop_before_opening_the_connection(self, driver, monkeypatch):
        mod = self._reset(monkeypatch)
        calls: list[str] = []

        def fake_ensure() -> None:
            calls.append("ensure")

        def fake_open(uri: str):
            calls.append("open")
            return MagicMock()

        with patch.object(mod, "_ensure_libvirt_event_loop", side_effect=fake_ensure), \
             patch("libvirt.open", side_effect=fake_open):
            driver._connect()

        assert calls == ["ensure", "open"]


class TestPumpLibvirtEventsOnce:
    def test_runs_one_event_loop_iteration(self):
        import agent.libvirt_driver as mod

        with patch.object(mod.libvirt, "virEventRunDefaultImpl") as mock_run:
            mod._pump_libvirt_events_once()

        mock_run.assert_called_once_with()

    def test_exception_is_logged_and_does_not_propagate(self):
        """A failing iteration must not kill the background pump thread."""
        import agent.libvirt_driver as mod

        with patch.object(
            mod.libvirt, "virEventRunDefaultImpl", side_effect=RuntimeError("boom")
        ), patch.object(mod.time, "sleep") as mock_sleep:
            mod._pump_libvirt_events_once()  # must not raise

        mock_sleep.assert_called_once()


# ---------------------------------------------------------------------------
# LibvirtDriver.open_console
# ---------------------------------------------------------------------------


class TestOpenConsole:
    @pytest.mark.asyncio
    async def test_raises_domain_not_found(self, driver):
        import libvirt as _lv

        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = _lv.libvirtError("no such domain")

        with patch("libvirt.open", return_value=conn):
            with pytest.raises(DomainNotFoundError):
                driver.open_console("missing-uuid", asyncio.get_running_loop())

        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_domain_not_running(self, driver):
        domain = MagicMock()
        domain.state.return_value = (5, 0)  # VIR_DOMAIN_SHUTOFF
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain

        with patch("libvirt.open", return_value=conn):
            with pytest.raises(DomainNotRunningError):
                driver.open_console("stopped-uuid", asyncio.get_running_loop())

        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_console_unavailable_when_openconsole_fails(self, driver):
        import libvirt as _lv

        domain = MagicMock()
        domain.state.return_value = (1, 0)  # VIR_DOMAIN_RUNNING
        domain.openConsole.side_effect = _lv.libvirtError("no console device")
        stream = MagicMock()
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        conn.newStream.return_value = stream

        with patch("libvirt.open", return_value=conn):
            with pytest.raises(ConsoleUnavailableError):
                driver.open_console("no-console-uuid", asyncio.get_running_loop())

        stream.abort.assert_called_once()
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_opens_console_with_force_flag_and_starts_session(self, driver):
        import libvirt as _lv

        domain = MagicMock()
        domain.state.return_value = (1, 0)  # VIR_DOMAIN_RUNNING
        stream = MagicMock()
        stream.recv.return_value = b""  # immediate EOF so the thread exits fast
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        conn.newStream.return_value = stream

        with patch("libvirt.open", return_value=conn):
            session = driver.open_console("running-uuid", asyncio.get_running_loop())

        try:
            conn.newStream.assert_called_once_with(_lv.VIR_STREAM_NONBLOCK)
            domain.openConsole.assert_called_once_with(None, stream, _lv.VIR_DOMAIN_CONSOLE_FORCE)
            assert isinstance(session, ConsoleSession)
            assert await session.inbound_queue.get() is None  # EOF sentinel
        finally:
            await asyncio.to_thread(session.close)


# ---------------------------------------------------------------------------
# Serial PTY console
# ---------------------------------------------------------------------------


class TestCreateVMSerialConsole:
    """Serial console (pty) is required for virsh console access."""

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
            driver.create_vm("vm-console", 2, 2048, 20, "", **kwargs)

        return captured[0]

    def test_xml_has_serial_pty_element(self, driver):
        xml = self._captured_xml(driver)
        assert "<serial type='pty'>" in xml
        assert "<target type='isa-serial' port='0'/>" in xml

    def test_xml_has_console_pty_element(self, driver):
        xml = self._captured_xml(driver)
        assert "<console type='pty'>" in xml
        assert "<target type='serial' port='0'/>" in xml

    def test_serial_and_console_nested_in_devices(self, driver):
        """Both serial and console must be inside the <devices> block."""
        xml = self._captured_xml(driver)
        devices_start = xml.index("<devices>")
        devices_end = xml.index("</devices>") + len("</devices>")
        devices_block = xml[devices_start:devices_end]
        assert "<serial type='pty'>" in devices_block
        assert "<console type='pty'>" in devices_block

    def test_serial_comes_before_console(self, driver):
        """Serial should be defined before console in the XML."""
        xml = self._captured_xml(driver)
        serial_pos = xml.index("<serial type='pty'>")
        console_pos = xml.index("<console type='pty'>")
        assert serial_pos < console_pos


class TestGetConsoleInfo:
    """Helper method to check if a domain has a pty serial console."""

    def _run(self, xml: str = _SAMPLE_DOMAIN_XML) -> dict:
        domain = _mock_domain_for_config(xml)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn):
            return driver.get_console_info("test-uuid")

    def test_returns_true_when_serial_pty_exists(self, driver):
        """Domains created via create_vm have a serial pty console."""
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
            driver.create_vm("vm-test", 2, 2048, 20, "")

        # Now test get_console_info on the captured XML
        domain.XMLDesc.return_value = captured[0]
        conn.lookupByUUIDString.return_value = domain
        conn2 = _mock_conn()
        conn2.lookupByUUIDString.return_value = domain

        with patch("libvirt.open", return_value=conn2):
            result = driver.get_console_info("test-uuid")

        assert result is True

    def test_returns_false_when_no_serial_element(self):
        """Domain without serial element returns False."""
        xml_no_serial = _SAMPLE_DOMAIN_XML  # sample lacks serial element
        domain = _mock_domain_for_config(xml_no_serial)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn):
            result = driver.get_console_info("test-uuid")

        assert result is False

    def test_returns_false_when_serial_type_not_pty(self):
        """Serial element with type != 'pty' returns False."""
        xml_file_serial = _SAMPLE_DOMAIN_XML.replace(
            "  </devices>",
            "    <serial type='file'>\n"
            "      <target type='isa-serial' port='0'/>\n"
            "    </serial>\n"
            "  </devices>",
        )
        domain = _mock_domain_for_config(xml_file_serial)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn):
            result = driver.get_console_info("test-uuid")

        assert result is False

    def test_returns_false_when_target_type_not_isa_serial(self):
        """Serial with wrong target type returns False."""
        xml_wrong_target = _SAMPLE_DOMAIN_XML.replace(
            "  </devices>",
            "    <serial type='pty'>\n"
            "      <target type='usb-serial' port='0'/>\n"
            "    </serial>\n"
            "  </devices>",
        )
        domain = _mock_domain_for_config(xml_wrong_target)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn):
            result = driver.get_console_info("test-uuid")

        assert result is False

    def test_returns_false_when_serial_missing_target_child(self):
        """Serial element without target child returns False."""
        xml_no_target = _SAMPLE_DOMAIN_XML.replace(
            "  </devices>",
            "    <serial type='pty'></serial>\n"
            "  </devices>",
        )
        domain = _mock_domain_for_config(xml_no_target)
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")

        with patch("libvirt.open", return_value=conn):
            result = driver.get_console_info("test-uuid")

        assert result is False

    def test_returns_false_on_libvirt_error(self, driver):
        """libvirt exceptions are caught; returns False."""
        import libvirt as _lv

        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = _lv.libvirtError("domain not found")

        with patch("libvirt.open", return_value=conn):
            result = driver.get_console_info("bad-uuid")

        assert result is False

    def test_returns_false_on_xml_parse_error(self, driver):
        """XML parsing errors are caught; returns False."""
        domain = MagicMock()
        domain.XMLDesc.return_value = "not valid xml <<>>"
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain

        with patch("libvirt.open", return_value=conn):
            result = driver.get_console_info("test-uuid")

        assert result is False


# ---------------------------------------------------------------------------
# set_user_password (live reset through the qemu guest agent)
# ---------------------------------------------------------------------------

import libvirt as _lv_pw  # noqa: E402

from agent.libvirt_driver import PasswordResetError  # noqa: E402

_PW_HASH = "$6$somesalt$abcdefghijklmnopqrstuvwxyz0123456789"


class _CodedLibvirtError(_lv_pw.libvirtError):
    """libvirtError with a fixed error code, for exercising code-based branches."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self._message = message
        self._code = code

    def get_error_code(self) -> int:
        return self._code

    def __str__(self) -> str:
        return self._message


class TestSetUserPassword:
    def _setup(self, state: int = _lv_pw.VIR_DOMAIN_RUNNING, set_error: Exception | None = None):
        domain = MagicMock()
        domain.state.return_value = (state, 0)
        if set_error is not None:
            domain.setUserPassword.side_effect = set_error
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        return conn, domain

    def _call(self, conn):
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        with patch("libvirt.open", return_value=conn):
            driver.set_user_password("abc-123", "ubuntu", _PW_HASH)

    def test_passes_hash_with_encrypted_flag(self):
        conn, domain = self._setup()
        self._call(conn)
        domain.setUserPassword.assert_called_once_with(
            "ubuntu", _PW_HASH, _lv_pw.VIR_DOMAIN_PASSWORD_ENCRYPTED
        )
        conn.close.assert_called_once()

    def test_stopped_vm_is_rejected_without_calling_guest_agent(self):
        conn, domain = self._setup(state=_lv_pw.VIR_DOMAIN_SHUTOFF)
        with pytest.raises(PasswordResetError, match="not running"):
            self._call(conn)
        domain.setUserPassword.assert_not_called()
        conn.close.assert_called_once()

    @pytest.mark.parametrize(
        "code", [_lv_pw.VIR_ERR_AGENT_UNRESPONSIVE, _lv_pw.VIR_ERR_AGENT_UNSYNCED]
    )
    def test_unresponsive_agent_gives_actionable_message(self, code):
        conn, _ = self._setup(set_error=_CodedLibvirtError("agent timeout", code))
        with pytest.raises(PasswordResetError, match="guest agent is not responding"):
            self._call(conn)
        conn.close.assert_called_once()

    def test_missing_agent_channel_gives_actionable_message(self):
        err = _CodedLibvirtError("unsupported", _lv_pw.VIR_ERR_OPERATION_UNSUPPORTED)
        conn, _ = self._setup(set_error=err)
        with pytest.raises(PasswordResetError, match="no guest agent channel"):
            self._call(conn)

    def test_other_libvirt_errors_are_wrapped_and_hash_is_masked(self):
        err = _CodedLibvirtError(f"chpasswd failed for {_PW_HASH}", _lv_pw.VIR_ERR_INTERNAL_ERROR)
        conn, _ = self._setup(set_error=err)
        with pytest.raises(PasswordResetError) as excinfo:
            self._call(conn)
        message = str(excinfo.value)
        assert message.startswith("Password reset failed:")
        assert _PW_HASH not in message
        assert "***" in message

    def test_unknown_domain_is_reported_as_not_found(self):
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = _CodedLibvirtError("no domain", _lv_pw.VIR_ERR_NO_DOMAIN)
        with pytest.raises(PasswordResetError, match="VM not found"):
            self._call(conn)
        conn.close.assert_called_once()

    def test_success_log_does_not_contain_the_hash(self, caplog):
        conn, _ = self._setup()
        with caplog.at_level("DEBUG"):
            self._call(conn)
        assert _PW_HASH not in caplog.text


class TestSetUserPasswordOffline:
    def _setup(self, state: int = _lv_pw.VIR_DOMAIN_SHUTOFF, xml_error: Exception | None = None):
        domain = MagicMock()
        domain.state.return_value = (state, 0)
        domain.XMLDesc.side_effect = xml_error
        disk_elem = MagicMock()
        disk_elem.get.return_value = "/var/lib/cos/vms/test-uuid.qcow2"
        if xml_error is None:
            # Mock the domain XML to include a disk element
            domain.XMLDesc.return_value = (
                '<domain><devices>'
                '<disk device="disk"><source file="/var/lib/cos/vms/test-uuid.qcow2"/></disk>'
                '</devices></domain>'
            )
        conn = MagicMock()
        conn.lookupByUUIDString.return_value = domain
        return conn, domain

    def _call(self, conn, helper_returncode: int = 0, helper_output: str = ""):
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        with patch("libvirt.open", return_value=conn):
            with patch("subprocess.run") as run_mock:
                result = MagicMock()
                result.returncode = helper_returncode
                result.stdout = helper_output
                result.stderr = "password reset successful for user 'ubuntu'\n"
                run_mock.return_value = result
                driver.set_user_password_offline("abc-123", "ubuntu", _PW_HASH)

    def test_running_vm_is_rejected(self):
        conn, _ = self._setup(state=_lv_pw.VIR_DOMAIN_RUNNING)
        with pytest.raises(PasswordResetError, match="must be stopped"):
            self._call(conn)

    def test_helper_invoked_with_disk_path_and_user(self):
        conn, _ = self._setup()
        with patch("libvirt.open", return_value=conn):
            with patch("subprocess.run") as run_mock:
                result = MagicMock()
                result.returncode = 0
                result.stderr = ""
                run_mock.return_value = result
                with patch("os.path.exists", return_value=True):
                    driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
                    driver.set_user_password_offline("abc-123", "ubuntu", _PW_HASH)
                run_mock.assert_called_once()
                args, kwargs = run_mock.call_args
                assert args[0][0] == "/usr/local/bin/cos-pw-reset-helper"
                assert args[0][1] == "/var/lib/cos/vms/test-uuid.qcow2"
                assert args[0][2] == "ubuntu"
                assert kwargs["input"] == _PW_HASH

    def test_user_not_found_error_code_2(self):
        conn, _ = self._setup()
        with patch("libvirt.open", return_value=conn):
            with patch("subprocess.run") as run_mock:
                result = MagicMock()
                result.returncode = 2
                result.stderr = "user 'baduser' not found\n"
                run_mock.return_value = result
                with patch("os.path.exists", return_value=True):
                    driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
                    with pytest.raises(PasswordResetError, match="not found"):
                        driver.set_user_password_offline("abc-123", "baduser", _PW_HASH)

    def test_helper_not_found(self):
        conn, _ = self._setup()
        with patch("libvirt.open", return_value=conn):
            with patch("subprocess.run", side_effect=FileNotFoundError("helper not found")):
                with patch("os.path.exists", return_value=True):
                    driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
                    with pytest.raises(PasswordResetError, match="helper not installed"):
                        driver.set_user_password_offline("abc-123", "ubuntu", _PW_HASH)

    def test_helper_timeout(self):
        conn, _ = self._setup()
        with patch("libvirt.open", return_value=conn):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 300)):
                with patch("os.path.exists", return_value=True):
                    driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
                    with pytest.raises(PasswordResetError, match="timed out"):
                        driver.set_user_password_offline("abc-123", "ubuntu", _PW_HASH)

    def test_vm_not_found(self):
        conn = MagicMock()
        conn.lookupByUUIDString.side_effect = _CodedLibvirtError("no domain", _lv_pw.VIR_ERR_NO_DOMAIN)
        driver = LibvirtDriver(uri="qemu:///system", bridge="nos-br")
        with patch("libvirt.open", return_value=conn):
            with pytest.raises(PasswordResetError, match="VM not found"):
                driver.set_user_password_offline("no-such-uuid", "ubuntu", _PW_HASH)
