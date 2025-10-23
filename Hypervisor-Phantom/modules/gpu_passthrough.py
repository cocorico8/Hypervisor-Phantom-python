import os
import re
import shutil
import subprocess
from pathlib import Path
import utils

# ==============================================================================
#  CONSTANTS
# ==============================================================================
VFIO_CONF_PATH = Path("/etc/modprobe.d/vfio.conf")
VFIO_KERNEL_OPTS_REGEX = r'\s*(intel_iommu=\w+|amd_iommu=\w+|iommu=\w+|vfio-pci\.ids=[^"\s]+|kvm\.ignore_msrs=\w+)'


# ==============================================================================
#  HELPER AND DETECTION FUNCTIONS
# ==============================================================================


def _detect_bootloader() -> tuple[str, Path | None]:
    """Detects the bootloader and returns its type and primary config path."""
    utils.info("Detecting system bootloader...")
    grub_cfg = Path("/etc/default/grub")
    if grub_cfg.is_file():
        utils.log("GRUB bootloader detected.")
        return "GRUB", grub_cfg

    sd_boot_dirs = [
        Path("/boot/loader/entries"),
        Path("/boot/efi/loader/entries"),
        Path("/efi/loader/entries"),
    ]
    for entry_dir in sd_boot_dirs:
        if entry_dir.is_dir():
            utils.log(f"systemd-boot detected at: {entry_dir}")
            return "systemd-boot", entry_dir

    return "Unknown", None


