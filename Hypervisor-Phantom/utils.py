import logging
import os
import subprocess
import sys
import termios
import tty
import select
import time
from pathlib import Path
from typing import List, Dict, Any

# Recommended third-party libraries for a better CLI experience
try:
    import colorama
    from getch import getch
    colorama.init(autoreset=True)
except ImportError:
    print("Error: Required libraries not found. Please run 'pip install colorama getch'")
    sys.exit(1)

# ==============================================================================
# 1. ANSI COLOR AND STYLE CONSTANTS (from formatter.sh)
# ==============================================================================
# Using colorama's constants for better readability and cross-platform support
class Style:
    RESET = colorama.Style.RESET_ALL
    BOLD = colorama.Style.BRIGHT
    DIM = colorama.Style.DIM
    NORMAL = colorama.Style.NORMAL

class Fore:
    BLACK = colorama.Fore.BLACK
    RED = colorama.Fore.RED
    GREEN = colorama.Fore.GREEN
    YELLOW = colorama.Fore.YELLOW
    BLUE = colorama.Fore.BLUE
    MAGENTA = colorama.Fore.MAGENTA
    CYAN = colorama.Fore.CYAN
    WHITE = colorama.Fore.WHITE
    RESET = colorama.Fore.RESET

class Back:
    GREEN = colorama.Back.GREEN
    RESET = colorama.Back.RESET

# Global variable to hold the configured logger
log_handler = None

# ==============================================================================
# 2. LOGGING AND FORMATTING (from debugger.sh and formatter.sh)
# ==============================================================================

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

    def format(self, record):
        # A little hack to allow for two different INFO styles
        if record.levelno == logging.INFO and hasattr(record, 'style_cyan'):
            log_fmt = self.FORMATS.get("INFO_CYAN")
        else:
            log_fmt = self.FORMATS.get(record.levelno)
        
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

def setup_logging(log_path: str = "logs") -> None:
    """Initializes the logging system with separate formatters for file and console."""
    global log_handler
    try:
        os.makedirs(log_path, exist_ok=True)
        # Use a more descriptive log file name
        log_file = os.path.join(log_path, f"auto_hypervisor_{os.getpid()}.log")

        log_handler = logging.getLogger('hypervisor_phantom')
        log_handler.setLevel(logging.DEBUG)

        if log_handler.hasHandlers():
            log_handler.handlers.clear()

        # File handler - plain text, no colors.
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        # This formatter is for the file, so it includes timestamp and level name.
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(file_formatter)
        log_handler.addHandler(fh)

        # Console handler - uses our custom color formatter.
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(ConsoleFormatter())
        log_handler.addHandler(ch)

    except Exception as e:
        print(f"Failed to create log directory or file: {e}", file=sys.stderr)
        sys.exit(1)

def log(message: str):
    """Log a standard green [+] message."""
    if log_handler:
        log_handler.info(message)

def info(message: str):
    """Log a cyan [i] message."""
    if log_handler:
        # We pass an extra context to our custom formatter to trigger the cyan style.
        log_handler.info(message, extra={'style_cyan': True})

def warn(message: str):
    """Log a yellow [!] message."""
    if log_handler:
        log_handler.warning(message)

def error(message: str):
    """Log a red [-] message to stderr."""
    if log_handler:
        log_handler.error(message)

def fatal(message: str):
    """Log a bold red [X] message to stderr."""
    if log_handler:
        log_handler.critical(message)

def box_text(text: str):
    """Draws a stylized box around given text."""
    # We need to access the Style constants for this to work.
    # Make sure the Style class is defined at the top of the file.
    width = len(text) + 2
    print(f"\n  {Style.BOLD}╔{'═' * width}╗")
    print(f"  ║ {text} ║")
    print(f"  ╚{'═' * width}╝\n{Style.RESET}")

def fail(message: str):
    """Prints a fatal message and exits the script."""
    fatal(message) # This calls our new logging wrapper
    sys.exit(1)

