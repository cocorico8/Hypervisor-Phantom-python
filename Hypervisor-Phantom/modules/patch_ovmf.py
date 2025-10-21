import os
import subprocess
import shutil
import getpass
import tempfile
import struct
from pathlib import Path
import utils
import requests

# ==============================================================================
#  CONSTANTS AND CONFIGURATION
# ==============================================================================
SRC_DIR = Path("src")
EDK2_URL = "https://github.com/tianocore/edk2.git"
EDK2_TAG = "edk2-stable202508"
PATCH_DIR = utils.get_resource_path("patches/EDK2")

# ==============================================================================
#  PACKAGE DEFINITIONS
# ==============================================================================
REQUIRED_PACKAGES = {
    "Arch": [
        "base-devel", "acpica", "git", "nasm", "python", "patch", "virt-firmware"
    ],
    "Debian": [
        "build-essential", "uuid-dev", "acpica-tools", "git", "nasm", "python-is-python3", "patch", "python3-virt-firmware"
    ],
    "openSUSE": [
        "gcc", "gcc-c++", "make", "acpica", "git", "nasm", "python3", "libuuid-devel", "patch", "virt-firmware"
    ],
    "Fedora": [
        "gcc", "gcc-c++", "make", "acpica-tools", "git", "nasm", "python3", "libuuid-devel", "patch", "python3-virt-firmware"
    ]
}

# ==============================================================================
#  HELPER FUNCTIONS
# ==============================================================================

def _run_command(command: list[str], cwd: Path, check=True, env=None):
    """Runs a command, logging its output."""
    utils.log(f"Running in '{cwd}': {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env
    )
    for line in iter(process.stdout.readline, ''):
        utils.log_handler.debug(line.strip())
    retcode = process.wait()
    if check and retcode != 0:
        raise subprocess.CalledProcessError(retcode, command)

def _validate_bmp(bmp_path: Path) -> bool:
    """
    Validates a BMP file header using Python's struct module.
    This is a major improvement over the `od`-based shell script version.
    """
    try:
        with open(bmp_path, "rb") as f:
            # Read the BMP header (first 54 bytes)
            header = f.read(54)
            if len(header) < 54:
                utils.error("BMP file is too small to be valid.")
                return False

            # Unpack the header fields we need to validate
            # '<' = little-endian
            # 2s = 2-byte string (magic number)
            # 16x = skip 16 bytes
            # I = 4-byte unsigned integer (width)
            # I = 4-byte unsigned integer (height)
            # H = 2-byte unsigned short (bit depth)
            # I = 4-byte unsigned integer (compression)
            magic, width, height, bit_depth, compression = struct.unpack('<2s16xIIHI', header[0:34])

            if magic != b'BM':
                utils.error(f"Invalid BMP magic number: {magic}")
                return False
            if compression != 0:
                utils.error(f"Unsupported BMP compression: {compression} (must be 0)")
                return False
            if bit_depth not in [1, 4, 8, 24]:
                utils.error(f"Unsupported bit depth: {bit_depth}-bit (must be 1, 4, 8, or 24)")
                return False

            utils.info(f"VALID: {width}x{height}, {bit_depth}-bit, No Compression")
            return True
    except Exception as e:
        utils.error(f"Failed to read or validate BMP file: {e}")
        return False

# ==============================================================================
#  CORE LOGIC FUNCTIONS
# ==============================================================================

def _acquire_source(ovmf_patch_name: str):
    """Clones the EDK2 repo, checks out the correct tag, and patches it."""
    SRC_DIR.mkdir(exist_ok=True)
    edk2_path = SRC_DIR / EDK2_TAG

    if edk2_path.exists():
        utils.warn(f"EDK2 source directory '{edk2_path}' already exists.")
        if utils.yes_or_no("Purge the directory and re-clone?"):
            shutil.rmtree(edk2_path)
        else:
            utils.info("Keeping existing directory.")
            if utils.yes_or_no("Attempt to patch the existing source?"):
                _patch_ovmf(edk2_path, ovmf_patch_name)
            return

    try:
        utils.info(f"Cloning EDK2 tag '{EDK2_TAG}'... (this may take a while)")
        _run_command(["git", "clone", "--depth=1", "--branch", EDK2_TAG, EDK2_URL, EDK2_TAG], cwd=SRC_DIR)
        utils.info("Initializing submodules...")
        _run_command(["git", "submodule", "update", "--init"], cwd=edk2_path)
        utils.info("EDK2 source successfully acquired.")
        _patch_ovmf(edk2_path, ovmf_patch_name)
    except subprocess.CalledProcessError:
        utils.fail("Failed to acquire EDK2 source. Check the log.")


