"""Legacy web page for checking a CSV for bearing anomalies (from before the Flutter app).

Not part of the backend API. Run it from the python folder, then open
http://localhost:5000 (stop the backend first; both use port 5000):

    python legacy/accelerometer.py
"""

import sys
from pathlib import Path

from flask import Flask, request, render_template
import pandas as pd
import numpy as np
import pickle
import pywt
from io import StringIO
import re

# Lets this script import pump_monitor when run as `python legacy/accelerometer.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pump_monitor import config  # noqa: E402

app = Flask(__name__)  # templates/ next to this file

with open(config.BEARING_MODEL_FILE, "rb") as f_model:
    model = pickle.load(f_model)

with open(config.BEARING_SCALER_FILE, "rb") as f_scaler:
    scaler = pickle.load(f_scaler)

def create_windows(data, window_size=128, step=64):
    windows = []
    for i in range(0, len(data) - window_size, step):
        windows.append(data.iloc[i:i+window_size].values)
    return np.array(windows)

def extract_features(windows):
    features = []
    for w in windows:
        feat = []
        for i in range (w.shape[1]):
            signal = w[:, i]

            mean = np.mean(signal)
            std = np.std(signal)
            max_val = np.max(signal)
            min_val = np.min(signal)
            rms = np.sqrt(np.mean(signal**2))

            fft_vals = np.abs(np.fft.fft(signal))
            fft_mean = np.mean(fft_vals)
            fft_max = np.max(fft_vals)

            coeffs = pywt.wavedec(signal, 'db4', level=3)
            wavelet_energy = sum(np.sum(c**2) for c in coeffs)

            feat.extend([mean, std, max_val, min_val, rms, fft_mean, fft_max, wavelet_energy])
        features.append(feat)
    return np.array(features)

@app.route("/", methods=["GET", "POST"])
def index():
    results = []
    overall_status = ""
    if request.method == "POST":
        try:
            if "file" in request.files and request.files["file"].filename !="":
                file = request.files["file"]
                df = pd.read_csv(file)
            else:
                raw_data = request.form["rawdata"]
                clean_data = re.sub(r'[^0-9\.,\-+\n]','', raw_data)
                df = pd.read_csv(StringIO(clean_data))

            df = df[['x', 'y', 'z']]

            windows = create_windows(df)
            X = extract_features(windows)
            X_scaled = scaler.transform(X)

            preds = model.predict(X_scaled)
            scores = model.decision_function(X_scaled)

            result_list = []
            anomaly_count = 0
            for i in range(len(preds)):
                if preds[i] == -1:
                    status = "Bearing Anomaly Detected"
                    anomaly_count += 1
                else:
                    status = "Normal"
                result_list.append({
                    "window": i,
                    "status": status,
                    "score":float(scores[i])
                })

            results = result_list
            overall_status = "Warning: Bearing anomalies detected!" if anomaly_count > 0 else "Machine is operating normally."
        
        except Exception as e:
            results = [{"window": "-", "status": "Error: "+str(e), "score": "-"}]
            overall_status = "Error processing data"

    return render_template("index.html", results=results, overall_status=overall_status)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)