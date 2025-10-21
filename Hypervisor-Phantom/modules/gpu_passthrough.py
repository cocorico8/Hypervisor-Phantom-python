import os
import re
import subprocess
from pathlib import Path
import utils

# ==============================================================================
#  CONSTANTS
# ==============================================================================
VFIO_CONF_PATH = Path("/etc/modprobe.d/vfio.conf")
SDBOOT_CONF_LOCATIONS = [Path("/boot/loader/entries"), Path("/boot/efi/loader/entries"), Path("/efi/loader/entries")]
VFIO_KERNEL_OPTS_REGEX = re.compile(r'(?:intel_iommu=\S*|iommu=\S*|vfio-pci\.ids=\S*|kvm\.ignore_msrs=\S*)')

# ==============================================================================
#  HELPER: BOOTLOADER AND SYSTEM INTROSPECTION
# ==============================================================================

def _detect_bootloader():
    """Detects if the system uses GRUB or systemd-boot and finds the config file."""
    if Path("/etc/default/grub").is_file():
        return "grub", Path("/etc/default/grub")
    
    for directory in SDBOOT_CONF_LOCATIONS:
        if directory.is_dir():
            # Find the first non-fallback config file
            for conf_file in sorted(directory.glob("*.conf")):
                if not conf_file.name.endswith("-fallback.conf"):
                    return "systemd-boot", conf_file
    
    return None, None

def _get_gpu_devices():
    """Finds all GPU devices by inspecting the /sys filesystem."""
    gpus = []
    pci_path = Path("/sys/bus/pci/devices")
    for device_path in pci_path.glob("*"):
        try:
            # VGA compatible controllers have a class code starting with 0x03
            with open(device_path / "class", "r") as f:
                if f.read(4) == "0x03":
                    bdf = device_path.name
                    vendor = (device_path / "vendor").read_text().strip()
                    device = (device_path / "device").read_text().strip()
                    
                    # Get human-readable description using lspci for convenience
                    desc_proc = subprocess.run(['lspci', '-s', bdf], capture_output=True, text=True)
                    description = desc_proc.stdout.strip().split(":", 2)[-1].strip()
                    
                    gpus.append({
                        "bdf": bdf,
                        "vendor": vendor,
                        "device": device,
                        "description": description
                    })
        except IOError:
            continue # Skip devices with missing files
    return gpus

# ==============================================================================
#  HELPER: FILE AND CONFIGURATION MANIPULATION
# ==============================================================================

def _modify_bootloader_config(config_path, new_options_str=""):
    """Safely removes old VFIO options and adds new ones if provided."""
    utils.info(f"Modifying bootloader config: {config_path}")
    try:
        original_content = config_path.read_text()
        lines = original_content.split('\n')
        new_lines = []
        modified = False

        for line in lines:
            if line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                # Remove old options
                cleaned_line = VFIO_KERNEL_OPTS_REGEX.sub('', line)
                # Add new options before the closing quote
                if new_options_str:
                    final_line = re.sub(r'"\s*$', f' {new_options_str}"', cleaned_line, 1)
                else:
                    final_line = cleaned_line
                new_lines.append(final_line.replace("  ", " "))
                modified = True
            elif line.strip().startswith("options "):
                cleaned_line = VFIO_KERNEL_OPTS_REGEX.sub('', line)
                if new_options_str:
                    final_line = f"{cleaned_line.rstrip()} {new_options_str}"
                else:
                    final_line = cleaned_line
                new_lines.append(final_line.replace("  ", " "))
                modified = True
            else:
                new_lines.append(line)
        
        if modified:
            new_content = "\n".join(new_lines)
            utils.update_config_file(str(config_path), original_content, new_content)
            return True
        else:
            utils.warn(f"No relevant line found in {config_path} to modify.")
            return False

    except Exception as e:
        utils.fail(f"Failed to modify bootloader config: {e}")
        return False

# ==============================================================================
#  CORE LOGIC: REVERT, CONFIGURE, REBUILD
# ==============================================================================

def _revert_vfio(bootloader_type, config_path):
    """Removes all VFIO configurations."""
    utils.info("Reverting VFIO configurations...")
    if VFIO_CONF_PATH.exists():
        utils.run_with_spinner(["sudo", "rm", "-f", str(VFIO_CONF_PATH)], Path.cwd())
        utils.log(f"Removed {VFIO_CONF_PATH}")
    
    return _modify_bootloader_config(config_path) # Call with no new options

