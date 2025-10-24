#!/usr/bin/env python
"""
Core utilities module for the Hypervisor Phantom toolkit.

This module provides a centralized set of functions for common tasks such as:
- Colored logging to both console and file.
- Robust execution of external shell commands with spinners.
- Standardized user interaction prompts.
- System-level operations like package installation and privileged file editing.
- Helper functions for finding resources and generating data.

Third-party dependencies:
- colorama: For cross-platform colored terminal text.
- getch: For cross-platform single-character input without requiring Enter.
  (pip install colorama getch)
"""

import logging
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, IO
from PySide6.QtCore import QObject, Signal

# Recommended third-party libraries for a better CLI experience
try:
    import colorama
    from getch import getch

    colorama.init(autoreset=True)
except ImportError:
    print(
        "Error: Required libraries not found. Please run 'pip install colorama getch'",
        file=sys.stderr,
    )
    sys.exit(1)


# ==============================================================================
# 1. ANSI COLOR AND STYLE CONSTANTS
# ==============================================================================
class Style:
    RESET = colorama.Style.RESET_ALL
    BOLD = colorama.Style.BRIGHT
    DIM = colorama.Style.DIM


class Fore:
    RED = colorama.Fore.RED
    GREEN = colorama.Fore.GREEN
    YELLOW = colorama.Fore.YELLOW
    CYAN = colorama.Fore.CYAN
    WHITE = colorama.Fore.WHITE


class Back:
    GREEN = colorama.Back.GREEN
    BLACK = colorama.Back.BLACK


# ==============================================================================
# 2. LOGGING
# ==============================================================================
# Global variable to hold the configured logger
log_handler = None


