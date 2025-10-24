"""
Module for building and installing a custom-patched Linux kernel using linux-tkg.

This module automates the process of:
1. Cloning the linux-tkg build scripts.
2. Guiding the user through a configuration menu for kernel options.
3. Applying custom patches for anti-cheat compatibility.
4. Building and installing the kernel package for the host distribution.
5. Optionally creating a systemd-boot entry for the new kernel.
"""

import getpass
import os
import shutil
from pathlib import Path
from typing import Dict, Tuple

# Import our custom utility functions
import utils


# ==============================================================================
# MAIN CLASS
# ==============================================================================

class KernelBuilder:
    """Orchestrates the download, configuration, and build of a TKG kernel."""

    # --- Configuration Constants ---
    TKG_URL = "https://github.com/Frogging-Family/linux-tkg.git"
    TKG_DIR_NAME = "linux-tkg"
    KERNEL_MAJOR = "6"
    KERNEL_MINOR = "14"
    KERNEL_PATCH = "latest"
    REQUIRED_DISK_SPACE_GB = 35

    # Locations to search for systemd-boot entries directory
    SDBOOT_CONF_LOCATIONS = [
        Path("/boot/loader/entries"),
        Path("/boot/efi/loader/entries"),
        Path("/efi/loader/entries"),
    ]

    def __init__(self, distro: str, cpu_vendor: str):
        """
        Initializes the kernel builder.

        Args:
            distro: The name of the detected Linux distribution.
            cpu_vendor: The CPU vendor string (e.g., 'GenuineIntel').
        """
        self.distro = distro
        self.cpu_vendor = cpu_vendor
        self.src_dir = Path("src")
        self.tkg_path = self.src_dir / self.TKG_DIR_NAME
        self.kernel_version = f"{self.KERNEL_MAJOR}.{self.KERNEL_MINOR}-{self.KERNEL_PATCH}"

    def _check_disk_space(self):
        """Checks for sufficient free disk space in the current directory."""
        utils.info(f"Checking for at least {self.REQUIRED_DISK_SPACE_GB}GB of free disk space...")
        required_bytes = self.REQUIRED_DISK_SPACE_GB * 1024 ** 3
        free_bytes = shutil.disk_usage(Path.cwd()).free

        if free_bytes < required_bytes:
            utils.fail(
                f"Insufficient disk space. At least {self.REQUIRED_DISK_SPACE_GB}GB is required, "
                f"but only {free_bytes / 1024 ** 3:.1f}GB is available."
            )
        utils.log(f"Disk space check passed ({free_bytes / 1024 ** 3:.1f}GB available).")

    def _acquire_tkg_source(self):
        """Clones or updates the linux-tkg git repository."""
        self.src_dir.mkdir(exist_ok=True)

        if self.tkg_path.is_dir():
            utils.warn(f"Directory '{self.tkg_path}' already exists.")
            if not utils.yes_or_no("Delete and re-clone the linux-tkg source?"):
                utils.info("Keeping existing directory.")
                return
            shutil.rmtree(self.tkg_path)

        utils.info("Cloning linux-tkg repository...")
        utils.run_command(
            ["git", "clone", "--depth=1", self.TKG_URL, str(self.tkg_path)],
            cwd=Path.cwd(),
            show_spinner=True
        )
        utils.info("TKG source successfully acquired.")

    @staticmethod
    def _select_from_menu(prompt: str, options: Dict[str, Tuple[str, str]]) -> str:
        """Displays a menu, validates input, and returns the chosen value."""
        while True:
            print(f"\n  [?] {prompt}\n")
            for key, (name, _) in options.items():
                print(f"    {utils.Fore.YELLOW}[{key}] {utils.Fore.WHITE}{name}")

            choice = utils.quick_prompt("\n    Enter your choice: ")
            if choice in options:
                selected_name, return_value = options[choice]
                utils.info(f"User selected: {selected_name}")
                return return_value
            else:
                utils.error("Invalid choice, please try again.")

    def _configure_tkg(self):
        """Guides the user through configuration and modifies customization.cfg."""
        config_file = self.tkg_path / "customization.cfg"

        # --- Gather User Choices ---
        acs_override = "true" if utils.yes_or_no("Apply ACS override patch for better IOMMU groups?") else "false"

        amd_opts = {
            "1": ("Zen 4 (Ryzen 7000)", "znver4"),
            "2": ("Zen 3 (Ryzen 5000)", "znver3"),
            "3": ("Zen 2 (Ryzen 3000)", "znver2"),
            "4": ("Zen 1/+/TR (Ryzen 1000/2000)", "znver1"),
            "5": ("Native (Optimized for this specific CPU)", "native"),
        }
        intel_opts = {
            "1": ("Alder Lake / Raptor Lake", "alderlake"),
            "2": ("Rocket Lake", "rocketlake"),
            "3": ("Ice Lake", "icelake"),
            "4": ("Skylake", "skylake"),
            "5": ("Native (Optimized for this specific CPU)", "native"),
        }

        cpu_opt = self._select_from_menu(
            "Select your CPU microarchitecture:",
            amd_opts if "AuthenticAMD" in self.cpu_vendor else intel_opts
        )

        # --- Build and Apply Config ---
        config_values = {
            "_distro": self.distro,
            "_version": self.kernel_version,
            "_acs_override": acs_override,
            "_processor_opt": cpu_opt,
            "_user_patches_no_confirm": "true",
        }

        utils.info("Applying choices to customization.cfg...")
        try:
            # This approach is safer than regex replacement for config files
            lines = config_file.read_text().splitlines()
            new_lines = []

            # Update existing lines
            for line in lines:
                key = line.split("=", 1)[0].strip()
                if key in config_values:
                    new_lines.append(f'{key}="{config_values.pop(key)}"')
                else:
                    new_lines.append(line)

            # Add any new keys that weren't in the original file
            for key, value in config_values.items():
                new_lines.append(f'{key}="{value}"')

            config_file.write_text("\n".join(new_lines) + "\n")
        except IOError as e:
            utils.fail(f"Failed to write to {config_file}: {e}")

    def _apply_custom_patches(self):
        """Copies the custom kernel patch into the tkg userpatches directory."""
        userpatches_dir = self.tkg_path / f"linux{self.KERNEL_MAJOR}{self.KERNEL_MINOR}-tkg-userpatches"
        userpatches_dir.mkdir(exist_ok=True)

        vendor_short = "amd" if "AuthenticAMD" in self.cpu_vendor else "intel"
        patch_name = f"{vendor_short}{self.KERNEL_MAJOR}{self.KERNEL_MINOR}.mypatch"
        patch_source = utils.get_resource_path("patches/Kernel") / patch_name

        if not patch_source.exists():
            utils.fail(f"Required kernel patch not found: {patch_source}")

        shutil.copy(patch_source, userpatches_dir)
        utils.log(f"Copied custom patch '{patch_name}' to userpatches directory.")

    def _build_and_install_kernel(self):
        """Runs the appropriate build and installation command for the distro."""
        utils.info("Starting kernel build... This will take a very long time.")

        try:
            if self.distro == "Arch":
                # On Arch, 'makepkg -si' needs to run as a non-root user
                original_user = os.environ.get("SUDO_USER")
                if not original_user or original_user == 'root':
                    utils.fail("For Arch, this must be run via sudo from a normal user.")

                utils.warn(f"Running 'makepkg' as user '{original_user}'. This is a security measure.")
                # We can use our utils runner by wrapping the command in 'sudo -u'
                cmd = ["sudo", "-u", original_user, "makepkg", "-si", "--noconfirm"]
                utils.run_command(cmd, self.tkg_path, show_spinner=True)
            else:
                # For other distros, the install script handles privileges internally
                utils.run_command(["./install.sh", "install"], self.tkg_path, show_spinner=True)
        except utils.CommandExecutionError:
            utils.fail("Kernel build failed. Check the log file for details.")

    def _create_systemd_boot_entry(self):
        """Creates a systemd-boot entry for the newly installed kernel."""
        utils.info("Creating systemd-boot entry...")
        try:
            root_dev = utils.run_command(
                ["findmnt", "-no", "SOURCE", "/"], Path.cwd(), capture_output=True
            ).stdout.strip()

            partuuid = utils.run_command(
                ["blkid", "-s", "PARTUUID", "-o", "value", root_dev], Path.cwd(), capture_output=True
            ).stdout.strip()

            if not partuuid:
                utils.error("Could not determine PARTUUID for the root device. Cannot create boot entry.")
                return

            entry_name = "HvP-Patched-Kernel"
            # TKG kernel names follow a predictable pattern
            kver = f"linux{self.KERNEL_MAJOR}{self.KERNEL_MINOR}-tkg-eevdf"

            entry_content = (
                f"title   Hypervisor Phantom Kernel ({kver})\n"
                f"linux   /vmlinuz-{kver}\n"
                f"initrd  /initramfs-{kver}.img\n"
                f"options root=PARTUUID={partuuid} rw\n"
            )

            for entry_dir in self.SDBOOT_CONF_LOCATIONS:
                if entry_dir.is_dir():
                    dest_file = entry_dir / f"{entry_name}.conf"
                    dest_file.write_text(entry_content)
                    utils.info(f"Boot entry created at: {dest_file}")
                    return

            utils.error("No valid systemd-boot entry directory found.")
        except (utils.CommandExecutionError, IOError) as e:
            utils.error(f"Failed to create systemd-boot entry: {e}")

    def run(self):
        """Executes the full kernel patching and building workflow."""
        self._check_disk_space()
        self._acquire_tkg_source()
        self._configure_tkg()
        self._apply_custom_patches()

        if utils.yes_or_no("Configuration complete. Proceed with kernel build and installation?"):
            self._build_and_install_kernel()
            if utils.yes_or_no("Kernel installed. Create a systemd-boot entry for it?"):
                self._create_systemd_boot_entry()


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================

def main(distro: str, cpu_vendor: str):
    """
    Main entry point for the kernel patcher module.

    Args:
        distro: The name of the detected Linux distribution.
        cpu_vendor: The CPU vendor string from the host system.
    """
    utils.info("Starting custom kernel build process...")
    builder = KernelBuilder(distro, cpu_vendor)
    builder.run()
    utils.log("Kernel patching process finished.")