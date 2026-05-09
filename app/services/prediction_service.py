"""
UC-01 / UC-03 / UC-04 / UC-05 — symptom-prediction service-layer orchestrator.

End-to-end flow
---------------
    1. Resolve target language (UC-05)
        - registered users → users.language_pref
        - guests          → "English"
    2. NLP preprocessing  — `preprocess_service.clean_text`
    3. Symptom extraction — `preprocess_service.extract_symptoms`
    4. ML prediction      — `ml_client.predict_top_k` (Top-3, calibrated probs)
    5. KB enrichment      — `kb_service.bulk_lookup` (single query for ALL Top-K)
    6. Smart triage (UC-03)
        - critical-keyword override
        - pregnancy + bleeding/severe
        - age extremes (<5 or >65) with moderate/severe severity
        - chronic disease with severe severity
        - KB triage_category of the top condition
        - severity-only fallback
    7. Explainability (UC-04) — `ExplainService.get_explanation`
       (rationale + key symptoms + feature_importance bar-chart payload)
    8. Persist log (user_id may be None for guests)
    9. Return PredictionResponse (bilingual care tips, language-specific
       disclaimer, per-condition red flags / specialist / triage_category).
"""
from __future__ import annotations

import json
from typing import Iterable, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import ml_client
from app.core.logging import get_logger
from app.db.models import SymptomLog, User
from app.models.request import SymptomTextRequest
from app.models.response import (
    CareTipsBilingual,
    Explanation,
    FeatureImportance,
    LanguageLiteral,
    PredictionResponse,
    TopCondition,
    TriageLevel,
    get_disclaimer,
)
from app.repositories import log_repo
from app.services import kb_service, preprocess_service
from app.services.explain_service import get_explain_service
from app.services.kb_service import LocalizedDisease

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Triage rule configuration                                                    #
# --------------------------------------------------------------------------- #
# Critical symptom keywords — any presence of these ALWAYS escalates to
# `urgent_care`, regardless of model confidence or KB triage. These mirror the
# UC-03 specification exactly.
_CRITICAL_KEYWORDS: tuple[str, ...] = (
    "chest pain",
    "shortness of breath",
    "difficulty breathing",
    "severe bleeding",
    "high fever",
    "confusion",
    "unconscious",
    "severe abdominal pain",
)

# Symptoms that, when combined with pregnancy, escalate to `urgent_care`.
_PREGNANCY_ESCALATION_KEYWORDS: tuple[str, ...] = (
    "bleeding", "severe pain", "abdominal pain", "fever",
)

_AGE_VULNERABLE_LOWER = 5
_AGE_VULNERABLE_UPPER = 65


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #
async def predict_from_text(
    db: AsyncSession,
    payload: SymptomTextRequest,
    user: User | None,
) -> PredictionResponse:
    """
    Main orchestrator for `POST /api/v1/predict/text`.

    `user` is `None` for guest callers — the prediction still runs, the
    log is still written (with user_id NULL) and the response is in English.
    """
    # ---- 1. Language resolution ------------------------------------------ #
    language: LanguageLiteral = _resolve_language(user)
    logger.info(
        "Language resolved: %s (user_id=%s, pref=%s)",
        language,
        getattr(user, "user_id", None),
        getattr(user, "language_pref", None),
    )

    # ---- 2-3. Preprocess + extract symptoms ------------------------------ #
    cleaned_text = preprocess_service.clean_text(payload.text)
    extracted_symptoms = preprocess_service.extract_symptoms(payload.text)
    logger.debug(
        "NLP: cleaned=%r extracted_symptoms=%s", cleaned_text, extracted_symptoms,
    )

    # ---- 4. ML prediction ------------------------------------------------ #
    raw_predictions = await ml_client.predict_top_k(
        cleaned_text or payload.text, k=3
    )
    logger.info(
        "ML: %s (stub_mode=%s)",
        [(p["condition"], round(p["confidence"], 3)) for p in raw_predictions],
        ml_client.is_stub(),
    )

    # ---- 5. KB enrichment (single query for ALL Top-K) ------------------- #
    english_names = [p["condition"] for p in raw_predictions]
    kb_index: dict[str, LocalizedDisease] = await kb_service.bulk_lookup(
        db, english_names, language=language
    )
    hits = [name for name in english_names if name.lower() in kb_index]
    logger.info(
        "KB: %d/%d hit (%s); miss=%s",
        len(hits), len(english_names),
        ", ".join(hits),
        ", ".join(n for n in english_names if n.lower() not in kb_index) or "—",
    )

    top_conditions: list[TopCondition] = [
        _build_top_condition(p["condition"], float(p["confidence"]), kb_index)
        for p in raw_predictions
    ]
    primary = top_conditions[0] if top_conditions else None
    primary_kb = kb_index.get(english_names[0].lower()) if english_names else None

    # ---- 6. Smart triage (UC-03) ----------------------------------------- #
    triage_level, triage_rule = _compute_triage(
        payload=payload,
        text_lower=payload.text.lower(),
        extracted_symptoms=extracted_symptoms,
        kb_entry=primary_kb,
    )
    logger.info(
        "Triage decision: %s (rule=%s, kb_triage=%s, severity=%s, age=%s, "
        "pregnancy=%s, chronic=%s)",
        triage_level, triage_rule,
        getattr(primary_kb, "triage_category", None),
        payload.severity, payload.age, payload.pregnancy,
        bool(payload.chronic_disease),
    )

    # ---- 7. Explainability (UC-04) --------------------------------------- #
    vectorizer, classifier = ml_client.get_explainer_artefacts()
    explain_payload = await get_explain_service().get_explanation(
        text=cleaned_text or payload.text,
        top_predictions=raw_predictions,
        vectorizer=vectorizer,
        model=classifier,
        language=language,
        extracted_symptoms=extracted_symptoms,
        cleaned_text=cleaned_text,
        top_condition_display_name=primary.name if primary else "Unknown",
    )
    explanation = Explanation(
        rationale=explain_payload["rationale"],
        key_symptoms=explain_payload["key_symptoms"],
        feature_importance=[
            FeatureImportance(**fi) for fi in explain_payload["feature_importance"]
        ],
        confidence_breakdown=explain_payload["confidence_breakdown"],
    )

    # ---- 8. Persist log -------------------------------------------------- #
    log_entry = await _persist_log(
        db=db,
        user_id=getattr(user, "user_id", None),
        payload=payload,
        top_conditions=top_conditions,
        triage_level=triage_level,
        triage_rule=triage_rule,
        extracted_symptoms=extracted_symptoms,
        language=language,
        explanation=explanation,
    )

    # ---- 9. Final structured response ------------------------------------ #
    return PredictionResponse(
        log_id=log_entry.log_id,
        language=language,
        top_conditions=top_conditions,
        triage_level=triage_level,
        recommended_specialist=primary.specialist_type if primary else None,
        care_tips=primary.care_tips if primary else CareTipsBilingual(),
        red_flags=primary.red_flags if primary else [],
        extracted_symptoms=list(extracted_symptoms),
        explanation=explanation,
        disclaimer=get_disclaimer(language),
    )


