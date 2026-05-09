"""
ML model client — embeds free-text symptom descriptions with
``sentence-transformers/all-MiniLM-L6-v2`` and serves Top-K predictions from a
calibrated classifier loaded out of ``models/model_bundle.pkl``.

Bundle layout::

    {
        "classifier": <classifier with .predict_proba()>,   # e.g. CalibratedClassifierCV
        "classes":    list[str]   # disease names, order matches classifier.classes_
    }

If the bundle is missing we fall back to a deterministic, clearly-labelled
keyword stub so the request pipeline still functions during development.

Public API:
    - async predict_top_k(text, k=3)
    - is_stub()
    - get_explainer_artefacts()  → (embedder, classifier)
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
_BUNDLE_PATH = _MODELS_DIR / "model_bundle.pkl"
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_load_lock = threading.Lock()
_loaded = False
_embedder: Any = None
_classifier: Any = None
_classes: list[str] = []
_is_stub = False


def _load_models() -> None:
    """Load the SentenceTransformer + classifier bundle once, lazily."""
    global _loaded, _embedder, _classifier, _classes, _is_stub
    if _loaded:
        return

    with _load_lock:
        if _loaded:
            return

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError:
            logger.exception(
                "sentence-transformers not installed — falling back to stub predictor."
            )
            _is_stub = True
            _loaded = True
            return

        logger.info("Loading SentenceTransformer (%s)...", _EMBEDDING_MODEL)
        try:
            _embedder = SentenceTransformer(_EMBEDDING_MODEL)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to load SentenceTransformer — falling back to stub."
            )
            _is_stub = True
            _loaded = True
            return

        if not _BUNDLE_PATH.exists():
            logger.warning(
                "No model bundle at %s — running in STUB mode. "
                "Drop a trained bundle to enable real predictions.",
                _BUNDLE_PATH,
            )
            _is_stub = True
            _loaded = True
            return

        try:
            import joblib  # type: ignore

            logger.info("Loading classifier from %s...", _BUNDLE_PATH)
            bundle = joblib.load(_BUNDLE_PATH)
            _classifier = bundle["classifier"]
            _classes = [str(c) for c in bundle["classes"]]
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to load %s — falling back to stub.", _BUNDLE_PATH,
            )
            _classifier = None
            _classes = []
            _is_stub = True
            _loaded = True
            return

        logger.info(
            "ML bundle loaded — classes=%d, classifier=%s",
            len(_classes), type(_classifier).__name__,
        )
        _loaded = True


def _predict_sync(text: str, k: int) -> list[dict[str, Any]]:
    if _is_stub or _classifier is None or _embedder is None:
        return _stub_predict(text, k)

    try:
        import numpy as np  # type: ignore

        # 1. Generate 384-D semantic vector
        vector = _embedder.encode([text])

        # 2. Predict probabilities
        probs = _classifier.predict_proba(vector)[0]

        # 3. Sort and extract Top-K
        top_indices = np.argsort(probs)[-max(k, 1):][::-1]
        return [
            {
                "condition": _classes[idx].title(),
                "confidence": float(probs[idx]),
            }
            for idx in top_indices
        ]
    except Exception:  # noqa: BLE001
        logger.exception("Prediction failed — using stub for this call only.")
        return _stub_predict(text, k)


async def predict_top_k(text: str, k: int = 3) -> list[dict[str, Any]]:
    """
    Return the Top-K predicted conditions for ``text``, sorted by confidence
    descending. Each item: ``{"condition": str, "confidence": float}``.
    """
    _load_models()
    return await asyncio.to_thread(_predict_sync, text, k)


def is_stub() -> bool:
    """True when the rule-based fallback is in use (no real model loaded)."""
    _load_models()
    return _is_stub


def get_explainer_artefacts() -> tuple[Any, Any]:
    """
    Return ``(embedder, classifier)`` for downstream explainability.

    Note: with dense semantic embeddings, attribution over individual
    dimensions isn't human-interpretable, so ``explain_service`` builds the
    payload from spaCy-extracted symptoms rather than SHAP.
    """
    _load_models()
    if _is_stub:
        return None, None
    return _embedder, _classifier


# --------------------------------------------------------------------------- #
# Deterministic stub (used only when no real artefact is available)            #
# --------------------------------------------------------------------------- #
_STUB_RULES: tuple[tuple[tuple[str, ...], tuple[tuple[str, float], ...]], ...] = (
    (
        ("fever", "cough", "sore throat", "runny nose", "cold"),
        (("Common Cold", 0.62), ("Influenza", 0.24), ("COVID-19", 0.14)),
    ),
    (
        ("chest pain", "shortness of breath", "breathless"),
        (("Angina", 0.55), ("Myocardial Infarction", 0.30), ("Anxiety", 0.15)),
    ),
    (
        ("rash", "itch", "itchy", "hives"),
        (("Allergic Reaction", 0.58), ("Eczema", 0.27), ("Urticaria", 0.15)),
    ),
    (
        ("diarrhea", "vomiting", "stomach", "nausea"),
        (("Gastroenteritis", 0.60), ("Food Poisoning", 0.28), ("IBS", 0.12)),
    ),
    (
        ("headache", "migraine"),
        (("Tension Headache", 0.55), ("Migraine", 0.30), ("Sinusitis", 0.15)),
    ),
)
_STUB_DEFAULT = (("Viral Infection", 0.50), ("Common Cold", 0.30), ("Allergy", 0.20))


def _stub_predict(text: str, k: int) -> list[dict[str, Any]]:
    lowered = text.lower()
    for keywords, predictions in _STUB_RULES:
        if any(kw in lowered for kw in keywords):
            return [{"condition": c, "confidence": p} for c, p in predictions[:k]]
    return [{"condition": c, "confidence": p} for c, p in _STUB_DEFAULT[:k]]
