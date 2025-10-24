"""
Module for creating a patched EDK2/OVMF firmware and managing Secure Boot keys.

This module provides two main functionalities:
1.  Building a custom OVMF firmware with CPU-specific patches and a custom boot logo.
2.  Injecting Microsoft Secure Boot certificates into an existing VM's VARS file.
"""

import json
import shutil
import struct
import tempfile
from pathlib import Path
import os

import requests
import utils
from config import versions, urls, packages, paths, core

# ==============================================================================
# OVMF BUILDER CLASS
# ==============================================================================
class OVMFBuilder:
    """Orchestrates the download, patch, and compilation of EDK2/OVMF."""

    def __init__(self, distro: str, cpu_vendor: str):
        self.distro = distro
        self.cpu_vendor = cpu_vendor

        self.src_dir = core.SOURCE_DIR
        self.edk2_path = self.src_dir / versions.EDK2_TAG
        self.patch_dir = paths.OVMF_PATCH_DIR
        self.ovmf_patch_name = f"{'amd' if 'AMD' in cpu_vendor else 'intel'}-{versions.EDK2_TAG}.patch"

    @staticmethod
    def _validate_bmp(bmp_path: Path) -> bool:
        """
        Validates that a file is a standard, uncompressed BMP required by EDK2.
        Checks for magic number, compression, and supported bit depth.
        """
        try:
            with open(bmp_path, "rb") as f:
                # Read the first 34 bytes of the header, which contains all the
                # fields we need for validation.
                header = f.read(34)
                if len(header) < 34:
                    utils.error("Invalid BMP: File is too small to contain a valid header.")
                    return False

                # Unpack multiple values from the header at once.
                # '<' = little-endian byte order
                # 2s = 2-byte char array (for 'BM')
                # 16x = skip 16 bytes (size, reserved, offset, header size)
                # I = 4-byte unsigned int (Width)
                # I = 4-byte unsigned int (Height)
                # H = 2-byte unsigned short (Color Planes)
                # H = 2-byte unsigned short (Bit Depth)
                # I = 4-byte unsigned int (Compression Method)
                magic, width, height, planes, bit_depth, compression = struct.unpack(
                    "<2s16xIIHHI", header
                )

                if magic != b"BM":
                    utils.error(f"Invalid BMP: Incorrect magic number '{magic.decode()}'. Expected 'BM'.")
                    return False

                if compression != 0:
                    utils.error(f"Invalid BMP: Unsupported compression type '{compression}'. Must be 0 (BI_RGB).")
                    return False

                # CRITICAL: Check for supported bit depths.
                supported_bpp = [1, 4, 8, 24]
                if bit_depth not in supported_bpp:
                    utils.error(f"Invalid BMP: Unsupported bit depth of {bit_depth}-bit. Must be one of {supported_bpp}.")
                    return False

            # Provide complete and useful feedback to the user.
            utils.info(
                f"BMP validation successful: {width}x{height}, {bit_depth}-bit, Uncompressed."
            )
            return True

        except (IOError, struct.error) as e:
            utils.error(f"Failed to read or validate BMP file: {e}")
            return False

    def _install_dependencies(self):
        """Installs all packages required for building EDK2."""
        ovmf_packages = packages.OVMF_BUILD.get(self.distro)
        if not ovmf_packages:
            utils.fail(f"OVMF building is not supported for distro: {self.distro}")
        utils.install_required_packages("EDK2 Build", ovmf_packages, self.distro)

    def _acquire_source(self):
        """Clones the EDK2 repo, checks out the correct tag, and patches it."""
        self.src_dir.mkdir(exist_ok=True)
        if self.edk2_path.exists():
            if not utils.yes_or_no("EDK2 source directory exists. Re-download and overwrite?"):
                utils.info("Using existing source directory.")
                return
            shutil.rmtree(self.edk2_path)

        utils.info(f"Cloning EDK2 tag '{versions.EDK2_TAG}'...")
        cmd = ["git", "clone", "--depth=1", "--branch", versions.EDK2_TAG, urls.EDK2_GIT, str(self.edk2_path)]
        utils.run_command(cmd, Path.cwd(), show_spinner=True)

        utils.info("Initializing submodules...")
        utils.run_command(["git", "submodule", "update", "--init"], self.edk2_path, show_spinner=True)

    def _apply_patch(self):
        """Applies the CPU-specific patch."""
        utils.info(f"Applying patch '{self.ovmf_patch_name}'...")
        patch_file = self.patch_dir / self.ovmf_patch_name
        if not patch_file.exists():
            utils.fail(f"Required patch file not found: {patch_file}")

        try:
            with open(patch_file, "r") as f:
                utils.run_command(["git", "apply", "-"], self.edk2_path, stdin=f)
        except utils.CommandExecutionError:
            utils.fail("Failed to apply OVMF patch. The patch may be incompatible with the current EDK2 tag.")

    def _configure_boot_logo(self):
        """Guides the user through selecting a custom boot logo."""
        logo_dest = self.edk2_path / "MdeModulePkg" / "Logo" / "Logo.bmp"

        while True:
            print(f"\n  {utils.Fore.YELLOW}[1] Apply host's default boot logo (if available)")
            print(f"  {utils.Fore.YELLOW}[2] Apply a custom BMP image")
            print(f"  {utils.Fore.YELLOW}[3] Skip / Use EDK2 default logo")
            choice = utils.quick_prompt("\n  Choose a BGRT boot logo for OVMF: ")

            if choice == "1":
                host_logo = Path("/sys/firmware/acpi/bgrt/image")
                if host_logo.exists():
                    shutil.copy(host_logo, logo_dest)
                    utils.info("Host's BMP logo copied successfully.")
                    return
                utils.error("Host BMP logo not found at /sys/firmware/acpi/bgrt/image.")
            elif choice == "2":
                custom_path_str = utils.ask("Enter the absolute path to your BMP image:")
                custom_path = Path(custom_path_str).expanduser()
                if custom_path.is_file() and self._validate_bmp(custom_path):
                    shutil.copy(custom_path, logo_dest)
                    utils.info("Custom BMP copied successfully.")
                    return
                utils.error("File does not exist or is not a valid uncompressed BMP. Please try again.")
            elif choice == "3":
                utils.info("Skipping custom logo.")
                return
            else:
                utils.error("Invalid choice.")

    def _compile(self):
        """Builds and compiles the patched OVMF source."""
        utils.info("Building BaseTools...")
        build_env = {**os.environ, "WORKSPACE": str(self.edk2_path.resolve())}
        utils.run_command(["make", "-C", "BaseTools"], self.edk2_path, show_spinner=True, env=build_env)

        utils.info("Compiling OVMF with Secure Boot and TPM flags...")
        build_script = f"""
        set -e
        source edksetup.sh
        build -a X64 -p OvmfPkg/OvmfPkgX64.dsc -b RELEASE \\
            -t GCC5 \\
            --define SECURE_BOOT_ENABLE=TRUE \\
            --define TPM_ENABLE=TRUE
        """
        utils.run_command(["bash", "-c", build_script], self.edk2_path, show_spinner=True, env=build_env)

    def _package_firmware(self):
        """Converts the compiled .fd files to the .qcow2 format."""
        utils.info("Converting compiled OVMF to .qcow2 format...")
        release_dir = self.edk2_path / "Build/OvmfX64/RELEASE_GCC5/FV"
        code_fd = release_dir / "OVMF_CODE.fd"
        vars_fd = release_dir / "OVMF_VARS.fd"

        if not code_fd.exists() or not vars_fd.exists():
            utils.fail(f"Compiled firmware not found in {release_dir}. Build may have failed.")

        output_dir = Path.cwd() / "output" / "firmware"
        output_dir.mkdir(parents=True, exist_ok=True)

        for src, name in [(code_fd, "OVMF_CODE.secboot.4m.qcow2"), (vars_fd, "OVMF_VARS.4m.qcow2")]:
            dest = output_dir / name
            cmd = ["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(src), str(dest)]
            utils.run_command(cmd, Path.cwd())

        utils.info(f"Firmware files created in: {output_dir}")

    def run(self):
        """Executes the full OVMF build workflow."""
        self._install_dependencies()
        self._acquire_source()
        self._apply_patch()
        self._configure_boot_logo()

        if utils.yes_or_no("Source is patched. Proceed with compilation?"):
            self._compile()
            self._package_firmware()

        if not utils.yes_or_no("Keep EDK2 source for faster re-patching?"):
            shutil.rmtree(self.edk2_path)
            utils.log("Cleaned up EDK2 source directory.")


# ==============================================================================
# SECURE BOOT CERTIFICATE INJECTOR CLASS
# ==============================================================================
class CertInjector:
    """Injects Microsoft Secure Boot certificates into a VM's VARS file."""

    # Certificate filenames and their download URLs
    CERT_URLS = {
        "ms_pk.der": f"{urls.MS_SB_BASE}/PreSignedObjects/PK/Certificate/WindowsOEMDevicesPK.der",
        "ms_kek1.der": f"{urls.MS_SB_BASE}/PreSignedObjects/KEK/Certificates/MicCorKEKCA2011_2011-06-24.der",
        "ms_kek2.der": f"{urls.MS_SB_BASE}/PreSignedObjects/KEK/Certificates/microsoft%20corporation%20kek%202k%20ca%202023.der",
        "ms_db1.der": f"{urls.MS_SB_BASE}/PreSignedObjects/DB/Certificates/MicCorUEFCA2011_2011-06-27.der",
        "ms_db2.der": f"{urls.MS_SB_BASE}/PreSignedObjects/DB/Certificates/MicWinProPCA2011_2011-10-19.der",
        "ms_db3.der": f"{urls.MS_SB_BASE}/PreSignedObjects/DB/Certificates/microsoft%20option%20rom%20uefi%20ca%202023.der",
        "ms_db4.der": f"{urls.MS_SB_BASE}/PreSignedObjects/DB/Certificates/microsoft%20uefi%20ca%202023.der",
        "ms_db5.der": f"{urls.MS_SB_BASE}/PreSignedObjects/DB/Certificates/windows%20uefi%20ca%202023.der",
        "dbx_update.bin": f"{urls.MS_SB_BASE}/PostSignedObjects/DBX/amd64/DBXUpdate.bin",
    }

    # Standard Microsoft Owner GUID for Secure Boot variables
    MS_OWNER_GUID = "77fa9abd-0359-4d32-bd60-28f4e78f784b"

    def __init__(self):
        """Initializes the certificate injector."""
        pass  # No initial state needed

    @staticmethod
    def _generate_defaults_json(temp_dir: Path) -> Path | None:
        """
        Reads host EFI variables for Secure Boot and creates a defaults.json file.
        This helps virt-fw-vars preserve any vendor-specific defaults.
        """
        utils.info("Generating defaults.json from host EFI variables...")
        efivar_dir = Path("/sys/firmware/efi/efivars")
        if not efivar_dir.is_dir():
            utils.warn("Host EFI variables not found. Skipping defaults.json generation.")
            return None

        vars_to_find = {
            "dbDefault": "8be4df61-93ca-11d2-aa0d-00e098032b8c",
            "KEKDefault": "8be4df61-93ca-11d2-aa0d-00e098032b8c",
            "PKDefault": "8be4df61-93ca-11d2-aa0d-00e098032b8c",
        }

        json_data = {"version": 2, "variables": []}
        for name, guid in vars_to_find.items():
            filepath = efivar_dir / f"{name}-{guid}"
            if filepath.is_file():
                try:
                    # The first 4 bytes are attributes, the rest is data
                    raw_data = filepath.read_bytes()
                    attributes = struct.unpack("<I", raw_data[:4])[0]
                    data_hex = raw_data[4:].hex()
                    json_data["variables"].append({
                        "name": name, "guid": guid, "attr": attributes, "data": data_hex
                    })
                    utils.log(f"Processed host EFI variable: {name}")
                except (IOError, struct.error) as e:
                    utils.error(f"Failed to read or parse EFI variable {name}: {e}")

        if not json_data["variables"]:
            utils.warn("No default Secure Boot variables found on host. Generated JSON will be empty.")

        defaults_json_path = temp_dir / "defaults.json"
        try:
            defaults_json_path.write_text(json.dumps(json_data, indent=4))
            utils.log(f"Successfully created defaults.json at {defaults_json_path}")
            return defaults_json_path
        except IOError as e:
            utils.error(f"Failed to write defaults.json: {e}")
            return None

    @staticmethod
    def _select_target_vm() -> tuple[str, Path] | None:
        """Lists virsh domains and prompts the user to select one."""
        utils.info("Searching for virtual machines...")
        try:
            result = utils.run_command(
                ["sudo", "virsh", "list", "--all", "--name"],
                Path.cwd(),
                capture_output=True
            )
            vm_list = [vm for vm in result.stdout.strip().splitlines() if vm]
            if not vm_list:
                utils.error("No virtual machines found.")
                return None
        except utils.CommandExecutionError:
            utils.fail("Could not list VMs. Is libvirt running and are you in the 'libvirt' group?")

        utils.info("Please select a VM to inject Microsoft Secure Boot keys into:")
        for i, vm_name in enumerate(vm_list, 1):
            print(f"  {utils.Fore.YELLOW}[{i}] {vm_name}")
        print(f"\n  {utils.Fore.RED}[0] Cancel")

        while True:
            try:
                choice = int(utils.ask("Enter your choice:"))
                if 0 <= choice <= len(vm_list):
                    break
                utils.error("Invalid choice.")
            except ValueError:
                utils.error("Please enter a number.")

        if choice == 0:
            utils.info("Operation cancelled.")
            return None

        selected_vm_name = vm_list[choice - 1]
        original_vars_file = paths.NVRAM_DIR / f"{selected_vm_name}_VARS.qcow2"

        if not original_vars_file.exists():
            utils.fail(f"VARS file not found for '{selected_vm_name}' at {original_vars_file}.")

        utils.log(f"Selected VM: {selected_vm_name}")
        return selected_vm_name, original_vars_file

    def _download_certs(self, temp_dir: Path):
        """Downloads all required MS certificates to the temporary directory."""
        utils.info("Downloading Microsoft Secure Boot certificates...")
        try:
            for filename, url in self.CERT_URLS.items():
                res = requests.get(url, timeout=15)
                res.raise_for_status()
                (temp_dir / filename).write_bytes(res.content)
                utils.log(f"Downloaded {filename}")
        except requests.RequestException as e:
            utils.fail(f"Failed to download certificate: {e}")

    def _run_injection(self, vm_name: str, base_vars: Path, temp_dir: Path):
        """Constructs and runs the virt-fw-vars command to inject the certs."""
        secure_vars_file = paths.NVRAM_DIR / f"{vm_name}_VARS_SECURE.qcow2"
        utils.info(f"Preparing to create new secure VARS file at: {secure_vars_file}")

        cmd = [
            "sudo", "virt-fw-vars",
            "--input", str(base_vars),
            "--output", str(secure_vars_file),
            "--secure-boot",
            # Platform Key (PK)
            "--set-pk", self.MS_OWNER_GUID, str(temp_dir / "ms_pk.der"),
            # Key Exchange Key (KEK)
            "--add-kek", self.MS_OWNER_GUID, str(temp_dir / "ms_kek1.der"),
            "--add-kek", self.MS_OWNER_GUID, str(temp_dir / "ms_kek2.der"),
            # Signature Database (db)
            "--add-db", self.MS_OWNER_GUID, str(temp_dir / "ms_db1.der"),
            "--add-db", self.MS_OWNER_GUID, str(temp_dir / "ms_db2.der"),
            "--add-db", self.MS_OWNER_GUID, str(temp_dir / "ms_db3.der"),
            "--add-db", self.MS_OWNER_GUID, str(temp_dir / "ms_db4.der"),
            "--add-db", self.MS_OWNER_GUID, str(temp_dir / "ms_db5.der"),
            # Forbidden Signatures Database (dbx)
            "--set-dbx", str(temp_dir / "dbx_update.bin"),
        ]

        defaults_json_path = self._generate_defaults_json(temp_dir)
        if defaults_json_path:
            cmd.extend(["--set-json", str(defaults_json_path)])

        utils.info("Injecting certificates using virt-fw-vars...")
        try:
            utils.run_command(cmd, Path.cwd(), show_spinner=True)
            utils.log("Successfully created secure VARS file.")
        except utils.CommandExecutionError:
            utils.fail("Failed to inject certificates. Check the log for virt-fw-vars errors.")

    def run(self):
        """Executes the full certificate injection workflow."""
        target = self._select_target_vm()
        if not target:
            return  # User cancelled

        vm_name, base_vars_file = target

        try:
            with tempfile.TemporaryDirectory() as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                self._download_certs(temp_dir)
                self._run_injection(vm_name, base_vars_file, temp_dir)

            utils.info("Process completed successfully.")
            utils.warn(
                f"To use the new keys, you must manually edit the XML configuration for '{vm_name}' "
                f"to point to the new VARS file:\n  -> {base_vars_file.name.replace('.qcow2', '_SECURE.qcow2')}"
            )

        except Exception as e:
            # Catch any other unexpected errors during the process
            utils.fail(f"An unexpected error occurred during certificate injection: {e}")


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================
def main(distro: str, cpu_vendor: str):
    """Main menu and entry point for the OVMF module."""
    while True:
        print(f"\n  {utils.Fore.YELLOW}[1] Build patched OVMF firmware")
        print(f"  {utils.Fore.YELLOW}[2] Inject Secure Boot certs into an existing VM")
        print(f"\n  {utils.Fore.RED}[0] Return to Main Menu")
        choice = utils.quick_prompt("\nEnter choice [0-2]: ")

        if choice == "1":
            builder = OVMFBuilder(distro, cpu_vendor)
            builder.run()
            return
        elif choice == "2":
            injector = CertInjector()
            injector.run()
            return
        elif choice == "0":
            return
        else:
            utils.error("Invalid choice.")