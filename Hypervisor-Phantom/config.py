"""
Central configuration file for the Hypervisor Phantom toolkit.

This file contains all static configuration data, such as version numbers,
URLs, package lists, and file paths. By centralizing this information,
updates and maintenance become significantly easier.
"""

from pathlib import Path


# ==============================================================================
#  1. CORE APPLICATION SETTINGS
# ==============================================================================
class Core:
    """Core application settings."""
    LOG_DIR = "logs"
    SOURCE_DIR = Path("src")
    OUTPUT_DIR = Path("output")
    RESOURCE_DIR = "resources"


# ==============================================================================
#  2. VERSION CONTROL
# ==============================================================================
class Versions:
    """Version tags and strings for all external software."""
    QEMU = "10.1.1"
    EDK2_TAG = "edk2-stable202508"
    LOOKING_GLASS = "B7"
    KERNEL_MAJOR = "6"
    KERNEL_MINOR = "14"
    KERNEL_PATCH = "latest"

    # This computed property makes it easy to change kernel versions
    @property
    def KERNEL_FULL(self):
        return f"{self.KERNEL_MAJOR}.{self.KERNEL_MINOR}-{self.KERNEL_PATCH}"


# ==============================================================================
#  3. EXTERNAL RESOURCES AND URLS
# ==============================================================================
class URLs:
    """URLs for all downloadable resources."""
    # QEMU
    QEMU_BASE = "https://download.qemu.org"
    QEMU_ARCHIVE = f"qemu-{Versions.QEMU}.tar.xz"
    QEMU_DOWNLOAD = f"{QEMU_BASE}/{QEMU_ARCHIVE}"
    QEMU_SIGNATURE = f"{QEMU_DOWNLOAD}.sig"

    # EDK2 / OVMF
    EDK2_GIT = "https://github.com/tianocore/edk2.git"

    # Kernel (linux-tkg)
    TKG_GIT = "https://github.com/Frogging-Family/linux-tkg.git"

    # Looking Glass
    LOOKING_GLASS_SRC = "https://looking-glass.io/artifact/stable/source"

    # Microsoft Secure Boot Certificates
    MS_SB_BASE = "https://github.com/microsoft/secureboot_objects/raw/main"


