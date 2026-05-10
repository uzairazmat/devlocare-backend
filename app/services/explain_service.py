"""
UC-04 Explainability — builds the ``Explanation`` block returned by
``POST /api/v1/predict/text``.

Contract (per project document)::

    {
        "rationale":          "<natural-language sentence>",
        "key_symptoms":       ["rash", "itching", ...],
        "feature_importance": [
            {"feature": "rash",    "importance": 0.92},
            {"feature": "itching", "importance": 0.41},
            ...
        ],
        "confidence_breakdown": "<natural-language sentence>"
    }

Approach
--------
The pipeline uses dense 384-D semantic embeddings
(``sentence-transformers/all-MiniLM-L6-v2``) into a calibrated classifier.
SHAP attribution over individual embedding dimensions isn't human-readable
("Dimension 42" means nothing to a clinician), so we use **LIME** over the
input text instead: LIME perturbs the input (drops words at random),
re-embeds each perturbation, queries ``classifier.predict_proba``, and fits
a local linear surrogate. The resulting per-word weights ARE human-readable
and tell us *which words drove the predicted class*.

We surface those weights, normalised against the largest absolute weight, as
``feature_importance`` so the frontend bar chart displays genuine ML
interpretability data — not dummy uniform weights.

If LIME is unavailable (package missing, stub model, empty input) we fall
back to the spaCy-extracted symptoms with uniform weights so the response
contract still holds.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Iterable, Literal

from app.core.logging import get_logger
from app.models.response import Explanation, FeatureImportance

logger = get_logger(__name__)

LanguageLiteral = Literal["English", "Urdu"]

_MAX_FEATURE_BARS = 10
_MAX_KEY_SYMPTOMS = 5

# LIME tuning. ``num_samples`` is the number of perturbations LIME generates;
# each one is encoded by the SentenceTransformer in a single batched call, so
# the cost is ~one forward-pass over 500 short strings (~0.5-1 s on CPU).
# Lower it to trade explanation stability for latency.
_LIME_NUM_SAMPLES = 500
_LIME_NUM_FEATURES = 10
_FALLBACK_IMPORTANCE = 0.5

# LIME unavailability is sticky — if the package isn't installed we don't
# retry on every request.
_lime_available: bool | None = None
_lime_check_lock = threading.Lock()


def _is_lime_available() -> bool:
    global _lime_available
    if _lime_available is not None:
        return _lime_available
    with _lime_check_lock:
        if _lime_available is not None:
            return _lime_available
        try:
            from lime.lime_text import LimeTextExplainer  # noqa: F401
            _lime_available = True
            logger.info("LIME explainability available.")
        except Exception:  # noqa: BLE001
            _lime_available = False
            logger.warning(
                "LIME not installed — feature_importance will fall back to "
                "extracted symptoms with uniform weights.",
            )
    return _lime_available


class ExplainService:
    """
    UC-04 explainability service.

    Stateless — every call to :meth:`get_explanation` is pure. Kept as a class
    for API symmetry with the rest of the service layer and to make future
    swap-outs (e.g. SHAP over a token-attention model) straightforward.
    """

    async def get_explanation(
        self,
        text: str,
        top_predictions: list[dict[str, Any]],
        vectorizer: Any = None,   # the SentenceTransformer embedder
        model: Any = None,        # the calibrated classifier
        *,
        language: LanguageLiteral = "English",
        extracted_symptoms: Iterable[str] | None = None,
        cleaned_text: str | None = None,
        top_condition_display_name: str | None = None,
    ) -> dict[str, Any]:
        """Build the UC-04 explanation dict for one prediction."""
        del cleaned_text  # text (post-PII redaction) is what LIME explains

        if not top_predictions:
            return self._empty_response(language)

        top = top_predictions[0]
        top_class_en = str(top.get("condition", "Unknown"))
        top_confidence = float(top.get("confidence", 0.0))
        display_name = top_condition_display_name or top_class_en

        key_symptoms = self._dedupe(extracted_symptoms or [], _MAX_KEY_SYMPTOMS)

        # Real LIME run when both the embedder and classifier are loaded.
        feature_importance = await _compute_feature_importance(
            ml_text=text,
            embedder=vectorizer,
            classifier=model,
            fallback_features=key_symptoms,
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
            "lime_features=%d",
            language, display_name, confidence_pct,
            len(key_symptoms), len(feature_importance),
        )

        return {
            "rationale": rationale,
            "key_symptoms": key_symptoms,
            "feature_importance": feature_importance,
            "confidence_breakdown": breakdown,
        }

    async def build(
        self,
        *,
        text: str,
        top_predictions: list[dict[str, Any]],
        vectorizer: Any = None,
        model: Any = None,
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

    @staticmethod
    def _dedupe(items: Iterable[str], max_items: int) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            tok = (item or "").strip().lower()
            if not tok or tok in seen:
                continue
            seen.add(tok)
            result.append(tok)
            if len(result) >= max_items:
                break
        return result

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
# Module-level singleton                                                       #
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


# --------------------------------------------------------------------------- #
# LIME wrapper                                                                 #
# --------------------------------------------------------------------------- #
async def _compute_feature_importance(
    *,
    ml_text: str,
    embedder: Any,
    classifier: Any,
    fallback_features: list[str],
) -> list[dict[str, float | str]]:
    """
    Run LIME against the embedder+classifier pipeline and return the
    ``feature_importance`` payload. Falls back to ``fallback_features`` (the
    spaCy-extracted symptoms) with uniform weights when LIME can't run.
    """
    if (
        not ml_text
        or embedder is None
        or classifier is None
        or not _is_lime_available()
    ):
        return _fallback_importance(fallback_features)

    try:
        # LIME's sampling + sklearn fitting is synchronous CPU work; off-load
        # to a thread so we don't block the FastAPI event loop.
        return await asyncio.to_thread(
            _run_lime_sync, ml_text, embedder, classifier
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "LIME explanation failed — falling back to extracted symptoms.",
        )
        return _fallback_importance(fallback_features)


def _run_lime_sync(
    ml_text: str, embedder: Any, classifier: Any,
) -> list[dict[str, float | str]]:
    """Run LIME synchronously and return normalised top-N features."""
    import numpy as np  # type: ignore
    from lime.lime_text import LimeTextExplainer  # type: ignore

    classes = list(getattr(classifier, "classes_", []))
    if not classes:
        return []
    class_names = [str(c) for c in classes]

    def predict_proba(texts: list[str]) -> "np.ndarray":
        """LIME's classifier_fn: list[str] -> ndarray (n_samples, n_classes)."""
        if not texts:
            return np.empty((0, len(classes)))
        embeddings = embedder.encode(list(texts), show_progress_bar=False)
        return classifier.predict_proba(embeddings)

    # Resolve the index of the predicted class once so we ask LIME for
    # weights against the *right* label.
    top_probs = predict_proba([ml_text])[0]
    top_idx = int(np.argmax(top_probs))

    explainer = LimeTextExplainer(class_names=class_names, bow=True)
    explanation = explainer.explain_instance(
        ml_text,
        predict_proba,
        num_features=_LIME_NUM_FEATURES,
        num_samples=_LIME_NUM_SAMPLES,
        labels=(top_idx,),
    )

    raw_features: list[tuple[str, float]] = explanation.as_list(label=top_idx)
    if not raw_features:
        return []

    logger.debug("LIME raw weights for class %r: %s", class_names[top_idx], raw_features)

    # Normalise against the largest absolute weight so the bar chart stays in
    # [0, 1]. Sort by descending absolute weight — the most informative words
    # surface first regardless of sign. We log the signed weights above so the
    # sign isn't lost for diagnostics.
    max_abs = max(abs(w) for _, w in raw_features) or 1.0
    ranked = sorted(raw_features, key=lambda fw: abs(fw[1]), reverse=True)
    return [
        {"feature": word, "importance": min(1.0, abs(weight) / max_abs)}
        for word, weight in ranked[:_MAX_FEATURE_BARS]
    ]


