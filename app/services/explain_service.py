"""
UC-04 Explainability — builds the ``Explanation`` block returned by
``POST /api/v1/predict/text``.

The exact contract (per the project document):

    {
        "rationale":          "<natural-language sentence>",
        "key_symptoms":       ["rash", "itching", ...],
        "feature_importance": [
            {"feature": "rash",    "importance": 0.45},
            {"feature": "itching", "importance": 0.32},
            ...
        ]
    }

Design notes
------------
- Per the project document, attributions are computed using **SHAP**
  (``shap.LinearExplainer``). For our linear pipeline
  (``TfidfVectorizer → CalibratedClassifierCV(LinearSVC)``) LinearExplainer
  is exact and runs in microseconds per prediction, so we stay well inside
  the 2.5 s budget.
- Multi-tier fallback so the endpoint never 500s on an unusual artefact:
      1. SHAP ``LinearExplainer`` on the underlying LinearSVC (averaged
         across calibration folds). REAL SHAP attribution.
      2. Analytic linear attribution ``tfidf * coef[class]`` — used when
         ``shap`` is unavailable or raises (mathematically the same as
         LinearExplainer with a zeros background, kept as a safety net).
      3. ``models/feature_importance.json`` per-class scores intersected
         with the user's input tokens.
      4. Empty list — rationale + key_symptoms still render.
- Bilingual: ``rationale`` and ``confidence_breakdown`` are rendered in Urdu
  when the caller's language is Urdu, English otherwise.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Literal

from app.core.logging import get_logger
from app.models.response import Explanation, FeatureImportance

logger = get_logger(__name__)

LanguageLiteral = Literal["English", "Urdu"]

_FEATURE_IMPORTANCE_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "feature_importance.json"
)
_MAX_FEATURE_BARS = 10  # ↑ for the bar chart
_MAX_KEY_SYMPTOMS = 5


# --------------------------------------------------------------------------- #
# Optional SHAP import — degrade gracefully if the wheel is missing.           #
#                                                                              #
# NOTE on the numba shim: ``shap.utils._clustering`` does                       #
# ``from numba import njit`` at module-import time, even though                 #
# ``LinearExplainer`` never executes any jitted code. On hardened Windows       #
# boxes the numba DLL (``_dynfunc.pyd``) is sometimes blocked by the OS         #
# Application Control policy, which crashes ``import shap`` outright. To stay   #
# resilient we install a no-op ``numba`` stub into ``sys.modules`` before       #
# importing shap whenever the real numba can't load. The stub provides          #
# pass-through ``njit`` / ``jit`` decorators — clustering helpers that depend   #
# on jitted code are unused by ``LinearExplainer``.                             #
# --------------------------------------------------------------------------- #
def _install_blocked_dll_shims() -> None:
    """
    Pre-load shims for scientific-Python DLLs that may be blocked by Windows
    Application Control policies on hardened machines (numba, scipy LAPACK
    cython bindings, etc.).

    None of the shimmed modules are touched by ``shap.LinearExplainer`` —
    they only get pulled in transitively by shap's clustering / plotting
    helpers, which we don't use. So as long as ``import shap`` succeeds, the
    SHAP attribution we compute is the real, mathematically-exact value.
    """
    import sys
    import types

    def _passthrough_decorator(*args: Any, **kwargs: Any):
        if args and callable(args[0]) and not kwargs:
            return args[0]

        def _wrap(fn: Any) -> Any:
            return fn

        return _wrap

    # ---- numba shim --------------------------------------------------- #
    if "numba" not in sys.modules:
        try:
            import numba  # type: ignore  # noqa: F401
        except Exception:  # noqa: BLE001
            for name in [m for m in sys.modules if m == "numba" or m.startswith("numba.")]:
                sys.modules.pop(name, None)

            # Pre-populate the most commonly-imported numba submodules so any
            # `from numba.X import Y` shap may use resolves cleanly.
            numba_stub = types.ModuleType("numba")
            numba_stub.__path__ = []  # type: ignore[attr-defined]   # mark as package
            numba_stub.njit = _passthrough_decorator       # type: ignore[attr-defined]
            numba_stub.jit = _passthrough_decorator        # type: ignore[attr-defined]
            numba_stub.vectorize = _passthrough_decorator  # type: ignore[attr-defined]
            numba_stub.guvectorize = _passthrough_decorator  # type: ignore[attr-defined]
            numba_stub.prange = range                      # type: ignore[attr-defined]
            sys.modules["numba"] = numba_stub

            numba_typed = types.ModuleType("numba.typed")
            numba_typed.List = list   # type: ignore[attr-defined]
            numba_typed.Dict = dict   # type: ignore[attr-defined]
            numba_stub.typed = numba_typed   # type: ignore[attr-defined]
            sys.modules["numba.typed"] = numba_typed

            for sub in ("core", "core.types", "types", "experimental"):
                full = f"numba.{sub}"
                m = types.ModuleType(full)
                m.__path__ = []  # type: ignore[attr-defined]
                sys.modules[full] = m

            # Auto-stub *any* further `numba.*` import via a meta-path finder.
            class _NumbaShimFinder:
                @staticmethod
                def find_spec(fullname: str, path: Any = None, target: Any = None):
                    import importlib.util
                    if fullname == "numba" or fullname.startswith("numba."):
                        if fullname in sys.modules:
                            return None  # already provided above
                        spec = importlib.util.spec_from_loader(
                            fullname, _NumbaShimLoader()
                        )
                        return spec
                    return None

            class _NumbaShimLoader:
                @staticmethod
                def create_module(spec: Any) -> Any:
                    m = types.ModuleType(spec.name)
                    m.__path__ = []
                    m.njit = _passthrough_decorator
                    m.jit = _passthrough_decorator
                    m.List = list
                    m.Dict = dict
                    return m

                @staticmethod
                def exec_module(module: Any) -> None:
                    return None

            sys.meta_path.append(_NumbaShimFinder())

            logger.warning(
                "Real numba unavailable (DLL likely blocked by OS policy) — "
                "installed an in-process numba stub (incl. numba.typed) so "
                "shap can import. LinearExplainer does not use jitted code "
                "so this is safe."
            )

    # ---- scipy native-extension shims ---------------------------------- #
    # `shap.utils._clustering` → `scipy.spatial.distance` → `scipy.linalg`
    # → cython_lapack/cython_blas; `shap.datasets` → `scipy.io.matlab`
    # → _mio_utils. On locked-down boxes those .pyd files fail Application
    # Control. Stub them so shap's _import_ chain succeeds; LinearExplainer
    # never calls them.
    for mod_name in (
        "scipy.linalg.cython_lapack",
        "scipy.linalg.cython_blas",
        "scipy.io",
        "scipy.io.matlab",
        "scipy.io.matlab._mio_utils",
        "scipy.io.matlab._streams",
        "scipy.io.matlab._mio5_utils",
    ):
        if mod_name in sys.modules:
            continue
        try:
            __import__(mod_name)
        except Exception:  # noqa: BLE001
            stub = types.ModuleType(mod_name)
            stub.__path__ = []  # type: ignore[attr-defined]
            sys.modules[mod_name] = stub
            logger.warning(
                "Real %s unavailable (likely blocked by OS policy) — "
                "installed an empty stub so shap's transitive imports succeed.",
                mod_name,
            )


_install_blocked_dll_shims()
try:
    import shap  # type: ignore

    _SHAP_AVAILABLE = True
    logger.info("SHAP %s loaded — UC-04 using shap.LinearExplainer.", shap.__version__)
except Exception as _shap_exc:  # noqa: BLE001
    shap = None  # type: ignore[assignment]
    _SHAP_AVAILABLE = False
    logger.warning(
        "Could not import shap (%s: %s) — UC-04 will use the analytic "
        "linear attribution fallback (mathematically equivalent to "
        "LinearExplainer with a zeros background).",
        type(_shap_exc).__name__, _shap_exc,
    )


# --------------------------------------------------------------------------- #
# Stop-word fallback for key-symptom extraction.                                #
# Mirrored from preprocess_service so this module has no hard dependency on it.#
# --------------------------------------------------------------------------- #
_GENERIC_TOKENS: frozenset[str] = frozenset({
    "feel", "feeling", "felt", "having", "have", "had", "got", "getting",
    "since", "from", "for", "the", "and", "with", "without", "very",
    "really", "much", "some", "any", "all", "this", "that", "these", "those",
    "today", "yesterday", "tomorrow", "day", "days", "week", "weeks",
    "month", "months", "year", "years", "hour", "hours", "minute", "minutes",
    "now", "then", "ago", "still", "always", "never", "sometimes",
    "more", "most", "less", "least", "lot", "lots", "bit", "little",
    "old", "young", "year",
    # words that show up as high-weight tokens but are not real symptoms
    "is", "are", "been", "of", "it", "my", "to", "in", "on", "at",
})

_TOKEN_RE = re.compile(r"[a-z]+(?:[\s-][a-z]+){0,2}")


# --------------------------------------------------------------------------- #
# Public class                                                                 #
# --------------------------------------------------------------------------- #
class ExplainService:
    """
    UC-04 explainability service.

    The class is stateful only insofar as it caches the bundled
    ``feature_importance.json`` (read once, kept in memory) — every call to
    ``get_explanation`` is otherwise pure.
    """

    def __init__(
        self, feature_importance_path: Path = _FEATURE_IMPORTANCE_PATH
    ) -> None:
        self._fi_path = feature_importance_path
        self._fi_cache: dict[str, Any] | None = None
        self._fi_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Primary entrypoint                                                  #
    # ------------------------------------------------------------------ #
    async def get_explanation(
        self,
        text: str,
        top_predictions: list[dict[str, Any]],
        vectorizer: Any,
        model: Any,
        *,
        language: LanguageLiteral = "English",
        extracted_symptoms: Iterable[str] | None = None,
        cleaned_text: str | None = None,
        top_condition_display_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Build the UC-04 explanation dict for one prediction.

        Parameters
        ----------
        text:
            Original (or cleaned) input text — used for vectorisation and as a
            fallback source of key symptoms.
        top_predictions:
            The same Top-K list returned by ``ml_client.predict_top_k`` —
            ``[{"condition": str, "confidence": float}, ...]``. Item 0 is
            treated as the focus class for feature attribution.
        vectorizer:
            Fitted text vectorizer (e.g. ``TfidfVectorizer``). May be ``None``
            when the loaded artefact is a full sklearn ``Pipeline`` — in that
            case we attempt to pull the vectorizer out of ``model.steps[0]``.
        model:
            Fitted classifier. Linear models (or
            ``CalibratedClassifierCV(LinearSVC)``) yield per-feature
            attribution; non-linear models fall back to the bundled
            per-class importance file.
        language:
            Caller's resolved language ("English" / "Urdu"). Drives the
            wording of ``rationale`` and ``confidence_breakdown``.
        extracted_symptoms:
            Optional pre-computed symptoms (from
            ``preprocess_service.extract_symptoms``). Used as the primary
            signal for ``key_symptoms``.
        cleaned_text:
            Optional cleaned text. Used for the key-symptom fallback when
            no symptoms were extracted.
        top_condition_display_name:
            Pre-localised display name of the top condition (e.g. the Urdu
            label). Defaults to the English condition name from
            ``top_predictions[0]``.

        Returns
        -------
        dict
            ``{"rationale", "key_symptoms", "feature_importance",
               "confidence_breakdown"}``
        """
        if not top_predictions:
            return self._empty_response(language)

        top = top_predictions[0]
        top_class_en = str(top.get("condition", "Unknown"))
        top_confidence = float(top.get("confidence", 0.0))
        display_name = top_condition_display_name or top_class_en

        vectorizer = self._resolve_vectorizer(vectorizer, model)
        underlying_model = self._unwrap_pipeline_classifier(model)

        feature_importance = self._compute_feature_importance(
            text=text,
            top_class_en=top_class_en,
            vectorizer=vectorizer,
            model=underlying_model,
        )

        key_symptoms = self._resolve_key_symptoms(
            extracted_symptoms=extracted_symptoms or [],
            cleaned_text=cleaned_text or text,
            feature_importance=feature_importance,
        )

        confidence_pct = max(0, min(100, round(top_confidence * 100)))
        if language == "Urdu":
            rationale, breakdown = _build_urdu(
                display_name, confidence_pct, key_symptoms
            )
        else:
            rationale, breakdown = _build_english(
                display_name, confidence_pct, key_symptoms
            )

        logger.info(
            "Explainability: lang=%s top=%r conf=%d%% key_symptoms=%d "
            "feature_importance=%d",
            language, display_name, confidence_pct,
            len(key_symptoms), len(feature_importance),
        )

        return {
            "rationale": rationale,
            "key_symptoms": key_symptoms,
            "feature_importance": feature_importance,
            "confidence_breakdown": breakdown,
        }

    # ------------------------------------------------------------------ #
    # Convenience wrappers                                                #
    # ------------------------------------------------------------------ #
    async def build(
        self,
        *,
        text: str,
        top_predictions: list[dict[str, Any]],
        vectorizer: Any,
        model: Any,
        language: LanguageLiteral = "English",
        extracted_symptoms: Iterable[str] | None = None,
        cleaned_text: str | None = None,
        top_condition_display_name: str | None = None,
    ) -> Explanation:
        """``get_explanation`` but returns a typed ``Explanation`` model."""
        data = await self.get_explanation(
            text=text,
            top_predictions=top_predictions,
            vectorizer=vectorizer,
            model=model,
            language=language,
            extracted_symptoms=extracted_symptoms,
            cleaned_text=cleaned_text,
            top_condition_display_name=top_condition_display_name,
        )
        return Explanation(
            rationale=data["rationale"],
            key_symptoms=data["key_symptoms"],
            feature_importance=[
                FeatureImportance(**fi) for fi in data["feature_importance"]
            ],
            confidence_breakdown=data["confidence_breakdown"],
        )

    # ------------------------------------------------------------------ #
    # Feature importance — multi-tier strategy                            #
    # ------------------------------------------------------------------ #
    def _compute_feature_importance(
        self,
        *,
        text: str,
        top_class_en: str,
        vectorizer: Any,
        model: Any,
    ) -> list[dict[str, Any]]:
        """
        Try, in order:
          1. SHAP ``LinearExplainer`` on the underlying linear classifier
             (REAL SHAP attribution per UC-04).
          2. Analytic ``tfidf * coef[class]`` — equivalent fallback when SHAP
             is unavailable or raises on this artefact.
          3. ``models/feature_importance.json`` per-class scores intersected
             with the user's input tokens.
          4. Empty list.
        """
        # Tier 1 — SHAP LinearExplainer
        bars = self._shap_based_importance(text, top_class_en, vectorizer, model)
        if bars:
            return bars

        # Tier 2 — analytic linear attribution (mathematical equivalent)
        bars = self._coef_based_importance(text, top_class_en, vectorizer, model)
        if bars:
            return bars

        # Tier 3 — bundled per-class importance file
        bars = self._json_based_importance(text, top_class_en)
        if bars:
            return bars

        return []

    # ---- Tier 1: SHAP LinearExplainer -------------------------------- #
    @staticmethod
    def _shap_based_importance(
        text: str, top_class_en: str, vectorizer: Any, model: Any
    ) -> list[dict[str, Any]]:
        """
        Compute SHAP values via ``shap.LinearExplainer`` against the
        underlying linear classifier.

        For ``CalibratedClassifierCV(LinearSVC)`` we run LinearExplainer
        once per calibration fold (each holds a real ``LinearSVC`` with
        ``coef_``) and average the resulting SHAP values, then take the
        Top-N features by absolute SHAP magnitude for the predicted class.
        """
        if not _SHAP_AVAILABLE or vectorizer is None or model is None or not text:
            return []
        try:
            import numpy as np  # type: ignore
            import scipy.sparse as sp  # type: ignore
        except ImportError:
            return []

        try:
            X = vectorizer.transform([text])
        except Exception:  # noqa: BLE001
            logger.debug("Vectorizer.transform failed for SHAP.", exc_info=True)
            return []

        estimators = _collect_linear_estimators(model)
        if not estimators:
            logger.debug("No linear sub-estimators found — skipping SHAP tier.")
            return []

        # Background = a single zero row (matches sparse TF-IDF prior:
        # most documents have zero for most features). LinearExplainer is
        # exact under this assumption.
        try:
            n_features = X.shape[1]
            background = sp.csr_matrix((1, n_features), dtype=X.dtype)
        except Exception:  # noqa: BLE001
            return []

        feature_names = _get_feature_names(vectorizer)
        if feature_names is None or len(feature_names) != X.shape[1]:
            return []

        shap_rows: list[Any] = []
        target_class = str(top_class_en)
        for est in estimators:
            classes_attr = getattr(est, "classes_", None)
            if classes_attr is None:
                continue
            classes = [str(c) for c in classes_attr]
            if target_class not in classes:
                continue
            class_idx = classes.index(target_class)
            try:
                explainer = shap.LinearExplainer(est, background)
                sv = explainer.shap_values(X)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "shap.LinearExplainer failed for one fold; skipping it.",
                    exc_info=True,
                )
                continue

            row = _select_class_row(sv, class_idx)
            if row is not None:
                shap_rows.append(np.asarray(row, dtype=float))

        if not shap_rows:
            return []

        shap_values = np.mean(np.vstack(shap_rows), axis=0)

        # Only attribute over features the user actually used (non-zero TF-IDF).
        try:
            row_dense = X.toarray()[0] if hasattr(X, "toarray") else np.asarray(X)[0]
        except Exception:  # noqa: BLE001
            return []
        nonzero = np.flatnonzero(row_dense)
        if nonzero.size == 0:
            return []

        idx_sorted = nonzero[np.argsort(-np.abs(shap_values[nonzero]))]
        idx_top = idx_sorted[:_MAX_FEATURE_BARS]
        magnitudes = np.abs(shap_values[idx_top])
        max_mag = float(magnitudes.max()) if magnitudes.size else 0.0
        if max_mag <= 0:
            return []

        bars: list[dict[str, Any]] = []
        for i, mag in zip(idx_top, magnitudes):
            feat = str(feature_names[i])
            if feat.lower() in _GENERIC_TOKENS:
                continue
            bars.append({
                "feature": feat,
                "importance": round(float(mag) / max_mag, 3),
            })
        if bars:
            logger.debug(
                "SHAP attribution: top_class=%s features=%d folds=%d",
                target_class, len(bars), len(shap_rows),
            )
        return bars

    # ---- Tier 1: linear-coef attribution ----------------------------- #
    @staticmethod
    def _coef_based_importance(
        text: str, top_class_en: str, vectorizer: Any, model: Any
    ) -> list[dict[str, Any]]:
        if vectorizer is None or model is None or not text:
            return []
        try:
            import numpy as np  # type: ignore
        except ImportError:
            return []

        try:
            X = vectorizer.transform([text])
        except Exception:  # noqa: BLE001
            logger.debug("Vectorizer.transform failed; skipping coef attribution.",
                         exc_info=True)
            return []

        coefs = _extract_class_coef(model, top_class_en)
        if coefs is None:
            return []

        try:
            row = X.toarray()[0] if hasattr(X, "toarray") else np.asarray(X)[0]
        except Exception:  # noqa: BLE001
            return []

        if row.shape[0] != coefs.shape[0]:
            logger.debug(
                "coef/feature dim mismatch (%d vs %d); skipping coef attribution.",
                row.shape[0], coefs.shape[0],
            )
            return []

        contributions = row * coefs
        nonzero = np.flatnonzero(row)
        if nonzero.size == 0:
            return []

        feature_names = _get_feature_names(vectorizer)
        if feature_names is None or len(feature_names) != row.shape[0]:
            return []

        # Rank by absolute contribution but keep sign info for the UI
        # (frontend just needs magnitudes for a bar chart, so we return
        # absolute, normalised values).
        idx_sorted = nonzero[np.argsort(-np.abs(contributions[nonzero]))]
        idx_top = idx_sorted[:_MAX_FEATURE_BARS]
        magnitudes = np.abs(contributions[idx_top])
        max_mag = float(magnitudes.max()) if magnitudes.size else 0.0
        if max_mag <= 0:
            return []

        bars: list[dict[str, Any]] = []
        for i, mag in zip(idx_top, magnitudes):
            feat = str(feature_names[i])
            if feat.lower() in _GENERIC_TOKENS:
                continue
            bars.append({
                "feature": feat,
                "importance": round(float(mag) / max_mag, 3),
            })
        return bars

    # ---- Tier 2: bundled JSON --------------------------------------- #
    def _json_based_importance(
        self, text: str, top_class_en: str
    ) -> list[dict[str, Any]]:
        data = self._load_feature_importance_json()
        if not data:
            return []

        per_class = data.get("per_class_top") or {}
        candidates: list[dict[str, Any]] = (
            per_class.get(top_class_en) or data.get("top_overall") or []
        )
        if not candidates:
            return []

        text_lower = (text or "").lower()
        # Keep only tokens that actually appear in the user's input — that's
        # what makes this *per prediction*, not just a global ranking.
        intersected: list[tuple[str, float]] = []
        for entry in candidates:
            token = str(entry.get("token", "")).strip().lower()
            score = float(entry.get("score", 0.0))
            if not token or token in _GENERIC_TOKENS:
                continue
            if re.search(rf"\b{re.escape(token)}\b", text_lower):
                intersected.append((token, score))

        if not intersected:
            return []

        intersected.sort(key=lambda kv: -kv[1])
        intersected = intersected[:_MAX_FEATURE_BARS]
        max_score = intersected[0][1] if intersected else 0.0
        if max_score <= 0:
            return []

        return [
            {"feature": tok, "importance": round(score / max_score, 3)}
            for tok, score in intersected
        ]

    def _load_feature_importance_json(self) -> dict[str, Any] | None:
        if self._fi_cache is not None:
            return self._fi_cache
        with self._fi_lock:
            if self._fi_cache is not None:
                return self._fi_cache
            if not self._fi_path.exists():
                logger.info(
                    "feature_importance.json not found at %s — Tier 2 disabled.",
                    self._fi_path,
                )
                self._fi_cache = {}
                return self._fi_cache
            try:
                with self._fi_path.open("r", encoding="utf-8") as f:
                    self._fi_cache = json.load(f)
                logger.info(
                    "Loaded feature_importance.json (%d classes).",
                    len((self._fi_cache or {}).get("per_class_top") or {}),
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to read %s; Tier 2 disabled.", self._fi_path)
                self._fi_cache = {}
        return self._fi_cache

    # ------------------------------------------------------------------ #
    # Key symptoms                                                        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_key_symptoms(
        *,
        extracted_symptoms: Iterable[str],
        cleaned_text: str,
        feature_importance: list[dict[str, Any]],
        max_items: int = _MAX_KEY_SYMPTOMS,
    ) -> list[str]:
        """
        Build the list of key symptoms / phrases that influenced the model.

        Order of preference:
            1. lexicon-extracted symptoms (highest signal-to-noise)
            2. top features from the importance list (these *are* the
               phrases the model leaned on)
            3. salient tokens from the cleaned text
        """
        seen: set[str] = set()
        result: list[str] = []

        for s in extracted_symptoms:
            tok = (s or "").strip().lower()
            if tok and tok not in seen:
                seen.add(tok)
                result.append(tok)
            if len(result) >= max_items:
                return result

        for fi in feature_importance:
            tok = str(fi.get("feature", "")).strip().lower()
            if not tok or tok in seen or tok in _GENERIC_TOKENS or len(tok) < 3:
                continue
            seen.add(tok)
            result.append(tok)
            if len(result) >= max_items:
                return result

        if result:
            return result

        for tok in _TOKEN_RE.findall((cleaned_text or "").lower()):
            tok = tok.strip()
            if not tok or tok in _GENERIC_TOKENS or tok in seen or len(tok) < 3:
                continue
            seen.add(tok)
            result.append(tok)
            if len(result) >= max_items:
                break
        return result

    # ------------------------------------------------------------------ #
    # Misc helpers                                                        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_vectorizer(vectorizer: Any, model: Any) -> Any:
        """When ``model`` is a Pipeline, fish the vectorizer out of step 0."""
        if vectorizer is not None:
            return vectorizer
        steps = getattr(model, "steps", None)
        if steps:
            first = steps[0][1]
            if hasattr(first, "transform"):
                return first
        return None

    @staticmethod
    def _unwrap_pipeline_classifier(model: Any) -> Any:
        """When ``model`` is a Pipeline, return the final classifier step."""
        steps = getattr(model, "steps", None)
        if steps:
            return steps[-1][1]
        return model

    @staticmethod
    def _empty_response(language: LanguageLiteral) -> dict[str, Any]:
        if language == "Urdu":
            return {
                "rationale": "ماڈل کسی واضح بیماری کی نشاندہی نہیں کر سکا۔",
                "key_symptoms": [],
                "feature_importance": [],
                "confidence_breakdown": (
                    "اعتماد کی تقسیم دستیاب نہیں — ماڈل سے کوئی پیش گوئی نہیں ملی۔"
                ),
            }
        return {
            "rationale": "The model could not identify a likely condition from the input.",
            "key_symptoms": [],
            "feature_importance": [],
            "confidence_breakdown": (
                "No confidence breakdown available — the model returned no predictions."
            ),
        }


