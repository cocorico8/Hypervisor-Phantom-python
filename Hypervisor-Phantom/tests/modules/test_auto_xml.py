import pytest
from unittest.mock import patch, call, MagicMock, ANY
import json

from modules import auto_xml
import utils

# Sample lscpu JSON output for testing
SAMPLE_LSCPU_JSON = json.dumps({
    "lscpu": [
        {"field": "CPU(s):", "data": "16"},
        {"field": "Thread(s) per core:", "data": "2"},
        {"field": "Core(s) per socket:", "data": "8"},
        {"field": "Socket(s):", "data": "1"},
    ]
})

# Sample XML template for testing
SAMPLE_XML_TEMPLATE = """
<domain type='kvm'>
  <name>@VM_NAME@</name>
  <vcpu placement='static'>@TOTAL_NUMBER_OF_CORES@</vcpu>
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <topology sockets='1' dies='1' cores='@NUMBER_OF_CORES@' threads='@NUMBER_OF_THREADS@'/>
  </cpu>
  <os>
    <loader readonly='yes' type='pflash'>@OVMF_CODE_PATH@</loader>
    <nvram>@OVMF_VARS_PATH@</nvram>
  </os>
</domain>
"""

@pytest.fixture
def generator_instance():
    """Provides a default XMLGenerator instance for testing."""
    return auto_xml.XMLGenerator(cpu_vendor="GenuineIntel")

# ==============================================================================
# 1. HELPER FUNCTION TESTS
# ==============================================================================

@patch("utils.run_command")
def test_get_cpu_topology_success(mock_run_command):
    """
    Tests successful parsing of CPU topology from mocked lscpu output.
    """
    # Arrange
    mock_run_command.return_value = MagicMock(stdout=SAMPLE_LSCPU_JSON)

    # Act
    cores, threads, total_vcpus = auto_xml._get_cpu_topology()

    # Assert
    assert cores == 8
    assert threads == 2
    assert total_vcpus == 16

@patch("utils.run_command", side_effect=utils.CommandExecutionError("lscpu failed", 1, "", ""))
def test_get_cpu_topology_failure(mock_run_command):
    """
    Tests the fallback mechanism when lscpu command fails.
    """
    # Act
    cores, threads, total_vcpus = auto_xml._get_cpu_topology()

    # Assert
    assert cores == 4
    assert threads == 1
    assert total_vcpus == 4

# ==============================================================================
# 2. XMLGENERATOR CLASS TESTS
# ==============================================================================

@patch("modules.auto_xml.Path.is_file", return_value=True)
def test_check_prerequisites_success(mock_is_file, generator_instance):
    """
    Tests that no error is raised when prerequisite firmware files are found.
    """
    try:
        generator_instance._check_prerequisites()
    except SystemExit:
        pytest.fail("SystemExit was raised unexpectedly when prerequisites were met.")

@patch("modules.auto_xml.Path.is_file", return_value=False)
def test_check_prerequisites_failure(mock_is_file, generator_instance):
    """
    Tests that a SystemExit is raised when firmware files are missing.
    """
    with pytest.raises(SystemExit):
        generator_instance._check_prerequisites()

@patch("modules.auto_xml._get_cpu_topology", return_value=(8, 2, 16))
@patch("modules.auto_xml.Path.read_text", return_value=SAMPLE_XML_TEMPLATE)
@patch("modules.auto_xml.Path.is_file", return_value=True)
def test_load_and_populate_template(mock_is_file, mock_read_text, mock_get_cpu, generator_instance):
    """
    Verifies that the XML template placeholders are correctly replaced.
    """
    # Arrange
    generator_instance.vm_name = "TestVM"

    # Act
    generator_instance._load_and_populate_template()
    xml = generator_instance.xml_content

    # Assert
    assert "<name>TestVM</name>" in xml
    assert "<vcpu placement='static'>16</vcpu>" in xml
    assert "cores='8'" in xml
    assert "threads='2'" in xml
    assert str(generator_instance.ovmf_code_path) in xml
    assert str(generator_instance.ovmf_vars_path) in xml

@patch("utils.run_command")
@patch("tempfile.NamedTemporaryFile")
def test_define_vm(mock_tempfile, mock_run_command, generator_instance):
    """
    Tests that the 'virsh define' command is called correctly with a temporary file.
    """
    # Arrange
    mock_file = MagicMock()
    mock_file.name = "/tmp/fakefile.xml"
    mock_tempfile.return_value.__enter__.return_value = mock_file

    generator_instance.xml_content = "<domain>...</domain>"

    # Act
    generator_instance._define_vm()

    # Assert
    mock_file.write.assert_called_once_with(generator_instance.xml_content)
    # FIX: Use ANY for the cwd argument to make the test less brittle.
    mock_run_command.assert_called_once_with(
        ["sudo", "virsh", "define", "/tmp/fakefile.xml"],
        cwd=ANY,
        show_spinner=True
    )

@patch("utils.run_command")
@patch("modules.auto_xml._get_dmidecode_info", return_value="Fake String")
def test_inject_smbios(mock_get_dmi, mock_run_command, generator_instance):
    """
    Tests that the virt-xml command for SMBIOS injection is constructed correctly.
    """
    # Arrange
    generator_instance.vm_name = "TestVM"

    # Act
    generator_instance._inject_smbios()

    # Assert
    mock_run_command.assert_called_once()
    command_list = mock_run_command.call_args[0][0]

    assert command_list[:3] == ["sudo", "virt-xml", "TestVM"]
    assert "--qemu-commandline" in command_list
    qemu_args = command_list[-1]
    assert "smbios type=1,manufacturer='Fake String',product='Fake String'" in qemu_args
    assert "smbios type=2,manufacturer='Fake String'" in qemu_args
    assert "smbios type=3,manufacturer='Fake String'" in qemu_args

@patch("utils.run_command")
@patch("utils.generate_random_mac", return_value="02:DE:AD:BE:EF:00")
def test_set_mac_address(mock_gen_mac, mock_run_command, generator_instance):
    """
    Ensures the virt-xml command to set the MAC address is correct.
    """
    # Arrange
    generator_instance.vm_name = "TestVM"

    # Act
    generator_instance._set_mac_address()

    # Assert
    mock_run_command.assert_called_once_with(
        ["sudo", "virt-xml", "TestVM", "--edit", "--network", "mac=02:DE:AD:BE:EF:00"],
        cwd=ANY
    )