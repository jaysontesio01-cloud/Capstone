"""Formula 2: Three-Axis RMS Acceleration.

    A_RMS = sqrt( (1/N) * sum_{i=1..N} (X_i^2 + Y_i^2 + Z_i^2) )

Summarizes a window of N samples into one overall vibration level, so a
single momentary reading has less effect and different periods can be
compared against the pump's normal baseline (Formula 3).

The formula assumes static gravity and sensor offsets were already removed
(for example by subtracting each axis' mean); these functions do not do that
themselves. The result has the same unit as the inputs (g in this system).

axis_rms() is the same RMS for one axis, used to find which direction
contributes most to the vibration.
"""

import numpy as np


def three_axis_rms(x, y, z):
    """Return the three-axis RMS acceleration of N samples.

    x, y and z are sequences of equal length holding one value per sample.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    if x.size == 0:
        raise ValueError("three_axis_rms needs at least one sample")
    return float(np.sqrt(np.mean(x ** 2 + y ** 2 + z ** 2)))


def axis_rms(values):
    """Return the RMS of a single axis: sqrt( (1/N) * sum(v_i^2) )."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        raise ValueError("axis_rms needs at least one sample")
    return float(np.sqrt(np.mean(values ** 2)))