# --------------------------------------------------------------------------- #
# Module-level singleton + backwards-compat ``build_explanation`` wrapper      #
# --------------------------------------------------------------------------- #
_default_service: ExplainService | None = None
_default_lock = threading.Lock()


def get_explain_service() -> ExplainService:
    """Return a process-wide singleton ``ExplainService``."""
    global _default_service
    if _default_service is not None:
        return _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = ExplainService()
    return _default_service


def build_explanation(
    *,
    language: LanguageLiteral,
    top_condition_name: str,
    top_condition_confidence: float,
    extracted_symptoms: Iterable[str],
    cleaned_text: str,
    top_predictions: list[dict[str, Any]] | None = None,
    vectorizer: Any = None,
    model: Any = None,
) -> Explanation:
    """
    Backwards-compatible synchronous wrapper used by older callers.

    When ``top_predictions`` / ``vectorizer`` / ``model`` are supplied the
    full UC-04 attribution pipeline runs; otherwise the function still
    returns a valid (but feature-importance-empty) ``Explanation`` so legacy
    code paths keep working.
    """
    service = get_explain_service()

    preds = top_predictions or [
        {"condition": top_condition_name, "confidence": top_condition_confidence}
    ]

    feature_importance = service._compute_feature_importance(  # noqa: SLF001
        text=cleaned_text or "",
        top_class_en=str(preds[0].get("condition", top_condition_name)),
        vectorizer=service._resolve_vectorizer(vectorizer, model),  # noqa: SLF001
        model=service._unwrap_pipeline_classifier(model),           # noqa: SLF001
    )

    key_symptoms = service._resolve_key_symptoms(  # noqa: SLF001
        extracted_symptoms=extracted_symptoms,
        cleaned_text=cleaned_text,
        feature_importance=feature_importance,
    )

    confidence_pct = max(0, min(100, round(top_condition_confidence * 100)))
    if language == "Urdu":
        rationale, breakdown = _build_urdu(
            top_condition_name, confidence_pct, key_symptoms
        )
    else:
        rationale, breakdown = _build_english(
            top_condition_name, confidence_pct, key_symptoms
        )

    logger.info(
        "Explainability: lang=%s top=%r conf=%d%% key_symptoms=%d "
        "feature_importance=%d",
        language, top_condition_name, confidence_pct,
        len(key_symptoms), len(feature_importance),
    )

    return Explanation(
        rationale=rationale,
        key_symptoms=key_symptoms,
        feature_importance=[FeatureImportance(**fi) for fi in feature_importance],
        confidence_breakdown=breakdown,
    )


