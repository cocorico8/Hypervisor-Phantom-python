import os
import subprocess
import re
import getpass
import utils

# ==============================================================================
#  PACKAGE DEFINITIONS
# ==============================================================================

REQUIRED_PACKAGES = {
    "Arch": [
        "qemu-base", "edk2-ovmf", "libvirt", "dnsmasq", "virt-manager", "swtpm"
    ],
    "Debian": [
        "qemu-system-x86", "ovmf", "virt-manager", "libvirt-clients", "swtpm",
        "libvirt-daemon-system", "libvirt-daemon-config-network"
    ],
    "openSUSE": [
        "libvirt", "libvirt-client", "libvirt-daemon", "virt-manager",
        "qemu", "qemu-kvm", "ovmf", "qemu-tools", "swtpm"
    ],
    "Fedora": [
        "@virtualization", "swtpm"
    ]
}

# ==============================================================================
#  HELPER FUNCTIONS
# ==============================================================================

def run_command(command: list[str], check=False) -> subprocess.CompletedProcess:
    """A wrapper for running subprocesses and logging their output."""
    utils.log(f"Running command: {' '.join(command)}")
    return subprocess.run(command, capture_output=True, text=True, check=check)

def _configure_firewall_arch():
    """Performs Arch Linux specific firewall configuration for libvirt."""
    utils.info("Running Arch-specific firewall configuration...")

    if run_command(["pacman", "-Qs", "iptables-nft"]).returncode == 0:
        utils.log("iptables-nft detected. Configuring for iptables compatibility layer.")
        utils.update_config_file(
            '/etc/libvirt/network.conf',
            r'^#?\s*firewall_backend\s*=',
            'firewall_backend = "iptables"',
            append_if_missing=True
        )
        run_command(["sudo", "systemctl", "enable", "--now", "nftables.service"])
        utils.info("nftables service enabled.")
    
    elif run_command(["pacman", "-Qs", "iptables"]).returncode == 0:
        utils.log("Legacy iptables detected.")
        if run_command(["pacman", "-Qs", "ebtables"]).returncode != 0:
            utils.fail(
                "The 'ebtables' AUR package is required for legacy iptables. "
                "Please install it manually (e.g., 'yay -S ebtables') and re-run."
            )
        run_command(["sudo", "systemctl", "enable", "--now", "iptables.service"])
        utils.info("iptables service enabled.")

    elif run_command(["pacman", "-Qs", "nftables"]).returncode == 0:
        utils.warn("Nftables without iptables compatibility isn't ideal for libvirt.")
        utils.info("See: https://bbs.archlinux.org/viewtopic.php?id=284664")
        run_command(["sudo", "systemctl", "enable", "--now", "nftables.service"])
        utils.info("nftables service enabled.")
    
    else:
        utils.error("Unsupported firewall implementation. Manual configuration may be required.")

def _configure_system_installation():
    """Sets up libvirt/qemu configs, user groups, and services for all distros."""
    utils.info("Configuring system for virtualization...")
    libvirtd_conf = '/etc/libvirt/libvirtd.conf'
    qemu_conf = '/etc/libvirt/qemu.conf'
    
    current_user = os.environ.get("SUDO_USER", getpass.getuser())
    if current_user == "root":
        utils.fail("Cannot determine original user. Please run with 'sudo' from a user account.")
        
    utils.info(f"Configuring for user: {current_user}")

    utils.update_config_file(libvirtd_conf, r'^#?unix_sock_group\s*=', 'unix_sock_group = "libvirt"', True)
    utils.update_config_file(libvirtd_conf, r'^#?unix_sock_rw_perms\s*=', 'unix_sock_rw_perms = "0770"', True)
    utils.update_config_file(qemu_conf, r'^#?user\s*=', f'user = "{current_user}"', True)
    utils.update_config_file(qemu_conf, r'^#?group\s*=', f'group = "{current_user}"', True)
    
    for group in ["kvm", "libvirt"]:
        try:
            groups_output = run_command(["id", "-nG", current_user], check=True).stdout
            if re.search(r'\b' + group + r'\b', groups_output):
                utils.info(f"User {current_user} is already in group '{group}'.")
            else:
                run_command(["sudo", "usermod", "-aG", group, current_user], check=True)
                utils.info(f"Added user {current_user} to group '{group}'.")
        except subprocess.CalledProcessError as e:
            utils.error(f"Failed to add user to group {group}: {e.stderr}")

    run_command(["sudo", "systemctl", "enable", "--now", "libvirtd.socket"])
    utils.info("Enabled and started libvirtd.socket.")
    
    if run_command(["sudo", "virsh", "net-info", "default"]).returncode != 0:
        utils.info("Default libvirt network not active. Starting it...")
        run_command(["sudo", "virsh", "net-autostart", "default"])
        run_command(["sudo", "virsh", "net-start", "default"])
        utils.info("Started and enabled default libvirt network.")
    else:
        utils.info("Default libvirt network is already active.")

# ==============================================================================
#  MAIN FUNCTION
# ==============================================================================

def main(distro: str):
    """
    Main entry point for the virtualization setup module.
    """
    if distro not in REQUIRED_PACKAGES:
        utils.fail(f"Virtualization setup is not supported for the detected distro: {distro}")

    packages_to_install = REQUIRED_PACKAGES[distro]
    utils.install_required_packages("Virtualization", packages_to_install, distro)

    if distro == "Arch":
        _configure_firewall_arch()
    else:
        utils.info(f"No specific firewall configuration needed for {distro}.")

    _configure_system_installation()
    
    utils.warn("A logout/reboot is required for all group and service changes to take effect.")