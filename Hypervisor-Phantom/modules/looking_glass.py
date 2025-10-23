import os
import shutil
import subprocess
import getpass
from pathlib import Path
import utils
import requests

# ==============================================================================
#  CONSTANTS AND CONFIGURATION
# ==============================================================================
SRC_DIR = Path("src")
LG_VERSION = "B7"
LG_ARCHIVE_NAME = f"looking-glass-{LG_VERSION}.tar.gz"
LG_SOURCE_DIR_NAME = f"looking-glass-{LG_VERSION}"
LG_URL = "https://looking-glass.io/artifact/stable/source"

# ==============================================================================
#  PACKAGE DEFINITIONS
# ==============================================================================
REQUIRED_PACKAGES = {
    "Arch": [
        "cmake",
        "gcc",
        "libgl",
        "fontconfig",
        "spice-protocol",
        "make",
        "nettle",
        "pkgconf",
        "libxi",
        "libxinerama",
        "wayland-protocols",
        "ttf-dejavu",
        "libsamplerate",
        "curl",
    ],
    "Debian": [
        "binutils-dev",
        "cmake",
        "fonts-dejavu-core",
        "libfontconfig-dev",
        "gcc",
        "g++",
        "pkg-config",
        "libegl-dev",
        "libgl-dev",
        "libspice-protocol-dev",
        "nettle-dev",
        "libx11-dev",
        "libwayland-dev",
        "wayland-protocols",
        "libpipewire-0.3-dev",
        "libsamplerate0-dev",
        "curl",
    ],
    "openSUSE": [
        "binutils-devel",
        "clang",
        "cmake",
        "dejavu-fonts",
        "fontconfig-devel",
        "gcc",
        "gcc-c++",
        "glibc-locale",
        "libdecor-devel",
        "libglvnd-devel",
        "libnettle-devel",
        "libsamplerate-devel",
        "libSDL2-2_0-0",
        "libSDL2_ttf-2_0-0",
        "libvulkan1",
        "libwayland-egl1",
        "libxkbcommon-devel",
        "libXpresent-devel",
        "libXrandr-devel",
        "libXss-devel",
        "make",
        "Mesa-libGLESv3-devel",
        "pipewire-devel",
        "pkgconf-pkg-config",
        "pkgconf",
        "spice-protocol-devel",
        "vulkan-devel",
        "wayland-devel",
        "zlib-devel-static",
        "libXi-devel",
        "libXinerama-devel",
        "libXcursor-devel",
        "dkms",
        "Mesa-libGL-devel",
        "Mesa-libGLESv2-devel",
        "libzstd-devel-static",
        "libconfig++-devel",
        "SDL2-devel",
        "curl",
    ],
    "Fedora": [
        "cmake",
        "gcc",
        "gcc-c++",
        "libglvnd-devel",
        "fontconfig-devel",
        "spice-protocol",
        "make",
        "nettle-devel",
        "pkgconf-pkg-config",
        "binutils-devel",
        "libXi-devel",
        "libXinerama-devel",
        "libXcursor-devel",
        "libXpresent-devel",
        "libxkbcommon-x11-devel",
        "wayland-devel",
        "wayland-protocols-devel",
        "libXScrnSaver-devel",
        "libXrandr-devel",
        "dejavu-sans-mono-fonts",
        "libdecor-devel",
        "pipewire-devel",
        "libsamplerate-devel",
        "dkms",
        "kernel-devel",
        "kernel-headers",
        "curl",
    ],
}

# ==============================================================================
#  CORE LOGIC
# ==============================================================================


def _install_looking_glass(cpu_vendor: str):
    """Downloads, patches, compiles, and installs the Looking Glass client."""
    SRC_DIR.mkdir(exist_ok=True)
    lg_archive_path = SRC_DIR / LG_ARCHIVE_NAME
    lg_source_path = SRC_DIR / LG_SOURCE_DIR_NAME

    # 1. Download
    utils.info(f"Downloading Looking Glass {LG_VERSION} source...")
    try:
        res = requests.get(LG_URL, timeout=30)
        res.raise_for_status()
        lg_archive_path.write_bytes(res.content)
    except requests.RequestException as e:
        utils.fail(f"Failed to download Looking Glass: {e}")

    # 2. Extract
    utils.log(f"Extracting archive: {lg_archive_path}")
    shutil.unpack_archive(lg_archive_path, SRC_DIR)
    lg_archive_path.unlink()  # Clean up archive

    # 3. Patch Vendor ID
    utils.log("Patching KVMFR module for guest detection...")
    kvmfr_module_file = lg_source_path / "module" / "kvmfr.c"
    vendor_map = {"GenuineIntel": "0x8086", "AuthenticAMD": "0x1022"}
    new_vendor_id = vendor_map.get(cpu_vendor, "0x1022")  # Default to AMD

    try:
        content = kvmfr_module_file.read_text()
        content = content.replace("0x1af4", new_vendor_id)  # KVM Vendor ID
        content = content.replace("0x1110", new_vendor_id)  # KVM Device ID
        kvmfr_module_file.write_text(content)
    except IOError as e:
        utils.fail(f"Failed to patch KVMFR module: {e}")

    # 4. Compile and Install
    build_dir = lg_source_path / "client" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    utils.info("Configuring Looking Glass build with CMake...")
    utils.run_with_spinner(["cmake", ".."], cwd=build_dir)

    utils.info("Compiling and installing Looking Glass...")
    utils.run_with_spinner(["sudo", "make", "install"], cwd=build_dir)

    # 5. Cleanup
    utils.log("Cleaning up source directory...")
    shutil.rmtree(lg_source_path)
    utils.info("Looking Glass client installed successfully.")