def _run_cmd(command: list[str]) -> str:
    """Helper to run a command and return its stripped stdout."""
    try:
        return subprocess.check_output(command, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _select_gpu_and_validate_iommu() -> tuple[str, str, str]:
    """
    Finds GPUs, prompts user to select one, validates its IOMMU group,
    and returns the comma-separated hardware IDs, PCI BDF, and vendor ID.
    """
    gpus = []
    pci_devices_path = Path("/sys/bus/pci/devices")
    for dev_path in pci_devices_path.iterdir():
        try:
            if (dev_path / "class").read_text().strip().startswith("0x03"):
                bdf = dev_path.name
                desc = _run_cmd(["lspci", "-s", bdf])
                gpus.append({"bdf": bdf, "path": dev_path, "desc": desc})
        except (IOError, FileNotFoundError):
            continue

    if not gpus:
        utils.fail("No PCI devices with display controller class (0x03) found.")
    if len(gpus) == 1:
        utils.warn(
            "Only one GPU detected. Passing it through will likely leave the host without a display."
        )

    utils.info("Please select the GPU you wish to pass through:")
    for i, gpu in enumerate(gpus):
        print(f"  {utils.Fore.YELLOW}[{i + 1}] {utils.Fore.WHITE}{gpu['desc']}")

    while True:
        choice = utils.ask("Select device number:")
        if choice.isdigit() and 1 <= int(choice) <= len(gpus):
            selected_gpu = gpus[int(choice) - 1]
            break
        utils.error("Invalid selection.")

    iommu_group_path = selected_gpu["path"] / "iommu_group"
    if not iommu_group_path.is_symlink():
        utils.fail(
            "Selected GPU is not in an IOMMU group. Enable IOMMU in your BIOS/UEFI."
        )

    group_id = Path(os.readlink(iommu_group_path)).name
    group_devices_path = Path(f"/sys/kernel/iommu_groups/{group_id}/devices")

    hw_ids, bad_functions = [], []
    pci_bus_dev = selected_gpu["bdf"].rsplit(".", 1)[0]

    for device in group_devices_path.iterdir():
        vendor = (device / "vendor").read_text().strip()
        dev_id = (device / "device").read_text().strip()
        hw_ids.append(f"{vendor[2:]}:{dev_id[2:]}")

        if not device.name.startswith(pci_bus_dev):
            bad_functions.append(
                f"  - {device.name} ({_run_cmd(['lspci', '-s', device.name])})"
            )

    if bad_functions:
        utils.fail(
            f"Bad IOMMU group detected!\nGroup #{group_id} also contains essential non-GPU devices:\n"
            + "\n".join(bad_functions)
            + "\nAborting. This must be fixed with an ACS override patch, BIOS update, or different hardware."
        )

    hw_ids_str = ",".join(hw_ids)
    vendor_id = (selected_gpu["path"] / "vendor").read_text().strip()
    return hw_ids_str, selected_gpu["bdf"], vendor_id


def _create_vfio_conf(hw_ids: str, vendor_id: str):
    """Creates an advanced vfio.conf with soft dependencies."""
    utils.info("Creating VFIO modprobe configuration...")
    softdeps = {"0x10de": "nvidia,nouveau", "0x1002": "amdgpu,radeon", "0x8086": "i915"}
    lines = [f"options vfio-pci ids={hw_ids} disable_vga=1"]
    drivers = softdeps.get(vendor_id, "").split(",")
    for driver in drivers:
        if driver:
            lines.append(f"softdep {driver} pre: vfio-pci")
    utils.update_config_file(
        str(VFIO_CONF_PATH), ".*", "\n".join(lines), append_if_missing=True
    )
    utils.log(f"Created/updated {VFIO_CONF_PATH}")


def _get_updated_opts_string(
    current_opts: str, new_opts_str: str, is_revert: bool
) -> str:
    """
    Takes a string of kernel options, removes all old VFIO options, and adds
    the new ones if not reverting. Returns the cleaned options string.
    """
    # 1. Remove all known VFIO options from the current options string.
    cleaned_opts = re.sub(VFIO_KERNEL_OPTS_REGEX, "", current_opts)
    # 2. Clean up any resulting extra whitespace.
    cleaned_opts = " ".join(cleaned_opts.split())
    # 3. If we are NOT reverting, add the new options to the end.
    if not is_revert:
        cleaned_opts += f" {new_opts_str}"
    return cleaned_opts.strip()


def _modify_bootloader(
    bootloader_type: str, config_path: Path, new_opts_str: str, is_revert: bool
):
    """Surgically adds or removes VFIO kernel parameters from bootloader configs."""
    utils.info(f"Modifying {bootloader_type} configuration...")

    if bootloader_type == "GRUB":
        try:
            content = _run_cmd(["sudo", "cat", str(config_path)])
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                    match = re.search(r'="([^"]*)"', line)
                    if match:
                        current_opts = match.group(1)
                        # Call the helper with ONLY the options string
                        updated_opts = _get_updated_opts_string(
                            current_opts, new_opts_str, is_revert
                        )
                        # Reconstruct the full line correctly
                        lines[i] = f'GRUB_CMDLINE_LINUX_DEFAULT="{updated_opts}"'
                    break
            utils.update_config_file(
                str(config_path), ".+", "\n".join(lines), append_if_missing=False
            )
        except Exception as e:
            utils.fail(f"Failed to modify GRUB config: {e}")

    elif bootloader_type == "systemd-boot":
        conf_files = list(config_path.glob("*.conf"))
        for conf_file in conf_files:
            try:
                content = _run_cmd(["sudo", "cat", str(conf_file)])
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith("options"):
                        # Extract the options string, which is everything after the first space
                        parts = line.strip().split(" ", 1)
                        current_opts = parts[1] if len(parts) > 1 else ""
                        # Call the helper with ONLY the options string
                        updated_opts = _get_updated_opts_string(
                            current_opts, new_opts_str, is_revert
                        )
                        # Reconstruct the full line correctly
                        lines[i] = f"options {updated_opts}"
                        break
                utils.update_config_file(
                    str(conf_file), ".+", "\n".join(lines), append_if_missing=False
                )
            except Exception as e:
                utils.warn(f"Failed to modify {conf_file.name}: {e}")


def _rebuild_grub():
    """Finds and runs the correct grub-mkconfig command."""
    utils.info("Updating GRUB...")
    for grub_cmd in ["update-grub", "grub-mkconfig", "grub2-mkconfig"]:
        if shutil.which(grub_cmd):
            output_path = ""
            if grub_cmd != "update-grub":
                if Path("/boot/grub/grub.cfg").exists():
                    output_path = "/boot/grub/grub.cfg"
                elif Path("/boot/grub2/grub.cfg").exists():
                    output_path = "/boot/grub2/grub.cfg"
                else:
                    utils.fail("Cannot find grub.cfg path.")
                    return
            cmd = ["sudo", grub_cmd]
            if output_path:
                cmd.extend(["-o", output_path])
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                utils.log("GRUB configuration rebuilt successfully.")
                return
            except subprocess.CalledProcessError as e:
                utils.fail(f"Failed to rebuild GRUB config. Error: {e.stderr.decode()}")
    utils.fail("No GRUB update command found.")


# ==============================================================================
#  MAIN LOGIC FUNCTIONS
# ==============================================================================


def configure_vfio(boot_type, config_path, vendor_id):
    """Main flow for setting up VFIO."""
    hw_ids, _, gpu_vendor_id = _select_gpu_and_validate_iommu()
    _create_vfio_conf(hw_ids, gpu_vendor_id)

    # 1. Start with the base options required for everyone.
    kernel_opts = ["iommu=pt", f"vfio-pci.ids={hw_ids}", "kvm.ignore_msrs=1"]

    # 2. Conditionally add the Intel-specific option.
    if "GenuineIntel" in vendor_id:
        # Prepend the Intel option to the list.
        kernel_opts.insert(0, "intel_iommu=on")

    _modify_bootloader(boot_type, config_path, " ".join(kernel_opts), is_revert=False)
    return True  # Indicates changes were made


def revert_vfio(boot_type, config_path):
    """Main flow for reverting VFIO."""
    utils.info("Reverting VFIO configurations...")
    if VFIO_CONF_PATH.exists():
        subprocess.run(["sudo", "rm", "-f", str(VFIO_CONF_PATH)], check=True)
        utils.log(f"Removed {VFIO_CONF_PATH}")
    _modify_bootloader(boot_type, config_path, "", is_revert=True)
    return True


# ==============================================================================
#  MAIN ENTRY POINT
# ==============================================================================


def main(vendor_id: str):
    """Main entry point for the GPU Passthrough setup module."""
    boot_type, config_path = _detect_bootloader()
    if boot_type == "Unknown":
        utils.fail("Cannot proceed without a recognized bootloader.")

    changes_made = False
    if utils.yes_or_no("Revert and remove existing GPU passthrough configurations?"):
        changes_made = revert_vfio(boot_type, config_path) or changes_made

    if utils.yes_or_no("Configure new GPU passthrough settings now?"):
        changes_made = configure_vfio(boot_type, config_path, vendor_id) or changes_made

    if changes_made and boot_type == "GRUB":
        if utils.yes_or_no(
            "Bootloader configuration was changed. Rebuild GRUB config now?"
        ):
            _rebuild_grub()
            utils.warn("A reboot is required for all changes to take effect.")
    elif changes_made:
        utils.warn("A reboot is required for all changes to take effect.")

    utils.info("GPU Passthrough module finished.")
