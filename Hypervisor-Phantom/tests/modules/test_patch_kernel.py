import pytest
from unittest.mock import patch, call, MagicMock
from pathlib import Path

from modules import patch_kernel
import utils

# ==============================================================================
# 1. FIXTURES AND SETUP
# ==============================================================================

@pytest.fixture
def builder_instance(monkeypatch):
    """Provides a KernelBuilder instance for testing."""
    monkeypatch.setenv("SUDO_USER", "testuser")
    return patch_kernel.KernelBuilder(distro="Arch", cpu_vendor="AuthenticAMD")

# ==============================================================================
# 2. CORE LOGIC TESTS
# ==============================================================================

@patch("shutil.disk_usage")
def test_check_disk_space_sufficient(mock_disk_usage, builder_instance):
    """
    Tests the disk space check for the success case (sufficient space).
    """
    mock_disk_usage.return_value = MagicMock(free=100 * 1024**3)
    try:
        builder_instance._check_disk_space()
    except SystemExit:
        pytest.fail("SystemExit was raised unexpectedly for sufficient disk space.")

@patch("shutil.disk_usage")
def test_check_disk_space_insufficient(mock_disk_usage, builder_instance):
    """
    Tests the disk space check for the failure case (insufficient space).
    """
    mock_disk_usage.return_value = MagicMock(free=10 * 1024**3)
    with pytest.raises(SystemExit):
        builder_instance._check_disk_space()

@patch("modules.patch_kernel.Path.write_text")
@patch("modules.patch_kernel.Path.read_text", return_value='_distro="Arch"\n_processor_opt="generic"')
@patch("modules.patch_kernel.KernelBuilder._select_from_menu", return_value="znver3")
@patch("utils.yes_or_no", return_value=True)
def test_configure_tkg(mock_yes_no, mock_select, mock_read, mock_write, builder_instance):
    """
    Tests the TKG configuration logic to ensure customization.cfg is written correctly.
    """
    builder_instance._configure_tkg()
    mock_yes_no.assert_called_once()
    mock_select.assert_called_once()
    mock_write.assert_called_once()
    written_content = mock_write.call_args[0][0]

    assert '_distro="Arch"' in written_content
    assert f'_version="{builder_instance.kernel_version}"' in written_content
    assert '_acs_override="true"' in written_content
    assert '_processor_opt="znver3"' in written_content
    assert '_user_patches_no_confirm="true"' in written_content

@patch("pathlib.Path.mkdir")
@patch("shutil.copy")
@patch("utils.get_resource_path")
def test_apply_custom_patches(mock_get_resource, mock_copy, mock_mkdir, builder_instance):
    """
    Ensures that the correct custom patch is copied to the userpatches directory.
    """
    mock_source_path = MagicMock()
    mock_source_path.exists.return_value = True
    (mock_source_path / "amd614.mypatch").exists.return_value = True # Make the specific patch exist
    mock_get_resource.return_value = mock_source_path

    builder_instance._apply_custom_patches()

    expected_dest_dir = builder_instance.tkg_path / "linux614-tkg-userpatches"
    mock_mkdir.assert_called_once_with(exist_ok=True)
    mock_copy.assert_called_once_with(mock_source_path / "amd614.mypatch", expected_dest_dir)


@patch("utils.run_command")
def test_build_and_install_kernel_arch(mock_run_command, builder_instance):
    """
    Tests that the Arch Linux-specific build command is used.
    """
    builder_instance.distro = "Arch"
    builder_instance._build_and_install_kernel()
    mock_run_command.assert_called_once()
    command_list = mock_run_command.call_args[0][0]
    assert command_list[:4] == ["sudo", "-u", "testuser", "makepkg"]
    assert "-si" in command_list

@patch("utils.run_command")
def test_build_and_install_kernel_debian(mock_run_command, builder_instance):
    """
    Tests that the generic ./install.sh command is used for non-Arch distros.
    """
    builder_instance.distro = "Debian"
    builder_instance._build_and_install_kernel()
    mock_run_command.assert_called_once_with(
        ["./install.sh", "install"],
        builder_instance.tkg_path,
        show_spinner=True
    )

@patch("modules.patch_kernel.Path.write_text")
@patch("modules.patch_kernel.Path.is_dir", return_value=True)
@patch("utils.run_command")
def test_create_systemd_boot_entry(mock_run_command, mock_is_dir, mock_write_text, builder_instance):
    """
    Verifies that the systemd-boot entry is created with the correct content.
    """
    mock_run_command.side_effect = [
        MagicMock(stdout="/dev/sda2\n"),
        MagicMock(stdout="FAKE-PARTUUID-1234\n")
    ]
    builder_instance._create_systemd_boot_entry()
    mock_write_text.assert_called_once()
    entry_content = mock_write_text.call_args[0][0]
    assert "title   Hypervisor Phantom Kernel (linux614-tkg-eevdf)" in entry_content
    assert "linux   /vmlinuz-linux614-tkg-eevdf" in entry_content
    assert "options root=PARTUUID=FAKE-PARTUUID-1234 rw" in entry_content