# --------------------------------------------------------------------------- #
# Sklearn introspection helpers                                                #
# --------------------------------------------------------------------------- #
def _collect_linear_estimators(model: Any) -> list[Any]:
    """
    Return the list of linear sub-estimators inside ``model``.

    For ``CalibratedClassifierCV`` we return one estimator per calibration
    fold (typically 3-5). For a bare linear classifier we return ``[model]``.
    Anything else returns ``[]`` so the SHAP tier is skipped gracefully.
    """
    folds = getattr(model, "calibrated_classifiers_", None)
    estimators: list[Any] = []
    if folds:
        for fold in folds:
            est = (
                getattr(fold, "estimator", None)
                or getattr(fold, "base_estimator", None)
            )
            if est is not None and hasattr(est, "coef_"):
                estimators.append(est)
        if estimators:
            return estimators

    if hasattr(model, "coef_"):
        return [model]
    return []


def _select_class_row(shap_values: Any, class_idx: int) -> Any:
    """
    Pick the single-sample SHAP row for ``class_idx`` regardless of which
    output shape ``shap.LinearExplainer`` produced this version.

    Possible shapes encountered in the wild:
        - list[ndarray] of length n_classes, each ndarray (n_samples, n_features)
        - ndarray (n_samples, n_features)                       (binary)
        - ndarray (n_samples, n_features, n_classes)
        - ndarray (n_classes, n_samples, n_features)
        - shap.Explanation with `.values` of one of the above
    """
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None

    values = getattr(shap_values, "values", shap_values)

    if isinstance(values, list):
        if class_idx >= len(values):
            return None
        arr = np.asarray(values[class_idx])
        return arr[0] if arr.ndim == 2 else arr

    arr = np.asarray(values)
    if arr.ndim == 2:
        return arr[0]
    if arr.ndim == 3:
        # Heuristic: the axis with size == n_classes is the class axis.
        n0, n1, n2 = arr.shape
        if n0 == 1:                                   # (1, n_features, n_classes)
            return arr[0, :, class_idx] if class_idx < n2 else None
        if n2 > 1 and class_idx < n2:                 # (n_samples, n_features, n_classes)
            return arr[0, :, class_idx]
        if class_idx < n0:                            # (n_classes, n_samples, n_features)
            return arr[class_idx, 0, :]
    return None


