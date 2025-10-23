import subprocess

# Import the code we want to test
import utils


# This is a mock object that simulates the result of a subprocess command.
class MockCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


# This is our mock function that will replace the real 'subprocess.run'.
def mock_subprocess_run(installed_packages):
    def _mock_run(command, *args, **kwargs):
        package_name = command[-1]
        if package_name in installed_packages:
            # If the package is in our "installed" list, simulate success.
            return MockCompletedProcess(returncode=0)
        else:
            # Otherwise, simulate failure (package not found).
            return MockCompletedProcess(returncode=1)

    return _mock_run


def test_install_required_packages(monkeypatch):
    """
    Tests the package installation logic by mocking subprocess.run.
    This allows us to test the function's logic without actually installing packages.
    """
    print("--> Testing package installation logic...")

    # We will simulate a system where 'git' is installed but 'make' is not.
    already_installed = ["git"]
    required = ["git", "make"]

    # This is the magic: we replace the real 'subprocess.run' with our mock function.
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run(already_installed))

    # We also need to mock the user input and the spinner function.
    # Simulate the user always answering "y" to prompts.
    monkeypatch.setattr(utils, "yes_or_no", lambda _: True)
    # Replace the spinner with a simple function that does nothing, so it doesn't hang.
    monkeypatch.setattr(utils, "run_with_spinner", lambda *args, **kwargs: None)

    # We need to capture the calls to our mocked spinner to see what would be installed.
    # We'll use a list to track calls to our mock.
    install_calls = []

    def mock_spinner_capture(command, *args, **kwargs):
        # When this mock is called, just record the command it was given.
        install_calls.append(command)

    monkeypatch.setattr(utils, "run_with_spinner", mock_spinner_capture)

    # Now, run the function we are testing.
    utils.install_required_packages("Test Component", required, "Arch")

    # Finally, assert that our logic worked correctly.
    # There should be exactly one call to the installer.
    assert len(install_calls) == 1

    # The command should be to install 'make', since 'git' was already present.
    install_command = install_calls[0]
    assert "make" in install_command
    assert "git" not in install_command
    print("    - Correctly identified and attempted to install the missing package.")
