"""API routes, one Flask blueprint per area.

The ESP32 firmware and the Flutter app call these URLs, so keep them
unchanged; they are documented in docs/FLUTTER_API_DOCS.md.

    status.py       GET  /api/status
    devices.py      GET  /api/pumps, /api/esp32/devices
                    POST /api/esp32/register
                    DELETE /api/esp32/devices/<id>
    sensor_data.py  POST /api/esp32/data, /api/esp32/clear/<id>
                    GET  /api/esp32/status/<id>, /api/esp32/history/<id>,
                         /api/esp32/analyze/<id>
    maintenance.py  GET  /api/esp32/predict/<id>, /api/esp32/maintenance/<id>
    baseline.py     GET  /api/esp32/baseline/<id>
                    POST /api/esp32/baseline/<id>/reset
    recording.py    POST /api/esp32/recording/<id>/start, .../stop
                    GET  /api/esp32/recording/<id>
    analysis.py     POST /api/analyze, /api/analyze/csv
    live.py         GET  /api/esp32/live/<id>, /api/esp32/test/<id>
                    POST /api/esp32/test/<id>/start, .../stop
"""

from . import analysis, baseline, devices, live, maintenance, recording, sensor_data, status

BLUEPRINTS = (
    status.bp,
    devices.bp,
    sensor_data.bp,
    maintenance.bp,
    baseline.bp,
    recording.bp,
    analysis.bp,
    live.bp,
)


def register_routes(app):
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)