def _configure_ivshmem_shmem():
    """Configures the shared memory file needed by Looking Glass."""
    utils.info("Configuring shared memory file for Looking Glass...")
    original_user = os.environ.get("SUDO_USER", getpass.getuser())

    # 1. Create tmpfiles.d config for persistence across reboots
    conf_path = Path("/etc/tmpfiles.d/10-looking-glass.conf")
    conf_content = f"f /dev/shm/looking-glass 0660 {original_user} kvm -"
    utils.update_config_file(
        str(conf_path), ".*", conf_content
    )  # Replaces or creates the file
    utils.log(f"Created systemd-tmpfiles config at {conf_path}")

    # 2. Create the file immediately
    shm_path = Path("/dev/shm/looking-glass")
    shm_path.touch(exist_ok=True)
    shutil.chown(shm_path, user=original_user, group="kvm")
    shm_path.chmod(0o660)
    utils.log(f"Created and set permissions on {shm_path}")

    # 3. Instruct user about the alias
    utils.info(
        "Configuration successful. To easily run the client, you can add this alias to your shell's config file (e.g., ~/.bashrc):"
    )
    alias_command = "alias lg='/usr/local/bin/looking-glass-client -S -K -1'"
    print(f"\n  {utils.Fore.CYAN}{alias_command}\n")
    utils.warn(
        "TIP: After adding the alias, run 'source ~/.bashrc' or open a new terminal, then simply type 'lg' to start."
    )


def _configure_ivshmem_kvmfr():
    """Configures the KVMFR kernel module for high-performance DMA transfers."""
    utils.info("Configuring KVMFR kernel module...")
    original_user = os.environ.get("SUDO_USER", getpass.getuser())
    memory_size_mb = "32"

    try:
        # Load the module for the current session
        utils.run_with_spinner(
            ["sudo", "modprobe", "kvmfr", f"static_size_mb={memory_size_mb}"],
            Path.cwd(),
        )

        # Make it persistent
        (Path("/etc/modprobe.d/kvmfr.conf")).write_text(
            f"options kvmfr static_size_mb={memory_size_mb}\n"
        )
        utils.log("Created modprobe options file.")

        (Path("/etc/modules-load.d/kvmfr.conf")).write_text(
            "# KVMFR Looking Glass module\nkvmfr\n"
        )
        utils.log("Configured module to load on boot.")

        # Set permissions on the device file
        kvmfr_dev = Path("/dev/kvmfr0")
        if kvmfr_dev.exists():
            shutil.chown(kvmfr_dev, user=original_user, group="kvm")
            utils.log(f"Set permissions on {kvmfr_dev}")
        else:
            utils.warn(
                f"Device {kvmfr_dev} not found. Permissions not set. It may be created on next boot."
            )

        utils.info("KVMFR module configured successfully.")
    except (IOError, subprocess.CalledProcessError) as e:
        utils.fail(f"Failed to configure KVMFR module: {e}")


# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================


def main(distro: str, cpu_vendor: str):
    """Main entry point for the Looking Glass setup module."""
    if distro not in REQUIRED_PACKAGES:
        utils.fail(
            f"Looking Glass setup is not supported for the detected distro: {distro}"
        )

    utils.install_required_packages("Looking Glass", REQUIRED_PACKAGES[distro], distro)

    if utils.yes_or_no("Install the Looking Glass client?"):
        _install_looking_glass(cpu_vendor)

    if utils.yes_or_no(
        "Configure IVSHMEM using the standard shared memory file method?"
    ):
        _configure_ivshmem_shmem()

    if utils.yes_or_no(
        "Configure the KVMFR kernel module for high-performance capture? (Recommended)"
    ):
        _configure_ivshmem_kvmfr()

    utils.info("Looking Glass setup process finished.")
