"""
Module for detecting host system information.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import utils

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
        # We don't need the full output, just the return code.
        result = utils.run_command(["lsmod"], Path.cwd(), capture_output=True, check=False)
        kvm_loaded = "kvm" in result.stdout if result.returncode == 0 else False
    except utils.CommandExecutionError:
        kvm_loaded = False
    return virt_support, iommu_enabled, kvm_loaded

def get_system_info() -> SystemInfo:
    """Gathers all system info and returns the dataclass."""
    info = SystemInfo()
    info.distro = detect_distro()
    info.cpu_vendor = detect_cpu_vendor()
    (
        info.virt_support,
        info.iommu_enabled,
        info.kvm_loaded,
    ) = check_hardware_support()
    return info