"""
Defines the QThread worker for running background tasks.
"""
from PySide6.QtCore import QThread, Signal

class Worker(QThread):
    """
    A generic worker thread for running long tasks without freezing the GUI.
    """
    # Signal emitted when the task is finished.
    # It carries a boolean indicating success or failure.
    finished = Signal(bool)

    def __init__(self, target_func, *args, **kwargs):
        super().__init__()
        self.target_func = target_func
        self.args = args
        self.kwargs = kwargs
        self.was_successful = False

    def run(self):
        """
        The entry point for the thread. Executes the target function.
        """
        try:
            # We run the target function provided during initialization.
            self.target_func(*self.args, **self.kwargs)
            self.was_successful = True
        except SystemExit as e:
            # A SystemExit with code 0 is a graceful exit (e.g., user cancelled a prompt)
            # which we consider a success in the context of the task not crashing.
            self.was_successful = True if e.code == 0 else False
        except Exception:
            # Any other unhandled exception is a clear failure.
            self.was_successful = False
        finally:
            # Emit the finished signal with the success status.
            self.finished.emit(self.was_successful)