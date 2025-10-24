#!/usr/bin/env python
"""
Hypervisor Phantom: An automated toolkit for preparing a Linux host for
hardware-passthrough virtualization with a focus on anti-cheat compatibility.

This script serves as the main entry point and menu for all modules.
"""

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Callable

# Import our custom utility functions and modules
import utils
from config import core
from modules import (
    auto_xml,
    gpu_passthrough,
    looking_glass,
    patch_kernel,
    patch_ovmf,
    patch_qemu,
    virtualization,
)


# ==============================================================================
# 1. SYSTEM STATE AND DETECTION
# ==============================================================================

@dataclass
class SystemInfo:
    """A dataclass to hold all detected host system information."""
    distro: str = "Unknown"
    cpu_vendor: str = "Unknown"
    virt_support: bool = False
    iommu_enabled: bool = False
    kvm_loaded: bool = False
    virt_support_name: str = field(init=False)
    iommu_support_name: str = field(init=False)

    def __post_init__(self):
        """Set dynamic names after initialization."""
        self.virt_support_name = {"GenuineIntel": "VT-x", "AuthenticAMD": "AMD-V"}.get(
            self.cpu_vendor, "Virtualization"
        )
        self.iommu_support_name = {"GenuineIntel": "VT-d", "AuthenticAMD": "AMD-Vi"}.get(
            self.cpu_vendor, "IOMMU"
        )


def detect_distro() -> str:
    """Detects the Linux distribution from /etc/os-release or package managers."""
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        lines = os_release.read_text().splitlines()
        distro_map = {line.split("=")[0]: line.split("=")[1].strip('"') for line in lines if "=" in line}
        distro_id = distro_map.get("ID", "").lower()

        if distro_id in ["arch", "manjaro", "endeavouros"]: return "Arch"
        if "suse" in distro_id: return "openSUSE"
        if distro_id in ["debian", "ubuntu", "linuxmint", "pop"]: return "Debian"
        if distro_id in ["fedora", "centos", "rhel"]: return "Fedora"

    # Fallback to checking for package managers
    if shutil.which("pacman"): return "Arch"
    if shutil.which("apt"): return "Debian"
    if shutil.which("zypper"): return "openSUSE"
    if shutil.which("dnf"): return "Fedora"

    return "Unknown"


