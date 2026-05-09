"""
UC-04 Explainability — builds the ``Explanation`` block returned by
``POST /api/v1/predict/text``.

Contract (per project document)::

    {
        "rationale":          "<natural-language sentence>",
        "key_symptoms":       ["rash", "itching", ...],
        "feature_importance": [
            {"feature": "rash",    "importance": 0.5},
            {"feature": "itching", "importance": 0.5},
            ...
        ],
        "confidence_breakdown": "<natural-language sentence>"
    }

Design note
-----------
The pipeline now uses dense 384-D semantic embeddings
(``sentence-transformers/all-MiniLM-L6-v2``) instead of TF-IDF, so SHAP
attribution over individual dimensions is no longer human-interpretable
("Dimension 42" is meaningless to a clinician). Until a token-level
attribution layer is added, we surface the spaCy-extracted symptoms as the
key drivers and assign each an equal dummy weight so the frontend bar chart
still renders cleanly.
"""
from __future__ import annotations

import threading
from typing import Any, Iterable, Literal

from app.core.logging import get_logger
from app.models.response import Explanation, FeatureImportance

logger = get_logger(__name__)

LanguageLiteral = Literal["English", "Urdu"]

_DUMMY_IMPORTANCE = 0.5
_MAX_FEATURE_BARS = 10
_MAX_KEY_SYMPTOMS = 5


class ExplainService:
    """
    UC-04 explainability service.

    Stateless — every call to :meth:`get_explanation` is pure. Kept as a class
    for API symmetry with the rest of the service layer and to make future
    re-introduction of a learned attribution model straightforward.
    """

    async def get_explanation(
        self,
        text: str,
        top_predictions: list[dict[str, Any]],
        vectorizer: Any = None,   # unused: kept for call-site compatibility
        model: Any = None,        # unused: kept for call-site compatibility
        *,
        language: LanguageLiteral = "English",
        extracted_symptoms: Iterable[str] | None = None,
        cleaned_text: str | None = None,
        top_condition_display_name: str | None = None,
    ) -> dict[str, Any]:
        """Build the UC-04 explanation dict for one prediction."""
        del vectorizer, model, cleaned_text  # unused under the embedding pipeline

        if not top_predictions:
            return self._empty_response(language)

        top = top_predictions[0]
        top_class_en = str(top.get("condition", "Unknown"))
        top_confidence = float(top.get("confidence", 0.0))
        display_name = top_condition_display_name or top_class_en

        key_symptoms = self._dedupe(extracted_symptoms or [], _MAX_KEY_SYMPTOMS)

        feature_importance = [
            {"feature": s, "importance": _DUMMY_IMPORTANCE}
            for s in key_symptoms[:_MAX_FEATURE_BARS]
        ]

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
            "Explainability: lang=%s top=%r conf=%d%% key_symptoms=%d",
            language, display_name, confidence_pct, len(key_symptoms),
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
