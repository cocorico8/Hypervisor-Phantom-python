import pytest
from unittest.mock import patch, call, MagicMock, ANY

from modules import looking_glass
import utils

# Sample C code for the KVMFR module to test patching
SAMPLE_KVMFR_C_CODE = """
#include <linux/module.h>
#define DRIVER_NAME "kvmfr"
#define KVMFR_VENDOR_ID 0x1af4
#define KVMFR_DEVICE_ID 0x1110

static const struct pci_device_id pci_ids[] = {
    { PCI_DEVICE(KVMFR_VENDOR_ID, KVMFR_DEVICE_ID) },
    { 0, }
};
MODULE_DEVICE_TABLE(pci, pci_ids);
"""

@pytest.fixture
def setup_instance(monkeypatch):
    """Provides a LookingGlassSetup instance for testing."""
    monkeypatch.setenv("SUDO_USER", "testuser")
    return looking_glass.LookingGlassSetup(distro="Arch", cpu_vendor="GenuineIntel")

# ==============================================================================
# 1. CORE LOGIC TESTS
# ==============================================================================

@patch("utils.run_command")
@patch("shutil.unpack_archive")
@patch("modules.looking_glass.requests.get")
@patch("pathlib.Path.read_text", return_value=SAMPLE_KVMFR_C_CODE)
@patch("pathlib.Path.write_text")
def test_install_client_patches_vendor_id(mock_write, mock_read, mock_requests, mock_unpack, mock_run, setup_instance):
    """
    Tests the _install_client method, focusing on the vendor ID patching logic.
    """
    mock_requests.return_value = MagicMock(content=b"fakedata")
    setup_instance._install_client()

    mock_requests.assert_called_once()
    mock_unpack.assert_called_once()
    mock_read.assert_called_once()
    mock_write.assert_called_once()

    written_content = mock_write.call_args[0][0]
    assert "0x1af4" not in written_content
    assert "0x8086" in written_content

    cmake_call = call(["cmake", ".."], cwd=setup_instance.lg_source_path / "client" / "build")
    make_install_call = call(["sudo", "make", "install"], cwd=setup_instance.lg_source_path / "client" / "build", show_spinner=True)
    mock_run.assert_has_calls([cmake_call, make_install_call])

@patch("utils.run_command")
@patch("utils.update_config_file")
def test_configure_shmem(mock_update_config, mock_run_command, setup_instance):
    """
    Verifies that the shared memory configuration creates the correct tmpfiles.d entry.
    """
    setup_instance._configure_shmem()
    expected_content = "f /dev/shm/looking-glass 0660 testuser kvm -"
    # FIX: Assert against ANY Path object instead of a specific MagicMock.
    mock_update_config.assert_called_once_with(ANY, ".*", expected_content)
    mock_run_command.assert_called_once_with(["sudo", "systemd-tmpfiles", "--create", ANY], ANY)

@patch("utils.run_command")
@patch("pathlib.Path.write_text")
@patch("pathlib.Path.exists", return_value=True)
def test_configure_kvmfr(mock_exists, mock_write, mock_run_command, setup_instance):
    """
    Tests the KVMFR module configuration, ensuring modprobe and modules-load files are correct.
    """
    setup_instance._configure_kvmfr()
    modprobe_call = call(["sudo", "modprobe", "kvmfr", "static_size_mb=32"], ANY)
    chown_call = call(["sudo", "chown", "testuser:kvm", "/dev/kvmfr0"], ANY)
    mock_run_command.assert_has_calls([modprobe_call, chown_call])

    assert mock_write.call_count == 2
    call_args_list = [c.args[0] for c in mock_write.call_args_list]
    assert "options kvmfr static_size_mb=32\n" in call_args_list
    assert "# KVMFR Looking Glass module\nkvmfr\n" in call_args_list