"""Gunicorn settings, read automatically from this folder (Render starts
`gunicorn app:app` here, see ../render.yaml). Gunicorn binds to $PORT.

One worker: the latest samples, timed tests and recordings are kept in memory,
so every request must reach the same process. Its threads answer the app while
the ESP32s send data, instead of one request at a time.
"""

workers = 1
threads = 4
# Starting may take a while on a small server (loading the models; retraining
# them if they cannot be loaded). Gunicorn's default of 30 s would kill it.
timeout = 120
