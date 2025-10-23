import os
import shutil
import subprocess
import getpass
from pathlib import Path
import utils

# ==============================================================================
#  CONSTANTS AND CONFIGURATION
# ==============================================================================
SRC_DIR = Path("src")
TKG_URL = "https://github.com/Frogging-Family/linux-tkg.git"
TKG_DIR_NAME = "linux-tkg"
KERNEL_MAJOR = "6"
KERNEL_MINOR = "14"
KERNEL_PATCH = "latest"
KERNEL_VERSION = f"{KERNEL_MAJOR}.{KERNEL_MINOR}-{KERNEL_PATCH}"
REQUIRED_DISK_SPACE_GB = 35
SDBOOT_CONF_LOCATIONS = [
    Path("/boot/loader/entries"),
    Path("/boot/efi/loader/entries"),
    Path("/efi/loader/entries"),
]

# ==============================================================================
#  HELPER FUNCTIONS
# ==============================================================================


def _check_disk_space():
    """Checks for sufficient free disk space in the current directory."""
    required_bytes = REQUIRED_DISK_SPACE_GB * 1024**3
    free_bytes = shutil.disk_usage(Path.cwd()).free

    if free_bytes < required_bytes:
        utils.fail(
            f"Insufficient disk space. At least {REQUIRED_DISK_SPACE_GB}GB is required, "
            f"but only {free_bytes / 1024**3:.1f}GB is available."
        )
    utils.log(f"Disk space check passed ({free_bytes / 1024**3:.1f}GB available).")


def _acquire_tkg_source():
    """Clones or updates the linux-tkg git repository."""
    tkg_path = SRC_DIR / TKG_DIR_NAME
    SRC_DIR.mkdir(exist_ok=True)

    if tkg_path.is_dir():
        utils.warn(f"Directory '{tkg_path}' already exists.")
        if not utils.yes_or_no("Delete and re-clone the linux-tkg source?"):
            utils.info("Keeping existing directory.")
            return
        shutil.rmtree(tkg_path)

    utils.info("Cloning linux-tkg repository (this may take a moment)...")
    try:
        utils.run_with_spinner(
            ["git", "clone", "--depth=1", TKG_URL, str(tkg_path)], cwd=Path.cwd()
        )
        utils.info("TKG source successfully acquired.")
    except subprocess.CalledProcessError:
        utils.fail("Failed to clone linux-tkg repository.")


def _select_from_menu(prompt_text: str, options: dict) -> str:
    """
    Displays a numbered menu of options, validates user input, and returns the chosen value.
    """
    while True:
        utils.log(f"[?] Asking user: {prompt_text}")
        print(f"\n  [?] {prompt_text}\n")
        for key, (name, value) in options.items():
            print(f"    {utils.Fore.YELLOW}[{key}] {utils.Fore.WHITE}{name}")

        print()
        choice = utils.quick_prompt("    Enter your choice: ")

        if choice in options:
            selected_name, return_value = options[choice]
            utils.info(f"User selected: {selected_name}")
            return return_value
        else:
            utils.error("Invalid choice, please try again.")


def _modify_customization_cfg(distro: str, cpu_vendor: str):
    """Guides the user through all configuration choices and modifies customization.cfg."""
    tkg_path = SRC_DIR / TKG_DIR_NAME
    config_file = tkg_path / "customization.cfg"

    # User prompts
    acs_override = (
        "true"
        if utils.yes_or_no("Apply ACS override kernel patch for better IOMMU groups?")
        else "false"
    )

    amd_opts = {
        "1": ("Zen 1 / Zen+", "znver1"),
        "2": ("Zen 2", "znver2"),
        "3": ("Zen 3", "znver3"),
        "4": ("Zen 4", "znver4"),
        "5": ("Zen 5", "znver5"),
        "6": (
            "Native (Optimized for this specific CPU)",
            "native",
        ),  # 'native_amd' might be TKG specific, 'native' is safer for GCC
    }
    intel_opts = {
        "1": ("Skylake", "skylake"),
        "2": ("Skylake-X", "skylakex"),
        "3": ("Ice Lake", "icelake"),
        "4": ("Rocket Lake", "rocketlake"),
        "5": ("Alder Lake", "alderlake"),
        "6": (
            "Native (Optimized for this specific CPU)",
            "native",
        ),  # 'native_intel' might be TKG specific, 'native' is safer for GCC
    }

    if "AuthenticAMD" in cpu_vendor:
        cpu_opt = _select_from_menu("Select your AMD CPU μarch code name:", amd_opts)
    else:
        cpu_opt = _select_from_menu(
            "Select your Intel CPU μarch code name:", intel_opts
        )

    # Build the dictionary of config values
    config_values = {
        "_distro": distro,
        "_version": KERNEL_VERSION,
        "_acs_override": acs_override,
        "_processor_opt": cpu_opt,
        "_user_patches_no_confirm": "true",
    }

    utils.info("Applying choices to customization.cfg...")
    try:
        lines = config_file.read_text().splitlines()
        new_lines = []
        config_map = {key: f'{key}="{value}"' for key, value in config_values.items()}

        for line in lines:
            key = line.split("=", 1)[0]
            if key in config_map:
                new_lines.append(config_map.pop(key))
            else:
                new_lines.append(line)

        for key, formatted_line in config_map.items():
            new_lines.append(formatted_line)

        config_file.write_text("\n".join(new_lines) + "\n")
    except Exception as e:
        utils.fail(f"Failed to write to {config_file}: {e}")


