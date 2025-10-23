import os
import subprocess
import re
import shutil
import random
import string
import getpass
from pathlib import Path
import utils
import requests

# ==============================================================================
#  CONSTANTS AND CONFIGURATION
# ==============================================================================
QEMU_VERSION = "10.1.1"
QEMU_DIR_NAME = f"qemu-{QEMU_VERSION}"
QEMU_ARCHIVE = f"{QEMU_DIR_NAME}.tar.xz"
QEMU_SIG = f"{QEMU_ARCHIVE}.sig"
QEMU_URL = f"https://download.qemu.org/{QEMU_ARCHIVE}"
QEMU_SIG_URL = f"{QEMU_URL}.sig"
GPG_KEY = "CEACC9E15534EBABB82D3FA03353C9CEF108B584"

# Using pathlib for robust path management
SRC_DIR = Path("src")

# ==============================================================================
#  PACKAGE DEFINITIONS
# ==============================================================================
REQUIRED_PACKAGES = {
    "Arch": [
        "acpica",
        "base-devel",
        "glib2",
        "ninja",
        "python-packaging",
        "gnupg",
        "patch",
        "spice",
        "gtk3",
        "libusb",
        "usbredir",
    ],
    "Debian": [
        "acpica-tools",
        "build-essential",
        "libfdt-dev",
        "libglib2.0-dev",
        "libpixman-1-dev",
        "ninja-build",
        "python3-venv",
        "zlib1g-dev",
        "gnupg",
        "patch",
        "libspice-server-dev",
        "libusb-1.0-0-dev",
        "libusbredirhost-dev",
    ],
    "openSUSE": [
        "acpica",
        "bzip2",
        "gcc-c++",
        "gpg2",
        "glib2-devel",
        "make",
        "qemu",
        "libpixman-1-0-devel",
        "patch",
        "python3-Sphinx",
        "ninja",
        "curl",
        "spice-server",
        "libusb-1_0-devel",
        "libusbredir-devel",
    ],
    "Fedora": [
        "acpica-tools",
        "bzip2",
        "glib2-devel",
        "libfdt-devel",
        "ninja-build",
        "pixman-devel",
        "python3",
        "zlib-ng-devel",
        "gnupg2",
        "patch",
        "curl",
        "spice-server-devel",
        "libusb1-devel",
        "usbredir-devel",
    ],
}


# ==============================================================================
#  HELPER FUNCTIONS FOR FILE MANIPULATION
# ==============================================================================
def _replace_in_file(file_path: Path, pattern: str, replacement: str):
    """Reads a file, performs a regex replacement, and writes it back."""
    try:
        content = file_path.read_text()
        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            file_path.write_text(new_content)
            utils.log(f"Spoofed data in {file_path}")
    except Exception as e:
        utils.error(f"Could not modify {file_path}: {e}")


def _run_as_user(command: list[str], cwd: Path, check=True):
    """Runs a command as the original user, not as root."""
    original_user = os.environ.get("SUDO_USER", getpass.getuser())
    utils.log(f"Running as user '{original_user}': {' '.join(command)}")
    # We must use Popen for user switching to work correctly
    process = subprocess.Popen(
        ["sudo", "-u", original_user, *command],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Log output line-by-line
    for line in iter(process.stdout.readline, ""):
        utils.log_handler.debug(line.strip())
    process.wait()
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)


def _apply_patch(patch_file: Path, source_dir: Path):
    """A reusable helper to apply a single patch file to a source directory."""
    utils.info(f"Applying patch: {patch_file.name}...")
    try:
        with open(patch_file, "r", encoding="utf-8") as f:
            subprocess.run(
                ["patch", "-p1"],
                cwd=source_dir,
                stdin=f,
                check=True,
                capture_output=True,
                text=True,
            )
        utils.log(f"Successfully applied patch: {patch_file.name}")
    except subprocess.CalledProcessError as e:
        utils.fail(f"Failed to apply patch {patch_file.name}. Error: {e.stderr}")
    except Exception as e:
        utils.fail(
            f"An unexpected error occurred while applying {patch_file.name}: {e}"
        )


# ==============================================================================
#  MAIN LOGIC - ACQUIRE, PATCH, SPOOF, COMPILE
# ==============================================================================


