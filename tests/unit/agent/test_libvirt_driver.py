"""Unit tests for agent/libvirt_driver.py with mocked libvirt connection."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch, call

import pytest
from agent.libvirt_driver import LibvirtDriver


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

    def test_with_vlan_id_adds_nos_metadata(self, driver):
        xml = self._captured_xml(driver, vlan_id=200)
        assert "<metadata>" in xml
        assert 'xmlns:nos="https://github.com/theloger-png/nos"' in xml
        assert "<nos:vlan" in xml
        assert ">200<" in xml

    def test_vlan_id_value_is_correct(self, driver):
        xml = self._captured_xml(driver, vlan_id=42)
        assert ">42<" in xml

    def test_without_vlan_id_no_metadata_block(self, driver):
        xml = self._captured_xml(driver)
        assert "<metadata>" not in xml
        assert "<nos:vlan" not in xml

    def test_vlan_id_none_no_metadata_block(self, driver):
        xml = self._captured_xml(driver, vlan_id=None)
        assert "<metadata>" not in xml


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
