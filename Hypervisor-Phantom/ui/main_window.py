"""
Defines the main application window for the Hypervisor Phantom GUI.
"""
import re

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QGridLayout,
    QPushButton, QTextEdit, QLabel, QGroupBox, QStatusBar,
    QProgressBar, QSizePolicy
)
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtCore import Qt

# Import system info logic
from system_info import get_system_info, SystemInfo

# Import the worker and all module main functions
from .workers import Worker
from modules import (
    virtualization, patch_qemu, patch_ovmf, gpu_passthrough,
    patch_kernel, looking_glass, auto_xml
)

# Import the logging bridge components from utils
import utils

class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hypervisor Phantom")
        self.setGeometry(100, 100, 800, 700)
        self.worker = None # To hold a reference to the running worker

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        self._create_system_info_group()
        self._create_modules_group()
        self._create_log_viewer_group()
        self._create_status_bar()

        self.main_layout.addWidget(self.system_info_group)
        self.main_layout.addWidget(self.modules_group)
        self.main_layout.addWidget(self.log_viewer_group, 1)

        # --- Setup Logging Bridge ---
        # This object will emit a signal whenever a log message is created
        self.log_signal_emitter = utils.QtLogSignal()
        self.log_signal_emitter.message_written.connect(self.append_log_message)
        # Re-initialize logging to include our new Qt handler
        utils.setup_logging(qt_signal_emitter=self.log_signal_emitter)

        self._populate_system_info()
        self._connect_signals()
        
    def _create_system_info_group(self):
        self.system_info_group = QGroupBox("System Information")
        layout = QGridLayout(self.system_info_group)
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
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

    def _create_modules_group(self):
        self.modules_group = QGroupBox("Modules")
        layout = QVBoxLayout(self.modules_group)
        self.module_buttons = {}
        self.module_map = {
            "Virtualization Setup": (virtualization.main, lambda: [self.system_info.distro]),
            "QEMU (Patched) Setup": (patch_qemu.main, lambda: [self.system_info.distro, self.system_info.cpu_vendor]),
            "EDK2 (Patched) Setup": (patch_ovmf.main, lambda: [self.system_info.distro, self.system_info.cpu_vendor]),
            "GPU Passthrough Setup": (gpu_passthrough.main, lambda: [self.system_info.cpu_vendor]),
            "Kernel (Patched) Setup": (patch_kernel.main, lambda: [self.system_info.distro, self.system_info.cpu_vendor]),
            "Looking Glass Setup": (looking_glass.main, lambda: [self.system_info.distro, self.system_info.cpu_vendor]),
            "Auto Libvirt XML Setup": (auto_xml.main, lambda: [self.system_info.cpu_vendor]),
        }
        for name in self.module_map.keys():
            button = QPushButton(name)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.module_buttons[name] = button
            layout.addWidget(button)

    def _create_log_viewer_group(self):
        self.log_viewer_group = QGroupBox("Logs")
        layout = QVBoxLayout(self.log_viewer_group)
        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setFont(QFont("Monospace", 10))
        layout.addWidget(self.log_viewer)

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.status_bar.showMessage("Ready")

    def _populate_system_info(self):
        self.system_info = get_system_info()
        self.distro_label.setText(self.system_info.distro)
        self.cpu_label.setText(self.system_info.cpu_vendor)
        self._set_status_label(self.virt_label, self.system_info.virt_support, self.system_info.virt_support_name)
        self._set_status_label(self.iommu_label, self.system_info.iommu_enabled, self.system_info.iommu_support_name)
        self._set_status_label(self.kvm_label, self.system_info.kvm_loaded, "KVM Module")

    def _set_status_label(self, label: QLabel, is_enabled: bool, name: str):
        if is_enabled:
            label.setText(f"<b style='color: #4CAF50;'>Enabled</b> ({name})") # Green
        else:
            label.setText(f"<b style='color: #F44336;'>Disabled</b> ({name})") # Red

    def append_log_message(self, message: str):
        """Appends a message to the log viewer, stripping ANSI color codes."""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_message = ansi_escape.sub('', message).strip()
        if clean_message:
            self.log_viewer.append(clean_message)
            self.log_viewer.moveCursor(QTextCursor.MoveOperation.End)

    def _connect_signals(self):
        """Connect all button `clicked` signals to the task starter."""
        for name, button in self.module_buttons.items():
            target_func, args_lambda = self.module_map[name]
            # Use a lambda to capture the correct function and args for each button
            button.clicked.connect(lambda checked=False, f=target_func, a=args_lambda, n=name: self.start_task(n, f, *a()))

    def start_task(self, task_name: str, target_func, *args):
        """A generic method to start a background task."""
        self.log_viewer.clear()
        self._set_ui_busy(True, f"Running: {task_name}...")
        
        self.worker = Worker(target_func, *args)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.start()

    def on_task_finished(self, was_successful: bool):
        """Slot for when a worker thread finishes."""
        if was_successful:
            self.status_bar.showMessage("Task finished.", 10000) # Message for 10s
        else:
            self.status_bar.showMessage("Task failed. Check logs for details.", 15000) # 15s
        
        self._set_ui_busy(False)

    def _set_ui_busy(self, is_busy: bool, status_message: str = "Ready"):
        """Disables/Enables UI elements and shows/hides the progress bar."""
        for button in self.module_buttons.values():
            button.setEnabled(not is_busy)
        
        if is_busy:
            self.progress_bar.setRange(0, 0) # Indeterminate (spinning) progress
            self.progress_bar.show()
            self.status_bar.showMessage(status_message)
        else:
            self.progress_bar.hide()
            self.status_bar.showMessage(status_message, 5000) # Show for 5s