# --------------------------------------------------------------------------- #
# Helpers — language / KB / top-condition assembly                              #
# --------------------------------------------------------------------------- #
def _resolve_language(user: User | None) -> LanguageLiteral:
    """
    UC-05 language resolution.

    Registered user → `users.language_pref` (already constrained to
    "English" / "Urdu" by the User schema).  Anything else → English default.
    """
    pref = getattr(user, "language_pref", None)
    return "Urdu" if pref == "Urdu" else "English"


def _build_top_condition(
    english_name: str,
    confidence: float,
    kb_index: dict[str, LocalizedDisease],
) -> TopCondition:
    """
    Assemble a fully-enriched `TopCondition`.

    When the predicted class isn't in the KB (e.g. an "Other" class or a
    label with no curated entry yet) we still return a useful row — the
    name + confidence — but enrichment fields stay empty.
    """
    loc = kb_index.get(english_name.lower())
    if loc is None:
        return TopCondition(
            name=english_name,
            name_en=english_name,
            confidence=confidence,
        )

    return TopCondition(
        name=loc.name,
        name_en=loc.name_en,
        confidence=confidence,
        specialist_type=loc.specialist_type,
        triage_category=_normalize_triage(loc.triage_category),
        care_tips=CareTipsBilingual(
            en=loc.care_tips_en,
            ur=loc.care_tips_ur,
        ),
        red_flags=_split_red_flags(loc.red_flags),
    )


