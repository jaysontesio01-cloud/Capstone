"""Shared test setup: keep tests away from the real data files and Firebase."""

import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pump_monitor import config, create_app, firebase_store, predictive_maintenance

# Every file or folder the backend writes while it runs.
WRITTEN_FILE_SETTINGS = (
    "PREDICTIVE_HISTORY_FILE",
    "DEVICE_RUNTIME_FILE",
    "MAINTENANCE_HISTORY_FILE",
    "VIBRATION_BASELINE_FILE",
    "ONLINE_TRAINING_FILE",
    "RECORDINGS_DIR",
    "APP_TESTS_DIR",
)

flask_app = create_app()


class BackendTestCase(unittest.TestCase):
    """Each test gets an empty temporary data folder and no Firebase connection.

    self.data_dir is the temporary folder and self.client a Flask test client.
    Tests that need Firebase patch firebase_store.init_firebase themselves.
    """

    def setUp(self):
        super().setUp()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in WRITTEN_FILE_SETTINGS:
            self.stack.enter_context(
                patch.object(config, name, self.data_dir / getattr(config, name).name)
            )
        self.stack.enter_context(patch.object(firebase_store, "init_firebase", return_value=None))
        self.stack.enter_context(
            patch.dict(predictive_maintenance.maintenance_log_last_at, clear=True)
        )
        self.client = flask_app.test_client()