def _patch_kernel(cpu_vendor: str):
    """Copies the custom kernel patch into the tkg userpatches directory."""
    tkg_path = SRC_DIR / TKG_DIR_NAME
    userpatches_dir = tkg_path / f"linux{KERNEL_MAJOR}{KERNEL_MINOR}-tkg-userpatches"
    userpatches_dir.mkdir(exist_ok=True)

    vendor_short = "amd" if "AuthenticAMD" in cpu_vendor else "intel"
    patch_name = f"{vendor_short}{KERNEL_MAJOR}{KERNEL_MINOR}.mypatch"
    patch_source = utils.get_resource_path("patches/Kernel") / patch_name

    if not patch_source.exists():
        utils.fail(f"Required kernel patch not found: {patch_source}")

    shutil.copy(patch_source, userpatches_dir)
    utils.log(f"Copied custom patch '{patch_name}' to userpatches directory.")


def _build_and_install(distro: str):
    """Runs the appropriate build and installation command for the detected distro."""
    tkg_path = SRC_DIR / TKG_DIR_NAME
    utils.info(
        "Starting kernel build and installation process... This will take a very long time."
    )

    try:
        if distro == "Arch":
            original_user = os.environ.get("SUDO_USER", getpass.getuser())
            if original_user == "root":
                utils.fail(
                    "For Arch, this script must be run via sudo from a normal user account to build packages safely."
                )

            utils.warn(
                f"Running 'makepkg' as user '{original_user}'. This is a required security measure."
            )
            # We use a direct sudo call here because run_with_spinner doesn't support user switching
            subprocess.run(
                ["sudo", "-u", original_user, "makepkg", "-si", "--noconfirm"],
                cwd=tkg_path,
                check=True,
            )
        else:
            # For other distros, the install script handles privileges internally
            utils.run_with_spinner(["./install.sh", "install"], cwd=tkg_path)
    except subprocess.CalledProcessError:
        utils.fail(
            "Kernel build failed. Check the output above and the log file for details."
        )


def _create_systemd_boot_entry():
    """Creates a systemd-boot entry for the newly installed kernel."""
    utils.info("Creating systemd-boot entry...")
    try:
        root_dev = subprocess.check_output(
            ["findmnt", "-no", "SOURCE", "/"], text=True
        ).strip()
        partuuid = subprocess.check_output(
            ["blkid", "-s", "PARTUUID", "-o", "value", root_dev], text=True
        ).strip()
        if not partuuid:
            utils.error("Could not determine PARTUUID for the root device.")
            return

        entry_name = "HvP-RDTSC-Patched"
        kver = f"{KERNEL_MAJOR}{KERNEL_MINOR}-tkg-eevdf"
        entry_content = f"""title   Hypervisor Phantom Kernel ({kver})
linux   /vmlinuz-linux{kver}
initrd  /initramfs-linux{kver}.img
options root=PARTUUID={partuuid} rw
"""
        for entry_dir in SDBOOT_CONF_LOCATIONS:
            if entry_dir.is_dir():
                (entry_dir / f"{entry_name}.conf").write_text(entry_content)
                utils.info(f"Boot entry created at: {entry_dir / f'{entry_name}.conf'}")
                return
        utils.error("No valid systemd-boot entry directory found.")
    except (subprocess.CalledProcessError, FileNotFoundError, IOError) as e:
        utils.error(f"Failed to create systemd-boot entry: {e}")


# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================


def main(distro: str, cpu_vendor: str):
    """Main entry point for the kernel patcher module."""
    _check_disk_space()
    _acquire_tkg_source()
    _modify_customization_cfg(distro, cpu_vendor)
    _patch_kernel(cpu_vendor)

    if utils.yes_or_no(
        "Configuration is complete. Proceed with kernel build and installation?"
    ):
        _build_and_install(distro)
        if utils.yes_or_no(
            "Kernel installed. Would you like to create a systemd-boot entry for it?"
        ):
            _create_systemd_boot_entry()

    utils.info("Kernel patching process finished.")