def _extract_class_coef(model: Any, class_name: str) -> Any:
    """
    Return the coefficient vector for ``class_name`` from a (possibly
    calibrated) linear classifier, averaged across calibration folds when
    needed. Returns ``None`` if the model isn't linear.
    """
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None

    classes = getattr(model, "classes_", None)
    if classes is None:
        return None
    try:
        classes_list = [str(c) for c in classes]
        class_idx = classes_list.index(str(class_name))
    except ValueError:
        return None

    # CalibratedClassifierCV → average coefs across folds of the underlying
    # linear estimator.
    calibrated = getattr(model, "calibrated_classifiers_", None)
    if calibrated:
        coef_rows = []
        for fold in calibrated:
            est = (
                getattr(fold, "estimator", None)
                or getattr(fold, "base_estimator", None)
            )
            coef = getattr(est, "coef_", None)
            if coef is None:
                continue
            # Binary classifiers expose coef_ of shape (1, n_features); skip
            # those — multiclass attribution only makes sense per class.
            if coef.shape[0] <= class_idx:
                continue
            coef_rows.append(coef[class_idx])
        if coef_rows:
            return np.mean(np.vstack(coef_rows), axis=0)

    coef = getattr(model, "coef_", None)
    if coef is not None and coef.shape[0] > class_idx:
        return coef[class_idx]

    return None


