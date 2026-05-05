"""
ML model client — loads a persisted scikit-learn artefact once (lazily) and
serves async Top-K predictions for free-text symptom descriptions.

Primary artefact: `models/model_bundle.pkl` — a dict with the structure

    {
        "vectorizer":     <fitted TfidfVectorizer>,
        "model":          <classifier with .predict_proba()>,   # e.g. CalibratedClassifierCV(LinearSVC)
        "classes":        list[str]  # 24 disease names, ORDER must match model.classes_
        "feature_names":  np.ndarray of feature names (informational)
    }

Backwards-compatible artefact paths (probed in this order):
    1. models/model_bundle.pkl        ← preferred (current trained pipeline)
    2. models/model.pkl + optional models/vectorizer.pkl
       - sklearn `Pipeline` ending in a classifier with predict_proba, OR
       - dict `{"vectorizer": ..., "model": ...}` (the old bundle layout), OR
       - bare classifier (requires vectorizer.pkl alongside)

If nothing loads we fall back to a deterministic, clearly-labelled rule-based
stub so the rest of the request pipeline still functions during development.

Public API:
    - class MLClient                     — instance form (`await client.predict_top_k(...)`).
    - async predict_top_k(text, k=3)     — module-level convenience (delegates to a singleton).
    - is_stub()                          — module-level helper used in logging.
"""
from __future__ import annotations

import asyncio
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Paths                                                                        #
# --------------------------------------------------------------------------- #
_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
_BUNDLE_PATH = _MODELS_DIR / "model_bundle.pkl"
_MODEL_PATH = _MODELS_DIR / "model.pkl"
_VECTORIZER_PATH = _MODELS_DIR / "vectorizer.pkl"


