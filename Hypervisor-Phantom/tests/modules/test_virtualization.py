import pytest
from unittest.mock import patch, call, MagicMock, ANY
from pathlib import Path

from modules import virtualization
import utils

@pytest.fixture
def setup_instance(monkeypatch):
    """Provides a default VirtualizationSetup instance for testing."""
    monkeypatch.setenv("SUDO_USER", "testuser")
    return virtualization.VirtualizationSetup(distro="Arch")

def test_initialization_unsupported_distro():
    """
    Tests that initialization fails gracefully with an unsupported distro.
    """
    with pytest.raises(SystemExit):
        virtualization.VirtualizationSetup(distro="UnsupportedOS")

def test_get_original_user_success(monkeypatch):
    """
    Tests that the original user is correctly identified from the SUDO_USER env var.
    """
    monkeypatch.setenv("SUDO_USER", "testuser")
    user = virtualization.VirtualizationSetup._get_original_user()
    assert user == "testuser"

def test_get_original_user_failure(monkeypatch):
    """
    Tests that the script fails if the SUDO_USER is not set.
    """
    monkeypatch.delenv("SUDO_USER", raising=False)
    with pytest.raises(SystemExit):
        virtualization.VirtualizationSetup._get_original_user()

@patch("utils.install_required_packages")
def test_install_packages_called_correctly(mock_install, setup_instance):
    """
    Verifies that _install_packages calls the utility function with the right arguments.
    """
    from config import packages
    setup_instance._install_packages()
    mock_install.assert_called_once_with(
        "Virtualization",
        packages.VIRTUALIZATION["Arch"],
        "Arch"
    )

@patch("utils.update_config_file")
def test_edit_config_files(mock_update_config, setup_instance):
    """
    Ensures that the configuration files are modified with the correct values.
    """
    setup_instance._edit_config_files()
    expected_calls = [
        call(setup_instance.LIBVIRTD_CONF, ANY, 'unix_sock_group = "libvirt"', append_if_missing=True),
        call(setup_instance.LIBVIRTD_CONF, ANY, 'unix_sock_rw_perms = "0770"', append_if_missing=True),
        call(setup_instance.QEMU_CONF, ANY, 'user = "testuser"', append_if_missing=True),
        call(setup_instance.QEMU_CONF, ANY, 'group = "testuser"', append_if_missing=True),
    ]
    mock_update_config.assert_has_calls(expected_calls, any_order=True)

@pytest.mark.parametrize("installed_packages, expected_handler", [
    ({"iptables-nft": True, "iptables": False, "nftables": True}, "_handle_iptables_nft"),
    ({"iptables-nft": False, "iptables": True, "nftables": False}, "_handle_legacy_iptables"),
    ({"iptables-nft": False, "iptables": False, "nftables": True}, "_handle_nftables"),
])
@patch("utils.run_command")
def test_configure_arch_firewall_logic(mock_run_command, setup_instance, installed_packages, expected_handler):
    """
    Tests the branching logic of _configure_arch_firewall by mocking pacman checks.
    """
    def side_effect(command, *args, **kwargs):
        package_name = command[2]
        return MagicMock(returncode=0 if installed_packages.get(package_name) else 1)
    mock_run_command.side_effect = side_effect
    with patch.object(setup_instance, expected_handler) as mock_handler:
        setup_instance._configure_firewall()
        mock_handler.assert_called_once()


@patch("utils.run_command")
def test_manage_user_groups_adds_user(mock_run_command, setup_instance):
    """
    Tests that a user is added to a group if they are not already a member.
    """
    mock_id_result = MagicMock(stdout="uid=1000(testuser) gid=1000(testuser) groups=1000(testuser)")
    mock_run_command.side_effect = [mock_id_result, MagicMock(), mock_id_result, MagicMock()]
    setup_instance._manage_user_groups()
    expected_calls = [
        call(["id", "-nG", "testuser"], ANY, capture_output=True),
        call(["sudo", "usermod", "-aG", "libvirt", "testuser"], ANY),
        call(["id", "-nG", "testuser"], ANY, capture_output=True),
        call(["sudo", "usermod", "-aG", "kvm", "testuser"], ANY),
    ]
    mock_run_command.assert_has_calls(expected_calls)


@patch("utils.run_command")
def test_manage_services_starts_inactive_network(mock_run_command):
    """
    Tests that the default libvirt network is started if it's found to be inactive.
    """
    mock_net_info = MagicMock(returncode=0, stdout="Active: no")
    mock_run_command.side_effect = [MagicMock(), mock_net_info, MagicMock(), MagicMock()]
    
    cwd = Path.cwd()
    virtualization.VirtualizationSetup._manage_services()
    
    expected_calls = [
        call(["sudo", "systemctl", "enable", "--now", "libvirtd.service"], cwd),
        call(["sudo", "virsh", "net-info", "default"], cwd, check=False, capture_output=True),
        call(["sudo", "virsh", "net-start", "default"], cwd),
        call(["sudo", "virsh", "net-autostart", "default"], cwd),
    ]
    assert mock_run_command.call_count == 4
    mock_run_command.assert_has_calls(expected_calls)