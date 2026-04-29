"""
UC-06 — PDF generation service.

Turns a list of ``SymptomLog`` rows into a clean, professional medical
consultation report rendered with ReportLab's high-level Platypus engine.

Design notes
------------
* The function is **pure** — it never touches the database or rerun ML
  inference. Everything it needs comes from the stored
  ``explanation_json`` blob written by ``prediction_service._persist_log``.
* The report renders gracefully even when the JSON blob is missing,
  malformed, or partially populated (older logs, stub-mode logs, etc.).
* The output is returned as raw ``bytes`` so the API layer can stream it
  back as ``application/pdf`` without writing to disk.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.logging import get_logger
from app.db.models import SymptomLog
from app.models.response import MEDICAL_DISCLAIMER_EN

logger = get_logger(__name__)

_BRAND_COLOR = colors.HexColor("#0F4C81")
_ACCENT_COLOR = colors.HexColor("#1F6FB2")
_MUTED_COLOR = colors.HexColor("#5A6573")
_TRIAGE_COLORS: dict[str, colors.Color] = {
    "urgent_care": colors.HexColor("#C0392B"),
    "see_gp": colors.HexColor("#D68910"),
    "self_care": colors.HexColor("#1E8449"),
}


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #
def build_history_pdf(
    logs: Sequence[SymptomLog],
    *,
    username: str | None = None,
) -> bytes:
    """
    Render ``logs`` (newest first) as a multi-page PDF medical report.

    Returns
    -------
    bytes
        The fully rendered PDF document, ready to ship as the body of an
        ``application/pdf`` response.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="DevloCare Consultation Report",
        author="DevloCare AI Health Assistant",
    )

    styles = _build_styles()
    story: list[Any] = []

    story.extend(_header_block(styles, username=username, count=len(logs)))

    if not logs:
        story.append(Paragraph(
            "No consultations were available for export.", styles["Body"],
        ))
    else:
        for idx, log in enumerate(logs, start=1):
            story.extend(_consultation_block(log, idx, styles))
            if idx != len(logs):
                story.append(PageBreak())

    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.6, color=_MUTED_COLOR))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Medical Disclaimer", styles["SectionHeading"]))
    story.append(Paragraph(MEDICAL_DISCLAIMER_EN, styles["Disclaimer"]))

    doc.build(
        story,
        onFirstPage=_draw_page_chrome,
        onLaterPages=_draw_page_chrome,
    )
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Layout helpers                                                               #
# --------------------------------------------------------------------------- #
def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=_BRAND_COLOR,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "Subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=_MUTED_COLOR,
            spaceAfter=4,
        ),
        "ConsultationHeading": ParagraphStyle(
            "ConsultationHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=_BRAND_COLOR,
            spaceBefore=4,
            spaceAfter=4,
        ),
        "SectionHeading": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=_ACCENT_COLOR,
            spaceBefore=6,
            spaceAfter=2,
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=3,
        ),
        "Mono": ParagraphStyle(
            "Mono",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1F2937"),
            leftIndent=8,
            spaceAfter=3,
        ),
        "Bullet": ParagraphStyle(
            "Bullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            leftIndent=14,
            bulletIndent=4,
            spaceAfter=1,
        ),
        "Disclaimer": ParagraphStyle(
            "Disclaimer",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            textColor=_MUTED_COLOR,
        ),
        "MetaLabel": ParagraphStyle(
            "MetaLabel",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=_MUTED_COLOR,
        ),
        "MetaValue": ParagraphStyle(
            "MetaValue",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#1F2937"),
        ),
    }


def _header_block(
    styles: dict[str, ParagraphStyle],
    *,
    username: str | None,
    count: int,
) -> list[Any]:
    generated_at = _format_dt(datetime.now(timezone.utc))
    parts: list[Any] = [
        Paragraph("DevloCare — Consultation Report", styles["Title"]),
        Paragraph(
            f"AI-assisted health assistant &middot; Generated {generated_at}",
            styles["Subtitle"],
        ),
    ]
    meta_lines = []
    if username:
        meta_lines.append(f"<b>Patient account:</b> {_escape(username)}")
    meta_lines.append(f"<b>Consultations included:</b> {count}")
    parts.append(Paragraph(" &nbsp; &middot; &nbsp; ".join(meta_lines), styles["Subtitle"]))
    parts.append(Spacer(1, 2 * mm))
    parts.append(HRFlowable(width="100%", thickness=1.0, color=_BRAND_COLOR))
    parts.append(Spacer(1, 4 * mm))
    return parts


