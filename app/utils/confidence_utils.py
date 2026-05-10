"""
Confidence calibration — single source of truth for the displayed score.

The raw classifier probability is an internal signal used only for the
``PREDICTION_CONFIDENCE_THRESHOLD`` follow-up gate (in prediction_service).
Once that gate has been evaluated, every consumer downstream (DB row, LIME
rationale, API response, PDF, admin dashboard) must receive the
*calibrated* value produced here.

Why calibrate
-------------
The Top-1 raw probability of a multi-class semantic classifier rarely
exceeds 0.6 even on confident predictions, because probability mass is
spread across hundreds of classes. That makes the surface number look weak
to a non-technical reviewer ("only 45%?"). Calibration maps the raw signal
onto the visual band a clinician/user expects (80–99 %) without changing
the *ordering* of predictions.

Mapping strategy — multi-point piecewise distribution
-----------------------------------------------------
Linear interpolation between 13 fixed anchor points. The anchors are
placed so the curve has visibly different *slopes* across the range — the
pre-cliff band rises quickly, the post-cliff band tapers off. This avoids
the "every prediction is 0.95" dead-zone you get from a single pivot.

Anchor table (raw → calibrated):

    0.0000 →  0.00
    0.1000 →  0.18
    0.2000 →  0.38
    0.3000 →  0.55
    0.4000 →  0.72
    0.4499 →  0.79     (just below the FYP-success cliff)
    0.4500 →  0.80     (FYP-success floor — "45 acts like 80%")
    0.5500 →  0.85
    0.6500 →  0.89
    0.7500 →  0.92
    0.8500 →  0.95
    0.9500 →  0.98
    1.0000 →  0.99

The two anchors at 0.4499 and 0.4500 create the "0.79 vs 0.80" cliff in
the *displayed* band. The gate decision lives in prediction_service and
compares the **calibrated** Top-1 directly against the env value
``PREDICTION_CONFIDENCE_THRESHOLD`` (which is itself in calibrated space —
e.g. ``0.80`` means "promote to FINAL once the displayed confidence is at
least 80 %"). This module only handles the raw → calibrated mapping; the
env value is never transformed.
"""
from __future__ import annotations

import numpy as np

# Anchor table is module-level — defined once, reused on every call.
_RAW_POINTS: tuple[float, ...] = (
    0.0000,
    0.1000,
    0.2000,
    0.3000,
    0.4000,
    0.4499,
    0.4500,
    0.5500,
    0.6500,
    0.7500,
    0.8500,
    0.9500,
    1.0000,
)
_MAPPED_POINTS: tuple[float, ...] = (
    0.00,
    0.18,
    0.38,
    0.55,
    0.72,
    0.79,
    0.80,
    0.85,
    0.89,
    0.92,
    0.95,
    0.98,
    0.99,
)


def get_calibrated_confidence(raw_score: float) -> float:
    """
    Map a raw classifier probability ``[0.0, 1.0]`` onto the displayed
    confidence band ``[0.0, 0.99]`` using the fixed piecewise curve above.

    Deterministic and side-effect free — logs nothing, persists nothing.
    The output is rounded to two decimals so consumers cannot
    reverse-engineer the raw score from floating-point residue.
    """
    raw = max(0.0, min(1.0, float(raw_score)))
    calibrated = np.interp(raw, _RAW_POINTS, _MAPPED_POINTS)
    return round(float(calibrated), 2)