# ==============================================================================
#  4. PACKAGE DEFINITIONS
# ==============================================================================
class Packages:
    """
    Package lists for various Linux distributions, organized by functionality.
    """
    VIRTUALIZATION = {
        "Arch": ["qemu-base", "edk2-ovmf", "libvirt", "dnsmasq", "virt-manager", "swtpm"],
        "Debian": [
            "qemu-system-x86", "ovmf", "virt-manager", "libvirt-clients",
            "libvirt-daemon-system", "swtpm",
        ],
        "openSUSE": [
            "libvirt", "libvirt-client", "libvirt-daemon", "virt-manager", "qemu-kvm",
            "ovmf", "qemu-tools", "swtpm",
        ],
        "Fedora": ["@virtualization", "swtpm"],
    }
    
    QEMU_BUILD = {
        "Arch": [
            "base-devel", "ninja", "glib2", "python-packaging", "gnupg", "patch",
            "spice", "gtk3", "libusb", "usbredir", "acpica",
        ],
        "Debian": [
            "build-essential", "ninja-build", "libfdt-dev", "libglib2.0-dev",
            "libpixman-1-dev", "python3-venv", "zlib1g-dev", "gnupg", "patch",
            "libspice-server-dev", "libusb-1.0-0-dev", "libusbredirhost-dev", "acpica-tools",
        ],
        "openSUSE": [
            "gcc-c++", "ninja", "glib2-devel", "libpixman-1-0-devel", "gpg2",
            "patch", "spice-server-devel", "libusb-1_0-devel", "libusbredir-devel",
            "acpica", "make", "curl", "bzip2",
        ],
        "Fedora": [
            "gcc", "gcc-c++", "ninja-build", "glib2-devel", "libfdt-devel",
            "pixman-devel", "gnupg2", "patch", "spice-server-devel",
            "libusb1-devel", "usbredir-devel", "acpica-tools", "zlib-devel",
        ],
    }

    OVMF_BUILD = {
        "Arch": ["base-devel", "acpica", "git", "nasm", "python", "patch", "virt-firmware"],
        "Debian": [
            "build-essential", "uuid-dev", "acpica-tools", "git", "nasm",
            "python-is-python3", "patch", "python3-dev",
        ],
        "openSUSE": [
            "gcc-c++", "make", "acpica", "git", "nasm", "python3", "libuuid-devel",
            "patch", "virt-firmware",
        ],
        "Fedora": [
            "gcc", "gcc-c++", "make", "acpica-tools", "git", "nasm", "python3",
            "libuuid-devel", "patch", "python3-devel", "virt-firmware",
        ],
    }
    
    KERNEL_BUILD = {
        "Arch": ["base-devel", "git", "bc", "pahole", "cpio", "perl", "tar", "xmlto"],
        "Debian": ["build-essential", "git", "bc", "libelf-dev", "bison", "flex", "libssl-dev", "dwarves"],
        "openSUSE": ["gcc", "git", "bc", "libelf-devel", "bison", "flex", "libopenssl-devel", "dwarves"],
        "Fedora": ["gcc", "git", "bc", "elfutils-libelf-devel", "bison", "flex", "openssl-devel", "dwarves", "perl-FindBin"],
    }
    
    LOOKING_GLASS_BUILD = {
        "Arch": [
            "cmake", "gcc", "libgl", "fontconfig", "spice-protocol", "make",
            "nettle", "pkgconf", "libxi", "libxinerama", "wayland-protocols",
            "ttf-dejavu", "libsamplerate", "curl",
        ],
        "Debian": [
            "cmake", "gcc", "g++", "pkg-config", "libfontconfig-dev", "libgl-dev",
            "libspice-protocol-dev", "nettle-dev", "libx11-dev", "wayland-protocols",
            "libpipewire-0.3-dev", "libsamplerate0-dev", "curl", "fonts-dejavu-core",
        ],
        "openSUSE": [
            "cmake", "gcc-c++", "pkgconf", "fontconfig-devel", "Mesa-libGL-devel",
            "spice-protocol-devel", "libnettle-devel", "libXi-devel", "libXinerama-devel",
            "wayland-protocols-devel", "pipewire-devel", "libsamplerate-devel", "curl", "dejavu-fonts", "dkms",
        ],
        "Fedora": [
            "cmake", "gcc-c++", "pkgconf", "fontconfig-devel", "libglvnd-devel",
            "spice-protocol-devel", "nettle-devel", "libXi-devel", "libXinerama-devel",
            "wayland-protocols-devel", "pipewire-devel", "libsamplerate-devel", "curl",
            "dejavu-sans-mono-fonts", "dkms", "kernel-devel", "kernel-headers",
        ],
    }


# ==============================================================================
#  5. STATIC PATHS AND FILENAMES
# ==============================================================================
class Paths:
    """Static paths used throughout the application."""
    # System paths
    LIBVIRTD_CONF = Path("/etc/libvirt/libvirtd.conf")
    QEMU_LIBVIRT_CONF = Path("/etc/libvirt/qemu.conf")
    NVRAM_DIR = Path("/var/lib/libvirt/qemu/nvram")

    # Relative paths to resources within the project
    QEMU_PATCH_DIR = Path(Core.RESOURCE_DIR) / "patches" / "QEMU"
    OVMF_PATCH_DIR = Path(Core.RESOURCE_DIR) / "patches" / "EDK2"
    KERNEL_PATCH_DIR = Path(Core.RESOURCE_DIR) / "patches" / "Kernel"
    XML_TEMPLATE_DIR = Path(Core.RESOURCE_DIR) / "xml" / "template"

