"""
Module for the automatic creation of a libvirt XML virtual machine definition.

This module automates the process of:
1. Loading a vendor-specific template (Intel/AMD).
2. Detecting host CPU topology.
3. Populating the template with VM name, CPU details, and paths to patched firmware.
4. Defining the VM with virsh.
5. Injecting spoofed SMBIOS data and a random MAC address using virt-xml.
"""

import json
import tempfile
import uuid
from pathlib import Path

# Import our custom utility functions
import utils
from config import paths, core


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def _get_cpu_topology() -> (int, int, int):
    """
    Gets host CPU core, thread, and total vCPU counts by parsing 'lscpu --json'.
    Returns a tuple of (cores, threads, total_vcpus).
    """
    utils.log("Detecting host CPU topology...")
    try:
        result = utils.run_command(
            ["lscpu", "--json"], cwd=Path.cwd(), capture_output=True
        )
        data = json.loads(result.stdout)
        # The JSON output is a list of dicts, find the relevant fields
        cpu_data = {item["field"].strip(":"): item["data"] for item in data["lscpu"]}

        cores = int(cpu_data.get("Core(s) per socket", 1))
        threads = int(cpu_data.get("Thread(s) per core", 1))
        # Sockets are not considered for a typical desktop passthrough setup
        total_vcpus = cores * threads

        utils.info(f"Detected: {cores} cores, {threads} threads/core ({total_vcpus} total vCPUs).")
        return cores, threads, total_vcpus
    except (utils.CommandExecutionError, json.JSONDecodeError, KeyError) as e:
        utils.warn(f"Could not auto-detect CPU topology: {e}. Using fallback values.")
        return 4, 1, 4  # Sensible fallback