def run_with_spinner(command: list[str], cwd: Path, env: dict = None):
    """
    Runs a long-running command, displaying only a simple spinner.
    The full command output is captured and saved to the debug log.
    User input is ignored to prevent display disruption.
    """
    is_interactive = sys.stdout.isatty() and os.name == 'posix'
    
    if not is_interactive:
        # Non-interactive fallback remains the same
        log(f"Non-interactive mode: Executing command in '{cwd}'...")
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace'
        )
        log_handler.debug(result.stdout)
        if result.returncode != 0:
            error("Command failed. Full log output:")
            print(result.stdout)
            raise subprocess.CalledProcessError(result.returncode, command)
        return

    # --- Interactive Mode with Deadlock Prevention ---
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)

        spinner_chars = ['|', '/', '-', '\\']
        spinner_index = 0
        static_message = "  Running command..."
        print(static_message, end='', flush=True)

        log(f"Executing command in '{cwd}': {' '.join(command)}")
        process = subprocess.Popen(
            command, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        
        # This loop correctly drains the output pipe while animating the spinner.
        while process.poll() is None:
            # Animate the spinner
            spinner_char = spinner_chars[spinner_index % len(spinner_chars)]
            print(f" {spinner_char}\r", end='', flush=True)
            spinner_index += 1

            # Use select to check for output on the pipe without blocking.
            # The timeout of 0.1 seconds drives the animation speed.
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            
            if ready:
                # There is output to read. We must read it to prevent the pipe from filling up.
                line = process.stdout.readline()
                if line:
                    # We save it to the log but DO NOT print it to the console.
                    log_handler.debug(line.strip())
        
        # Clean up the final spinner line
        print(" Done.          ", flush=True)

        # After the process finishes, there might be some remaining output.
        # We read it to ensure our logs are complete.
        remaining_output = process.stdout.read()
        if remaining_output:
            log_handler.debug(f"Remaining command output:\n{remaining_output}")

        if process.returncode != 0:
            error("The command failed. See the log file for the full output.")
            raise subprocess.CalledProcessError(process.returncode, command)

    finally:
        # ALWAYS restore the terminal and flush the input buffer
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        termios.tcflush(fd, termios.TCIFLUSH)

# ==============================================================================
# 3. USER PROMPTS (from prompter.sh)
# ==============================================================================

def ask(question: str) -> str:
    """Displays a stylized question prompt."""
    prompt = f"\n  {Back.GREEN}{Fore.BLACK}{Style.BOLD}[?] {question}{Style.RESET} "
    log_handler.info(f"[?] Asking user: {question}") # Log the question
    return input(prompt)

def yes_or_no(question: str) -> bool:
    """Asks a yes/no question and returns True for yes, False for no."""
    while True:
        # The prompt for the user's input
        prompt = f"\n  {Back.GREEN}{Fore.BLACK}{Style.BOLD}[?] {question}{Style.RESET} [y/n]: "
        
        log_handler.debug(f"[?] Asking user (y/n): {question}")
        
        answer = input(prompt).lower().strip()
        
        # Log the user's raw answer for debugging purposes
        log_handler.debug(f"User answered: {answer}")

        if answer.startswith('y'):
            print()
            return True
        elif answer.startswith('n'):
            print()
            return False
        else:
            error("Please answer y/n.")

def quick_prompt(prompt: str) -> str:
    """Prompts the user and captures a single keypress."""
    print(prompt, end='', flush=True)
    response = getch()
    print() # Move to the next line after keypress
    log_handler.info(f"User pressed a key for quick_prompt: '{response}'")
    return response

# ==============================================================================
# 4. PACKAGE MANAGEMENT (from packages.sh)
# ==============================================================================

# This dictionary replaces the large case statement in the bash script.
# It's much cleaner and easier to extend.
PKG_MANAGERS: Dict[str, Dict[str, str]] = {
    "Arch": {
        "install": "sudo pacman -S --noconfirm",
        "check": "pacman -Q",
    },
    "Debian": {
        "install": "sudo apt-get install -y",
        "check": "dpkg -s",
    },
    "openSUSE": {
        "install": "sudo zypper install -y",
        "check": "rpm -q",
    },
    "Fedora": {
        "install": "sudo dnf install -yq",
        "check": "rpm -q",
    },
}

def install_required_packages(component: str, required_packages: List[str], distro: str):
    """
    Checks for and installs missing packages for a given component.
    """
    if distro not in PKG_MANAGERS:
        fail(f"Unsupported distribution for package management: {distro}")

    manager = PKG_MANAGERS[distro]
    check_cmd = manager["check"].split()
    install_cmd = manager["install"].split()

    log(f"Checking for required '{component}' packages for {distro}...")

    missing_packages = []
    for pkg in required_packages:
        # We expect a non-zero exit code if the package is NOT installed.
        result = subprocess.run(check_cmd + [pkg], capture_output=True, text=True)
        if result.returncode != 0:
            missing_packages.append(pkg)

    if not missing_packages:
        log(f"All required '{component}' packages are already installed.")
        return

    warn(f"Missing required packages: {', '.join(missing_packages)}")

    if yes_or_no(f"Install these {len(missing_packages)} missing packages?"):
        info(f"Installing packages... See log file for details.")
        try:
            # Run the install command. Output will be logged via the logger.
            process = subprocess.Popen(install_cmd + missing_packages, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            # Log output line by line
            for line in iter(process.stdout.readline, ''):
                log_handler.debug(line.strip())
            process.wait()

            if process.returncode != 0:
                fail(f"Failed to install packages. Check the log for details.")

            log("Packages installed successfully.")
        except Exception as e:
            fail(f"An error occurred during installation: {e}")
    else:
        fail("User chose not to install required packages. Aborting.")


def update_config_file(file_path: str, pattern: str, new_line: str, append_if_missing: bool = False):
    """
    Safely edits a root-owned file by writing to a temporary file and using sudo to
    move it into place. This adheres to the Principle of Least Privilege.
    """
    temp_file = None
    try:
        # Get original file stats to re-apply later
        try:
            original_stat = os.stat(file_path)
            owner_uid, owner_gid = original_stat.st_uid, original_stat.st_gid
        except FileNotFoundError:
            # If the file doesn't exist, it will be owned by root by default
            utils.warn(f"Config file not found: {file_path}. A new one will be created.")
            owner_uid, owner_gid = 0, 0 # root:root
            lines = []
        else:
             with open(file_path, 'r') as f:
                lines = f.readlines()

        # Create a secure temporary file that our user can write to
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as temp:
            temp_file = temp.name
            
            found = False
            for line in lines:
                if re.search(pattern, line.strip()):
                    temp.write(new_line + '\n')
                    found = True
                else:
                    temp.write(line)
            
            if not found and append_if_missing:
                temp.write(new_line + '\n')

        # --- Privilege Escalation Point ---
        # The following commands use 'sudo' to perform root-level operations.
        
        # Move the temporary file to the final destination
        utils.log(f"Applying changes to {file_path} with root privileges...")
        subprocess.run(["sudo", "mv", temp_file, file_path], check=True)
        
        # Restore original ownership and permissions
        subprocess.run(["sudo", "chown", f"{owner_uid}:{owner_gid}", file_path], check=True)

        utils.info(f"Successfully updated configuration in {file_path}")

    except Exception as e:
        utils.error(f"Failed to update {file_path}: {e}")
    finally:
        # Ensure the temporary file is always cleaned up, even if an error occurs
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)

def get_resource_path(relative_path: str) -> Path:
    """
    Get the absolute path to a resource, works for dev and for PyInstaller.
    This is a CRITICAL function for making the executable work.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = Path(sys._MEIPASS)
    else:
        # We are running in a normal Python environment
        base_path = Path.cwd()
    
    return base_path / relative_path

if __name__ == "__main__":
    # ==========================================================================
    # DEMONSTRATION of how to use the utility functions
    # ==========================================================================
    setup_logging()

    box_text("Utility Module Demonstration")

    log("This is a standard log message.")
    info("This is an informational message.")
    warn("This is a warning message.")
    error("This is an error message.")

    try:
        user_response = ask("What is your favorite Linux distro?")
        info(f"User's favorite distro is: {user_response}")

        if yes_or_no("Do you like Python?"):
            log("Great choice!")
        else:
            warn("You should give it another try!")

        quick_prompt(info("Press any key to continue to the package manager demo..."))

        # --- Package Manager Demo ---
        # In a real script, this data would come from the specific module
        # (e.g., qemu_patcher.py)
        detected_distro = "Arch" # Hardcoded for demonstration
        qemu_packages_arch = ["base-devel", "ninja", "glib2", "nonexistent-package"]

        info(f"Demonstrating package installation for Distro: {detected_distro}")
        install_required_packages("QEMU", qemu_packages_arch, detected_distro)

    except (KeyboardInterrupt):
        print("\n")
        fail("Operation cancelled by user.")
    except Exception as e:
        fail(f"An unexpected error occurred: {e}")

    log("Demonstration finished.")