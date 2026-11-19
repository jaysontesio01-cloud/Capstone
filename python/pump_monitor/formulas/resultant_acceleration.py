"""Formula 1: Resultant Acceleration.

    A_i = sqrt(X_i^2 + Y_i^2 + Z_i^2)

Combines the X, Y and Z readings of one ADXL345 sample into a single
acceleration magnitude, so vibration in every direction is captured in one
number. The result has the same unit as the inputs (g in this system).

Example: X = 2, Y = 3, Z = 4  ->  A_i = sqrt(29) = 5.39
"""

import numpy as np


def resultant_acceleration(x, y, z):
    """Return the resultant acceleration of each sample.

    x, y and z can be single numbers, NumPy arrays or pandas Series of equal
    length; the result has the same shape (one magnitude per sample).
    """
    return np.sqrt(np.square(x) + np.square(y) + np.square(z))
