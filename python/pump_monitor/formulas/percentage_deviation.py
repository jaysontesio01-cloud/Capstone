"""Formula 3: Percentage Deviation.

    D = |A_current - A_baseline| / A_baseline * 100

Shows how much the current vibration level (the three-axis RMS from
Formula 2) has changed compared with the pump's baseline. Following
ISO 17359:2018, the baseline is measured when the pump's operation is known
to be acceptable and stable, in its normal operating state, after wear-in
(see pump_monitor/vibration_baseline.py). A large deviation is a sign of a possible mechanical problem
and is used to raise an inspection warning.

Example: baseline = 2.0, current = 3.0  ->  D = 50 %
"""


def percentage_deviation(current, baseline):
    """Return the percentage deviation of `current` from `baseline`."""
    if baseline <= 0:
        raise ValueError("baseline must be greater than zero")
    return float(abs(current - baseline) / baseline * 100.0)