class ConsoleFormatter(logging.Formatter):
    """A custom logging formatter that adds colors based on log level."""

    FORMATS = {
        logging.DEBUG: f"{Fore.WHITE}{Style.DIM}[D] %(message)s{Style.RESET}",
        logging.INFO: f"{Fore.GREEN}{Style.BOLD}[+] %(message)s{Style.RESET}",
        "INFO_CYAN": f"{Fore.CYAN}{Style.BOLD}[i] %(message)s{Style.RESET}",
        logging.WARNING: f"{Fore.YELLOW}{Style.BOLD}[!] %(message)s{Style.RESET}",
        logging.ERROR: f"{Fore.RED}{Style.BOLD}[-] %(message)s{Style.RESET}",
        logging.CRITICAL: f"{Fore.RED}{Style.BOLD}[X] %(message)s{Style.RESET}",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Hack to allow for two different INFO styles via the 'extra' dict
        style_key = "INFO_CYAN" if getattr(record, "style_cyan", False) else record.levelno
        log_fmt = self.FORMATS.get(style_key, self.FORMATS[logging.INFO])
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

class QtLogSignal(QObject):
    """A simple QObject that holds a signal for logging."""
    # Signal that emits a single string argument
    message_written = Signal(str)

class QtLoggingHandler(logging.Handler):
    """
    A logging handler that emits a Qt signal for each log record.
    """
    def __init__(self, signal_emitter: QtLogSignal):
        super().__init__()
        self.signal_emitter = signal_emitter

    def emit(self, record: logging.LogRecord):
        # We use the formatter attached to this handler to format the record
        message = self.format(record)
        self.signal_emitter.message_written.emit(message)

def setup_logging(log_dir: str = "logs", qt_signal_emitter: Optional[QtLogSignal] = None) -> None:
    """Initializes logging with separate formats for file and console."""
    global log_handler
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = Path(log_dir) / f"auto_hypervisor_{os.getpid()}.log"

        log_handler = logging.getLogger("hypervisor_phantom")
        log_handler.setLevel(logging.DEBUG)

        if log_handler.hasHandlers():
            log_handler.handlers.clear()

        # File handler (plain text, verbose)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        fh.setFormatter(file_formatter)
        log_handler.addHandler(fh)

        # Console handler (colored, less verbose)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(ConsoleFormatter())
        log_handler.addHandler(ch)

        # NEW: Add the Qt handler if an emitter is provided
        if qt_signal_emitter:
            qt_handler = QtLoggingHandler(qt_signal_emitter)
            qt_handler.setLevel(logging.INFO)  # Only show INFO and above in GUI
            qt_handler.setFormatter(ConsoleFormatter()) # Use the same formatter for colors
            log_handler.addHandler(qt_handler)

    except Exception as e:
        print(f"Critical Error: Failed to initialize logging: {e}", file=sys.stderr)
        sys.exit(1)


# --- Logging Wrapper Functions ---
def log(message: str) -> None:
    """Log a standard green [+] message."""
    if log_handler:
        log_handler.info(message)


def info(message: str) -> None:
    """Log a cyan [i] informational message."""
    if log_handler:
        log_handler.info(message, extra={"style_cyan": True})


def warn(message: str) -> None:
    """Log a yellow [!] warning message."""
    if log_handler:
        log_handler.warning(message)


def error(message: str) -> None:
    """Log a red [-] error message."""
    if log_handler:
        log_handler.error(message)


def fail(message: str, exit_code: int = 1) -> None:
    """Log a bold red [X] fatal message and exit the script."""
    if log_handler:
        log_handler.critical(message)
    sys.exit(exit_code)


# ==============================================================================
# 3. COMMAND EXECUTION
# ==============================================================================
class CommandExecutionError(Exception):
    """Custom exception for failed subprocess commands."""

    def __init__(self, message, return_code, stdout, stderr):
        super().__init__(message)
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


def run_command(
    command: List[str],
    cwd: Path,
    *,
    show_spinner: bool = False,
    capture_output: bool = False,
    check: bool = True,
    env: Optional[Dict[str, str]] = None,
    stdin: Optional[IO] = None,
) -> subprocess.CompletedProcess:
    """
    Runs an external command with enhanced logging, error handling, and UI.

    Args:
        command: The command to execute as a list of strings.
        cwd: The working directory for the command.
        show_spinner: If True, show start/end messages and pipe output to the
                      debug log. Mutually exclusive with capture_output.
        capture_output: If True, capture and return stdout/stderr.
        check: If True, raise CommandExecutionError on non-zero exit codes.
        env: Optional dictionary of environment variables to add/override.

    Returns:
        A CompletedProcess object with stdout, stderr, and returncode.

    Raises:
        CommandExecutionError: If the command fails and `check` is True.
    """
    cmd_str = shlex.join(command)
    log_handler.debug(f"Executing in '{cwd}': {cmd_str}")

    if show_spinner and capture_output:
        raise ValueError("show_spinner and capture_output are mutually exclusive.")

    if show_spinner:
        info(f"Running: {command[0]}...")
        log("This may take a while. Full output is in the log file.")
        stdout_pipe, stderr_pipe = subprocess.PIPE, subprocess.STDOUT
    elif capture_output:
        stdout_pipe, stderr_pipe = subprocess.PIPE, subprocess.PIPE
    else: # Stream directly to console
        stdout_pipe, stderr_pipe = None, None

    try:
        # Prepare the environment for the subprocess.
        # Start with a copy of the current environment to preserve PATH, etc.
        effective_env = os.environ.copy()

        # If the user passed a custom environment, update our copy with it.
        if env:
            effective_env.update(env)

        # Force the C locale to ensure predictable command output for parsing.
        effective_env['LC_ALL'] = 'C'

        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=effective_env,
            stdout=stdout_pipe,
            stderr=stderr_pipe,
            stdin=stdin,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stdout, stderr = "", ""
        if show_spinner:
            # Stream output to the debug log in real-time
            for line in iter(process.stdout.readline, ""):
                log_handler.debug(line.strip())
            process.wait()
            # After finishing, read any remaining stderr (should be none with STDOUT redirect)
            _, stderr = process.communicate()
        else:
            stdout, stderr = process.communicate()

        if check and process.returncode != 0:
            raise CommandExecutionError(
                f"Command failed with exit code {process.returncode}",
                process.returncode,
                stdout,
                stderr,
            )

        if show_spinner:
            log(f"'{command[0]}' completed successfully.")

        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    except FileNotFoundError:
        raise CommandExecutionError(f"Command not found: {command[0]}", -1, "", "")
    except Exception as e:
        if isinstance(e, CommandExecutionError):
             fail(f"{str(e)}. See log for details.")
        fail(f"An unexpected error occurred running command: {e}")


# ==============================================================================
# 4. USER INTERACTION
# ==============================================================================
def box_text(text: str) -> None:
    """Draws a stylized box around the given text."""
    width = len(text) + 2
    print(f"\n  {Style.BOLD}╔{'═' * width}╗")
    print(f"  ║ {text} ║")
    print(f"  ╚{'═' * width}╝\n{Style.RESET}")


def ask(question: str) -> str:
    """Displays a stylized question prompt and returns the user's input."""
    prompt = f"\n  {Back.GREEN}{Back.BLACK}{Style.BOLD}[?] {question}{Style.RESET} "
    log_handler.debug(f"Asking user: '{question}'")
    response = input(prompt)
    log_handler.debug(f"User response: '{response}'")
    return response


def yes_or_no(question: str) -> bool:
    """Asks a yes/no question and returns True for yes, False for no."""
    prompt = f"\n  {Back.GREEN}{Back.BLACK}{Style.BOLD}[?] {question}{Style.RESET} [y/n]: "
    log_handler.debug(f"Asking user (y/n): '{question}'")
    while True:
        answer = input(prompt).lower().strip()
        log_handler.debug(f"User answered: '{answer}'")
        if answer.startswith("y"):
            print()
            return True
        if answer.startswith("n"):
            print()
            return False
        error("Invalid input. Please answer with 'y' or 'n'.")



def quick_prompt(prompt: str) -> str:
    """
    Prompts the user and captures a single keypress without needing Enter.
    This version is hardened against errors from special keys (e.g., arrows, Del).
    """
    print(prompt, end="", flush=True)
    try:
        # getch() returns a string for normal keys, but can raise errors
        # or return multi-byte strings for special keys. We only want single chars.
        response = getch()
        
        # We are only interested in single, simple characters.
        # Escape sequences for special keys are often multi-character strings.
        # This check filters them out.
        if len(response) > 1:
            print() # Move to a new line for clean output
            log_handler.debug(f"User pressed a special key sequence: {response!r}")
            return "" # Treat as invalid input

        print() # Move to a new line
        log_handler.debug(f"User pressed key for quick_prompt: '{response}'")
        return response

    except OverflowError:
        # This specific error is raised by getch for some special keys (like arrow keys).
        print()
        log_handler.debug("User pressed a special key that caused an OverflowError.")
        return "" # Treat as invalid input

    except (KeyboardInterrupt, EOFError):
        # This catches Ctrl+C or Ctrl+D cleanly.
        print()
        fail("Operation cancelled by user.")


# ==============================================================================
# 5. SYSTEM AND FILE OPERATIONS
# ==============================================================================
PKG_MANAGERS: Dict[str, Dict[str, str]] = {
    "Arch": {"install": "sudo pacman -S --noconfirm", "check": "pacman -Q"},
    "Debian": {"install": "sudo apt-get install -y", "check": "dpkg -s"},
    "openSUSE": {"install": "sudo zypper install -y", "check": "rpm -q"},
    "Fedora": {"install": "sudo dnf install -yq", "check": "rpm -q"},
}


def install_required_packages(component: str, packages: List[str], distro: str) -> None:
    """Checks for and installs missing packages for a given component."""
    if distro not in PKG_MANAGERS:
        fail(f"Unsupported distribution for package management: {distro}")

    manager = PKG_MANAGERS[distro]
    check_cmd = shlex.split(manager["check"])
    install_cmd = shlex.split(manager["install"])
    
    log(f"Checking '{component}' packages for {distro}...")
    missing = [
        pkg
        for pkg in packages
        if run_command(check_cmd + [pkg], Path.cwd(), check=False).returncode != 0
    ]

    if not missing:
        log(f"All required '{component}' packages are already installed.")
        return

    warn(f"Missing packages: {', '.join(missing)}")
    if yes_or_no(f"Install these {len(missing)} missing packages?"):
        run_command(install_cmd + missing, Path.cwd(), show_spinner=True)
        log("Packages installed successfully.")
    else:
        fail("User declined to install required packages. Aborting.")


def _read_privileged_file(file_path: Path) -> List[str]:
    """Helper to read a root-owned file using sudo."""
    try:
        result = run_command(["sudo", "cat", str(file_path)], Path.cwd(), capture_output=True)
        return result.stdout.splitlines(True)
    except CommandExecutionError:
        warn(f"Could not read '{file_path}' (it may not exist). A new file will be created.")
        return []


def _write_privileged_file(file_path: Path, content_lines: List[str]) -> None:
    """Helper to write to a root-owned file using a temporary file."""
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
            tmp_path = Path(tmp.name)
            tmp.writelines(content_lines)
        
        # Atomically move and set permissions
        run_command(["sudo", "chown", "root:root", str(tmp_path)], Path.cwd())
        run_command(["sudo", "chmod", "644", str(tmp_path)], Path.cwd())
        run_command(["sudo", "mv", str(tmp_path), str(file_path)], Path.cwd())
    finally:
        if 'tmp_path' in locals() and tmp_path.exists():
            tmp_path.unlink()


def update_config_file(
    file_path: Path,
    pattern: str,
    new_line: str,
    *,
    append_if_missing: bool = False,
) -> None:
    """
    Safely edits a root-owned file using sudo.

    It reads the file, finds a line matching the regex pattern, replaces it,
    and writes the content back.

    Args:
        file_path: The absolute path to the configuration file.
        pattern: A regex string to find the line to replace.
        new_line: The new content for the line (without newline character).
        append_if_missing: If True and pattern is not found, append new_line.
    """
    original_lines = _read_privileged_file(file_path)
    
    found = False
    output_lines = []
    for line in original_lines:
        # Search the stripped line to avoid issues with leading whitespace
        if re.search(pattern, line.strip()):
            output_lines.append(new_line + "\n")
            found = True
        else:
            output_lines.append(line)

    if not found and append_if_missing:
        # Ensure the new line has a newline character if it's the last one
        if not new_line.endswith('\n'):
             new_line += '\n'
        output_lines.append(new_line)

    _write_privileged_file(file_path, output_lines)
    info(f"Successfully updated configuration in {file_path}")


def get_resource_path(relative_path: str) -> Path:
    """
    Gets the absolute path to a resource, working for both source and frozen
    (e.g., PyInstaller) executables.
    """
    # If the application is running in a bundled state (e.g., PyInstaller)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # The base path is the temporary directory PyInstaller creates
        base_path = Path(sys._MEIPASS)
    else:
        # Otherwise, the base path is the directory containing this script
        base_path = Path(__file__).parent.resolve()

    return base_path / relative_path


def generate_random_mac() -> str:
    """Generates a random, locally-administered, unicast MAC address."""
    # 0x02 sets the locally administered bit in the first octet
    mac = [
        0x02,
        random.randint(0x00, 0xFF),
        random.randint(0x00, 0xFF),
        random.randint(0x00, 0xFF),
        random.randint(0x00, 0xFF),
        random.randint(0x00, 0xFF),
    ]
    return ":".join(f"{b:02x}" for b in mac)


# ==============================================================================
# 6. DEMONSTRATION
# ==============================================================================
if __name__ == "__main__":
    setup_logging()

    box_text("Utility Module Demonstration")

    log("This is a standard log message.")
    info("This is an informational message.")
    warn("This is a warning message.")
    error("This is an error message.")

    info("Demonstrating command execution...")
    try:
        # Spinner example
        run_command(["sleep", "2"], cwd=Path.cwd(), show_spinner=True)
        # Capture example
        result = run_command(["ls", "-l", "/"], cwd=Path.cwd(), capture_output=True)
        log(f"Captured 'ls' output (first 50 chars): {result.stdout[:50].strip()}...")
        # Failure example
        run_command(["command-that-does-not-exist"], cwd=Path.cwd())

    except CommandExecutionError as e:
        error(f"Caught expected command failure: {e.message}")
    except Exception as e:
        fail(f"Caught unexpected error: {e}")

    user_response = ask("What is your name?")
    log(f"User's name is: {user_response}")

    if yes_or_no("Do you like this new utils module?"):
        log("Excellent!")
    else:
        warn("Feedback is welcome!")

    fail("Demonstration finished successfully.", exit_code=0)