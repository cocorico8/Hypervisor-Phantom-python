#!/usr/bin/env python
"""
Hypervisor Phantom: An automated toolkit for preparing a Linux host for
hardware-passthrough virtualization with a focus on anti-cheat compatibility.

This script serves as the main entry point and menu for all modules.
"""

import sys
from PySide6.QtWidgets import QApplication

import utils
from config import core
from ui.main_window import MainWindow

def main():
    """The main entry point of the script."""
    utils.setup_logging(log_dir=core.LOG_DIR)

    # We no longer check for root here. The GUI must run as a normal user.
    # Privileges will be requested by individual actions later.

    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        # A fallback for critical errors during startup
        if utils.log_handler:
            utils.log_handler.exception("A critical unhandled exception occurred during GUI startup.")
        print(f"A critical error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()