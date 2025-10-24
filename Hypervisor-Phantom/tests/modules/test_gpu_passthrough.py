import pytest
from unittest.mock import patch, call, MagicMock, ANY
from pathlib import Path
import os

from modules import gpu_passthrough
import utils

# ==============================================================================
# 1. FIXTURES AND SETUP
# ==============================================================================

@pytest.fixture
def vfio_setup_instance(monkeypatch):
    """Provides a VFIOSetup instance with a mocked bootloader."""
    with patch("modules.gpu_passthrough.VFIOSetup._detect_bootloader") as mock_detect:
        mock_detect.return_value = ("GRUB", Path("/etc/default/grub"))
        instance = gpu_passthrough.VFIOSetup(cpu_vendor="GenuineIntel")
    return instance

# FIX: Added 'monkeypatch' as an argument.
def create_mock_pci_device(monkeypatch, name, class_id, vendor, device, iommu_group, is_symlink=True):
    """Helper function to create a MagicMock representing a PCI device in /sys."""
    dev_path = MagicMock(spec=Path, name=f"dev_{name}")
    dev_path.name = name
    # This allows chaining path divisions, e.g., dev_path / "class"
    dev_path.__truediv__.side_effect = lambda x: {
        "class": MagicMock(read_text=MagicMock(return_value=class_id)),
        "vendor": MagicMock(read_text=MagicMock(return_value=vendor)),
        "device": MagicMock(read_text=MagicMock(return_value=device)),
        "iommu_group": MagicMock(is_symlink=MagicMock(return_value=is_symlink))
    }[x]

    if is_symlink:
        monkeypatch.setattr(os, "readlink", lambda path: f"../../../../kernel/iommu_groups/{iommu_group}")

    return dev_path


# ==============================================================================
# 2. CORE LOGIC TESTS
# ==============================================================================

@pytest.mark.parametrize("bootloader_files, expected_result", [
    ({"grub": True, "sd-boot": False}, ("GRUB", Path("/etc/default/grub"))),
    ({"grub": False, "sd-boot": True}, ("systemd-boot", Path("/boot/loader/entries"))),
    ({"grub": False, "sd-boot": False}, ("Unknown", None)),
])
def test_detect_bootloader(monkeypatch, bootloader_files, expected_result):
    """Tests the bootloader detection logic under different filesystem conditions."""
    monkeypatch.setattr(Path, "is_file", lambda self: "/etc/default/grub" in str(self) and bootloader_files["grub"])
    monkeypatch.setattr(Path, "is_dir", lambda self: any(d in str(self) for d in ["/boot/loader/entries", "/efi/loader/entries"]) and bootloader_files["sd-boot"])
    monkeypatch.setattr(Path, "glob", lambda self, pattern: [Path("entry.conf")] if bootloader_files["sd-boot"] else [])

    result = gpu_passthrough.VFIOSetup._detect_bootloader()
    assert result == expected_result

@pytest.mark.parametrize("scenario", ["good_group", "bad_group", "iommu_disabled"])
@patch("utils.run_command")
@patch("utils.ask", return_value="1")
def test_select_gpu_and_validate_iommu_scenarios(mock_ask, mock_run_command, monkeypatch, scenario):
    """
    Tests the GPU selection and IOMMU validation logic with a mocked /sys filesystem.
    """
    gpu = create_mock_pci_device(monkeypatch, "0000:01:00.0", "0x030000", "0x10de", "0x1f06", "1",
                                 is_symlink=(scenario != "iommu_disabled"))
    gpu_audio = create_mock_pci_device(monkeypatch, "0000:01:00.1", "0x040300", "0x10de", "0x10f9", "1")
    root_bridge = create_mock_pci_device(monkeypatch, "0000:00:00.0", "0x060000", "0x8086", "0x191f", "1")

    iommu_group_devices = [gpu, gpu_audio]
    pci_devices = [gpu, gpu_audio, root_bridge]

    if scenario == "bad_group":
        iommu_group_devices.append(root_bridge)

    # Mock iterdir to return our virtual devices
    def mock_iterdir(self):
        if "pci/devices" in str(self):
            return pci_devices
        if "iommu_groups/1/devices" in str(self):
            return iommu_group_devices
        return []
    monkeypatch.setattr(Path, "iterdir", mock_iterdir)

    mock_run_command.return_value = MagicMock(stdout="Mock GPU Description")

    if scenario == "good_group":
        result = gpu_passthrough.VFIOSetup._select_gpu_and_validate_iommu()
        assert result is not None
        hw_ids, vendor_id = result
        assert "10de:1f06" in hw_ids
        assert "10de:10f9" in hw_ids
        assert vendor_id == "0x10de"
    else:
        with pytest.raises(SystemExit):
            gpu_passthrough.VFIOSetup._select_gpu_and_validate_iommu()


@patch("utils._write_privileged_file")
def test_create_vfio_modprobe_conf(mock_write, vfio_setup_instance):
    """
    Ensures the vfio.conf file is generated with the correct content.
    """
    hw_ids = "10de:1f06,10de:10f9"
    gpu_vendor = "0x10de" # NVIDIA
    vfio_setup_instance._create_vfio_modprobe_conf(hw_ids, gpu_vendor)
    mock_write.assert_called_once()
    written_lines = mock_write.call_args[0][1]
    assert f"options vfio-pci ids={hw_ids} disable_vga=1\n" in written_lines
    assert "softdep nvidia pre: vfio-pci\n" in written_lines
    assert "softdep nouveau pre: vfio-pci\n" in written_lines

@pytest.mark.parametrize("current_opts, new_opts, is_revert, expected_opts", [
    ('GRUB_CMDLINE_LINUX_DEFAULT=""', "intel_iommu=on iommu=pt", False, 'GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"'),
    ('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"', "intel_iommu=on", False, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash intel_iommu=on"'),
    ('GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on vfio-pci.ids=... splash"', "", True, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"'),
    ('GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on"', "intel_iommu=on", False, 'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on"'),
])
@patch("utils._write_privileged_file")
@patch("utils.run_command")
def test_update_bootloader_grub(mock_run_command, mock_write, vfio_setup_instance, current_opts, new_opts, is_revert, expected_opts):
    """
    Tests the logic for modifying the GRUB command line.
    """
    mock_run_command.return_value = MagicMock(stdout=current_opts)
    vfio_setup_instance._update_bootloader(new_opts, is_revert)
    mock_write.assert_called_once()
    written_content = "".join(mock_write.call_args[0][1])
    assert expected_opts in written_content