def _configure_vfio(cpu_vendor):
    """Guides the user through selecting a GPU and configures VFIO."""
    gpus = _get_gpu_devices()
    if not gpus:
        utils.error("No GPU devices found.")
        return None, False

    if len(gpus) == 1:
        utils.warn("Only one GPU detected. Passthrough will leave the host without a display.")

    utils.info("Please select the GPU to passthrough:")
    for i, gpu in enumerate(gpus, 1):
        print(f"  {utils.Fore.YELLOW}[{i}] {gpu['description']}")
    
    choice = int(utils.ask(f"Enter your choice [1-{len(gpus)}]:")) - 1
    selected_gpu = gpus[choice]
    bdf = selected_gpu['bdf']

    # Check IOMMU group
    iommu_group_path = Path(f"/sys/bus/pci/devices/{bdf}/iommu_group/devices")
    group_devices = [p.name for p in iommu_group_path.glob("*")]
    pci_bus_dev = bdf.rsplit('.', 1)[0]
    
    bad_group = any(not dev.startswith(pci_bus_dev) for dev in group_devices)
    if bad_group:
        utils.fail(f"Bad IOMMU group for GPU {bdf}! All devices in a group must be passed through together. This group contains unrelated devices.")
        return None, False
    
    # Get all device IDs in the group
    hw_ids = []
    for device_bdf in group_devices:
        vendor = (iommu_group_path.parent.parent / device_bdf / "vendor").read_text().strip()[2:]
        device = (iommu_group_path.parent.parent / device_bdf / "device").read_text().strip()[2:]
        hw_ids.append(f"{vendor}:{device}")
    
    hw_ids_str = ",".join(hw_ids)
    utils.log(f"Hardware IDs for IOMMU group: {hw_ids_str}")

    # Create modprobe config
    softdeps = {
        "0x10de": "nvidia nouveau", # NVIDIA
        "0x1002": "amdgpu radeon",   # AMD
        "0x8086": "i915",            # Intel
    }
    drivers_to_blacklist = softdeps.get(selected_gpu["vendor"], "").split()
    
    conf_content = f"options vfio-pci ids={hw_ids_str}\n"
    for driver in drivers_to_blacklist:
        conf_content += f"softdep {driver} pre: vfio-pci\n"
    
    utils.update_config_file(str(VFIO_CONF_PATH), ".*", conf_content)
    utils.log(f"Created {VFIO_CONF_PATH}")
    
    # Construct kernel options
    kernel_opts = ["iommu=pt", f"vfio-pci.ids={hw_ids_str}", "kvm.ignore_msrs=1"]
    if "GenuineIntel" in cpu_vendor:
        kernel_opts.insert(0, "intel_iommu=on")
        
    return " ".join(kernel_opts), True

def _rebuild_grub():
    """Finds and runs the correct grub-mkconfig command."""
    utils.info("Rebuilding GRUB configuration...")
    grub_cmd = "grub-mkconfig"
    if not Path("/usr/bin/grub-mkconfig").exists():
        grub_cmd = "grub2-mkconfig"

    grub_cfg_path = Path("/boot/grub/grub.cfg")
    if Path("/boot/grub2/grub.cfg").exists():
        grub_cfg_path = Path("/boot/grub2/grub.cfg")
    
    try:
        utils.run_with_spinner(["sudo", grub_cmd, "-o", str(grub_cfg_path)], Path.cwd())
        utils.info("GRUB configuration updated successfully.")
    except subprocess.CalledProcessError:
        utils.fail("Failed to rebuild GRUB config.")

# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================

def main(cpu_vendor: str):
    """Main entry point for the GPU passthrough module."""
    bootloader_type, config_path = _detect_bootloader()
    if not bootloader_type:
        utils.fail("Could not detect a supported bootloader (GRUB or systemd-boot).")
        return

    utils.info(f"Detected Bootloader: {bootloader_type.capitalize()}")
    config_changed = False

    if utils.yes_or_no("Revert/Remove existing GPU passthrough configurations?"):
        if _revert_vfio(bootloader_type, config_path):
            config_changed = True

    if utils.yes_or_no("Configure new GPU passthrough?"):
        new_opts, success = _configure_vfio(cpu_vendor)
        if success:
            if _modify_bootloader_config(config_path, new_opts):
                config_changed = True

    if config_changed and bootloader_type == "grub":
        if utils.yes_or_no("Bootloader configuration has changed. Rebuild GRUB now?"):
            _rebuild_grub()

    utils.warn("A reboot is required for any changes to take effect.")