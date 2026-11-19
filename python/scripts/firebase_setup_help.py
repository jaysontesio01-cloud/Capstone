"""Firebase setup help: where the backend looks for the service-account key.

The Firebase connection itself is made in pump_monitor/firebase_store.py.
If you are seeing Google 2-step verification issues, you are trying to sign in
with a personal Google account instead of a Firebase service-account JSON key.

Use either:
1) Put the downloaded service-account JSON in the python folder, or
2) Set FIREBASE_SERVICE_ACCOUNT_JSON to the full path of the JSON file
   (or to the JSON text itself, e.g. as a secret on Render).

Run from the python folder:

    python scripts/firebase_setup_help.py
"""

import os
import sys
from pathlib import Path

# Lets this script import pump_monitor when run as `python scripts/<name>.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pump_monitor import config  # noqa: E402


def print_setup_help():
    print("Firebase setup for this project:")
    print("- Use the service-account JSON from your Firebase project.")
    print(f"- File name the backend looks for: {config.FIREBASE_CREDENTIALS_FILENAME}")
    print(f"- Place it in {config.BASE_DIR} or set FIREBASE_SERVICE_ACCOUNT_JSON.")


if __name__ == "__main__":
    print_setup_help()
    print("Current environment variable:", os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))
    print("The actual Firebase connection is handled by pump_monitor/firebase_store.py.")