def _get_dmidecode_info(dmi_type: str, field: str) -> str:
    """A helper to run dmidecode and parse a specific field."""
    try:
        cmd = ["sudo", "dmidecode", "-t", dmi_type, "-s", field]
        result = utils.run_command(cmd, Path.cwd(), capture_output=True, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
    except utils.CommandExecutionError:
        # This handles cases where dmidecode is not installed
        pass
    return "To be filled by O.E.M."


# ==============================================================================
# MAIN CLASS
# ==============================================================================

class XMLGenerator:
    """Orchestrates the creation and definition of a libvirt VM."""

    def __init__(self, cpu_vendor: str):
        """
        Initializes the generator with system-specific information.

        Args:
            cpu_vendor: The CPU vendor string (e.g., 'GenuineIntel').
        """
        self.cpu_vendor = cpu_vendor
        self.project_root = Path.cwd().resolve()
        self.firmware_dir = self.project_root / core.OUTPUT_DIR / "firmware"
        self.template_dir = paths.XML_TEMPLATE_DIR

        # Paths to required firmware files
        self.ovmf_code_path = self.firmware_dir / "OVMF_CODE.secboot.4m.qcow2"
        self.ovmf_vars_path = self.firmware_dir / "OVMF_VARS.4m.qcow2"

        # State variables to be populated
        self.vm_name: str = ""
        self.xml_content: str = ""

    def _check_prerequisites(self):
        """Verify that the required patched OVMF files exist."""
        utils.log("Checking for patched OVMF firmware files...")
        if not self.ovmf_code_path.is_file() or not self.ovmf_vars_path.is_file():
            utils.fail(
                "Patched OVMF firmware not found in 'output/firmware/'.\n"
                "Please run the 'EDK2 (Patched) Setup' module first to generate them."
            )
        utils.log("OVMF files found.")

    def _gather_user_input(self):
        """Prompts the user for required information, like the VM name."""
        name = utils.ask("Enter the name for the new virtual machine:")
        if not name or not name.strip():
            utils.fail("VM name cannot be empty.")
        self.vm_name = name.strip()

    def _load_and_populate_template(self):
        """Loads the correct XML template and fills it with detected data."""
        vendor_short = "intel" if "GenuineIntel" in self.cpu_vendor else "amd"
        template_file = self.template_dir / f"template-{vendor_short}.xml"
        if not template_file.is_file():
            utils.fail(f"Required template file not found: {template_file}")

        utils.log(f"Loading template: {template_file.name}")
        self.xml_content = template_file.read_text()

        cores, threads, total_vcpus = _get_cpu_topology()

        replacements = {
            "@VM_NAME@": self.vm_name,
            "@TOTAL_NUMBER_OF_CORES@": str(total_vcpus),
            "@NUMBER_OF_CORES@": str(cores),
            "@NUMBER_OF_THREADS@": str(threads),
            "@OVMF_CODE_PATH@": str(self.ovmf_code_path),
            "@OVMF_VARS_PATH@": str(self.ovmf_vars_path),
        }

        for placeholder, value in replacements.items():
            self.xml_content = self.xml_content.replace(placeholder, value)
        utils.log("XML template populated successfully.")

    def _define_vm(self):
        """Writes the XML to a temp file and defines the VM with virsh."""
        try:
            with tempfile.NamedTemporaryFile(mode="w+", delete=True, suffix=".xml") as temp_xml:
                temp_xml.write(self.xml_content)
                temp_xml.flush()  # Ensure content is written to disk

                utils.info(f"Defining VM '{self.vm_name}' with libvirt...")
                utils.run_command(
                    ["sudo", "virsh", "define", temp_xml.name],
                    cwd=Path.cwd(),
                    show_spinner=True
                )
        except utils.CommandExecutionError:
            utils.fail("Failed to define the VM. Check log for virsh errors.")

    def _inject_smbios(self):
        """Gathers host SMBIOS data and injects it into the VM via virt-xml."""
        utils.info("Gathering and injecting host SMBIOS data...")

        # Build the QEMU command-line arguments for SMBIOS spoofing
        qemu_args = (
            f"-smbios type=0,uefi=on "
            f"-smbios type=1,manufacturer='{_get_dmidecode_info('system', 'manufacturer')}',"
            f"product='{_get_dmidecode_info('system', 'product-name')}',"
            f"version='{_get_dmidecode_info('system', 'version')}',"
            f"serial='{_get_dmidecode_info('system', 'serial-number')}',"
            f"uuid='{uuid.uuid4()}' "  # Generate a new UUID for the VM
            f"-smbios type=2,manufacturer='{_get_dmidecode_info('baseboard', 'manufacturer')}',"
            f"product='{_get_dmidecode_info('baseboard', 'product-name')}',"
            f"version='{_get_dmidecode_info('baseboard', 'version')}',"
            f"serial='{_get_dmidecode_info('baseboard', 'serial-number')}' "
            f"-smbios type=3,manufacturer='{_get_dmidecode_info('chassis', 'manufacturer')}',"
            f"serial='{_get_dmidecode_info('chassis', 'serial-number')}',"
            f"asset='{_get_dmidecode_info('chassis', 'asset-tag')}' "
        )

        try:
            utils.run_command(
                ["sudo", "virt-xml", self.vm_name, "--edit", "--qemu-commandline", qemu_args],
                cwd=Path.cwd()
            )
            utils.log("Successfully injected SMBIOS data.")
        except utils.CommandExecutionError:
            utils.warn("Failed to inject SMBIOS data. This is an optional but recommended step.")

    def _set_mac_address(self):
        """Generates and sets a random MAC address for the VM's network interface."""
        mac = utils.generate_random_mac()
        utils.info(f"Setting a random MAC address: {mac}")
        try:
            utils.run_command(
                ["sudo", "virt-xml", self.vm_name, "--edit", "--network", f"mac={mac}"],
                cwd=Path.cwd()
            )
        except utils.CommandExecutionError:
            utils.warn("Failed to set MAC address. This can be set manually in virt-manager.")

    def run(self):
        """Executes the full workflow to create the VM."""
        self._check_prerequisites()
        self._gather_user_input()
        self._load_and_populate_template()
        self._define_vm()
        self._inject_smbios()
        self._set_mac_address()

        utils.log(f"VM '{self.vm_name}' has been created successfully!")
        utils.warn(
            "Further customization (e.g., adding disks, USB devices) should be "
            "done via virt-manager or by manually editing the XML."
        )


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================

def main(cpu_vendor: str):
    """
    Main entry point for the auto libvirt XML creation module.

    Args:
        cpu_vendor: The CPU vendor string from the host system.
    """

    utils.info("Starting automatic libvirt XML creation...")
    generator = XMLGenerator(cpu_vendor)
    generator.run()