import pytest
from pathlib import Path
import re
from config import packages, urls, versions, paths

# A list of all package dictionaries defined in the config.Packages class.
# This makes it easy to add new package groups to the test without modifying the test logic.
ALL_PACKAGE_DICTS = {
    "VIRTUALIZATION": packages.VIRTUALIZATION,
    "QEMU_BUILD": packages.QEMU_BUILD,
    "OVMF_BUILD": packages.OVMF_BUILD,
    "KERNEL_BUILD": packages.KERNEL_BUILD,
    "LOOKING_GLASS_BUILD": packages.LOOKING_GLASS_BUILD,
}

def test_all_package_dicts_have_consistent_distro_keys():
    """
    Ensures that every package dictionary has the exact same set of distribution keys.
    This prevents KeyError exceptions if a new distro is added to one list but not another.
    """
    # Arrange
    # Use the keys from the first dictionary as the reference set.
    reference_keys = set(ALL_PACKAGE_DICTS["VIRTUALIZATION"].keys())

    # Assert
    # Check that at least one key exists to prevent a false positive on empty dicts.
    assert reference_keys, "The reference package dictionary has no keys."

    # Iterate through all other package dictionaries.
    for name, package_dict in ALL_PACKAGE_DICTS.items():
        current_keys = set(package_dict.keys())
        # Assert that the current dictionary's keys are identical to the reference set.
        assert current_keys == reference_keys, (
            f"Mismatch in distribution keys for '{name}'.\n"
            f"Expected: {sorted(list(reference_keys))}\n"
            f"Got:      {sorted(list(current_keys))}"
        )

def test_urls_are_well_formed():
    """
    Performs a basic sanity check on all URLs to ensure they start with http/https.
    """
    # Arrange
    url_regex = re.compile(r"^https?://")
    url_attribute_names = ["QEMU_BASE", "QEMU_DOWNLOAD", "QEMU_SIGNATURE", "EDK2_GIT", "TKG_GIT", "LOOKING_GLASS_SRC", "MS_SB_BASE"]

    # Act & Assert
    for attr_name in url_attribute_names:
        url_value = getattr(urls, attr_name, None)
        assert url_value is not None, f"URL attribute '{attr_name}' not found in config."
        assert isinstance(url_value, str), f"URL attribute '{attr_name}' is not a string."
        assert url_regex.match(url_value), (
            f"URL '{attr_name}' with value '{url_value}' is not a valid http/https URL."
        )


def test_kernel_full_version_is_correct():
    """
    Tests that the computed KERNEL_FULL property is formatted correctly.
    """
    # Act
    full_version = versions.KERNEL_FULL

    # Assert
    expected = f"{versions.KERNEL_MAJOR}.{versions.KERNEL_MINOR}-{versions.KERNEL_PATCH}"
    assert full_version == expected

def test_paths_are_pathlib_objects():
    """
    Ensures that all defined paths in the Paths class are pathlib.Path objects.
    """
    # Act & Assert
    for attr_name in dir(paths):
        if not attr_name.startswith('_'):
            path_value = getattr(paths, attr_name)
            assert isinstance(path_value, Path), (
                f"Path '{attr_name}' is not a pathlib.Path object, but a {type(path_value)}."
            )