def _get_feature_names(vectorizer: Any) -> Any:
    """Return the vectorizer's feature names (sklearn ≥1.0 API), or None."""
    if vectorizer is None:
        return None
    if hasattr(vectorizer, "get_feature_names_out"):
        try:
            return vectorizer.get_feature_names_out()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(vectorizer, "get_feature_names"):
        try:
            return vectorizer.get_feature_names()
        except Exception:  # noqa: BLE001
            pass
    return None


# --------------------------------------------------------------------------- #
# Bilingual rationale assembly                                                 #
# --------------------------------------------------------------------------- #
def _build_english(
    top_name: str, confidence_pct: int, key_symptoms: list[str]
) -> tuple[str, str]:
    if key_symptoms:
        symptoms_phrase = _join_en(key_symptoms)
        rationale = (
            f"Based on the symptoms you described ({symptoms_phrase}), the model "
            f"identified {top_name} as the most likely condition with about "
            f"{confidence_pct}% confidence."
        )
        breakdown = (
            f"{top_name} received the highest confidence ({confidence_pct}%) "
            f"because the words you used closely match its typical symptom "
            f"profile in the training data, especially: {symptoms_phrase}."
        )
    else:
        rationale = (
            f"Based on your description, the model identified {top_name} as the "
            f"most likely condition with about {confidence_pct}% confidence."
        )
        breakdown = (
            f"{top_name} received the highest confidence ({confidence_pct}%) "
            f"based on the overall pattern of words in your description, even "
            f"though no specific symptom keywords were extracted."
        )
    return rationale, breakdown