def _patch_ovmf(edk2_path: Path, ovmf_patch_name: str):
    """Applies the CPU-specific patch and the custom BMP logo."""
    ovmf_patch_file = PATCH_DIR / ovmf_patch_name
    if not ovmf_patch_file.exists():
        utils.fail(f"Patch file not found: {ovmf_patch_file}")

    # 1. Apply the main OVMF patch
    utils.info(f"Applying patch '{ovmf_patch_name}'...")
    try:
        with open(ovmf_patch_file, 'r') as f:
            subprocess.run(["git", "apply"], cwd=edk2_path, stdin=f, check=True)
        utils.info("Patch applied successfully.")
    except subprocess.CalledProcessError:
        utils.fail("Failed to apply OVMF patch.")

    # 2. Handle the boot logo
    logo_dest = edk2_path / "MdeModulePkg" / "Logo" / "Logo.bmp"
    utils.info("Choose a BGRT BMP boot logo for OVMF:")
    print(f"  {utils.Fore.YELLOW}[1] Apply host's default logo (if available)")
    print(f"  {utils.Fore.YELLOW}[2] Apply a custom BMP image")
    
    while True:
        choice = utils.quick_prompt("Enter choice [1-2]: ")
        if choice == '1':
            host_logo = Path("/sys/firmware/acpi/bgrt/image")
            if host_logo.exists():
                shutil.copy(host_logo, logo_dest)
                utils.info("Host's BMP logo copied successfully.")
                break
            else:
                utils.error("Host BMP logo not found at /sys/firmware/acpi/bgrt/image.")
        elif choice == '2':
            while True:
                custom_path_str = utils.ask("Enter the absolute path to your BMP image:")
                custom_path = Path(custom_path_str).expanduser()
                if not custom_path.is_file():
                    utils.error("File does not exist. Try again.")
                    continue
                if _validate_bmp(custom_path):
                    shutil.copy(custom_path, logo_dest)
                    utils.info("Custom BMP copied successfully.")
                    return # Exit both loops
                else:
                    utils.error("Invalid BMP file. Please choose another.")
        else:
            utils.error("Invalid choice. Please enter 1 or 2.")


def _compile_ovmf():
    """Builds and compiles OVMF using the new spinner UI for long-running commands."""
    edk2_path = SRC_DIR / EDK2_TAG
    if not edk2_path.is_dir():
        utils.fail("EDK2 source directory not found. Please acquire the source first.")

    gcc_version_target = "GCC5"
    absolute_edk2_path = edk2_path.resolve()

    try:
        build_env = os.environ.copy()
        build_env["WORKSPACE"] = str(absolute_edk2_path)
        build_env["EDK_TOOLS_PATH"] = str(absolute_edk2_path / "BaseTools")
        build_env["CONF_PATH"] = str(absolute_edk2_path / "Conf")

        utils.info("Building BaseTools (this may take a moment)...")
        # Use the new spinner function for the 'make' command
        utils.run_with_spinner(["make", "-C", "BaseTools", "-s"], cwd=edk2_path, env=build_env)

        utils.info(f"Compiling OVMF with the '{gcc_version_target}' toolchain tag...")
        build_flags = [
            "build -a X64 -p OvmfPkg/OvmfPkgX64.dsc -b RELEASE",
            f"-t {gcc_version_target} -n 0", # The -s silent flag can be re-added if preferred
        ]
        build_flags.extend([
            "--define SECURE_BOOT_ENABLE=TRUE", "--define TPM_CONFIG_ENABLE=TRUE",
            "--define TPM_ENABLE=TRUE", "--define TPM1_ENABLE=TRUE", "--define TPM2_ENABLE=TRUE"
        ])
        build_command_str = " \\\n    ".join(build_flags)

        master_build_script = f"""
set -e
source edksetup.sh
{build_command_str}
"""
        # Use the new spinner function for the main build script
        utils.run_with_spinner(["bash", "-c", master_build_script], cwd=edk2_path, env=build_env)
        utils.log("OVMF build command finished successfully.")

        # --- Post-build steps ---
        utils.info("Converting compiled OVMF to .qcow2 format...")

        output_dir = Path.cwd() / "output" / "firmware"
        output_dir.mkdir(parents=True, exist_ok=True)
        release_dir = edk2_path / f"Build/OvmfX64/RELEASE_{gcc_version_target}/FV"
        code_fd, vars_fd = release_dir / "OVMF_CODE.fd", release_dir / "OVMF_VARS.fd"
        
        if not code_fd.exists() or not vars_fd.exists():
            utils.fail(f"Compiled firmware files not found in {release_dir}. Build may have failed.")

        code_dest = output_dir / "OVMF_CODE.secboot.4m.qcow2"
        vars_dest = output_dir / "OVMF_VARS.4m.qcow2"
        subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(code_fd), str(code_dest)], check=True)
        subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(vars_fd), str(vars_dest)], check=True)
        utils.info(f"Firmware files created in: {output_dir}")

    except subprocess.CalledProcessError:
        utils.fail("The EDK2 build process failed. See the log file for the full output.")
    except Exception as e:
        utils.fail(f"An unexpected error occurred during the build process: {e}")


