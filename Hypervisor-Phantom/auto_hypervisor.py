#!/usr/bin/env python

import os
import shutil
import subprocess
import sys

# Import our custom utility functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utils

# And all our modules
from modules import virtualization
from modules import patch_qemu
from modules import patch_ovmf
from modules import gpu_passthrough
from modules import patch_kernel
from modules import looking_glass
from modules import auto_xml

# ==============================================================================
#  GLOBAL VARIABLES
# ==============================================================================
DISTRO = "Unknown"
VENDOR_ID = "Unknown"

# ==============================================================================
#  SYSTEM DETECTION FUNCTIONS
# ==============================================================================

def detect_distro() -> str:
    """
    Detects the Linux distribution based on /etc/os-release or package managers.
    Replaces the detect_distro() function in the bash script.
    """
    # First, try reading /etc/os-release
    if os.path.isfile("/etc/os-release"):
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    distro_id = line.strip().split("=")[1].strip('"')
                    # Map IDs to our categories
                    if distro_id in ["arch", "manjaro", "endeavouros", "arcolinux"]:
                        return "Arch"
                    if distro_id in ["opensuse-tumbleweed", "opensuse-leap", "sles"]:
                        return "openSUSE"
                    if distro_id in ["debian", "ubuntu", "linuxmint", "pop", "kali"]:
                        return "Debian"
                    if distro_id in ["fedora", "centos", "rhel", "rocky", "almalinux"]:
                        return "Fedora"

    # Fallback to checking for package managers if /etc/os-release fails
    if shutil.which("pacman"):
        return "Arch"
    if shutil.which("apt"):
        return "Debian"
    if shutil.which("zypper"):
        return "openSUSE"
    if shutil.which("dnf"):
        return "Fedora"

    return "Unknown"

def detect_cpu_vendor() -> str:
    """
    Detects the CPU vendor ID from /proc/cpuinfo.
    Replaces the cpu_vendor_id() function.
    """
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.strip().startswith("vendor_id"):
                    return line.split(":")[1].strip()
    except Exception as e:
        utils.error(f"Could not read /proc/cpuinfo: {e}")
        return "Unknown"
    return "Unknown"

def print_system_info():
    """
    Checks for virtualization, IOMMU, and KVM support and prints a summary.
    Replaces the print_system_info() function.
    """
    output = ""
    show_output = False

    virt_map = {"GenuineIntel": "VT-x", "AuthenticAMD": "AMD-V"}
    iommu_map = {"GenuineIntel": "VT-d", "AuthenticAMD": "AMD-Vi"}

    virt_name = virt_map.get(VENDOR_ID, "Virtualization")
    iommu_name = iommu_map.get(VENDOR_ID, "IOMMU")

    # 1. Check for Virtualization support
    try:
        with open("/proc/cpuinfo") as f:
            content = f.read()
            if "vmx" in content or "svm" in content:
                output += f"\n  {utils.Fore.GREEN}✓ {virt_name} (Virtualization): Supported"
            else:
                output += f"\n  {utils.Fore.RED}✗ {virt_name} (Virtualization): Not supported"
                show_output = True
    except FileNotFoundError:
        output += f"\n  {utils.Fore.YELLOW}? Could not check for virtualization support."
        show_output = True

    # 2. Check for IOMMU support
    iommu_path = "/sys/kernel/iommu_groups"
    if os.path.isdir(iommu_path) and len(os.listdir(iommu_path)) > 0:
        output += f"\n  {utils.Fore.GREEN}✓ {iommu_name} (IOMMU): Enabled"
    else:
        output += f"\n  {utils.Fore.RED}✗ {iommu_name} (IOMMU): Not enabled"
        show_output = True

    # 3. Check for KVM modules
    try:
        result = subprocess.run(["lsmod"], capture_output=True, text=True, check=True)
        if "kvm" in result.stdout:
            output += f"\n  {utils.Fore.GREEN}✓ KVM Kernel Module: Loaded"
        else:
            output += f"\n  {utils.Fore.RED}✗ KVM Kernel Module: Not loaded"
            show_output = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        output += f"\n  {utils.Fore.YELLOW}? Could not check for KVM kernel modules."
        show_output = True

    if show_output:
        print(output)
        print(f"\n  ──────────────────────────────\n{utils.Style.RESET}")

# ==============================================================================
#  MAIN MENU AND APPLICATION LOGIC
# ==============================================================================

def main_menu():
    """
    Displays the main menu and handles user choices.
    """
    # Using a dictionary as a dispatcher is a clean, Pythonic way
    # to replace a bash 'case' statement.
    menu_options = {
        "1": "Virtualization Setup",
        "2": "QEMU (Patched) Setup",
        "3": "EDK2 (Patched) Setup",
        "4": "GPU Passthrough Setup",
        "5": "Kernel (Patched) Setup",
        "6": "Looking Glass Setup",
        "7": "Auto Libvirt XML Setup",
        "0": "Exit",
    }

    while True:
        os.system("clear")
        utils.box_text(">> Hypervisor Phantom <<")
        print_system_info()

        for key, value in menu_options.items():
            if key == "0":
                continue # Print exit option last
            print(f"  {utils.Fore.YELLOW}[{key}] {utils.Fore.WHITE}{value}")

        print(f"\n  {utils.Fore.RED}[0] {utils.Fore.WHITE}{menu_options['0']}\n")

        choice = utils.quick_prompt("  Enter your choice: ")

        os.system("clear")
        if choice in menu_options:
            utils.box_text(menu_options[choice])

            if choice == "1":
                virtualization.main(DISTRO)
            elif choice == "2":
                patch_qemu.main(DISTRO, VENDOR_ID)
            elif choice == "3":
                patch_ovmf.main(DISTRO, VENDOR_ID)
            elif choice == "4":
                gpu_passthrough.main(VENDOR_ID)
            elif choice == "5":
                patch_kernel.main(DISTRO, VENDOR_ID)
            elif choice == "6":
                looking_glass.main(DISTRO, VENDOR_ID)
            elif choice == "7":
                auto_xml.main(VENDOR_ID)
            elif choice == "0":
                if utils.yes_or_no("Do you want to clear the logs directory?"):
                    try:
                        if os.path.isdir("logs"):
                            shutil.rmtree("logs")
                            utils.log("Logs directory cleared.")
                    except Exception as e:
                        utils.error(f"Could not remove logs directory: {e}")
                sys.exit(0)

            utils.quick_prompt(f"\n{utils.Fore.CYAN}[i] Press any key to return to the main menu...")
        else:
            utils.error("Invalid option, please try again.")
            utils.quick_prompt(f"\n{utils.Fore.CYAN}[i] Press any key to continue...")


def main():
    """
    The main entry point of the script.
    """

    # 1. Initialize logging
    utils.setup_logging()

    # 2. Detect system environment
    global DISTRO, VENDOR_ID
    DISTRO = detect_distro()
    VENDOR_ID = detect_cpu_vendor()
    utils.log(f"Detected Distro: {DISTRO}")
    utils.log(f"Detected CPU Vendor: {VENDOR_ID}")
    
    if DISTRO == "Unknown" or VENDOR_ID == "Unknown":
        utils.warn("Could not fully determine system environment. Some features may not work.")

    # 3. Launch the main menu
    main_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n") # Move to a new line after Ctrl+C
        utils.fail("Operation cancelled by user.")
    except Exception as e:
        utils.fail(f"An unexpected error occurred: {e}")