# --------------------------------------------------------------------------- #
# Class API                                                                    #
# --------------------------------------------------------------------------- #
class MLClient:
    """
    Thread-safe lazy-loaded ML client. The expensive load happens once on the
    first prediction; subsequent calls are cheap. The actual sklearn call is
    dispatched to a worker thread so the FastAPI event loop stays responsive.
    """

    def __init__(
        self,
        bundle_path: Path = _BUNDLE_PATH,
        model_path: Path = _MODEL_PATH,
        vectorizer_path: Path = _VECTORIZER_PATH,
    ) -> None:
        self._bundle_path = bundle_path
        self._model_path = model_path
        self._vectorizer_path = vectorizer_path

        self._lock = threading.Lock()
        self._loaded = False
        self._is_stub = False

        self._vectorizer: Any = None    # text → feature matrix transformer
        self._model: Any = None         # classifier with predict_proba (preferred)
        self._classes: list[str] = []   # human-readable class labels
        self._feature_names: Any = None # informational only

        # True when the loaded `_model` is a full sklearn Pipeline that takes
        # raw text directly; in that case `_vectorizer` stays None.
        self._uses_pipeline_text_input = False

    # ----------------------------------------------------------------------- #
    # Public                                                                  #
    # ----------------------------------------------------------------------- #
    async def predict_top_k(self, text: str, k: int = 3) -> list[dict[str, Any]]:
        """
        Return the Top-K predicted conditions for `text`, sorted by confidence
        descending. Each item: ``{"condition": str, "confidence": float}``.
        """
        self._ensure_loaded()
        return await asyncio.to_thread(self._predict_sync, text, k)

    def is_stub(self) -> bool:
        """True when the rule-based fallback is in use (no real model loaded)."""
        self._ensure_loaded()
        return self._is_stub

    @property
    def classes(self) -> list[str]:
        """The 24 disease class labels, in model order."""
        self._ensure_loaded()
        return list(self._classes)

    def get_explainer_artefacts(self) -> tuple[Any, Any]:
        """
        Return ``(vectorizer, model)`` for downstream explainability.

        The classifier is unwrapped from a sklearn ``Pipeline`` (when the
        artefact uses one), and the vectorizer is pulled out of the same
        Pipeline's first step in that case. Either may be ``None`` when the
        stub predictor is active.
        """
        self._ensure_loaded()
        if self._is_stub:
            return None, None

        if self._uses_pipeline_text_input:
            steps = getattr(self._model, "steps", None)
            if not steps:
                return None, self._model
            vectorizer = steps[0][1] if hasattr(steps[0][1], "transform") else None
            classifier = steps[-1][1]
            return vectorizer, classifier

        return self._vectorizer, self._model

    # ----------------------------------------------------------------------- #
    # Loading                                                                 #
    # ----------------------------------------------------------------------- #
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def _load(self) -> None:
        """Try each supported artefact layout in order; fall back to stub."""
        try:
            import joblib  # type: ignore
        except ImportError:
            logger.exception(
                "joblib not installed — cannot load any model artefact. "
                "Falling back to stub predictor."
            )
            self._is_stub = True
            return

        # 1. Preferred: explicit bundle file
        if self._bundle_path.exists():
            if self._load_bundle(joblib, self._bundle_path):
                return

        # 2. Legacy: model.pkl (Pipeline / dict bundle / bare classifier)
        if self._model_path.exists():
            if self._load_legacy(joblib, self._model_path):
                return

        logger.warning(
            "No ML artefact found (looked for %s and %s). Using STUB predictor — "
            "drop a trained bundle to enable real predictions.",
            self._bundle_path, self._model_path,
        )
        self._is_stub = True

    # --- bundle loader --------------------------------------------------- #
    def _load_bundle(self, joblib_mod: Any, path: Path) -> bool:
        """Load the canonical `model_bundle.pkl` produced by the ML pipeline."""
        try:
            bundle = joblib_mod.load(path)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load bundle %s — trying legacy paths.", path)
            return False

        if not isinstance(bundle, dict):
            logger.warning(
                "%s does not contain a dict bundle (got %s). Trying legacy paths.",
                path, type(bundle).__name__,
            )
            return False

        vectorizer = bundle.get("vectorizer")
        model = bundle.get("model")
        if vectorizer is None or model is None:
            logger.warning(
                "%s is missing required keys 'vectorizer' / 'model' "
                "(found: %s). Trying legacy paths.",
                path, list(bundle.keys()),
            )
            return False

        if not hasattr(model, "predict_proba"):
            logger.warning(
                "Bundle's classifier (%s) has no predict_proba — confidence "
                "scores will be derived from decision_function as a fallback.",
                type(model).__name__,
            )

        self._vectorizer = vectorizer
        self._model = model
        self._classes = self._resolve_classes(
            explicit=bundle.get("classes"),
            classifier=model,
        )
        self._feature_names = bundle.get("feature_names")
        self._uses_pipeline_text_input = False
        self._is_stub = False

        logger.info(
            "ML bundle loaded from %s — classes=%d, features=%s, "
            "model=%s, vectorizer=%s",
            path, len(self._classes),
            "n/a" if self._feature_names is None else len(self._feature_names),
            type(model).__name__, type(vectorizer).__name__,
        )
        return True

    # --- legacy loader --------------------------------------------------- #
    def _load_legacy(self, joblib_mod: Any, path: Path) -> bool:
        """Support the older Pipeline-or-dict-or-classifier layouts."""
        try:
            artefact = joblib_mod.load(path)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load %s — falling back to stub.", path)
            return False

        # Dict bundle: same shape as `_load_bundle`, just stored at model.pkl
        if isinstance(artefact, dict) and "model" in artefact:
            return self._load_bundle(joblib_mod, path)

        # Full Pipeline whose first step is a text vectorizer → feed text directly.
        if _has_text_pipeline(artefact):
            self._vectorizer = None
            self._model = artefact
            self._classes = self._resolve_classes(None, artefact)
            self._uses_pipeline_text_input = True
            self._is_stub = False
            logger.info(
                "ML Pipeline loaded from %s — classes=%d, model=%s",
                path, len(self._classes), type(artefact).__name__,
            )
            return True

        # Bare classifier → requires sibling vectorizer.pkl
        if not self._vectorizer_path.exists():
            logger.warning(
                "Loaded a bare classifier from %s but no %s alongside — "
                "cannot vectorize input. Falling back to stub.",
                path, self._vectorizer_path,
            )
            return False
        try:
            vectorizer = joblib_mod.load(self._vectorizer_path)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to load %s — falling back to stub.", self._vectorizer_path
            )
            return False

        self._vectorizer = vectorizer
        self._model = artefact
        self._classes = self._resolve_classes(None, artefact)
        self._uses_pipeline_text_input = False
        self._is_stub = False
        logger.info(
            "Legacy split artefacts loaded — classifier=%s, vectorizer=%s, classes=%d",
            type(artefact).__name__, type(vectorizer).__name__, len(self._classes),
        )
        return True

    @staticmethod
    def _resolve_classes(explicit: Any, classifier: Any) -> list[str]:
        """
        Prefer the bundle's explicit `classes` list; otherwise fall back to
        the classifier's `.classes_` attribute. Always returns a list of str.
        """
        candidates = explicit if explicit is not None else getattr(
            classifier, "classes_", None
        )
        if candidates is None:
            return []
        return [str(c) for c in candidates]

    # ----------------------------------------------------------------------- #
    # Prediction                                                              #
    # ----------------------------------------------------------------------- #
    def _predict_sync(self, text: str, k: int) -> list[dict[str, Any]]:
        if self._is_stub or self._model is None:
            return _stub_predict(text, k)

        try:
            import numpy as np  # type: ignore

            if self._uses_pipeline_text_input:
                X = [text]
            else:
                X = self._vectorizer.transform([text])

            if hasattr(self._model, "predict_proba"):
                probs = self._model.predict_proba(X)[0]
            elif hasattr(self._model, "decision_function"):
                # Softmax over decision scores as a reasonable proxy.
                scores = np.atleast_2d(self._model.decision_function(X))[0]
                exp = np.exp(scores - scores.max())
                probs = exp / exp.sum()
            else:
                label = self._model.predict(X)[0]
                return [{"condition": str(label), "confidence": 1.0}]

            classes = self._classes or [str(c) for c in getattr(self._model, "classes_", [])]
            top_idx = np.argsort(probs)[::-1][: max(k, 1)]
            return [
                {"condition": classes[i], "confidence": float(probs[i])}
                for i in top_idx
            ]
        except Exception:  # noqa: BLE001
            logger.exception("Prediction failed — using stub for this call only.")
            return _stub_predict(text, k)


# --------------------------------------------------------------------------- #
# Module-level convenience API (used by app.services.prediction_service)       #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_ml_client() -> MLClient:
    """Return a process-wide singleton MLClient."""
    return MLClient()


async def predict_top_k(text: str, k: int = 3) -> list[dict[str, Any]]:
    """Module-level shortcut equivalent to ``get_ml_client().predict_top_k(...)``."""
    return await get_ml_client().predict_top_k(text, k)


def is_stub() -> bool:
    """Whether the active client is currently using the rule-based stub."""
    return get_ml_client().is_stub()


def get_explainer_artefacts() -> tuple[Any, Any]:
    """
    Module-level shortcut for ``get_ml_client().get_explainer_artefacts()``.

    Used by ``app.services.explain_service`` to compute UC-04 feature
    importance directly off the trained linear model.
    """
    return get_ml_client().get_explainer_artefacts()


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _has_text_pipeline(obj: Any) -> bool:
    """Heuristic: a sklearn Pipeline whose first step is a text vectorizer."""
    steps = getattr(obj, "steps", None)
    if not steps:
        return False
    first = steps[0][1]
    return hasattr(first, "transform") and hasattr(first, "get_feature_names_out")


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
