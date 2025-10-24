"""
Module for setting up the host system's virtualization environment.

This module handles:
1. Installing required packages (libvirt, QEMU, virt-manager, etc.) for the host OS.
2. Performing distribution-specific configurations (e.g., Arch Linux firewall setup).
3. Configuring libvirt and QEMU permissions and user settings.
4. Adding the user to the necessary user groups (`libvirt`, `kvm`).
5. Enabling and starting essential libvirt services and networks.
"""

import getpass
import os
from pathlib import Path

# Import our custom utility functions
import utils
from config import packages, paths

# ==============================================================================
# MAIN CLASS
# ==============================================================================

class VirtualizationSetup:
    """Orchestrates the setup of host virtualization packages and services."""

    def __init__(self, distro: str):
        """
        Initializes the setup process.

        Args:
            distro: The name of the detected Linux distribution.
        """
        if distro not in packages.VIRTUALIZATION:
            utils.fail(f"Virtualization setup is not supported for distro: {distro}")

        self.distro = distro
        self.user = self._get_original_user()

        # Define configuration paths
        self.LIBVIRTD_CONF = paths.LIBVIRTD_CONF
        self.QEMU_CONF = paths.QEMU_LIBVIRT_CONF

    @staticmethod
    def _get_original_user() -> str:
        """
        Safely determines the original user who invoked sudo.

        Returns:
            The username of the original user.
        """
        user = os.environ.get("SUDO_USER")
        if not user or user == "root":
            utils.fail(
                "Cannot determine the original user. "
                "Please run this script from a standard user account using 'sudo'."
            )
        utils.info(f"Configuring system for user: {user}")
        return user

    def _install_packages(self):
        """Installs the necessary virtualization packages for the host distro."""
        virt_packages = packages.VIRTUALIZATION[self.distro]
        utils.install_required_packages("Virtualization", virt_packages, self.distro)

    def _configure_firewall(self):
        """Performs distribution-specific firewall configuration."""
        if self.distro == "Arch":
            self._configure_arch_firewall()
        else:
            utils.info(f"No specific firewall configuration needed for {self.distro}.")

    def _configure_arch_firewall(self):
        """Handles Arch Linux specific firewall setup for libvirt."""
        utils.info("Running Arch-specific firewall configuration...")

        # Check for iptables-nft compatibility package
        if utils.run_command(["pacman", "-Qs", "iptables-nft"], Path.cwd(), check=False).returncode == 0:
            self._handle_iptables_nft()
        # Check for legacy iptables
        elif utils.run_command(["pacman", "-Qs", "iptables"], Path.cwd(), check=False).returncode == 0:
            self._handle_legacy_iptables()
        # Check for standalone nftables
        elif utils.run_command(["pacman", "-Qs", "nftables"], Path.cwd(), check=False).returncode == 0:
            self._handle_nftables()
        else:
            utils.error("No supported firewall implementation (iptables/nftables) found.")

    def _handle_iptables_nft(self):
        """Configures for iptables-nft compatibility layer."""
        utils.log("iptables-nft detected. Configuring libvirt for iptables backend.")
        utils.update_config_file(
            self.LIBVIRTD_CONF,
            r"^#?\s*firewall_backend\s*=",
            'firewall_backend = "iptables"',
            append_if_missing=True,
        )
        utils.run_command(["sudo", "systemctl", "enable", "--now", "nftables.service"], Path.cwd())
        utils.info("nftables.service enabled and started.")

    @staticmethod
    def _handle_legacy_iptables():
        """Configures for legacy iptables and ensures ebtables is present."""
        utils.log("Legacy iptables detected.")
        if utils.run_command(["pacman", "-Qs", "ebtables"], Path.cwd(), check=False).returncode != 0:
            utils.fail(
                "The 'ebtables' package is required for legacy iptables with libvirt. "
                "Please install it manually (e.g., 'yay -S ebtables') and re-run."
            )
        utils.run_command(["sudo", "systemctl", "enable", "--now", "iptables.service"], Path.cwd())
        utils.info("iptables.service enabled and started.")

    @staticmethod
    def _handle_nftables():
        """Warns about standalone nftables and enables the service."""
        utils.warn("Standalone nftables without iptables compatibility is not ideal for libvirt.")
        utils.info("For more info, see: https://wiki.archlinux.org/title/Libvirt#Firewall")
        utils.run_command(["sudo", "systemctl", "enable", "--now", "nftables.service"], Path.cwd())
        utils.info("nftables.service enabled and started.")

    def _edit_config_files(self):
        """Modifies libvirt and QEMU configuration files for proper permissions."""
        utils.info("Updating libvirt and QEMU configuration files...")
        # Configure libvirt to use the 'libvirt' group
        utils.update_config_file(
            self.LIBVIRTD_CONF,
            r"^#?unix_sock_group\s=",
            'unix_sock_group = "libvirt"',
            append_if_missing=True,
        )
        utils.update_config_file(
            self.LIBVIRTD_CONF,
            r"^#?\sunix_sock_rw_perms\s=",
            'unix_sock_rw_perms = "0770"',
            append_if_missing=True,
        )
        # Configure QEMU to run VMs as the user for better permissions
        utils.update_config_file(
            self.QEMU_CONF, r"^#?user\s=", f'user = "{self.user}"', append_if_missing=True
        )
        utils.update_config_file(
            self.QEMU_CONF, r"^#?group\s=", f'group = "{self.user}"', append_if_missing=True
        )

    def _manage_user_groups(self):
        """Adds the original user to the 'libvirt' and 'kvm' groups."""
        utils.info(f"Managing group memberships for user '{self.user}'...")
        for group in ["libvirt", "kvm"]:
            try:
                # Check if user is already a member
                check_result = utils.run_command(["id", "-nG", self.user], Path.cwd(), capture_output=True)
                if f" {group} " in f" {check_result.stdout.strip()} ":
                    utils.log(f"User '{self.user}' is already in group '{group}'.")
                else:
                    utils.run_command(
                        ["sudo", "usermod", "-aG", group, self.user], Path.cwd()
                    )
                    utils.info(f"Added user '{self.user}' to group '{group}'.")
            except utils.CommandExecutionError:
                utils.warn(f"Failed to add user to group '{group}'. This may require manual action.")

    @staticmethod
    def _manage_services():
        """Enables and starts the libvirt service and default network."""
        utils.info("Enabling and starting libvirt services...")
        utils.run_command(["sudo", "systemctl", "enable", "--now", "libvirtd.service"], Path.cwd())
        utils.info("Enabled and started libvirtd.service.")

        # Check if the default network is active
        net_info = utils.run_command(["sudo", "virsh", "net-info", "default"], Path.cwd(), check=False, capture_output=True)
        if net_info.returncode != 0 or "Active: no" in net_info.stdout:
            utils.info("Default libvirt network is not active. Starting it now...")
            utils.run_command(["sudo", "virsh", "net-start", "default"], Path.cwd())
            utils.run_command(["sudo", "virsh", "net-autostart", "default"], Path.cwd())
            utils.info("Started and enabled autostart for the default libvirt network.")
        else:
            utils.info("Default libvirt network is already active.")

    def run(self):
        """Executes the full virtualization setup workflow."""
        self._install_packages()
        self._configure_firewall()
        self._edit_config_files()
        self._manage_user_groups()
        self._manage_services()

        utils.warn(
            "A system reboot is required for all group and service changes to take full effect."
        )


# ==============================================================================
# MODULE ENTRY POINT
# ==============================================================================

def main(distro: str):
    """
    Main entry point for the virtualization setup module.

    Args:
        distro: The name of the detected Linux distribution.
    """
    utils.info("Starting host virtualization setup...")
    setup = VirtualizationSetup(distro)
    setup.run()
    utils.log("Host virtualization setup process finished.")