def _inject_certs():
    """Downloads MS Secure Boot certs and injects them into a selected VM's VARS file."""
    utils.info("Starting Secure Boot certificate injection process...")
    NVRAM_DIR = Path("/var/lib/libvirt/qemu/nvram")

    # 1. Get list of available VMs from virsh
    try:
        result = subprocess.run(
            ["sudo", "virsh", "list", "--all", "--name"],
            capture_output=True, text=True, check=True
        )
        vm_list = [vm for vm in result.stdout.strip().split('\n') if vm]
        if not vm_list:
            utils.error("No virtual machines found by virsh.")
            return
    except (subprocess.CalledProcessError, FileNotFoundError):
        utils.fail("Could not list virsh domains. Is libvirt running and are you in the libvirt group?")
        return

    # 2. Present menu and get user's choice
    utils.info("Please select a VM to inject Microsoft Secure Boot keys into:")
    for i, vm_name in enumerate(vm_list, start=1):
        print(f"  {utils.Fore.YELLOW}[{i}] {vm_name}")
    print(f"\n  {utils.Fore.RED}[0] Cancel")

    while True:
        try:
            choice = int(utils.ask("Enter your choice:"))
            if 0 <= choice <= len(vm_list):
                break
            else:
                utils.error(f"Invalid choice. Please enter a number between 0 and {len(vm_list)}.")
        except ValueError:
            utils.error("Invalid input. Please enter a number.")
    
    if choice == 0:
        utils.info("Operation cancelled.")
        return

    selected_vm_name = vm_list[choice - 1]
    original_vars_file = NVRAM_DIR / f"{selected_vm_name}_VARS.qcow2"
    if not original_vars_file.exists():
        utils.fail(f"VARS file not found for {selected_vm_name} at '{original_vars_file}'. Does the VM have UEFI firmware enabled?")
        return
        
    utils.log(f"Selected VM: {selected_vm_name}")
    utils.log(f"Using base VARS file: {original_vars_file}")

    # 3. Download certificates into a secure temporary directory
    certs_to_download = {
        "ms_pk_oem.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/PK/Certificate/WindowsOEMDevicesPK.der",
        "ms_kek_2011.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/KEK/Certificates/MicCorKEKCA2011_2011-06-24.der",
        "ms_kek_2023.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/KEK/Certificates/microsoft%20corporation%20kek%202k%20ca%202023.der",
        "ms_db_uef_2011.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/DB/Certificates/MicCorUEFCA2011_2011-06-27.der",
        "ms_db_pro_2011.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/DB/Certificates/MicWinProPCA2011_2011-10-19.der",
        "ms_db_optionrom_2023.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/DB/Certificates/microsoft%20option%20rom%20uefi%20ca%202023.der",
        "ms_db_uefi_2023.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/DB/Certificates/microsoft%20uefi%20ca%202023.der",
        "ms_db_windows_2023.der": "https://raw.githubusercontent.com/microsoft/secureboot_objects/main/PreSignedObjects/DB/Certificates/windows%20uefi%20ca%202023.der",
        "dbxupdate_x64.bin": "https://uefi.org/sites/default/files/resources/dbxupdate_x64.bin"
    }
    
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        utils.info(f"Downloading {len(certs_to_download)} certificates to a temporary directory...")
        
        try:
            for filename, url in certs_to_download.items():
                res = requests.get(url, timeout=15)
                res.raise_for_status() # Raise an exception for bad status codes
                (temp_dir / filename).write_bytes(res.content)
                utils.log(f"Downloaded {filename}")
        except requests.RequestException as e:
            utils.fail(f"Failed to download certificate: {e}")
            return

        # 4. Construct and run the virt-fw-vars command
        secure_vars_file = NVRAM_DIR / f"{selected_vm_name}_VARS_SECURE.qcow2"
        uuid = "77fa9abd-0359-4d32-bd60-28f4e78f784b" # Standard MS UUID

        cmd = [
            "sudo", "virt-fw-vars",
            "--input", str(original_vars_file),
            "--output", str(secure_vars_file),
            "--secure-boot",
            # Platform Key (PK)
            "--set-pk", uuid, str(temp_dir / "ms_pk_oem.der"),
            # Key Exchange Keys (KEK)
            "--add-kek", uuid, str(temp_dir / "ms_kek_2011.der"),
            "--add-kek", uuid, str(temp_dir / "ms_kek_2023.der"),
            # Signature Database (db)
            "--add-db", uuid, str(temp_dir / "ms_db_uef_2011.der"),
            "--add-db", uuid, str(temp_dir / "ms_db_pro_2011.der"),
            "--add-db", uuid, str(temp_dir / "ms_db_optionrom_2023.der"),
            "--add-db", uuid, str(temp_dir / "ms_db_uefi_2023.der"),
            "--add-db", uuid, str(temp_dir / "ms_db_windows_2023.der"),
            # Forbidden Signatures Database (dbx)
            "--set-dbx", str(temp_dir / "dbxupdate_x64.bin"),
        ]

        utils.info("Injecting certificates into new VARS file...")
        try:
            utils.run_with_spinner(cmd, cwd=Path.cwd())
            utils.log(f"Successfully created secure VARS file.")
            utils.info(f"New file created at: {secure_vars_file}")
            utils.warn("To use this, you must manually edit the VM's XML to point to this new VARS file.")
        except subprocess.CalledProcessError:
            utils.fail("Failed to inject certificates using virt-fw-vars. Check the log for details.")