# --------------------------------------------------------------------------- #
# Smart triage engine (UC-03)                                                  #
# --------------------------------------------------------------------------- #
def _compute_triage(
    payload: SymptomTextRequest,
    text_lower: str,
    extracted_symptoms: list[str],
    kb_entry: LocalizedDisease | None,
) -> tuple[TriageLevel, str]:
    """
    Apply the UC-03 triage rule engine.

    Returns ``(triage_level, rule_name)`` so the rule that fired can be logged
    and stored in the audit log. Rules are evaluated in priority order:

        1. Critical-keyword in the input    →  urgent_care
        2. Pregnancy + bleeding / severe pain / fever / abdominal pain
                                            →  urgent_care
        3. Age <5 or >65 with severity moderate/severe
                                            →  urgent_care
        4. Chronic disease + severity=severe →  urgent_care
        5. KB triage_category == urgent_care →  urgent_carex`
        6. Severity=severe                  →  urgent_care
        7. KB triage_category == see_gp     →  see_gp
        8. Severity=moderate                →  see_gp
        9. KB triage_category == self_care  →  self_care
       10. Default                          →  self_care
    """
    extracted_set = {s.lower() for s in extracted_symptoms}

    # Rule 1
    fired = _first_match(text_lower, extracted_set, _CRITICAL_KEYWORDS)
    if fired:
        return "urgent_care", f"critical_keyword:{fired}"

    # Rule 2 — pregnancy escalation
    if payload.pregnancy and (
        _first_match(text_lower, extracted_set, _PREGNANCY_ESCALATION_KEYWORDS)
        or payload.severity == "severe"
    ):
        return "urgent_care", "pregnancy_escalation"

    severe_or_moderate = payload.severity in {"moderate", "severe"}

    # Rule 3 — vulnerable ages
    if payload.age is not None and (
        payload.age < _AGE_VULNERABLE_LOWER or payload.age > _AGE_VULNERABLE_UPPER
    ) and severe_or_moderate:
        return "urgent_care", f"vulnerable_age:{payload.age}"

    # Rule 4 — chronic disease + severe
    if payload.chronic_disease and payload.severity == "severe":
        return "urgent_care", "chronic_disease_severe"

    # Rule 5 — KB says urgent
    kb_triage = _normalize_triage(
        kb_entry.triage_category if kb_entry else None
    )
    if kb_triage == "urgent_care":
        return "urgent_care", "kb_urgent_care"

    # Rule 6 — severity alone is severe
    if payload.severity == "severe":
        return "urgent_care", "severity_severe"

    # Rule 7 — KB says see_gp
    if kb_triage == "see_gp":
        return "see_gp", "kb_see_gp"

    # Rule 8 — moderate severity
    if payload.severity == "moderate":
        return "see_gp", "severity_moderate"

    # Rule 9 — KB says self_care
    if kb_triage == "self_care":
        return "self_care", "kb_self_care"

    # Rule 10 — default
    return "self_care", "default"


def _first_match(
    text: str, extracted: set[str], keywords: Iterable[str]
) -> str | None:
    """Return the first keyword that occurs in either the raw text or extracted set."""
    for kw in keywords:
        if kw in text or kw in extracted:
            return kw
    return None


def _normalize_triage(value: str | None) -> TriageLevel | None:
    """
    Normalise free-form `triage_category` strings to our internal TriageLevel.

    Accepts the seed-script vocabulary (`self_care`, `see_gp`, `urgent_care`)
    and a few other reasonable variations so future seed edits don't break.
    """
    if not value:
        return None
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    mapping: dict[str, TriageLevel] = {
        "urgent_care": "urgent_care",
        "urgent": "urgent_care",
        "emergency": "urgent_care",
        "er": "urgent_care",
        "see_gp": "see_gp",
        "moderate": "see_gp",
        "self_care": "self_care",
        "selfcare": "self_care",
        "mild": "self_care",
    }
    return mapping.get(v)


# --------------------------------------------------------------------------- #
# Red-flag parsing                                                             #
# --------------------------------------------------------------------------- #
def _split_red_flags(raw: str | None) -> list[str]:
    """KB stores red flags as free text — split on newline / pipe / semicolon / comma."""
    if not raw:
        return []
    parts: list[str] = []
    text = raw.replace("|", "\n").replace(";", "\n").replace(",", "\n")
    for chunk in text.splitlines():
        item = chunk.strip(" -*\t")
        if item:
            parts.append(item)
    return parts


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #
async def _persist_log(
    *,
    db: AsyncSession,
    user_id: int | None,
    payload: SymptomTextRequest,
    top_conditions: list[TopCondition],
    triage_level: TriageLevel,
    triage_rule: str,
    extracted_symptoms: list[str],
    language: LanguageLiteral,
    explanation: Explanation,
) -> SymptomLog:
    """
    Store the request + prediction bundle in `symptom_logs`.

    The English class name is persisted in the dedicated column so analytics
    aren't fragmented by language; the full bilingual + explainability
    bundle lives inside `explanation_json`.
    """
    explanation_blob = {
        "language": language,
        "triage_rule": triage_rule,
        "stub_mode": ml_client.is_stub(),
        "top_conditions": [
            cast(dict, c.model_dump()) for c in top_conditions
        ],
        "extracted_symptoms": extracted_symptoms,
        "explanation": explanation.model_dump(),
        "metadata": {
            "age": payload.age,
            "sex": payload.sex,
            "duration": payload.duration,
            "severity": payload.severity,
            "pregnancy": payload.pregnancy,
            "chronic_disease": payload.chronic_disease,
        },
    }

    primary = top_conditions[0] if top_conditions else None
    log = SymptomLog(
        user_id=user_id,
        raw_text=payload.text,
        predicted_condition=primary.name_en if primary else None,
        confidence_score=primary.confidence if primary else None,
        explanation_json=json.dumps(explanation_blob, ensure_ascii=False),
        triage_level=triage_level,
    )
    return await log_repo.create(db, log)