# ==============================================================================
#  6. SPOOFING DATA
# ==============================================================================
class Spoofing:
    """Static data lists used for spoofing hardware identifiers in QEMU."""

    IDE_CD_MODELS = [
        "HL-DT-ST BD-RE WH16NS60", "HL-DT-ST DVDRAM GH24NSC0", "HL-DT-ST BD-RE BH16NS40",
        "HL-DT-ST DVD+-RW GT80N", "HL-DT-ST DVD-RAM GH22NS30", "HL-DT-ST DVD+RW GCA-4040N",
        "Pioneer BDR-XD07B", "Pioneer DVR-221LBK", "Pioneer BDR-209DBK", "Pioneer DVR-S21WBK",
        "Pioneer BDR-XD05B", "ASUS BW-16D1HT", "ASUS DRW-24B1ST", "ASUS SDRW-08D2S-U",
        "ASUS BC-12D2HT", "ASUS SBW-06D2X-U", "Samsung SH-224FB", "Samsung SE-506BB",
        "Samsung SH-B123L", "Samsung SE-208GB", "Samsung SN-208DB", "Sony NEC Optiarc AD-5280S",
        "Sony DRU-870S", "Sony BWU-500S", "Sony NEC Optiarc AD-7261S", "Sony AD-7200S",
        "Lite-On iHAS124-14", "Lite-On iHBS112-04", "Lite-On eTAU108", "Lite-On iHAS324-17",
        "Lite-On eBAU108", "HP DVD1260i", "HP DVD640", "HP BD-RE BH30L", "HP DVD Writer 300n",
        "HP DVD Writer 1265i",
    ]

    IDE_CFATA_MODELS = [
        "SanDisk Ultra microSDXC UHS-I", "SanDisk Extreme microSDXC UHS-I",
        "SanDisk High Endurance microSDXC", "SanDisk Industrial microSD", "SanDisk Mobile Ultra microSDHC",
        "Samsung EVO Select microSDXC", "Samsung PRO Endurance microSDHC", "Samsung PRO Plus microSDXC",
        "Samsung EVO Plus microSDXC", "Samsung PRO Ultimate microSDHC", "Kingston Canvas React Plus microSD",
        "Kingston Canvas Go! Plus microSD", "Kingston Canvas Select Plus microSD",
        "Kingston Industrial microSD", "Kingston Endurance microSD", "Lexar Professional 1066x microSDXC",
        "Lexar High-Performance 633x microSDHC", "Lexar PLAY microSDXC", "Lexar Endurance microSD",
        "Lexar Professional 1000x microSDHC", "PNY Elite-X microSD", "PNY PRO Elite microSD",
        "PNY High Performance microSD", "PNY Turbo Performance microSD", "PNY Premier-X microSD",
        "Transcend High Endurance microSDXC", "Transcend Ultimate microSDXC", "Transcend Industrial Temp microSD",
        "Transcend Premium microSDHC", "Transcend Superior microSD", "ADATA Premier Pro microSDXC",
        "ADATA XPG microSDXC", "ADATA High Endurance microSDXC", "ADATA Premier microSDHC",
        "ADATA Industrial microSD", "Toshiba Exceria Pro microSDXC", "Toshiba Exceria microSDHC",
        "Toshiba M203 microSD", "Toshiba N203 microSD", "Toshiba High Endurance microSD",
    ]

    DEFAULT_DRIVE_MODELS = [
        "Samsung SSD 970 EVO 1TB", "Samsung SSD 860 QVO 1TB", "Samsung SSD 850 PRO 1TB",
        "Samsung SSD T7 Touch 1TB", "Samsung SSD 840 EVO 1TB", "WD Blue SN570 NVMe SSD 1TB",
        "WD Black SN850 NVMe SSD 1TB", "WD Green 1TB SSD", "WD Blue 3D NAND 1TB SSD",
        "Crucial P3 1TB PCIe 3.0 3D NAND NVMe SSD", "Seagate BarraCuda SSD 1TB",
        "Seagate FireCuda 520 SSD 1TB", "Seagate IronWolf 110 SSD 1TB", "SanDisk Ultra 3D NAND SSD 1TB",
        "Seagate Fast SSD 1TB", "Crucial MX500 1TB 3D NAND SSD", "Crucial P5 Plus NVMe SSD 1TB",
        "Crucial BX500 1TB 3D NAND SSD", "Kingston A2000 NVMe SSD 1TB", "Kingston KC2500 NVMe SSD 1TB",
        "Kingston A400 SSD 1TB", "Kingston HyperX Savage SSD 1TB", "SanDisk SSD PLUS 1TB",
        "SanDisk Ultra 3D 1TB NAND SSD",
    ]


# Create singleton instances for easy import
# This allows you to do `from config import versions` instead of `from config import Versions`
# and then `versions = Versions()`. It's a convenient shortcut.
core = Core()
versions = Versions()
urls = URLs()
packages = Packages()
paths = Paths()
spoofing = Spoofing()