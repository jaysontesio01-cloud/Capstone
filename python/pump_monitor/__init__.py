"""Water pump monitoring backend: Flask API for the ESP32 sensors and the Flutter app.

create_app() builds the app; app.py in the python folder runs it.
"""

from flask import Flask
from flask_cors import CORS

from . import config


def create_app():
    """Create the Flask app, load the anomaly model and register every route."""
    # Imported here so that `import pump_monitor.config` (used by the scripts)
    # does not load the models.
    from . import anomaly_model
    from .routes import register_routes

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    anomaly_model.load_model()

    app = Flask(__name__)
    CORS(app)
    register_routes(app)
    return app