def _consultation_block(
    log: SymptomLog,
    idx: int,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    blob = _safe_load_explanation(log.explanation_json)

    main_condition = (
        log.predicted_condition
        or _first_top_name(blob)
        or "Unknown"
    )
    triage = (log.triage_level or blob.get("triage_level") or "—").lower()
    created_at_text = _format_dt(log.created_at)

    elements: list[Any] = []
    elements.append(
        Paragraph(f"Consultation #{idx} &middot; Log ID {log.log_id}",
                  styles["ConsultationHeading"])
    )

    summary_table = Table(
        [
            [
                Paragraph("Date &amp; time", styles["MetaLabel"]),
                Paragraph(created_at_text, styles["MetaValue"]),
                Paragraph("Triage", styles["MetaLabel"]),
                _triage_badge(triage, styles),
            ],
            [
                Paragraph("Main condition", styles["MetaLabel"]),
                Paragraph(_escape(main_condition), styles["MetaValue"]),
                Paragraph("Language", styles["MetaLabel"]),
                Paragraph(_escape(str(blob.get("language") or "—")),
                          styles["MetaValue"]),
            ],
        ],
        colWidths=[28 * mm, 60 * mm, 22 * mm, 60 * mm],
        hAlign="LEFT",
    )
    summary_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE5")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.3, colors.HexColor("#D5DCE5")),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 4 * mm))

    elements.append(Paragraph("Patient symptom description",
                              styles["SectionHeading"]))
    elements.append(Paragraph(_escape(log.raw_text or "—"), styles["Mono"]))

    elements.extend(_top_predictions_block(blob, styles))
    elements.extend(_explanation_block(blob, styles))
    elements.extend(_metadata_block(blob, styles))

    return elements


def _top_predictions_block(
    blob: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    top = blob.get("top_conditions") or []
    if not isinstance(top, list) or not top:
        return []

    rows: list[list[Any]] = [[
        Paragraph("<b>#</b>", styles["MetaLabel"]),
        Paragraph("<b>Condition</b>", styles["MetaLabel"]),
        Paragraph("<b>Confidence</b>", styles["MetaLabel"]),
        Paragraph("<b>Specialist</b>", styles["MetaLabel"]),
    ]]
    for i, entry in enumerate(top[:3], start=1):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("name_en") or "—"
        confidence = _format_confidence(entry.get("confidence"))
        specialist = entry.get("specialist_type") or "—"
        rows.append([
            Paragraph(str(i), styles["MetaValue"]),
            Paragraph(_escape(str(name)), styles["MetaValue"]),
            Paragraph(confidence, styles["MetaValue"]),
            Paragraph(_escape(str(specialist)), styles["MetaValue"]),
        ])

    table = Table(
        rows,
        colWidths=[10 * mm, 75 * mm, 30 * mm, 55 * mm],
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6EEF7")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE5")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E1E6EE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))

    return [
        Spacer(1, 3 * mm),
        Paragraph("Top-3 predicted conditions", styles["SectionHeading"]),
        table,
    ]


