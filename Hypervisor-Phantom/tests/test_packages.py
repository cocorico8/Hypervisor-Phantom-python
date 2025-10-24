# tests/test_utils.py

import pytest
import re
from pathlib import Path
import utils

# Note: The setup_test_logging fixture in conftest.py already handles logging.

def test_generate_random_mac_format():
    """
    Tests that the generated MAC address has the correct format and prefix.
    """
    # Arrange
    mac_regex = re.compile(r"^02(:[0-9a-f]{2}){5}$")

    # Act
    mac = utils.generate_random_mac()

    # Assert
    assert isinstance(mac, str)
    assert len(mac) == 17
    assert mac_regex.match(mac) is not None, f"MAC {mac} did not match expected format."

@pytest.mark.parametrize("user_input, expected_result", [
    ("y", True),
    ("Y", True),
    ("yes", True),
    ("n", False),
    ("N", False),
    ("no", False),
])
def test_yes_or_no_handles_various_inputs(monkeypatch, user_input, expected_result):
    """
    Tests the yes_or_no prompt with various valid inputs using parameterization.
    """
    # Arrange
    # Mock the built-in input() function to return the canned user_input
    monkeypatch.setattr("builtins.input", lambda _: user_input)

    # Act
    result = utils.yes_or_no("A test question?")

    # Assert
    assert result is expected_result

def test_update_config_file_replaces_line(monkeypatch):
    """
    Tests that update_config_file correctly replaces an existing line.
    """
    # Arrange
    initial_content = ["# Some comment\n", "TARGET_KEY = old_value\n", "OTHER_KEY = 123\n"]
    expected_content = ["# Some comment\n", "TARGET_KEY = new_value\n", "OTHER_KEY = 123\n"]
    
    # Mock the privileged file I/O functions in utils
    # This is a list to track what _write_privileged_file was called with
    write_calls = []
    monkeypatch.setattr("utils._read_privileged_file", lambda path: initial_content)
    monkeypatch.setattr("utils._write_privileged_file", lambda path, content: write_calls.append(content))
    
    # Act
    utils.update_config_file(
        file_path=Path("/fake/config.conf"),
        pattern=r"^TARGET_KEY\s*=",
        new_line="TARGET_KEY = new_value",
    )

    # Assert
    assert len(write_calls) == 1, "File should have been written to exactly once."
    assert write_calls[0] == expected_content, "The new file content is incorrect."