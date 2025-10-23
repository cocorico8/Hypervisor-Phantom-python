import os
import subprocess
import json
import uuid
import tempfile
from pathlib import Path
import utils

# ==============================================================================
#  HELPER FUNCTIONS
# ==============================================================================

def _get_cpu_topology():
    """Gets host CPU core and thread counts by parsing 'lscpu --json'."""
    utils.log("Detecting host CPU topology...")
    try:
        result = subprocess.run(
            ["lscpu", "--json"], capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        
        cpu_data = {item['field'].strip(':'): item['data'] for item in data['lscpu']}
        
        cores = int(cpu_data.get('Core(s) per socket', 1))
        threads = int(cpu_data.get('Thread(s) per core', 1))
        total_vcpus = cores * threads
        
        utils.info(f"Detected: {cores} cores * {threads} threads = {total_vcpus} total vCPUs.")
        return cores, threads, total_vcpus
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        utils.warn(f"Could not auto-detect CPU topology: {e}. Using fallback values.")
        return 4, 1, 4 # Sensible fallback

def _get_dmidecode_info(dmi_type: str, field: str) -> str:
    """A helper to run dmidecode and parse a specific field."""
    try:
        cmd = ["sudo", "dmidecode", "-t", dmi_type]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith(field):
                return line.split(':', 1)[1].strip()
        return "Not Specified"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "Not Specified"

# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================

def main(cpu_vendor: str):
    """Main entry point for the auto libvirt XML creation module."""
    utils.info("Starting automatic libvirt XML creation...")

    # --- Step 1: Determine project root and firmware paths ---
    project_root = Path.cwd().resolve() # Gets the absolute path to the project directory
    ovmf_code_path = project_root / "output" / "firmware" / "OVMF_CODE.secboot.4m.qcow2"
    ovmf_vars_path = project_root / "output" / "firmware" / "OVMF_VARS.4m.qcow2"

    # --- Step 2: Safety check for firmware files ---
    if not ovmf_code_path.is_file() or not ovmf_vars_path.is_file():
        utils.fail(
            "Patched OVMF firmware files not found in the 'output/firmware' directory. "
            "Please run the 'EDK2 (Patched) Setup' module first to generate them."
        )
        return

    # --- Step 3: Get VM Name ---
    vm_name = utils.ask("Enter the name for the new virtual machine:")
    if not vm_name:
        utils.fail("VM name cannot be empty.")
        return

    # --- Step 4: Select and Load Template ---
    template_dir = utils.get_resource_path("xml/template")
    template_file = template_dir / f"template-{'intel' if 'GenuineIntel' in cpu_vendor else 'amd'}.xml"
    if not template_file.is_file():
        utils.fail(f"Required template file not found: {template_file}")
    utils.log(f"Using template: {template_file.name}")
    xml_content = template_file.read_text()

    # --- Step 5: Populate Template with All Data ---
    cores, threads, total_vcpus = _get_cpu_topology()
    
    replacements = {
        "@VM_NAME@": vm_name,
        "@TOTAL_NUMBER_OF_CORES@": str(total_vcpus),
        "@NUMBER_OF_CORES@": str(cores),
        "@NUMBER_OF_THREADS@": str(threads),
        "@OVMF_CODE_PATH@": str(ovmf_code_path),
        "@OVMF_VARS_PATH@": str(ovmf_vars_path),
    }
    for placeholder, value in replacements.items():
        xml_content = xml_content.replace(placeholder, value)

    # --- Step 6: Define the VM using the populated template ---
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".xml") as temp_xml:
        temp_xml.write(xml_content)
        temp_xml_path = temp_xml.name
    
    try:
        utils.info(f"Defining VM '{vm_name}' with libvirt...")
        utils.run_with_spinner(["sudo", "virsh", "define", temp_xml_path], cwd=Path.cwd())
    except subprocess.CalledProcessError:
        utils.fail(f"Failed to define the VM with virsh. The generated XML may be invalid. Check the log.")
    finally:
        os.remove(temp_xml_path) # Clean up the temporary file

    # --- Step 7: Inject SMBIOS data using virt-xml ---
    utils.info("Gathering SMBIOS data from host to spoof...")
    cpu_manufacturer = _get_dmidecode_info("processor", "Manufacturer")
    cpu_version = _get_dmidecode_info("processor", "Version")
    mem_manufacturer = _get_dmidecode_info("memory", "Manufacturer")
    mem_part_number = _get_dmidecode_info("memory", "Part Number")
    
    # virt-xml requires a single string for qemu-commandline args
    qemu_args = (
        f"-smbios type=0,uefi='true' "
        f"-smbios type=1,serial='To be filled by O.E.M.',uuid='{uuid.uuid4()}' "
        f"-smbios type=2,serial='To be filled by O.E.M.' "
        f"-smbios type=3,serial='To be filled by O.E.M.' "
        f"-smbios type=4,manufacturer='{cpu_manufacturer}',version='{cpu_version}' "
        f"-smbios type=17,manufacturer='{mem_manufacturer}',part='{mem_part_number}'"
    )

    try:
        utils.log("Injecting SMBIOS data via virt-xml...")
        utils.run_with_spinner(
            ["sudo", "virt-xml", vm_name, "--edit", "--qemu-commandline", qemu_args],
            cwd=Path.cwd()
        )
    except subprocess.CalledProcessError:
        utils.error("Failed to inject SMBIOS data. This is an optional step and can be ignored.")

    # --- Step 8: Set a random MAC address ---
    mac_address = utils.generate_random_mac()
    utils.info(f"Setting a random MAC address: {mac_address}")
    try:
        utils.run_with_spinner(
            ["sudo", "virt-xml", vm_name, "--edit", "--network", f"mac={mac_address}"],
            cwd=Path.cwd()
        )
    except subprocess.CalledProcessError:
        utils.error("Failed to set MAC address. This can be set manually in virt-manager.")
        
    utils.info(f"VM '{vm_name}' has been created successfully!")
    utils.warn("Further customization (e.g., adding disks, USB devices) should be done via virt-manager or by manually editing the XML.")