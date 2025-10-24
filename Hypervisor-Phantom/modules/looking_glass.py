"""
Module for installing and configuring the Looking Glass client and host components.

This module automates the process of:
1. Installing dependencies for building the Looking Glass client.
2. Downloading, patching, compiling, and installing the client.
3. Configuring the standard shared memory (`/dev/shm`) method for IVSHMEM.
4. Configuring the high-performance KVMFR kernel module for IVSHMEM.
"""

import getpass
import os
import shutil
from pathlib import Path

import requests
import utils
from config import versions, packages, urls, core

# ==============================================================================
# MAIN CLASS
# ==============================================================================

class LookingGlassSetup:
    """Orchestrates the installation and configuration of Looking Glass."""

    def __init__(self, distro: str, cpu_vendor: str):
        """
        Initializes the Looking Glass setup process.

        Args:
            distro: The name of the detected Linux distribution.
            cpu_vendor: The CPU vendor string (e.g., 'GenuineIntel').
        """
        self.distro = distro
        self.cpu_vendor = cpu_vendor
        self.original_user = os.environ.get("SUDO_USER", getpass.getuser())

        # Derived from config.py
        self.lg_archive_name = f"looking-glass-{versions.LOOKING_GLASS}.tar.gz"
        self.lg_source_dir_name = f"looking-glass-{versions.LOOKING_GLASS}"
        self.lg_archive_path = core.SOURCE_DIR / self.lg_archive_name
        self.lg_source_path = core.SOURCE_DIR / self.lg_source_dir_name

    def _install_dependencies(self):
        """Installs all packages required for building Looking Glass."""
        lg_packages = packages.LOOKING_GLASS_BUILD.get(self.distro)
        if not lg_packages:
            utils.fail(f"Looking Glass setup is not supported for distro: {self.distro}")
        utils.install_required_packages("Looking Glass", lg_packages, self.distro)

    def _install_client(self):
        """Downloads, patches, compiles, and installs the Looking Glass client."""
        core.SOURCE_DIR.mkdir(exist_ok=True)
        
        # 1. Download source
        utils.info(f"Downloading Looking Glass {versions.LOOKING_GLASS} source...")
        try:
            res = requests.get(urls.LOOKING_GLASS_SRC, timeout=30)
            res.raise_for_status()
            self.lg_archive_path.write_bytes(res.content)
        except requests.RequestException as e:
            utils.fail(f"Failed to download Looking Glass: {e}")

        # 2. Extract
        utils.log(f"Extracting archive: {self.lg_archive_path}")
        if self.lg_source_path.exists():
            shutil.rmtree(self.lg_source_path)
        shutil.unpack_archive(self.lg_archive_path, core.SOURCE_DIR)
        self.lg_archive_path.unlink()  # Clean up archive

        # 3. Patch Vendor ID in KVMFR module source
        utils.log("Patching KVMFR module for guest detection...")
        kvmfr_module_file = self.lg_source_path / "module" / "kvmfr.c"
        vendor_map = {"GenuineIntel": "0x8086", "AuthenticAMD": "0x1022"}
        new_vendor_id = vendor_map.get(self.cpu_vendor, "0x1022") # Default to AMD

        try:
            content = kvmfr_module_file.read_text()
            # Replace the default Red Hat KVM vendor/device IDs
            content = content.replace("0x1af4", new_vendor_id)
            content = content.replace("0x1110", new_vendor_id)
            kvmfr_module_file.write_text(content)
        except IOError as e:
            utils.fail(f"Failed to patch KVMFR module: {e}")

        # 4. Compile and Install
        build_dir = self.lg_source_path / "client" / "build"
        build_dir.mkdir(parents=True, exist_ok=True)

        utils.info("Configuring Looking Glass build with CMake...")
        utils.run_command(["cmake", ".."], cwd=build_dir)

        utils.info("Compiling and installing Looking Glass client...")
        utils.run_command(["sudo", "make", "install"], cwd=build_dir, show_spinner=True)

        # 5. Cleanup
        utils.log("Cleaning up source directory...")
        shutil.rmtree(self.lg_source_path)
        utils.info("Looking Glass client installed successfully.")

    def _configure_shmem(self):
        """Configures the standard shared memory file for Looking Glass."""
        utils.info("Configuring shared memory file for Looking Glass...")

        # 1. Create tmpfiles.d config for persistence across reboots
        conf_path = Path("/etc/tmpfiles.d/10-looking-glass.conf")
        # Creates a file 'f' at /dev/shm/looking-glass with mode 0660, owned by user:kvm
        conf_content = f"f /dev/shm/looking-glass 0660 {self.original_user} kvm -"
        utils.update_config_file(conf_path, ".*", conf_content)
        utils.log(f"Created systemd-tmpfiles config at {conf_path}")

        # 2. Create the file immediately for the current session
        utils.info("Creating shared memory file for current session...")
        try:
            utils.run_command(
                ["sudo", "systemd-tmpfiles", "--create", str(conf_path)],
                Path.cwd()
            )
        except utils.CommandExecutionError as e:
            utils.warn(f"Could not create tmpfile immediately, may require reboot: {e}")

        # 3. Instruct user about the alias
        utils.info(
            "Configuration successful. To easily run the client, add this alias to your shell's config (e.g., ~/.bashrc):"
        )
        alias_command = "alias lg='looking-glass-client -S'"
        print(f"\n  {utils.Fore.CYAN}{alias_command}\n")

    def _configure_kvmfr(self):
        """Configures the KVMFR kernel module for high-performance DMA transfers."""
        utils.info("Configuring KVMFR kernel module...")
        memory_size_mb = "32" # Standard size for Looking Glass

        try:
            # 1. Load the module for the current session
            utils.run_command(
                ["sudo", "modprobe", "kvmfr", f"static_size_mb={memory_size_mb}"],
                Path.cwd(),
            )

            # 2. Make it persistent
            modprobe_conf = Path("/etc/modprobe.d/kvmfr.conf")
            modprobe_conf.write_text(f"options kvmfr static_size_mb={memory_size_mb}\n")
            utils.log("Created modprobe options file.")

            modules_load_conf = Path("/etc/modules-load.d/kvmfr.conf")
            modules_load_conf.write_text("# KVMFR Looking Glass module\nkvmfr\n")
            utils.log("Configured module to load on boot.")

            # 3. Set permissions on the device file
            kvmfr_dev = Path("/dev/kvmfr0")
            if kvmfr_dev.exists():
                utils.run_command(
                    ["sudo", "chown", f"{self.original_user}:kvm", str(kvmfr_dev)],
                    Path.cwd()
                )
                utils.log(f"Set permissions on {kvmfr_dev}")
            else:
                utils.warn(f"Device {kvmfr_dev} not found. Permissions not set. A reboot may be required.")
            
            utils.info("KVMFR module configured successfully.")
        except (IOError, utils.CommandExecutionError) as e:
            utils.fail(f"Failed to configure KVMFR module: {e}")

    def run(self):
        """Presents the main menu for Looking Glass setup options."""
        self._install_dependencies()

        while True:
            print(f"\n  {utils.Fore.YELLOW}[1] Install/Re-install the Looking Glass Client")
            print(f"  {utils.Fore.YELLOW}[2] Configure IVSHMEM (Standard /dev/shm method)")
            print(f"  {utils.Fore.YELLOW}[3] Configure IVSHMEM (High-performance KVMFR module)")
            print(f"\n  {utils.Fore.RED}[0] Return to Main Menu")
            choice = utils.quick_prompt("\nEnter choice [0-3]: ")
            
            if choice == "1":
                self._install_client()
            elif choice == "2":
                self._configure_shmem()
            elif choice == "3":
                if 'dkms' in packages.LOOKING_GLASS_BUILD.get(self.distro, []):
                    self._configure_kvmfr()
                else:
                    utils.error(f"KVMFR requires the 'dkms' package, which is not listed as a dependency for {self.distro}.")
            elif choice == "0":
                return
            else:
                utils.error("Invalid choice.")


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================

def main(distro: str, cpu_vendor: str):
    """
    Main entry point for the Looking Glass setup module.

    Args:
        distro: The name of the detected Linux distribution.
        cpu_vendor: The CPU vendor string from the host system.
    """
    utils.info("Starting Looking Glass setup process...")
    setup = LookingGlassSetup(distro, cpu_vendor)
    setup.run()
    utils.log("Looking Glass setup process finished.")