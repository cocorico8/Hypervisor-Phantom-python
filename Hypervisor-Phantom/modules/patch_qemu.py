"""
Module for building and installing a custom-patched version of QEMU.

This module automates the process of:
1. Downloading and verifying the official QEMU source code.
2. Applying custom patches for anti-cheat compatibility and other fixes.
3. Spoofing various hardware identifiers within the source code to mimic real hardware.
4. Compiling and installing the patched QEMU binaries.
"""

import getpass
import os
import random
import re
import shutil
import string
import subprocess
from pathlib import Path

import requests
import utils
from config import versions, urls, packages, paths, core, spoofing

# ==============================================================================
# MAIN CLASS
# ==============================================================================

class QEMUBuilder:
    """Orchestrates the download, patch, spoof, and build process for QEMU."""

    # --- Configuration Constants ---
    GPG_KEY = "CEACC9E15534EBABB82D3FA03353C9CEF108B584"

    def __init__(self, distro: str, cpu_vendor: str):
        self.distro = distro
        self.cpu_vendor = cpu_vendor

        self.src_dir = core.SOURCE_DIR
        self.qemu_dir_name = f"qemu-{versions.QEMU}"
        self.qemu_source_path = core.SOURCE_DIR / self.qemu_dir_name
        self.qemu_archive = core.SOURCE_DIR / urls.QEMU_ARCHIVE
        self.qemu_sig = core.SOURCE_DIR / f"{urls.QEMU_ARCHIVE}.sig"

        self.patch_dir = paths.QEMU_PATCH_DIR
        self.fake_battery_dsl_path = self.patch_dir / "fake_battery.dsl"

    def _install_dependencies(self):
        """Install all packages required for building QEMU."""
        qemu_packages = packages.QEMU_BUILD.get(self.distro)
        if not qemu_packages:
            utils.fail(f"QEMU patching is not supported for distro: {self.distro}")
        utils.install_required_packages("QEMU Build", qemu_packages, self.distro)

    def _acquire_source(self):
        """Downloads, verifies, and extracts the QEMU source code."""
        self.src_dir.mkdir(exist_ok=True)
        if self.qemu_source_path.exists():
            if not utils.yes_or_no("QEMU source directory exists. Re-download and overwrite?"):
                utils.info("Skipping download and using existing source.")
                return
            shutil.rmtree(self.qemu_source_path)

        # Download source and signature
        utils.info(f"Downloading QEMU {versions.QEMU}...")
        try:
            # CORRECTED: Always use the full Path objects for the destination.
            # This ensures both files are saved inside the 'src/' directory.
            for url, dest_path in [(urls.QEMU_DOWNLOAD, self.qemu_archive), (urls.QEMU_SIGNATURE, self.qemu_sig)]:
                response = requests.get(url, stream=True, timeout=30)
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
        except requests.RequestException as e:
            utils.fail(f"Failed to download QEMU files: {e}")

        # Verify GPG signature
        utils.info("Verifying GPG signature...")
        utils.run_command(
            ["gpg", "--keyserver", "keys.openpgp.org", "--recv-keys", self.GPG_KEY],
            self.src_dir,
            check=False ,
            show_spinner=True
        )
        try:
            # Use .name to pass only the filenames since cwd is 'src/'
            utils.run_command(
                ["gpg", "--verify", self.qemu_sig.name, self.qemu_archive.name],
                self.src_dir
            )
            utils.log("Signature verification successful.")
        except utils.CommandExecutionError:
            if not utils.yes_or_no("GPG signature verification FAILED! The source may be compromised. Continue anyway?"):
                utils.fail("Aborting due to failed signature verification.")

        # Extract archive
        utils.info("Extracting QEMU source archive...")
        try:
            # Use the system's `tar` command for extraction so we can show a spinner.
            # '-xf' means eXtract File.
            # We use .name because the CWD is already self.src_dir.
            utils.run_command(
                ["tar", "-xf", self.qemu_archive.name],
                cwd=self.src_dir,
                show_spinner=True
            )
            utils.info("QEMU source successfully extracted.")
        except utils.CommandExecutionError as e:
            utils.fail(f"Failed to extract QEMU archive using 'tar': {e}")
        except Exception as e:
            utils.fail(f"An unexpected error occurred during extraction: {e}")

    def _apply_patches(self):
        """Applies all custom patches to the QEMU source code."""
        utils.info("Applying custom patches...")
        short_vendor = "amd" if "AuthenticAMD" in self.cpu_vendor else "intel"

        # Patches are a list of (filename, is_mandatory) tuples
        patches_to_apply = [
            (self.patch_dir / f"libnfs-qemu-{versions.QEMU}.patch", False),
            (self.patch_dir / f"{short_vendor}-qemu-{versions.QEMU}.patch", True),
        ]

        for patch_file, is_mandatory in patches_to_apply:
            if patch_file.exists():
                utils.log(f"Applying patch: {patch_file.name}")
                try:
                    with open(patch_file, "r", encoding="utf-8") as f:
                        utils.run_command(
                            ["patch", "-p1"], 
                            self.qemu_source_path, 
                            stdin=f
                        )
                except utils.CommandExecutionError:
                    utils.fail(f"Failed to apply patch: {patch_file.name}")
            elif is_mandatory:
                utils.fail(f"Required patch file not found: {patch_file}")


    @staticmethod
    def _replace_in_file(file_path: Path, pattern: str, replacement: str):
        """Safely performs a regex replacement in a given file."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            new_content = re.sub(pattern, replacement, content)
            if content != new_content:
                file_path.write_text(new_content, encoding="utf-8")
                # The log message needs to be generic since it's a static helper
                utils.log(f"Modified '{file_path.name}'")
        except IOError as e:
            utils.warn(f"Could not modify {file_path}: {e}")

    def _spoof_identifiers(self):
        """Orchestrates all spoofing operations within the QEMU source."""
        utils.info("Spoofing hardcoded hardware identifiers...")
        self._spoof_usb()
        self._spoof_drives()
        self._spoof_acpi()
        self._patch_smbios_data()

    def _spoof_usb(self):
        """Spoofs USB device serial numbers."""
        usb_dir = self.qemu_source_path / "hw" / "usb"
        for c_file in usb_dir.glob("*.c"):
            try:
                content = c_file.read_text(encoding="utf-8", errors="ignore")
                # Pattern to find string definitions like ["STRING_SERIALNUMBER"] = "..."
                pattern = r'(\[\s*STR_SERIAL(?:NUMBER|MOUSE|TABLET|KEYBOARD|COMPAT)?\s*\]\s*=\s*")[^"]*(")'

                # The lambda function generates a new random serial for each match
                def repl(match):
                    random_serial = "".join(random.choices(string.ascii_uppercase + string.digits, k=12))
                    return f'{match.group(1)}{random_serial}{match.group(2)}'

                new_content = re.sub(pattern, repl, content)
                if content != new_content:
                    c_file.write_text(new_content, encoding="utf-8")
                    utils.log(f"Spoofed USB serials in '{c_file.name}'")
            except IOError as e:
                utils.warn(f"Could not process {c_file}: {e}")
    
    def _spoof_drives(self):
        """
        Spoofs hard drive and CD-ROM model numbers using data from config.
        """
        
        # Target: hw/ide/core.c for standard IDE drives
        ide_core_c = self.qemu_source_path / "hw/ide/core.c"
        
        # Replaces the default CD/DVD drive model
        self.__class__._replace_in_file(
            ide_core_c,
            r'"HL-DT-ST BD-RE WH16NS60"',
            f'"{random.choice(spoofing.IDE_CD_MODELS)}"',
        )
        
        # Replaces the default CompactFlash / Microdrive model
        self.__class__._replace_in_file(
            ide_core_c,
            r'"Hitachi HMS360404D5CF00"',
            f'"{random.choice(spoofing.IDE_CFATA_MODELS)}"',
        )
        
        # Replaces the default IDE hard disk model
        self.__class__._replace_in_file(
            ide_core_c,
            r'"Samsung SSD 980 500GB"',
            f'"{random.choice(spoofing.DEFAULT_DRIVE_MODELS)}"',
        )
        
        # Target: hw/nvme/ctrl.c for NVMe drives
        nvme_ctrl_c = self.qemu_source_path / "hw/nvme/ctrl.c"

        # Replaces the default NVMe controller model
        self.__class__._replace_in_file(
            nvme_ctrl_c,
            r'"NVMe Ctrl"',
            f'"{random.choice(spoofing.DEFAULT_DRIVE_MODELS)}"',
        )

        utils.log("Spoofed drive models and serials.")

    def _spoof_acpi(self):
        """Spoofs ACPI OEM IDs and generates a fake battery table if needed."""
        oem_pairs = [("DELL  ", "Dell Inc"), ("ASUS  ", "Notebook"), ("LENOVO", "TC-O5Z ")]
        vendor_map = {"AuthenticAMD": ("ALASKA", "A M I "), "GenuineIntel": ("INTEL ", "U Rvp  ")}
        if self.cpu_vendor in vendor_map:
            oem_pairs.append(vendor_map[self.cpu_vendor])

        appname6, appname8 = random.choice(oem_pairs)
        acpi_header = self.qemu_source_path / "include/hw/acpi/aml-build.h"
        self._replace_in_file(acpi_header, r'(#define ACPI_BUILD_APPNAME6\s*").*"', rf'\1{appname6}"')
        self._replace_in_file(acpi_header, r'(#define ACPI_BUILD_APPNAME8\s*").*"', rf'\1{appname8}"')

        # Fake battery generation for laptops
        try:
            chassis_type = utils.run_command(
                ["sudo", "dmidecode", "-s", "chassis-type"], Path.cwd(), capture_output=True
            ).stdout.strip()

            if "Notebook" in chassis_type or "Laptop" in chassis_type:
                utils.warn("Host is a notebook. Generating a fake battery ACPI table...")
                home_dir = Path.home()
                dsl_dest = home_dir / "fake_battery.dsl"
                aml_dest = home_dir / "fake_battery.aml"

                template = self.fake_battery_dsl_path.read_text()
                template = template.replace("BOCHS", appname6.strip())
                template = template.replace("BXPCSSDT", appname8.strip())
                dsl_dest.write_text(template)

                utils.info("Compiling ACPI table with 'iasl'...")
                utils.run_command(["iasl", "-tc", str(dsl_dest)], home_dir, show_spinner=True)
                utils.warn(f"Fake battery table created at '{aml_dest}'. Passthrough this in your VM config.")
        except (utils.CommandExecutionError, FileNotFoundError):
            utils.warn("Could not check chassis type or generate fake battery. This is optional.")

    @staticmethod
    def _get_dmi_hex_data(dmi_path: Path) -> str | None:
        """
        Safely reads raw binary DMI data from a sysfs file using sudo.

        Returns:
            An uppercase hexadecimal string of the data, or None on failure.
        """
        if not dmi_path.exists():
            utils.warn(f"DMI data file not found at {dmi_path}.")
            return None
        try:
            # We use subprocess.run directly here to get raw bytes
            result = subprocess.run(
                ["sudo", "cat", str(dmi_path)],
                check=True,
                capture_output=True,
            )
            return result.stdout.hex().upper()
        except (subprocess.CalledProcessError, FileNotFoundError):
            utils.error(f"Failed to read DMI data from {dmi_path}.")
            return None

    def _patch_smbios_data(self):
        """
        Patches host SMBIOS Processor (Type 4) data into the QEMU source.
        This reads the host's CPU information and injects it into smbios.c
        to make the VM's reported CPU match the host's.
        """
        utils.info("Patching SMBIOS Type 4 (Processor) data...")
        smbios_file = self.qemu_source_path / "hw/smbios/smbios.c"
        dmi_path = Path("/sys/firmware/dmi/entries/4-0/raw")

        # Ensure the kernel module that provides DMI data is loaded
        if not dmi_path.exists():
            utils.info("DMI sysfs entry not found, attempting to load 'dmi-sysfs' module...")
            utils.run_command(["sudo", "modprobe", "dmi-sysfs"], Path.cwd(), check=False)

        data = self._get_dmi_hex_data(dmi_path)
        if not data:
            utils.warn("Could not read host DMI Type 4 data. Skipping SMBIOS patch.")
            return

        # --- Data Parsing ---
        # Helper to parse little-endian values from the hex string
        def parse_le(offset: int, length: int) -> str:
            """Slices, reverses, and joins bytes from the hex string."""
            start, end = offset * 2, (offset + length) * 2
            sub = data[start:end]
            if not sub: return "00"
            # Split into bytes (e.g., "112233" -> ["11", "22", "33"])
            byte_chunks = [sub[i: i + 2] for i in range(0, len(sub), 2)]
            return "".join(reversed(byte_chunks))

        # Offsets are based on the SMBIOS Type 4 specification
        # All values are extracted as hex strings
        processor_family = data[12 * 2: 13 * 2]
        voltage = data[34 * 2: 35 * 2]
        external_clock = parse_le(36, 2)
        l1_cache = parse_le(52, 2)
        l2_cache = parse_le(54, 2)
        l3_cache = parse_le(56, 2)
        upgrade = data[50 * 2: 51 * 2]
        characteristics = parse_le(76, 4)
        family2 = parse_le(80, 2)

        # --- Source Code Replacement ---
        try:
            content = smbios_file.read_text(encoding="utf-8")

            # A dictionary of regex patterns and their replacements.
            # This is much cleaner and safer than repeated file writes.
            replacements = {
                r"(t->processor_family\s*=\s*)0x[0-9A-Fa-f]+;": rf"\g<1>0x{processor_family};",
                r"(t->voltage\s*=\s*)0x[0-9A-Fa-f]+;": rf"\g<1>0x{voltage};",
                r"(t->processor_upgrade\s*=\s*)0x[0-9A-Fa-f]+;": rf"\g<1>0x{upgrade};",
                r"(t->external_clock\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{external_clock}\g<2>",
                r"(t->l1_cache_handle\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{l1_cache}\g<2>",
                r"(t->l2_cache_handle\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{l2_cache}\g<2>",
                r"(t->l3_cache_handle\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{l3_cache}\g<2>",
                r"(t->processor_family2\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{family2}\g<2>",
                r"(t->processor_characteristics\s*=\s*cpu_to_le32\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{characteristics}\g<2>",
            }

            modified_content = content
            for pattern, replacement in replacements.items():
                modified_content = re.sub(pattern, replacement, modified_content, flags=re.MULTILINE)

            if content != modified_content:
                smbios_file.write_text(modified_content, encoding="utf-8")
                utils.log("Successfully patched SMBIOS data into 'hw/smbios/smbios.c'.")
            else:
                utils.warn(
                    "Could not find SMBIOS patterns to replace in source file. It may be an unsupported QEMU version.")

        except (IOError, re.error) as e:
            utils.error(f"Failed to patch smbios.c: {e}")

    def _compile(self):
        """Configures, compiles, and installs the patched QEMU."""
        original_user = os.environ.get("SUDO_USER", getpass.getuser())
        if not original_user or original_user == 'root':
            utils.fail("Cannot determine original user for safe build. Please run via sudo.")

        utils.info("Configuring QEMU build environment...")
        configure_cmd = [
            "sudo", "-u", original_user, "./configure",
            "--target-list=x86_64-softmmu", "--enable-kvm", "--enable-spice",
            "--enable-libusb", "--enable-usb-redir",
        ]
        utils.run_command(configure_cmd, self.qemu_source_path, show_spinner=True)

        utils.info(f"Building QEMU with {os.cpu_count()} threads...")
        make_cmd = ["sudo", "-u", original_user, "make", f"-j{os.cpu_count()}"]
        utils.run_command(make_cmd, self.qemu_source_path, show_spinner=True)

        if utils.yes_or_no("Build successful. Install QEMU to /usr/local/bin?"):
            utils.info("Installing QEMU with root privileges...")
            utils.run_command(["sudo", "make", "install"], self.qemu_source_path, show_spinner=True)
            utils.info("QEMU installed successfully!")
        else:
            utils.info("Installation skipped.")

    def _cleanup(self):
        """Removes downloaded source files and build directory."""
        if utils.yes_or_no("Remove QEMU source directory and archives?"):
            utils.info("Cleaning up source files...")
            if self.qemu_source_path.exists():
                shutil.rmtree(self.qemu_source_path)
            for f in [self.qemu_archive, self.qemu_sig]:
                if f.exists():
                    f.unlink()
            utils.log("Cleanup complete.")

    def run(self):
        """Executes the full QEMU build workflow."""
        self._install_dependencies()
        self._acquire_source()
        self._apply_patches()
        self._spoof_identifiers()

        if utils.yes_or_no("Configuration and patching complete. Proceed with build?"):
            self._compile()

        self._cleanup()


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================

def main(distro: str, cpu_vendor: str):
    """
    Main entry point for the QEMU patcher module.

    Args:
        distro: The name of the detected Linux distribution.
        cpu_vendor: The CPU vendor string from the host system.
    """
    utils.info("Starting patched QEMU build process...")
    builder = QEMUBuilder(distro, cpu_vendor)
    builder.run()
    utils.log("QEMU patching process finished.")