def _cleanup():
    """Removes the EDK2 source directory."""
    edk2_path = SRC_DIR / EDK2_TAG
    if edk2_path.exists():
        utils.info(f"Removing source directory: {edk2_path}")
        shutil.rmtree(edk2_path)

# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================

def main(distro: str, cpu_vendor: str):
    """Main menu and entry point for the OVMF patcher module."""
    if distro not in REQUIRED_PACKAGES:
        utils.fail(f"OVMF patching is not supported for the detected distro: {distro}")

    vendor_map = {"AuthenticAMD": "amd", "GenuineIntel": "intel"}
    short_vendor = vendor_map.get(cpu_vendor, "unknown")
    ovmf_patch_name = f"{short_vendor}-{EDK2_TAG}.patch"

    utils.install_required_packages("EDK2/OVMF", REQUIRED_PACKAGES[distro], distro)

    while True:
        print(f"\n  {utils.Fore.YELLOW}[1] Create patched OVMF firmware")
        print(f"  {utils.Fore.YELLOW}[2] Inject Secure Boot certs into VARS file")
        print(f"\n  {utils.Fore.RED}[0] Return to Main Menu")

        choice = utils.quick_prompt("Enter choice [0-2]: ")
        if choice == '1':
            _acquire_source(ovmf_patch_name)
            if utils.yes_or_no("Source is patched. Proceed with compilation?"):
                _compile_ovmf()
            if not utils.yes_or_no("Keep EDK2 source for faster re-patching?"):
                _cleanup()
            break
        elif choice == '2':
            _inject_certs()
            break
        elif choice == '0':
            utils.info("Returning to main menu.")
            break
        else:
            utils.error("Invalid choice.")