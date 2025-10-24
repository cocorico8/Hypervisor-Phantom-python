"""
Module for configuring VFIO GPU passthrough.

This module handles:
1. Detecting the system's bootloader (GRUB or systemd-boot).
2. Guiding the user to select a GPU for passthrough.
3. Validating the selected GPU's IOMMU group to ensure it's safe to pass through.
4. Creating the necessary VFIO modprobe configuration.
5. Surgically adding/removing kernel parameters from the bootloader configuration.
6. Triggering a bootloader update if required (e.g., for GRUB).
"""

import os
import re
import shutil
from pathlib import Path

import utils
from config import paths


# ==============================================================================
# MAIN CLASS
# ==============================================================================

class VFIOSetup:
    """Orchestrates the setup or reversion of VFIO GPU passthrough settings."""

    VFIO_CONF_PATH = Path("/etc/modprobe.d/vfio.conf")
    # Regex to find all known kernel options related to VFIO
    VFIO_KERNEL_OPTS_REGEX = re.compile(
        r'\s*(?:intel_iommu=\w+|amd_iommu=\w+|iommu=\w+|vfio-pci\.ids=[^"\s]+|kvm\.ignore_msrs=\w+)'
    )

    def __init__(self, cpu_vendor: str):
        self.cpu_vendor = cpu_vendor
        self.bootloader_type, self.config_path = self._detect_bootloader()

        if self.bootloader_type == "Unknown":
            utils.fail("Could not detect GRUB or systemd-boot. Cannot proceed.")

    @staticmethod
    def _detect_bootloader() -> tuple[str, Path | None]:
        """Detects the bootloader and returns its type and primary config path."""
        utils.info("Detecting system bootloader...")
        grub_cfg = Path("/etc/default/grub")
        if grub_cfg.is_file():
            utils.log("GRUB bootloader detected.")
            return "GRUB", grub_cfg

        sd_boot_dirs = [Path("/boot/loader/entries"), Path("/efi/loader/entries")]
        for entry_dir in sd_boot_dirs:
            if entry_dir.is_dir() and any(entry_dir.glob("*.conf")):
                utils.log(f"systemd-boot detected at: {entry_dir}")
                return "systemd-boot", entry_dir

        return "Unknown", None

    @staticmethod
    def _select_gpu_and_validate_iommu() -> tuple[str, str] | None:
        """
        Guides user to select a GPU, validates its IOMMU group, and returns
        the comma-separated hardware IDs and the GPU's vendor ID.
        """
        utils.info("Scanning for GPUs and their IOMMU groups...")
        gpus = []
        try:
            pci_devices = list(Path("/sys/bus/pci/devices").iterdir())
            for dev_path in pci_devices:
                # Class 0x03 is for Display Controllers (GPUs)
                if (dev_path / "class").read_text().strip().startswith("0x03"):
                    desc = utils.run_command(["lspci", "-s", dev_path.name], Path.cwd(), capture_output=True).stdout
                    gpus.append({"bdf": dev_path.name, "path": dev_path, "desc": desc.strip()})
        except (IOError, FileNotFoundError, utils.CommandExecutionError):
            utils.fail("Could not read PCI device information from /sys/.")

        if not gpus:
            utils.fail("No GPUs found.")

        utils.info("Please select the GPU you wish to pass through:")
        for i, gpu in enumerate(gpus, 1):
            print(f"  {utils.Fore.YELLOW}[{i}] {gpu['desc']}")

        choice = -1
        while choice < 1 or choice > len(gpus):
            try:
                choice = int(utils.ask(f"Select device number [1-{len(gpus)}]:"))
            except ValueError:
                utils.error("Invalid input.")

        selected_gpu = gpus[choice - 1]
        iommu_group_path = selected_gpu["path"] / "iommu_group"
        if not iommu_group_path.is_symlink():
            utils.fail("IOMMU is not enabled in your BIOS/UEFI. Please enable it (e.g., VT-d, AMD-Vi) and reboot.")

        group_id = Path(os.readlink(iommu_group_path)).name
        group_devices_path = Path(f"/sys/kernel/iommu_groups/{group_id}/devices")

        hw_ids, bad_devices = [], []
        pci_bus_id = selected_gpu["bdf"].rsplit(".", 1)[0]

        for device in group_devices_path.iterdir():
            vendor = (device / "vendor").read_text().strip()[2:]
            dev_id = (device / "device").read_text().strip()[2:]
            hw_ids.append(f"{vendor}:{dev_id}")

            # A group is "bad" if it contains non-GPU devices on a different PCI bus ID
            if not device.name.startswith(pci_bus_id):
                desc = utils.run_command(["lspci", "-s", device.name], Path.cwd(), capture_output=True).stdout
                bad_devices.append(f"  - {device.name} ({desc.strip()})")

        if bad_devices:
            utils.fail(
                f"Bad IOMMU group detected!\nGroup #{group_id} contains essential non-GPU devices:\n"
                + "\n".join(bad_devices)
                + "\n\nAborting. This can sometimes be fixed with an ACS override kernel patch."
            )

        utils.log("IOMMU group validation successful.")
        gpu_vendor_id = (selected_gpu["path"] / "vendor").read_text().strip()
        return ",".join(hw_ids), gpu_vendor_id

    def _create_vfio_modprobe_conf(self, hw_ids: str, gpu_vendor: str):
        """Creates or overwrites the /etc/modprobe.d/vfio.conf file."""
        utils.info(f"Creating VFIO modprobe configuration at {self.VFIO_CONF_PATH}...")
        
        softdeps = {"0x10de": "nvidia nouveau", "0x1002": "amdgpu radeon", "0x8086": "i915"}
        
        # Build the list of lines for the new file content.
        content_lines = [
            f"options vfio-pci ids={hw_ids} disable_vga=1\n"
        ]
        
        drivers = softdeps.get(gpu_vendor, "").split()
        for driver in drivers:
            content_lines.append(f"softdep {driver} pre: vfio-pci\n")

        # Use the low-level helper to write the new file content directly.
        utils._write_privileged_file(self.VFIO_CONF_PATH, content_lines)
        utils.info(f"Successfully updated configuration in {self.VFIO_CONF_PATH}")

    @staticmethod
    def _get_updated_kernel_opts(current_opts: str, new_opts: str, is_revert: bool) -> str:
        """Strips all old VFIO options and adds new ones if not reverting."""
        # Remove all known VFIO options from the current options string.
        cleaned_opts = VFIOSetup.VFIO_KERNEL_OPTS_REGEX.sub("", current_opts)
        # Clean up any resulting extra whitespace.
        final_opts = " ".join(cleaned_opts.split())

        if not is_revert:
            final_opts = f"{final_opts} {new_opts}".strip()

        return final_opts

    def _update_bootloader(self, new_opts: str, is_revert: bool):
        """Modifies the configuration file for the detected bootloader."""
        utils.info(f"Modifying {self.bootloader_type} configuration...")

        if self.bootloader_type == "GRUB":
            content = utils.run_command(["sudo", "cat", str(self.config_path)], Path.cwd(), capture_output=True).stdout
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                    match = re.search(r'="([^"]*)"', line)
                    current_opts = match.group(1) if match else ""
                    updated_opts = self._get_updated_kernel_opts(current_opts, new_opts, is_revert)
                    lines[i] = f'GRUB_CMDLINE_LINUX_DEFAULT="{updated_opts}"'
                    utils.update_config_file(self.config_path, r".*", "\n".join(lines))
                    return

        elif self.bootloader_type == "systemd-boot":
            for conf_file in self.config_path.glob("*.conf"):
                content = utils.run_command(["sudo", "cat", str(conf_file)], Path.cwd(), capture_output=True).stdout
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith("options"):
                        current_opts = line.strip().split(" ", 1)[1] if " " in line else ""
                        updated_opts = self._get_updated_kernel_opts(current_opts, new_opts, is_revert)
                        lines[i] = f"options {updated_opts}"
                        utils.update_config_file(conf_file, r".*", "\n".join(lines))
                        utils.log(f"Updated {conf_file.name}")
                        break  # Assume one 'options' line per file

    @staticmethod
    def _update_grub():
        """Finds and runs the correct grub-mkconfig command."""
        utils.info("Updating GRUB configuration...")
        grub_cfg_paths = ["/boot/grub/grub.cfg", "/boot/grub2/grub.cfg"]
        output_path = next((path for path in grub_cfg_paths if Path(path).exists()), None)

        for cmd_name in ["update-grub", "grub-mkconfig", "grub2-mkconfig"]:
            if shutil.which(cmd_name):
                cmd = ["sudo", cmd_name]
                if cmd_name != "update-grub" and output_path:
                    cmd.extend(["-o", output_path])

                utils.run_command(cmd, Path.cwd(), show_spinner=True)
                return
        utils.fail("No GRUB update command (like grub-mkconfig) found.")

    def _configure(self) -> bool:
        """Main workflow for setting up VFIO."""
        selection = self._select_gpu_and_validate_iommu()
        if not selection:
            return False  # User aborted or validation failed
        hw_ids, gpu_vendor = selection

        self._create_vfio_modprobe_conf(hw_ids, gpu_vendor)

        kernel_opts = ["iommu=pt", f"vfio-pci.ids={hw_ids}", "kvm.ignore_msrs=1"]
        if "GenuineIntel" in self.cpu_vendor:
            kernel_opts.insert(0, "intel_iommu=on")

        self._update_bootloader(" ".join(kernel_opts), is_revert=False)
        return True

    def _revert(self) -> bool:
        """Main workflow for reverting VFIO."""
        utils.info("Reverting VFIO configurations...")
        if self.VFIO_CONF_PATH.exists():
            utils.run_command(["sudo", "rm", "-f", str(self.VFIO_CONF_PATH)], Path.cwd())
            utils.log(f"Removed {self.VFIO_CONF_PATH}")

        self._update_bootloader("", is_revert=True)
        return True

    def run(self):
        """Presents the main menu and orchestrates the selected action."""
        changes_made = False

        print(f"\n  {utils.Fore.YELLOW}[1] Configure GPU Passthrough (VFIO)")
        print(f"  {utils.Fore.YELLOW}[2] Revert all VFIO configurations")
        print(f"\n  {utils.Fore.RED}[0] Return to Main Menu")
        choice = utils.quick_prompt("\nEnter choice [0-2]: ")

        if choice == "1":
            changes_made = self._configure()
        elif choice == "2":
            changes_made = self._revert()
        elif choice == "0":
            return
        else:
            utils.error("Invalid choice.")
            return

        if changes_made:
            if self.bootloader_type == "GRUB":
                if utils.yes_or_no("Bootloader configuration changed. Update GRUB now?"):
                    self._update_grub()
            utils.warn("A system reboot is required for all changes to take effect.")


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================

def main(cpu_vendor: str):
    """
    Main entry point for the GPU Passthrough setup module.

    Args:
        cpu_vendor: The CPU vendor string from the host system.
    """
    utils.info("Starting GPU Passthrough (VFIO) setup...")
    setup = VFIOSetup(cpu_vendor)
    setup.run()
    utils.log("GPU Passthrough setup process finished.")