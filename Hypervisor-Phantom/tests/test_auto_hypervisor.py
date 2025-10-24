import pytest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import auto_hypervisor
import utils
import shutil

# ==============================================================================
# 1. SYSTEM DETECTION TESTS
# ==============================================================================

@pytest.mark.parametrize("release_content, expected_distro", [
    ('ID=arch', "Arch"),
    ('ID=manjaro', "Arch"),
    ('ID=debian', "Debian"),
    ('ID=ubuntu', "Debian"),
    ('ID=fedora', "Fedora"),
    ('ID=opensuse-tumbleweed', "openSUSE"),
])
def test_detect_distro_from_os_release(monkeypatch, release_content, expected_distro):
    """
    Tests the detect_distro function by mocking the content of /etc/os-release.
    """
    monkeypatch.setattr(Path, "is_file", lambda self: "/etc/os-release" in str(self))
    monkeypatch.setattr(Path, "read_text", lambda self: release_content)
    distro = auto_hypervisor.detect_distro()
    assert distro == expected_distro

def test_detect_distro_unknown_and_fallback_fails(monkeypatch):
    """
    Tests the case where os-release is unhelpful and no package managers are found.
    """
    monkeypatch.setattr(Path, "is_file", lambda self: "/etc/os-release" in str(self))
    monkeypatch.setattr(Path, "read_text", lambda self: 'PRETTY_NAME="Unknown Linux"')
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    distro = auto_hypervisor.detect_distro()
    assert distro == "Unknown"

def test_detect_distro_fallback_to_pacman(monkeypatch):
    """
    Tests the fallback mechanism for distro detection if os-release fails.
    """
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/pacman" if cmd == "pacman" else None)
    distro = auto_hypervisor.detect_distro()
    assert distro == "Arch"

@pytest.mark.parametrize("cpuinfo_content, expected_vendor", [
    (["vendor_id\t: GenuineIntel"], "GenuineIntel"),
    (["vendor_id\t: AuthenticAMD"], "AuthenticAMD"),
    (["some other info"], "Unknown"),
])
def test_detect_cpu_vendor(monkeypatch, cpuinfo_content, expected_vendor):
    """
    Tests CPU vendor detection by mocking /proc/cpuinfo.
    """
    m = mock_open(read_data="\n".join(cpuinfo_content))
    with patch("builtins.open", m):
        vendor = auto_hypervisor.detect_cpu_vendor()
        assert vendor == expected_vendor

def test_check_hardware_support_all_enabled(monkeypatch):
    """
    Tests the ideal scenario where all hardware virtualization features are enabled.
    """
    # Mock reading the cpuinfo file
    monkeypatch.setattr(Path, "read_text", lambda self: "flags: vmx" if "/proc/cpuinfo" in str(self) else "")

    # Mock methods on Path instances to simulate the IOMMU directory
    original_is_dir = Path.is_dir
    def mock_is_dir(self):
        if "/sys/kernel/iommu_groups" in str(self):
            return True
        return original_is_dir(self)

    original_iterdir = Path.iterdir
    def mock_iterdir(self):
        if "/sys/kernel/iommu_groups" in str(self):
            return iter([Path("0")])  # Return an iterator with one item
        return original_iterdir(self)

    monkeypatch.setattr(Path, "is_dir", mock_is_dir)
    monkeypatch.setattr(Path, "iterdir", mock_iterdir)

    # Mock the lsmod command
    mock_lsmod_result = MagicMock(stdout="kvm_intel 123\nkvm 456 1 kvm_intel")
    monkeypatch.setattr(utils, "run_command", lambda *args, **kwargs: mock_lsmod_result)

    virt, iommu, kvm = auto_hypervisor.check_hardware_support()
    assert virt is True and iommu is True and kvm is True

# ==============================================================================
# 2. APPLICATION FLOW TESTS
# ==============================================================================

@patch("modules.virtualization.main")
@patch("utils.quick_prompt")
def test_app_main_menu_dispatches_correctly(mock_quick_prompt, mock_virt_main, monkeypatch):
    """
    Tests that the main menu correctly calls the target module's main function.
    """
    mock_quick_prompt.side_effect = ["1", "", "0"]
    monkeypatch.setattr(utils, "yes_or_no", lambda _: False)
    with pytest.raises(SystemExit) as e:
        app = auto_hypervisor.HypervisorPhantomApp()
        app.run()
    assert e.type == SystemExit and e.value.code == 0
    mock_virt_main.assert_called_once_with(app.system_info.distro)