import sys
import re
from pathlib import Path
import pytest

# This is how we import the code we want to test
import utils


# ==============================================================================
#  TEST FUNCTIONS
# ==============================================================================

def test_generate_random_mac():
    """
    Tests the MAC address generator.
    """
    print("--> Testing MAC address generation...")
    mac = utils.generate_random_mac()
    assert isinstance(mac, str)
    assert len(mac) == 17
    assert re.match(r'^02(:[0-9a-f]{2}){5}$', mac)
    print("    - MAC format is correct.")


def test_get_resource_path(monkeypatch):
    """
    Tests the critical get_resource_path function in both modes.
    """
    print("--> Testing resource path resolution...")

    # --- Scenario 1: Running from source ---
    if hasattr(sys, 'frozen'):
        monkeypatch.setattr(sys, 'frozen', False)
    if hasattr(sys, '_MEIPASS'):
        monkeypatch.delattr(sys, '_MEIPASS')
    
    # We are in tests/test_utils.py. The parent is tests/, grandparent is project root.
    # The parent of utils.py (the __file__ in the function) is the project root.
    source_path = utils.get_resource_path("data/test.xml")
    expected_source_path = Path(__file__).parent.parent / "data/test.xml"
    assert source_path == expected_source_path
    print("    - Path is correct when running from source.")

    # --- Scenario 2: Running as a frozen executable ---
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', '/tmp/_MEIxxxxxx', raising=False)

    frozen_path = utils.get_resource_path("data/test.xml")
    expected_frozen_path = Path("/tmp/_MEIxxxxxx") / "data/test.xml"
    assert frozen_path == expected_frozen_path
    print("    - Path is correct when running as a frozen executable.")


def test_yes_or_no(monkeypatch):
    """
    Tests the yes_or_no prompt by mocking the built-in input() function.
    This test will now pass because the setup_test_logging fixture runs first.
    """
    print("--> Testing yes_or_no prompt...")

    monkeypatch.setattr('builtins.input', lambda _: 'y')
    assert utils.yes_or_no("A test question?") is True
    print("    - Correctly handles 'y' input.")

    monkeypatch.setattr('builtins.input', lambda _: 'NO')
    assert utils.yes_or_no("Another question?") is False
    print("    - Correctly handles 'NO' input.")

def test_box_text(capsys):
    """
    Tests the box_text function by capturing its standard output.
    'capsys' is a pytest fixture that captures whatever is printed to stdout and stderr.
    """
    print("--> Testing box_text output...")
    utils.box_text("Hello")

    # 'capsys.readouterr()' returns what was captured.
    captured = capsys.readouterr()
    
    # We can now check if the captured output contains the characters we expect.
    assert "╔═══════╗" in captured.out
    assert "║ Hello ║" in captured.out
    assert "╚═══════╝" in captured.out
    print("    - Box was printed correctly.")


def test_fail():
    """
    Tests the fail function to ensure it exits the program.
    'pytest.raises' is a context manager that checks if the expected exception is raised.
    """
    print("--> Testing that fail() exits the program...")
    
    # sys.exit() works by raising a 'SystemExit' exception.
    # This test will pass ONLY if the code inside the 'with' block raises SystemExit.
    with pytest.raises(SystemExit) as excinfo:
        utils.fail("This is a test failure.")
    
    # We can even check the exit code. A normal sys.exit() is 1.
    assert excinfo.value.code == 1
    print("    - Correctly raised SystemExit.")