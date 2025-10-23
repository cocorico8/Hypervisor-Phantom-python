import pytest
import utils


@pytest.fixture(scope="session", autouse=True)
def setup_test_logging():
    """
    This is a session-scoped, auto-used fixture that sets up the logging
    for all test files. By placing it in conftest.py, pytest makes it
    globally available.
    """
    utils.setup_logging(log_path="test_logs")