def _acquire_qemu_source():
    """Downloads, verifies, and extracts the QEMU source code."""
    SRC_DIR.mkdir(exist_ok=True)
    qemu_source_path = SRC_DIR / QEMU_DIR_NAME

    if qemu_source_path.exists():
        utils.warn(f"Directory {qemu_source_path} already exists.")
        if not utils.yes_or_no("Purge the existing directory and re-download?"):
            utils.info("Keeping existing directory. Skipping download.")
            return
        utils.log(f"Removing existing directory: {qemu_source_path}")
        shutil.rmtree(qemu_source_path)

    # Download files
    utils.info("Downloading QEMU source and signature...")
    try:
        with requests.get(QEMU_URL, stream=True) as r:
            r.raise_for_status()
            with open(SRC_DIR / QEMU_ARCHIVE, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        with requests.get(QEMU_SIG_URL, stream=True) as r:
            r.raise_for_status()
            with open(SRC_DIR / QEMU_SIG, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
    except requests.RequestException as e:
        utils.fail(f"Failed to download QEMU files: {e}")

    # Verify signature
    utils.log("Verifying source authenticity...")
    gpg_recv_proc = subprocess.run(
        ["gpg", "--keyserver", "keys.openpgp.org", "--recv-keys", GPG_KEY],
        capture_output=True,
    )
    if gpg_recv_proc.returncode != 0:
        utils.warn("Failed to import QEMU signing key. GPG output:")
        utils.log_handler.warning(gpg_recv_proc.stderr.decode())
        if not utils.yes_or_no("Continue anyway despite key import failure?"):
            utils.fail("Aborting due to key import failure.")

    gpg_verify_proc = subprocess.run(
        ["gpg", "--verify", SRC_DIR / QEMU_SIG, SRC_DIR / QEMU_ARCHIVE],
        capture_output=True,
    )
    if gpg_verify_proc.returncode != 0:
        utils.warn("Signature verification FAILED! Archive may be compromised.")
        utils.log_handler.warning(gpg_verify_proc.stderr.decode())
        if not utils.yes_or_no(
            "Continue anyway despite failed signature verification?"
        ):
            utils.fail("Aborting due to failed signature verification.")
    else:
        utils.log("Signature verification successful.")

    # Extract
    utils.info("Extracting QEMU source archive...")
    try:
        shutil.unpack_archive(SRC_DIR / QEMU_ARCHIVE, SRC_DIR)
        utils.info("QEMU source successfully extracted.")
    except Exception as e:
        utils.fail(f"Failed to extract QEMU archive: {e}")


def _patch_qemu(cpu_vendor: str, patch_dir: Path):
    """Applies custom patches to the QEMU source."""
    qemu_source_path = SRC_DIR / QEMU_DIR_NAME

    # 1. Define all the patch files
    vendor_map = {"AuthenticAMD": "amd", "GenuineIntel": "intel"}
    short_vendor_name = vendor_map.get(cpu_vendor)

    if not short_vendor_name:
        utils.fail(f"Unsupported CPU Vendor for patching: {cpu_vendor}")

    # Patches are now in a list to make it easy to add more later if needed
    patches_to_apply = [
        (
            patch_dir / f"libnfs6-qemu-{QEMU_VERSION}.patch",
            False,
        ),  # (file, is_mandatory)
        (patch_dir / f"{short_vendor_name}-qemu-{QEMU_VERSION}.patch", True),
    ]

    # 2. Loop through the patches and apply them
    for patch_file, is_mandatory in patches_to_apply:
        if patch_file.exists():
            _apply_patch(patch_file, qemu_source_path)
        else:
            if is_mandatory:
                utils.fail(f"Required patch file not found: {patch_file}")
            else:
                utils.warn(f"Optional patch file not found, skipping: {patch_file}")


def _ensure_dmi_sysfs():
    """Checks if the dmi-sysfs module is loaded and tries to load it if not."""
    dmi_path = Path("/sys/firmware/dmi/entries/4-0/raw")
    if not dmi_path.exists():
        utils.info(
            "DMI sysfs entries not found, attempting to load 'dmi-sysfs' kernel module..."
        )
        try:
            subprocess.run(
                ["sudo", "modprobe", "dmi-sysfs"], check=True, capture_output=True
            )
            if not dmi_path.exists():
                utils.warn(
                    "Failed to find DMI entries even after loading module. SMBIOS patching may be incomplete."
                )
        except (subprocess.CalledProcessError, FileNotFoundError):
            utils.warn(
                "Could not run 'modprobe dmi-sysfs'. SMBIOS patching may be incomplete."
            )


def _get_hex_data(file_path: Path) -> str:
    """Reads a raw binary file and returns its uppercase hexadecimal string representation."""
    if not file_path.exists():
        return ""
    try:
        # Use sudo to read the file, as it may be root-owned
        result = subprocess.run(
            ["sudo", "cat", str(file_path)], check=True, capture_output=True
        )
        return result.stdout.hex().upper()
    except (IOError, subprocess.CalledProcessError):
        return ""


def _patch_smbios_processor_data():
    """
    Reads the host's DMI Type 4 (Processor) data and patches it into the
    QEMU source file 'hw/smbios/smbios.c'. This is called from inside _spoof_identifiers.
    """
    utils.info("Patching DMI Type 4 (Processor Information) into QEMU source...")
    smbios_file = Path("hw/smbios/smbios.c")
    data = _get_hex_data(Path("/sys/firmware/dmi/entries/4-0/raw"))

    if not data:
        utils.error("Could not read DMI Type 4 data. Skipping patch.")
        return

    def parse_le(offset, length):
        sub = data[offset * 2 : (offset + length) * 2]
        if not sub:
            return "00"
        return "".join(reversed([sub[i : i + 2] for i in range(0, len(sub), 2)]))

    # All values are extracted, even if not all are used, for future expansion
    processor_family = data[12 * 2 : 13 * 2]
    voltage = data[34 * 2 : 35 * 2]
    external_clock = parse_le(36, 2)
    l1_cache_handle = parse_le(52, 2)
    l2_cache_handle = parse_le(56, 2)
    l3_cache_handle = parse_le(60, 2)
    processor_upgrade = data[50 * 2 : 51 * 2]
    # NOTE: The original script used a 2-byte read here, but SMBIOS spec says 4 bytes (uint32)
    processor_characteristics = parse_le(76, 4)
    processor_family2 = parse_le(80, 2)

    # Use the existing _replace_in_file helper for safety
    replacements = {
        r"(t->processor_family\s*=\s*)0x[0-9A-Fa-f]+;": rf"\g<1>0x{processor_family};",
        r"(t->voltage\s*=\s*)0x[0-9A-Fa-f]+;": rf"\g<1>0x{voltage};",
        r"(t->external_clock\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{external_clock}\g<2>",
        r"(t->l1_cache_handle\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{l1_cache_handle}\g<2>",
        r"(t->l2_cache_handle\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{l2_cache_handle}\g<2>",
        r"(t->l3_cache_handle\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{l3_cache_handle}\g<2>",
        r"(t->processor_upgrade\s*=\s*)0x[0-9A-Fa-f]+;": rf"\g<1>0x{processor_upgrade};",
        # Updated to handle uint32 for characteristics
        r"(t->processor_characteristics\s*=\s*cpu_to_le32\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{processor_characteristics}\g<2>",
        r"(t->processor_family2\s*=\s*cpu_to_le16\()0x[0-9A-Fa-f]+(\);)": rf"\g<1>0x{processor_family2}\g<2>",
    }

    original_content = smbios_file.read_text(encoding="utf-8", errors="ignore")
    modified_content = original_content
    for pattern, replacement in replacements.items():
        modified_content, count = re.subn(
            pattern, replacement, modified_content, flags=re.MULTILINE
        )

    smbios_file.write_text(modified_content)
    utils.log(f"Completed SMBIOS Type 4 patching for '{smbios_file.name}'.")


def _spoof_identifiers(cpu_vendor: str, fake_battery_dsl_path: Path):
    """Orchestrates all the spoofing functions."""
    utils.log("Spoofing all unique hardcoded QEMU identifiers...")
    qemu_source_path = SRC_DIR / QEMU_DIR_NAME

    # Temporarily change directory to simplify file paths within this function
    original_cwd = Path.cwd()
    os.chdir(qemu_source_path)

    try:
        # Spoof USB serial numbers
        usb_dir = Path("./hw/usb")
        patterns = [
            "STRING_SERIALNUMBER",
            "STR_SERIALNUMBER",
            "STR_SERIAL_MOUSE",
            "STR_SERIAL_TABLET",
            "STR_SERIAL_KEYBOARD",
            "STR_SERIAL_COMPAT",
        ]
        for c_file in usb_dir.glob("*.c"):
            content = c_file.read_text(encoding="utf-8", errors="ignore")
            for pat in patterns:
                content = re.sub(
                    rf'(\[\s*{pat}\s*]\s*=\s*")[^"]*(")',
                    lambda m: m.group(1)
                    + "".join(
                        random.choices(string.ascii_uppercase + string.digits, k=10)
                    )
                    + m.group(2),
                    content,
                )
            c_file.write_text(content)
        utils.log("Spoofed USB serial numbers.")

        # Spoof Drive Serial Numbers
        ide_cd_models = [
            "HL-DT-ST BD-RE WH16NS60",
            "HL-DT-ST DVDRAM GH24NSC0",
            "HL-DT-ST BD-RE BH16NS40",
            "HL-DT-ST DVD+-RW GT80N",
            "HL-DT-ST DVD-RAM GH22NS30",
            "HL-DT-ST DVD+RW GCA-4040N",
            "Pioneer BDR-XD07B",
            "Pioneer DVR-221LBK",
            "Pioneer BDR-209DBK",
            "Pioneer DVR-S21WBK",
            "Pioneer BDR-XD05B",
            "ASUS BW-16D1HT",
            "ASUS DRW-24B1ST",
            "ASUS SDRW-08D2S-U",
            "ASUS BC-12D2HT",
            "ASUS SBW-06D2X-U",
            "Samsung SH-224FB",
            "Samsung SE-506BB",
            "Samsung SH-B123L",
            "Samsung SE-208GB",
            "Samsung SN-208DB",
            "Sony NEC Optiarc AD-5280S",
            "Sony DRU-870S",
            "Sony BWU-500S",
            "Sony NEC Optiarc AD-7261S",
            "Sony AD-7200S",
            "Lite-On iHAS124-14",
            "Lite-On iHBS112-04",
            "Lite-On eTAU108",
            "Lite-On iHAS324-17",
            "Lite-On eBAU108",
            "HP DVD1260i",
            "HP DVD640",
            "HP BD-RE BH30L",
            "HP DVD Writer 300n",
            "HP DVD Writer 1265i",
        ]
        ide_cfata_models = [
            "SanDisk Ultra microSDXC UHS-I",
            "SanDisk Extreme microSDXC UHS-I",
            "SanDisk High Endurance microSDXC",
            "SanDisk Industrial microSD",
            "SanDisk Mobile Ultra microSDHC",
            "Samsung EVO Select microSDXC",
            "Samsung PRO Endurance microSDHC",
            "Samsung PRO Plus microSDXC",
            "Samsung EVO Plus microSDXC",
            "Samsung PRO Ultimate microSDHC",
            "Kingston Canvas React Plus microSD",
            "Kingston Canvas Go! Plus microSD",
            "Kingston Canvas Select Plus microSD",
            "Kingston Industrial microSD",
            "Kingston Endurance microSD",
            "Lexar Professional 1066x microSDXC",
            "Lexar High-Performance 633x microSDHC",
            "Lexar PLAY microSDXC",
            "Lexar Endurance microSD",
            "Lexar Professional 1000x microSDHC",
            "PNY Elite-X microSD",
            "PNY PRO Elite microSD",
            "PNY High Performance microSD",
            "PNY Turbo Performance microSD",
            "PNY Premier-X microSD",
            "Transcend High Endurance microSDXC",
            "Transcend Ultimate microSDXC",
            "Transcend Industrial Temp microSD",
            "Transcend Premium microSDHC",
            "Transcend Superior microSD",
            "ADATA Premier Pro microSDXC",
            "ADATA XPG microSDXC",
            "ADATA High Endurance microSDXC",
            "ADATA Premier microSDHC",
            "ADATA Industrial microSD",
            "Toshiba Exceria Pro microSDXC",
            "Toshiba Exceria microSDHC",
            "Toshiba M203 microSD",
            "Toshiba N203 microSD",
            "Toshiba High Endurance microSD",
        ]
        default_models = [
            "Samsung SSD 970 EVO 1TB",
            "Samsung SSD 860 QVO 1TB",
            "Samsung SSD 850 PRO 1TB",
            "Samsung SSD T7 Touch 1TB",
            "Samsung SSD 840 EVO 1TB",
            "WD Blue SN570 NVMe SSD 1TB",
            "WD Black SN850 NVMe SSD 1TB",
            "WD Green 1TB SSD",
            "WD Blue 3D NAND 1TB SSD",
            "Crucial P3 1TB PCIe 3.0 3D NAND NVMe SSD",
            "Seagate BarraCuda SSD 1TB",
            "Seagate FireCuda 520 SSD 1TB",
            "Seagate IronWolf 110 SSD 1TB",
            "SanDisk Ultra 3D NAND SSD 1TB",
            "Seagate Fast SSD 1TB",
            "Crucial MX500 1TB 3D NAND SSD",
            "Crucial P5 Plus NVMe SSD 1TB",
            "Crucial BX500 1TB 3D NAND SSD",
            "Crucial P3 1TB PCIe 3.0 3D NAND NVMe SSD",
            "Kingston A2000 NVMe SSD 1TB",
            "Kingston KC2500 NVMe SSD 1TB",
            "Kingston A400 SSD 1TB",
            "Kingston HyperX Savage SSD 1TB",
            "SanDisk SSD PLUS 1TB",
            "SanDisk Ultra 3D 1TB NAND SSD",
        ]

        _replace_in_file(
            Path("hw/ide/core.c"),
            r'"HL-DT-ST BD-RE WH16NS60"',
            f'"{random.choice(ide_cd_models)}"',
        )
        _replace_in_file(
            Path("hw/ide/core.c"),
            r'"Hitachi HMS360404D5CF00"',
            f'"{random.choice(ide_cfata_models)}"',
        )
        _replace_in_file(
            Path("hw/ide/core.c"),
            r'"Samsung SSD 980 500GB"',
            f'"{random.choice(default_models)}"',
        )
        _replace_in_file(
            Path("hw/nvme/ctrl.c"), r'"NVMe Ctrl"', f'"{random.choice(default_models)}"'
        )
        utils.log("Spoofed drive models and serials.")

        # Spoof ACPI Table Data
        oem_pairs = [
            ("DELL  ", "Dell Inc"),
            ("ASUS ", "Notebook"),
            ("LENOVO", "TC-O5Z  "),
        ]
        vendor_map = {
            "AuthenticAMD": ("ALASKA", "A M I "),
            "GenuineIntel": ("INTEL ", "U Rvp   "),
        }
        if cpu_vendor in vendor_map:
            oem_pairs.append(vendor_map[cpu_vendor])

        appname6, appname8 = random.choice(oem_pairs)
        _replace_in_file(
            Path("include/hw/acpi/aml-build.h"),
            r'(#define ACPI_BUILD_APPNAME6\s*").*"',
            rf'\1{appname6}"',
        )
        _replace_in_file(
            Path("include/hw/acpi/aml-build.h"),
            r'(#define ACPI_BUILD_APPNAME8\s*").*"',
            rf'\1{appname8}"',
        )
        utils.log("Spoofed ACPI OEM IDs.")

        utils.info("Obtaining machine's chassis-type...")
        c_file_path = Path("hw/acpi/aml-build.c")

        try:
            # Get chassis type from dmidecode
            chassis_proc = subprocess.run(
                ["sudo", "dmidecode", "--string", "chassis-type"],
                capture_output=True,
                text=True,
                check=True,
            )
            chassis_type = chassis_proc.stdout.strip()
            utils.log(f"Detected chassis type: {chassis_type}")

            pm_type = "2" if chassis_type == "Notebook" else "1"

            # Patch the PM type in the C file
            original_c_line = r"build_append_int_noprefix(tbl, 0 \/\* Unspecified \*\/"
            replacement_c_line = (
                f"build_append_int_noprefix(tbl, {pm_type} /* {chassis_type} */"
            )
            _replace_in_file(
                c_file_path, re.escape(original_c_line), replacement_c_line
            )

            # If it's a notebook, generate the fake battery table
            if chassis_type == "Notebook":
                utils.warn(
                    "Host is a Notebook. Generating a fake battery ACPI table..."
                )
                home_dir = Path.home()
                dsl_dest = home_dir / "fake_battery.dsl"
                aml_dest = home_dir / "fake_battery.aml"

                # Read template, replace placeholders, write to user's home
                template_content = fake_battery_dsl_path.read_text()
                template_content = template_content.replace("BOCHS", appname6.strip())
                template_content = template_content.replace(
                    "BXPCSSDT", appname8.strip()
                )
                dsl_dest.write_text(template_content)

                utils.info("Compiling ACPI table with 'iasl'...")
                # Use a spinner because iasl can be slow
                utils.run_with_spinner(["iasl", "-tc", str(dsl_dest)], cwd=home_dir)

                utils.info(f"ACPI table saved to: {aml_dest}")
                utils.warn(
                    "It is highly recommended to passthrough this ACPI table in your VM's configuration (e.g., via QEMU args or libvirt XML)."
                )

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            utils.error(
                f"Failed to process chassis type or generate battery table: {e}"
            )
            utils.warn(
                "This is an optional step and can be ignored, but may affect anti-cheat compatibility."
            )

    finally:
        os.chdir(original_cwd)


def _compile_qemu():
    """Configures, compiles, and installs QEMU."""
    qemu_source_path = SRC_DIR / QEMU_DIR_NAME

    utils.log("Configuring QEMU build environment as user...")
    try:
        # Run configure and make as the original user for safety
        _run_as_user(
            [
                "./configure",
                "--target-list=x86_64-softmmu",
                "--enable-libusb",
                "--enable-usb-redir",
                "--enable-spice",
                "--enable-spice-protocol",
            ],
            cwd=qemu_source_path,
        )

        utils.log(f"Building QEMU with {os.cpu_count()} threads as user...")
        _run_as_user(["make", f"-j{os.cpu_count()}"], cwd=qemu_source_path)

        # The 'install' step is the ONLY part that requires root
        if utils.yes_or_no("Build successful. Install QEMU to /usr/local/bin?"):
            utils.log("Installing QEMU with root privileges...")
            # This is run directly with sudo by the script
            result = subprocess.run(
                ["sudo", "make", "install"],
                cwd=qemu_source_path,
                check=True,
                capture_output=True,
                text=True,
            )

            utils.log_handler.debug("--- Begin 'make install' output ---")
            utils.log_handler.debug(result.stdout)
            utils.log_handler.debug("--- End 'make install' output ---")
            utils.info("QEMU installed successfully!")
        else:
            utils.info("Skipping installation.")

    except subprocess.CalledProcessError:
        utils.fail(
            "A step in the QEMU build process failed. Check the log for details."
        )


def _cleanup():
    """Removes the downloaded source files and build directory."""
    utils.info("Cleaning up source files...")
    if (SRC_DIR / QEMU_DIR_NAME).exists():
        shutil.rmtree(SRC_DIR / QEMU_DIR_NAME)
    for f in [QEMU_ARCHIVE, QEMU_SIG]:
        if (SRC_DIR / f).exists():
            (SRC_DIR / f).unlink()
    utils.log("Cleanup complete.")


# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================


def main(distro: str, cpu_vendor: str):
    """Main entry point for the QEMU patcher module."""
    PATCH_DIR = utils.get_resource_path("patches/QEMU")
    FAKE_BATTERY_ACPITABLE = PATCH_DIR / "fake_battery.dsl"

    if distro not in REQUIRED_PACKAGES:
        utils.fail(f"QEMU patching is not supported for the detected distro: {distro}")

    utils.install_required_packages("QEMU", REQUIRED_PACKAGES[distro], distro)

    _acquire_qemu_source()
    _patch_qemu(cpu_vendor, PATCH_DIR)
    _spoof_identifiers(cpu_vendor, FAKE_BATTERY_ACPITABLE)

    if utils.yes_or_no("Proceed with building and installing the patched QEMU?"):
        _compile_qemu()

    if not utils.yes_or_no("Keep QEMU source for faster re-patching in the future?"):
        _cleanup()

    utils.info("QEMU patching process finished.")
