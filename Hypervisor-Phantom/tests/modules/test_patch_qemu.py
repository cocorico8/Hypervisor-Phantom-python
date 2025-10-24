import pytest
from unittest.mock import patch, call, MagicMock, ANY
import subprocess

from modules import patch_qemu
import utils

# Sample C code content for testing the spoofing functions
SAMPLE_USB_CODE = """
static const struct usb_string_descriptor str_desc[] = {
    [STR_SERIALNUMBER] = "1234567890",
    [STR_SERIALMOUSE] = "QEMU MOUSE",
};
"""

SAMPLE_SMBIOS_CODE = """
void smbios_build_type_4_fields(void) {
    t->processor_family = 0x02;
    t->voltage = 0x22;
    t->processor_upgrade = 0x04;
    t->external_clock = cpu_to_le16(0x0000);
    t->l1_cache_handle = cpu_to_le16(0x0005);
    t->l2_cache_handle = cpu_to_le16(0x0006);
    t->l3_cache_handle = cpu_to_le16(0x0007);
    t->processor_family2 = cpu_to_le16(0x0105);
    t->processor_characteristics = cpu_to_le32(0x00000003);
}
"""

# Each line represents 16 bytes (32 hex characters).
SAMPLE_DMI_HEX = (
    "000000000000000000000000FB000000"  # Offset 12: processor_family = FB
    "00000000000000000000000000000000"
    "00002200640000000000000000000000"  # Offset 34: voltage = 22, Offset 36: external_clock = 6400
    "00000400110022003300000000000000"  # Offset 50: upgrade = 04, L1=1100, L2=2200, L3=3300
    "0000000000000000FD020000FE020000"  # Offset 76: characteristics = FD020000, Offset 80: family2 = FE02
)


@pytest.fixture
def builder_instance(monkeypatch):
    """Provides a default QEMUBuilder instance for testing."""
    monkeypatch.setenv("SUDO_USER", "testuser")
    monkeypatch.setattr("modules.patch_qemu.Path.exists", lambda self: False)
    return patch_qemu.QEMUBuilder(distro="Arch", cpu_vendor="GenuineIntel")


@patch("utils.install_required_packages")
def test_install_dependencies(mock_install, builder_instance):
    """Tests that the correct list of packages is passed to the installer utility."""
    from config import packages
    builder_instance._install_dependencies()
    mock_install.assert_called_once_with("QEMU Build", packages.QEMU_BUILD["Arch"], "Arch")

@patch("modules.patch_qemu.requests.get")
@patch("utils.run_command")
def test_acquire_source_gpg_failure_continue(mock_run_command, mock_requests_get, builder_instance):
    """
    Tests that the user can continue even if GPG verification fails.
    """
    mock_response = MagicMock(iter_content=MagicMock(return_value=[b'data']))
    mock_requests_get.return_value = mock_response
    def gpg_side_effect(*args, **kwargs):
        if "gpg" in args[0] and "--verify" in args[0]:
            raise utils.CommandExecutionError("GPG failed", 1, "", "")
        return MagicMock()
    mock_run_command.side_effect = gpg_side_effect
    with patch("utils.yes_or_no", return_value=True), patch("builtins.open", MagicMock()):
        builder_instance._acquire_source()
    assert any("tar" in call.args[0][0] for call in mock_run_command.call_args_list)

@patch("modules.patch_qemu.Path.glob")
def test_spoof_usb(mock_glob, builder_instance):
    """
    Tests that USB serial numbers in a C file string are correctly replaced.
    """
    mock_file = MagicMock(read_text=MagicMock(return_value=SAMPLE_USB_CODE))
    mock_glob.return_value = [mock_file]
    builder_instance._spoof_usb()
    mock_file.write_text.assert_called_once()
    written_content = mock_file.write_text.call_args[0][0]
    assert "1234567890" not in written_content
    assert '[STR_SERIALNUMBER] = "' in written_content

@patch("modules.patch_qemu.QEMUBuilder._get_dmi_hex_data", return_value=SAMPLE_DMI_HEX)
@patch("pathlib.Path.read_text", return_value=SAMPLE_SMBIOS_CODE)
@patch("pathlib.Path.write_text")
def test_patch_smbios_data(mock_write_text, mock_read_text, mock_get_dmi, builder_instance):
    """
    Tests that SMBIOS data is correctly parsed from hex and patched into the C source string.
    """
    builder_instance._patch_smbios_data()
    mock_write_text.assert_called_once()
    written_content = mock_write_text.call_args[0][0]

    assert "t->processor_family = 0xFB;" in written_content
    assert "t->voltage = 0x22;" in written_content
    assert "t->processor_upgrade = 0x04;" in written_content
    assert "t->external_clock = cpu_to_le16(0x0064);" in written_content
    assert "t->l1_cache_handle = cpu_to_le16(0x0011);" in written_content
    assert "t->l2_cache_handle = cpu_to_le16(0x0022);" in written_content
    assert "t->l3_cache_handle = cpu_to_le16(0x0033);" in written_content
    assert "t->processor_characteristics = cpu_to_le32(0x000002FE);" in written_content
    assert "t->processor_family2 = cpu_to_le16(0x00);" in written_content

@patch("utils.run_command")
@patch("utils.yes_or_no", return_value=True)
def test_compile_uses_correct_user(mock_yes_no, mock_run_command, builder_instance):
    """
    Verifies that 'configure' and 'make' are run as the original non-root user.
    """
    builder_instance._compile()
    commands = [c.args[0] for c in mock_run_command.call_args_list]
    configure_cmd = next(cmd for cmd in commands if './configure' in cmd)
    make_cmd = next(cmd for cmd in commands if 'make' in cmd and 'install' not in cmd)
    install_cmd = next(cmd for cmd in commands if 'install' in cmd)
    assert configure_cmd[0:3] == ["sudo", "-u", "testuser"]
    assert make_cmd[0:3] == ["sudo", "-u", "testuser"]
    assert install_cmd[0] == "sudo"