def _build_urdu(
    top_name: str, confidence_pct: int, key_symptoms: list[str]
) -> tuple[str, str]:
    if key_symptoms:
        symptoms_phrase = _join_ur(key_symptoms)
        rationale = (
            f"آپ کی بیان کردہ علامات ({symptoms_phrase}) کی بنیاد پر، ماڈل نے "
            f"{top_name} کو سب سے ممکنہ بیماری قرار دیا ہے جس کا اعتماد تقریباً "
            f"{confidence_pct}٪ ہے۔"
        )
        breakdown = (
            f"{top_name} کو سب سے زیادہ اعتماد ({confidence_pct}٪) ملا کیونکہ آپ "
            f"کے استعمال کردہ الفاظ اس بیماری کی عام علامات سے قریبی مماثلت "
            f"رکھتے ہیں، خاص طور پر: {symptoms_phrase}۔"
        )
    else:
        rationale = (
            f"آپ کی تفصیل کی بنیاد پر، ماڈل نے {top_name} کو سب سے ممکنہ بیماری "
            f"قرار دیا ہے جس کا اعتماد تقریباً {confidence_pct}٪ ہے۔"
        )
        breakdown = (
            f"{top_name} کو سب سے زیادہ اعتماد ({confidence_pct}٪) ملا — اگرچہ "
            f"کسی خاص علامت کا لفظ نہیں نکالا گیا، مگر مجموعی الفاظ کا انداز اس "
            f"بیماری سے ملتا ہے۔"
        )
    return rationale, breakdown


def _join_en(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _join_ur(items: list[str]) -> str:
    """Urdu uses '،' (Arabic comma) and the conjunction 'اور'."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} اور {items[1]}"
    return "، ".join(items[:-1]) + f" اور {items[-1]}"