def detect_cpu_vendor() -> str:
    """Detects the CPU vendor ID from /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.strip().startswith("vendor_id"):
                    return line.split(":", 1)[1].strip()
    except FileNotFoundError:
        utils.error("Could not read /proc/cpuinfo to determine CPU vendor.")
    return "Unknown"


def check_hardware_support() -> (bool, bool, bool):
    """Checks for virtualization, IOMMU, and KVM support."""
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        virt_support = "vmx" in cpuinfo or "svm" in cpuinfo
    except FileNotFoundError:
        virt_support = False

    iommu_path = Path("/sys/kernel/iommu_groups")
    iommu_enabled = iommu_path.is_dir() and any(iommu_path.iterdir())

    try:
        lsmod_output = utils.run_command(["lsmod"], Path.cwd(), capture_output=True).stdout
        kvm_loaded = "kvm" in lsmod_output
    except utils.CommandExecutionError:
        kvm_loaded = False

    return virt_support, iommu_enabled, kvm_loaded


# ==============================================================================
# 2. MAIN APPLICATION CLASS
# ==============================================================================

class HypervisorPhantomApp:
    """The main application class that orchestrates the UI and module calls."""

    def __init__(self):
        """Initializes the app and gathers system information."""
        self.system_info = SystemInfo()
        self._gather_system_info()
        self._define_menu()

    def _gather_system_info(self):
        """Populates the system_info object with detected properties."""
        self.system_info.distro = detect_distro()
        self.system_info.cpu_vendor = detect_cpu_vendor()
        (
            self.system_info.virt_support,
            self.system_info.iommu_enabled,
            self.system_info.kvm_loaded,
        ) = check_hardware_support()

        if self.system_info.distro == "Unknown":
            utils.warn("Could not determine Linux distribution. Some features may fail.")
        if self.system_info.cpu_vendor == "Unknown":
            utils.warn("Could not determine CPU vendor. Some features may fail.")

    def _define_menu(self):
        """Creates the menu structure and maps choices to functions."""
        self.menu_options: Dict[str, str] = {
            "1": "Virtualization Setup",
            "2": "QEMU (Patched) Setup",
            "3": "EDK2 (Patched) Setup",
            "4": "GPU Passthrough Setup",
            "5": "Kernel (Patched) Setup",
            "6": "Looking Glass Setup",
            "7": "Auto Libvirt XML Setup",
            "0": "Exit",
        }

        self.menu_actions: Dict[str, Callable[[], None]] = {
            "1": lambda: virtualization.main(self.system_info.distro),
            "2": lambda: patch_qemu.main(self.system_info.distro, self.system_info.cpu_vendor),
            "3": lambda: patch_ovmf.main(self.system_info.distro, self.system_info.cpu_vendor),
            "4": lambda: gpu_passthrough.main(self.system_info.cpu_vendor),
            "5": lambda: patch_kernel.main(self.system_info.distro, self.system_info.cpu_vendor),
            "6": lambda: looking_glass.main(self.system_info.distro, self.system_info.cpu_vendor),
            "7": lambda: auto_xml.main(self.system_info.cpu_vendor),
            "0": self._exit_app,
        }

    def _print_system_info_header(self):
        """Prints the formatted system information at the top of the menu."""
        utils.info(f"Distro: {self.system_info.distro} | CPU: {self.system_info.cpu_vendor}")

        def print_check(label, supported):
            icon = f"{utils.Fore.GREEN}✓" if supported else f"{utils.Fore.RED}✗"
            print(f"  {icon} {label}: {'Enabled' if supported else 'Disabled'}{utils.Style.RESET}")

        print_check(self.system_info.virt_support_name, self.system_info.virt_support)
        print_check(self.system_info.iommu_support_name, self.system_info.iommu_enabled)
        print_check("KVM Kernel Module", self.system_info.kvm_loaded)
        print(f"  ──────────────────────────────\n")

    def _exit_app(self):
        """Handles the application exit process."""
        if utils.yes_or_no("Clear the 'logs' directory before exiting?"):
            log_dir = Path("logs")
            if log_dir.is_dir():
                try:
                    shutil.rmtree(log_dir)
                    utils.log("Logs directory cleared.")
                except OSError as e:
                    utils.error(f"Could not remove logs directory: {e}")
        utils.info("Exiting.")
        sys.exit(0)

    def run(self):
        """Starts the main application loop."""
        while True:
            os.system("clear")
            utils.box_text(">> Hypervisor Phantom <<")
            self._print_system_info_header()

            for key, value in self.menu_options.items():
                color = utils.Fore.RED if key == "0" else utils.Fore.YELLOW
                print(f"  {color}[{key}] {utils.Fore.WHITE}{value}")

            choice = utils.quick_prompt("\n  Enter your choice: ")

            action = self.menu_actions.get(choice)
            if action:
                os.system("clear")
                utils.box_text(self.menu_options[choice])
                action()
                if choice != "0":  # Don't show "press any key" on exit
                    utils.quick_prompt(f"\n{utils.Fore.CYAN}[i] Press any key to return to the main menu...")
            else:
                utils.error("Invalid option, please try again.")
                utils.quick_prompt(f"\n{utils.Fore.CYAN}[i] Press any key to continue...")


# ==============================================================================
# 3. SCRIPT ENTRY POINT
# ==============================================================================

def main():
    """The main entry point of the script."""
    utils.setup_logging(log_dir=core.LOG_DIR)

    # Pre-flight check: Ensure the script is run with root privileges
    if os.geteuid() != 0:
        utils.fail("This script requires root privileges to manage packages and system files. Please run with 'sudo'.")

    try:
        app = HypervisorPhantomApp()
        app.run()
    except KeyboardInterrupt:
        print()  # Move to a new line after Ctrl+C
        utils.fail("Operation cancelled by user.")
    except Exception as e:
        utils.log_handler.exception("An unhandled exception occurred.")
        utils.fail(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    main()