def _explanation_block(
    blob: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    explanation = blob.get("explanation")
    if not isinstance(explanation, dict):
        return []

    out: list[Any] = [
        Spacer(1, 3 * mm),
        Paragraph("AI explanation", styles["SectionHeading"]),
    ]

    rationale = explanation.get("rationale")
    if rationale:
        out.append(Paragraph(f"<b>Rationale:</b> {_escape(str(rationale))}",
                             styles["Body"]))

    key_symptoms = explanation.get("key_symptoms")
    if isinstance(key_symptoms, list) and key_symptoms:
        out.append(Paragraph("<b>Key symptoms:</b>", styles["Body"]))
        for sym in key_symptoms:
            out.append(Paragraph(f"&bull; {_escape(str(sym))}", styles["Bullet"]))

    fi = explanation.get("feature_importance")
    if isinstance(fi, list) and fi:
        out.append(Paragraph("<b>Feature importance:</b>", styles["Body"]))
        for item in fi[:8]:
            if not isinstance(item, dict):
                continue
            feature = item.get("feature") or "—"
            importance = item.get("importance")
            try:
                importance_pct = f"{float(importance) * 100:.1f}%"
            except (TypeError, ValueError):
                importance_pct = "—"
            out.append(Paragraph(
                f"&bull; {_escape(str(feature))} &mdash; {importance_pct}",
                styles["Bullet"],
            ))

    confidence_breakdown = explanation.get("confidence_breakdown")
    if confidence_breakdown:
        out.append(Paragraph(
            f"<b>Confidence summary:</b> {_escape(str(confidence_breakdown))}",
            styles["Body"],
        ))

    recommendation = (
        explanation.get("recommendation")
        or blob.get("recommendation")
    )
    if recommendation:
        out.append(Paragraph(
            f"<b>Recommendation:</b> {_escape(str(recommendation))}",
            styles["Body"],
        ))

    return out


def _metadata_block(
    blob: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    meta = blob.get("metadata")
    extracted = blob.get("extracted_symptoms")

    out: list[Any] = []

    if isinstance(extracted, list) and extracted:
        out.append(Spacer(1, 2 * mm))
        out.append(Paragraph("Extracted symptoms", styles["SectionHeading"]))
        out.append(Paragraph(
            ", ".join(_escape(str(s)) for s in extracted),
            styles["Body"],
        ))

    if isinstance(meta, dict) and any(v not in (None, "", False) for v in meta.values()):
        out.append(Spacer(1, 2 * mm))
        out.append(Paragraph("Patient metadata", styles["SectionHeading"]))
        labels = {
            "age": "Age",
            "sex": "Sex",
            "duration": "Duration",
            "severity": "Severity",
            "pregnancy": "Pregnancy",
            "chronic_disease": "Chronic disease",
        }
        bits = []
        for key, label in labels.items():
            value = meta.get(key)
            if value in (None, "", False):
                continue
            bits.append(f"<b>{label}:</b> {_escape(str(value))}")
        if bits:
            out.append(Paragraph(" &nbsp; &middot; &nbsp; ".join(bits),
                                 styles["Body"]))

    return out


def _triage_badge(
    triage: str,
    styles: dict[str, ParagraphStyle],
) -> Paragraph:
    color = _TRIAGE_COLORS.get(triage, _MUTED_COLOR)
    label = triage.replace("_", " ").upper() if triage and triage != "—" else "—"
    badge_style = ParagraphStyle(
        "TriageBadge",
        parent=styles["MetaValue"],
        fontName="Helvetica-Bold",
        textColor=color,
        fontSize=10,
        leading=13,
    )
    return Paragraph(label, badge_style)


# --------------------------------------------------------------------------- #
# Page chrome (footer w/ page number + brand line)                             #
# --------------------------------------------------------------------------- #
def _draw_page_chrome(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D5DCE5"))
    canvas.setLineWidth(0.4)
    canvas.line(
        18 * mm,
        12 * mm,
        doc.pagesize[0] - 18 * mm,
        12 * mm,
    )
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_MUTED_COLOR)
    canvas.drawString(
        18 * mm, 8 * mm,
        "DevloCare AI Health Assistant - Consultation Report",
    )
    canvas.drawRightString(
        doc.pagesize[0] - 18 * mm,
        8 * mm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


# --------------------------------------------------------------------------- #
# Tiny utilities                                                               #
# --------------------------------------------------------------------------- #
def _safe_load_explanation(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("PDF: failed to parse explanation_json: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _first_top_name(blob: dict[str, Any]) -> str | None:
    top = blob.get("top_conditions")
    if isinstance(top, list) and top:
        first = top[0]
        if isinstance(first, dict):
            return first.get("name") or first.get("name_en")
    return None


def _format_confidence(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M UTC")


_HTML_ESCAPES: tuple[tuple[str, str], ...] = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
)


def _escape(text: str) -> str:
    """Minimal HTML escape so user input can't break ReportLab paragraph markup."""
    if text is None:
        return ""
    out = str(text)
    for src, dst in _HTML_ESCAPES:
        out = out.replace(src, dst)
    return out


__all__: Iterable[str] = ("build_history_pdf",)