def _fallback_importance(features: list[str]) -> list[dict[str, float | str]]:
    """Uniform-weight payload built from extracted symptoms."""
    return [
        {"feature": s, "importance": _FALLBACK_IMPORTANCE}
        for s in features[:_MAX_FEATURE_BARS]
    ]


# --------------------------------------------------------------------------- #
# Bilingual rationale assembly                                                 #
# --------------------------------------------------------------------------- #
def _build_english(
    top_name: str, confidence_pct: int, key_symptoms: list[str]
) -> tuple[str, str]:
    if key_symptoms:
        symptoms_phrase = _join_en(key_symptoms)
        rationale = (
            f"We identified this condition because your description matches "
            f"the semantic profile of {top_name}, specifically involving "
            f"symptoms like {symptoms_phrase}."
        )
        breakdown = (
            f"{top_name} received the highest confidence ({confidence_pct}%) "
            f"because the overall meaning of your description closely matches "
            f"its typical symptom profile, especially: {symptoms_phrase}."
        )
    else:
        rationale = (
            f"We identified this condition because your description matches "
            f"the semantic profile of {top_name}."
        )
        breakdown = (
            f"{top_name} received the highest confidence ({confidence_pct}%) "
            f"based on the overall semantic pattern of your description, even "
            f"though no specific symptom keywords were extracted."
        )
    return rationale, breakdown


def _build_urdu(
    top_name: str, confidence_pct: int, key_symptoms: list[str]
) -> tuple[str, str]:
    if key_symptoms:
        symptoms_phrase = _join_ur(key_symptoms)
        rationale = (
            f"ہم نے اس بیماری کی نشاندہی اس لیے کی کیونکہ آپ کی تفصیل "
            f"{top_name} کے سیمنٹک پروفائل سے میل کھاتی ہے، خاص طور پر "
            f"{symptoms_phrase} جیسی علامات کی موجودگی میں۔"
        )
        breakdown = (
            f"{top_name} کو سب سے زیادہ اعتماد ({confidence_pct}٪) ملا کیونکہ "
            f"آپ کی تفصیل کا مجموعی معنی اس بیماری کی عام علامات سے قریبی "
            f"مماثلت رکھتا ہے، خاص طور پر: {symptoms_phrase}۔"
        )
    else:
        rationale = (
            f"ہم نے اس بیماری کی نشاندہی اس لیے کی کیونکہ آپ کی تفصیل "
            f"{top_name} کے سیمنٹک پروفائل سے میل کھاتی ہے۔"
        )
        breakdown = (
            f"{top_name} کو سب سے زیادہ اعتماد ({confidence_pct}٪) ملا — "
            f"اگرچہ کسی خاص علامت کا لفظ نہیں نکالا گیا، مگر آپ کی تفصیل کا "
            f"مجموعی سیمنٹک انداز اس بیماری سے ملتا ہے۔"
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
