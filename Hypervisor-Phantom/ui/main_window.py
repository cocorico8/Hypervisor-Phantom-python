"""
Defines the main application window for the Hypervisor Phantom GUI.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QGridLayout,
    QPushButton, QTextEdit, QLabel, QGroupBox, QStatusBar,
    QProgressBar, QSizePolicy
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

from system_info import get_system_info, SystemInfo

class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hypervisor Phantom")
        self.setGeometry(100, 100, 800, 700) # x, y, width, height

        # --- Central Widget and Main Layout ---
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # --- Create UI Components ---
        self._create_system_info_group()
        self._create_modules_group()
        self._create_log_viewer_group()
        self._create_status_bar()

        # --- Add Components to Main Layout ---
        self.main_layout.addWidget(self.system_info_group)
        self.main_layout.addWidget(self.modules_group)
        self.main_layout.addWidget(self.log_viewer_group, 1) # Give log viewer extra space

        # --- Initial Population ---
        self._populate_system_info()

    def _create_system_info_group(self):
        """Creates the 'System Information' box."""
        self.system_info_group = QGroupBox("System Information")
        layout = QGridLayout(self.system_info_group)

        # Create labels that will be populated with data later
        self.distro_label = QLabel("Detecting...")
        self.cpu_label = QLabel("Detecting...")
        self.virt_label = QLabel("Detecting...")
        self.iommu_label = QLabel("Detecting...")
        self.kvm_label = QLabel("Detecting...")

        layout.addWidget(QLabel("<b>Distribution:</b>"), 0, 0)
        layout.addWidget(self.distro_label, 0, 1)
        layout.addWidget(QLabel("<b>CPU Vendor:</b>"), 1, 0)
        layout.addWidget(self.cpu_label, 1, 1)
        layout.addWidget(QLabel("<b>Virtualization:</b>"), 0, 2)
        layout.addWidget(self.virt_label, 0, 3)
        layout.addWidget(QLabel("<b>IOMMU:</b>"), 1, 2)
        layout.addWidget(self.iommu_label, 1, 3)
        layout.addWidget(QLabel("<b>KVM Module:</b>"), 2, 2)
        layout.addWidget(self.kvm_label, 2, 3)
        
        # Make columns stretch nicely
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

    def _create_modules_group(self):
        """Creates the 'Modules' box with all the action buttons."""
        self.modules_group = QGroupBox("Modules")
        layout = QVBoxLayout(self.modules_group)

        # A dictionary to hold our buttons for easy access
        self.module_buttons = {}
        module_names = [
            "Virtualization Setup",
            "QEMU (Patched) Setup",
            "EDK2 (Patched) Setup",
            "GPU Passthrough Setup",
            "Kernel (Patched) Setup",
            "Looking Glass Setup",
            "Auto Libvirt XML Setup",
        ]

        for name in module_names:
            button = QPushButton(name)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.module_buttons[name] = button
            layout.addWidget(button)

    def _create_log_viewer_group(self):
        """Creates the 'Logs' box."""
        self.log_viewer_group = QGroupBox("Logs")
        layout = QVBoxLayout(self.log_viewer_group)
        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setFont(QFont("Monospace", 10))
        layout.addWidget(self.log_viewer)

    def _create_status_bar(self):
        """Creates the status bar at the bottom of the window."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide() # Only show it when a task is running
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.status_bar.showMessage("Ready")

    def _populate_system_info(self):
        """Fetches system info and updates the UI labels."""
        info = get_system_info()
        self.distro_label.setText(info.distro)
        self.cpu_label.setText(info.cpu_vendor)
        self._set_status_label(self.virt_label, info.virt_support, info.virt_support_name)
        self._set_status_label(self.iommu_label, info.iommu_enabled, info.iommu_support_name)
        self._set_status_label(self.kvm_label, info.kvm_loaded, "KVM Module")
        
    def _set_status_label(self, label: QLabel, is_enabled: bool, name: str):
        """Helper to set text and color for status labels."""
        if is_enabled:
            label.setText(f"<b style='color: green;'>Enabled</b> ({name})")
        else:
            label.setText(f"<b style='color: red;'>Disabled</b> ({name})")