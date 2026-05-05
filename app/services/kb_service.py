"""
Knowledge-base lookup for the `disease_kb` table.

Two public entry points:

    lookup_disease(db, condition_name, language="English")  → LocalizedDisease | None
    bulk_lookup     (db, condition_names, language="English") → dict[str, LocalizedDisease]

The lookup is case-insensitive and tries an exact match first, then falls
back to a partial `LIKE` against both English and Urdu names. The returned
`LocalizedDisease` already has `name` and `care_tips` resolved to the
caller's preferred language (with automatic English fallback when the Urdu
field is missing or empty), so callers don't have to repeat that logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import DiseaseKB

logger = get_logger(__name__)

LanguageLiteral = Literal["English", "Urdu"]


# --------------------------------------------------------------------------- #
# Localized result                                                             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class LocalizedDisease:
    """
    Read-only view over a `disease_kb` row with the language already resolved.

    `name` and `care_tips` always contain the caller's preferred language when
    available, otherwise the English value. The raw English / Urdu fields are
    kept too so the API can expose both if a client needs them.
    """

    disease_id: int
    name: str
    name_en: str
    name_ur: str | None
    specialist_type: str | None
    triage_category: str | None
    care_tips: str | None
    care_tips_en: str | None
    care_tips_ur: str | None
    red_flags: str | None
    language: LanguageLiteral

    @classmethod
    def from_row(
        cls, row: DiseaseKB, language: LanguageLiteral
    ) -> "LocalizedDisease":
        """Build a localized view from an ORM row, applying English fallback."""
        prefers_urdu = language == "Urdu"
        name = (row.name_ur or "").strip() if prefers_urdu else ""
        care = (row.care_tips_ur or "").strip() if prefers_urdu else ""

        # Fallback: English when the requested language is missing/empty.
        resolved_name = name or row.name_en
        resolved_tips = care or row.care_tips_en
        # Tell the caller which language was *actually* used so the client
        # can pick the right text-direction / font.
        resolved_lang: LanguageLiteral = (
            "Urdu" if prefers_urdu and (name or care) else "English"
        )

        return cls(
            disease_id=row.disease_id,
            name=resolved_name,
            name_en=row.name_en,
            name_ur=row.name_ur,
            specialist_type=row.specialist_type,
            triage_category=row.triage_category,
            care_tips=resolved_tips,
            care_tips_en=row.care_tips_en,
            care_tips_ur=row.care_tips_ur,
            red_flags=row.red_flags,
            language=resolved_lang,
        )


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
async def lookup_disease(
    db: AsyncSession,
    condition_name: str,
    language: LanguageLiteral = "English",
) -> LocalizedDisease | None:
    """
    Find a KB row whose `name_en` (or `name_ur`) matches `condition_name`.

    Strategy:
        1. exact match on `name_en` (case-insensitive)
        2. exact match on `name_ur` (case-insensitive)
        3. partial `LIKE` against either name
    """
    if not condition_name:
        return None

    needle = condition_name.strip().lower()

    # 1. Exact match on either localized name
    result = await db.execute(
        select(DiseaseKB).where(
            or_(
                func.lower(DiseaseKB.name_en) == needle,
                func.lower(DiseaseKB.name_ur) == needle,
            )
        )
    )
    row = result.scalar_one_or_none()

    # 2. Partial fallback
    if row is None:
        like = f"%{needle}%"
        result = await db.execute(
            select(DiseaseKB).where(
                or_(
                    func.lower(DiseaseKB.name_en).like(like),
                    func.lower(DiseaseKB.name_ur).like(like),
                )
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            logger.debug("KB partial-match: '%s' → '%s'", condition_name, row.name_en)

    if row is None:
        logger.debug("KB miss for '%s'", condition_name)
        return None

    return LocalizedDisease.from_row(row, language)


async def bulk_lookup(
    db: AsyncSession,
    condition_names: Iterable[str],
    language: LanguageLiteral = "English",
) -> dict[str, LocalizedDisease]:
    """
    Lookup several conditions in a single query and return them keyed by
    *lowercase English name* (which is what the model emits).

    Used to localize all Top-K predictions in one DB hit instead of K. Names
    that don't match any KB row simply don't appear in the returned dict.
    """
    cleaned = [c.strip() for c in condition_names if c and c.strip()]
    if not cleaned:
        return {}

    lowered = [c.lower() for c in cleaned]
    result = await db.execute(
        select(DiseaseKB).where(
            or_(
                func.lower(DiseaseKB.name_en).in_(lowered),
                func.lower(DiseaseKB.name_ur).in_(lowered),
            )
        )
    )
    rows = result.scalars().all()

    out: dict[str, LocalizedDisease] = {}
    for row in rows:
        loc = LocalizedDisease.from_row(row, language)
        # Index by both names so callers can hit the dict with whatever they have.
        out[row.name_en.lower()] = loc
        if row.name_ur:
            out[row.name_ur.lower()] = loc
    return out
