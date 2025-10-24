import pytest
from unittest.mock import patch, call, MagicMock, mock_open
import struct
import json
from pathlib import Path

from modules import patch_ovmf
import utils

# ==============================================================================
# 1. OVMFBuilder TESTS
# ==============================================================================

@pytest.fixture
def builder_instance():
    """Provides a default OVMFBuilder instance for testing."""
    return patch_ovmf.OVMFBuilder(distro="Arch", cpu_vendor="AuthenticAMD")

@pytest.mark.parametrize("header_bytes, expected_result, test_id", [
    (struct.pack("<2s16xIIHHI", b"BM", 100, 100, 1, 24, 0), True, "valid_24bit"),
    (struct.pack("<2s16xIIHHI", b"BM", 100, 100, 1, 8, 0), True, "valid_8bit"),
    (struct.pack("<2s16xIIHHI", b"BB", 100, 100, 1, 24, 0), False, "invalid_magic"),
    (struct.pack("<2s16xIIHHI", b"BM", 100, 100, 1, 24, 1), False, "invalid_compression"),
    (struct.pack("<2s16xIIHHI", b"BM", 100, 100, 1, 32, 0), False, "invalid_bpp"),
    (b"BM", False, "file_too_small"),
])
def test_validate_bmp_scenarios(builder_instance, header_bytes, expected_result, test_id):
    """
    Tests the _validate_bmp method with various crafted BMP headers.
    """
    m = mock_open(read_data=header_bytes)
    with patch("builtins.open", m):
        result = builder_instance._validate_bmp(Path("/fake.bmp"))
        assert result is expected_result

@patch("shutil.copy")
@patch("utils.ask", return_value="/fake/path/logo.bmp")
@patch("modules.patch_ovmf.OVMFBuilder._validate_bmp", return_value=True)
@patch("modules.patch_ovmf.Path.is_file", return_value=True)
def test_configure_boot_logo_custom_path(mock_is_file, mock_validate, mock_ask, mock_copy, builder_instance):
    """
    Tests the "custom BMP" option in the boot logo configuration menu.
    """
    with patch("utils.quick_prompt", return_value="2"):
        builder_instance._configure_boot_logo()

    mock_ask.assert_called_once()
    mock_validate.assert_called_once_with(Path("/fake/path/logo.bmp"))
    mock_copy.assert_called_once()


# ==============================================================================
# 2. CertInjector TESTS
# ==============================================================================

@pytest.fixture
def injector_instance():
    """Provides a default CertInjector instance for testing."""
    return patch_ovmf.CertInjector()

@patch("utils.run_command")
@patch("utils.ask", return_value="1")
def test_select_target_vm_success(mock_ask, mock_run_command, injector_instance):
    """
    Tests the VM selection process with a mocked 'virsh list' output.
    """
    mock_result = MagicMock(stdout="win11\ndebian12\n")
    mock_run_command.return_value = mock_result
    with patch("modules.patch_ovmf.Path.exists", return_value=True):
        result = injector_instance._select_target_vm()

    assert result is not None
    vm_name, vars_path = result
    assert vm_name == "win11"
    assert vars_path.name == "win11_VARS.qcow2"

# FIX: Corrected function signature to accept all mocked arguments.
@patch("modules.patch_ovmf.Path.is_dir", return_value=True)
@patch("modules.patch_ovmf.Path.is_file", return_value=True)
@patch("modules.patch_ovmf.Path.read_bytes")
def test_generate_defaults_json(mock_read_bytes, mock_is_file, mock_is_dir, injector_instance):
    """
    Tests the generation of defaults.json from mocked host EFI variables.
    """
    mock_read_bytes.return_value = struct.pack("<I", 7) + b"\xDE\xAD\xBE\xEF"
    temp_dir = Path("/fake/tmp")

    with patch("modules.patch_ovmf.Path.write_text") as mock_write_text:
        injector_instance._generate_defaults_json(temp_dir)
        mock_write_text.assert_called_once()
        json_string = mock_write_text.call_args[0][0]
        data = json.loads(json_string)

        assert data["version"] == 2
        assert len(data["variables"]) == 3
        assert data["variables"][0]["name"] == "dbDefault"
        assert data["variables"][0]["attr"] == 7
        assert data["variables"][0]["data"] == "deadbeef"

@patch("utils.run_command")
@patch("modules.patch_ovmf.CertInjector._generate_defaults_json", return_value=None)
def test_run_injection_constructs_correct_command(mock_gen_json, mock_run_command, injector_instance):
    """
    Verifies that the virt-fw-vars command is constructed with all expected arguments.
    """
    injector_instance._run_injection("win11", Path("/fake/vars"), Path("/tmp"))
    mock_run_command.assert_called_once()
    command_list = mock_run_command.call_args[0][0]

    assert "sudo" in command_list
    assert "virt-fw-vars" in command_list
    assert "--secure-boot" in command_list
    assert command_list.count("--add-db") == 2