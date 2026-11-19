"""Start the pump monitoring backend.

    python app.py       development server on port 5000 (or $PORT)
    gunicorn app:app    production (Render, see ../render.yaml)

The code is in the pump_monitor package; see README.md for the layout.
"""

import os

from pump_monitor import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
