"""
NLP preprocessing for UC-01 Symptom Prediction.

Pipeline stages:
    1.  clean_text()        — lowercase, strip noise, tokenize, remove stopwords
    2.  extract_symptoms()  — spaCy matcher over a small symptom lexicon

All heavy resources (NLTK stopwords, spaCy model) are loaded lazily on first
call and cached. Failures degrade gracefully — e.g. if NLTK data isn't present
we fall back to an in-file stopword list rather than 500ing.

TF-IDF vectorisation lived here previously; the pipeline now uses
sentence-transformers embeddings (see ``app.clients.ml_client``), so cleaned
text and extracted symptoms are the only outputs consumed downstream.
"""
from __future__ import annotations

import re
import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Small but usable fallback list if NLTK data is unavailable.
_FALLBACK_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "at", "by", "for",
    "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "to", "from", "up", "down", "in",
    "out", "on", "off", "over", "under", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "s", "t",
    "can", "will", "just", "don", "should", "now", "i", "me", "my", "we",
    "our", "you", "your", "he", "she", "it", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "am", "this", "that", "these", "those",
})

# Symptom lexicon: token patterns fed to spaCy's Matcher. Multi-word phrases
# are encoded as sequences of {"LOWER": ...} dicts.
_SYMPTOM_LEXICON: dict[str, list[list[dict[str, Any]]]] = {
    "fever": [[{"LOWER": "fever"}], [{"LOWER": "temperature"}]],
    "cough": [[{"LOWER": "cough"}], [{"LOWER": "coughing"}]],
    "sore throat": [[{"LOWER": "sore"}, {"LOWER": "throat"}]],
    "runny nose": [[{"LOWER": "runny"}, {"LOWER": "nose"}], [{"LOWER": "rhinorrhea"}]],
    "headache": [[{"LOWER": "headache"}], [{"LOWER": "migraine"}]],
    "nausea": [[{"LOWER": "nausea"}], [{"LOWER": "nauseous"}]],
    "vomiting": [[{"LOWER": "vomiting"}], [{"LOWER": "vomit"}]],
    "diarrhea": [[{"LOWER": "diarrhea"}], [{"LOWER": "diarrhoea"}]],
    "stomach pain": [[{"LOWER": "stomach"}, {"LOWER": "pain"}], [{"LOWER": "abdominal"}, {"LOWER": "pain"}]],
    "chest pain": [[{"LOWER": "chest"}, {"LOWER": "pain"}]],
    "shortness of breath": [
        [{"LOWER": "shortness"}, {"LOWER": "of"}, {"LOWER": "breath"}],
        [{"LOWER": "breathless"}],
        [{"LOWER": "dyspnea"}],
    ],
    "rash": [[{"LOWER": "rash"}], [{"LOWER": "hives"}]],
    "itching": [[{"LOWER": "itching"}], [{"LOWER": "itchy"}], [{"LOWER": "itch"}]],
    "fatigue": [[{"LOWER": "fatigue"}], [{"LOWER": "tired"}], [{"LOWER": "weakness"}]],
    "dizziness": [[{"LOWER": "dizzy"}], [{"LOWER": "dizziness"}], [{"LOWER": "vertigo"}]],
    "pain": [[{"LOWER": "pain"}], [{"LOWER": "ache"}]],
    "bleeding": [[{"LOWER": "bleeding"}], [{"LOWER": "blood"}]],
    "swelling": [[{"LOWER": "swelling"}], [{"LOWER": "swollen"}]],
    "burning urination": [[{"LOWER": "burning"}, {"LOWER": "urination"}]],
    "joint pain": [[{"LOWER": "joint"}, {"LOWER": "pain"}]],
    "back pain": [[{"LOWER": "back"}, {"LOWER": "pain"}]],
}

# Regex backup used when spaCy isn't installed.
_SYMPTOM_REGEX: dict[str, re.Pattern[str]] = {
    label: re.compile(
        r"\b(" + "|".join(
            " ".join(tok.get("LOWER", "") for tok in pat) for pat in patterns
        ) + r")\b",
        flags=re.IGNORECASE,
    )
    for label, patterns in _SYMPTOM_LEXICON.items()
}

# --------------------------------------------------------------------------- #
# Lazy resources                                                               #
# --------------------------------------------------------------------------- #
_lock = threading.Lock()
_stopwords: frozenset[str] | None = None
_nlp: Any = None                 # spaCy Language, or None if unavailable
_matcher: Any = None             # spaCy Matcher, or None


_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")


def _get_stopwords() -> frozenset[str]:
    """Load NLTK stopwords on demand; fall back to built-in list on any error."""
    global _stopwords
    if _stopwords is not None:
        return _stopwords

    with _lock:
        if _stopwords is not None:
            return _stopwords
        try:
            from nltk.corpus import stopwords  # type: ignore

            try:
                words = stopwords.words("english")
            except LookupError:
                import nltk  # type: ignore

                nltk.download("stopwords", quiet=True)
                words = stopwords.words("english")

            _stopwords = frozenset(words)
            logger.info("NLTK stopwords loaded (%d tokens).", len(_stopwords))
        except Exception:  # noqa: BLE001
            logger.warning(
                "NLTK unavailable — using built-in stopword list.", exc_info=True,
            )
            _stopwords = _FALLBACK_STOPWORDS
    return _stopwords


def _get_spacy() -> tuple[Any, Any]:
    """Return (nlp, matcher). Returns (None, None) if spaCy is unavailable."""
    global _nlp, _matcher
    if _nlp is not None or _matcher is not None:
        return _nlp, _matcher

    with _lock:
        if _nlp is not None:
            return _nlp, _matcher
        try:
            import spacy  # type: ignore
            from spacy.matcher import Matcher  # type: ignore

            try:
                nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
            except Exception:
                logger.warning(
                    "spaCy model 'en_core_web_sm' not found — using blank English pipeline."
                )
                nlp = spacy.blank("en")

            matcher = Matcher(nlp.vocab)
            for label, patterns in _SYMPTOM_LEXICON.items():
                matcher.add(label, patterns)

            _nlp, _matcher = nlp, matcher
            logger.info("spaCy matcher ready (%d symptom patterns).", len(_SYMPTOM_LEXICON))
        except Exception:  # noqa: BLE001
            logger.warning(
                "spaCy unavailable — falling back to regex symptom extraction.",
                exc_info=True,
            )
            _nlp, _matcher = None, None
    return _nlp, _matcher


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def clean_text(text: str) -> str:
    """
    Lowercase the input, drop non-letter characters, tokenize and remove
    English stopwords. Returns a space-joined clean string suitable for
    downstream embedding.
    """
    if not text:
        return ""

    lowered = text.lower()
    tokens = _WORD_RE.findall(lowered)
    stops = _get_stopwords()
    filtered = [t for t in tokens if t not in stops and len(t) > 1]
    return " ".join(filtered)


def extract_symptoms(text: str) -> list[str]:
    """
    Extract known symptom labels from free text using spaCy's Matcher. If
    spaCy isn't available we fall back to a compiled regex set covering the
    same lexicon. Returns a de-duplicated, order-preserving list.
    """
    if not text:
        return []

    nlp, matcher = _get_spacy()
    found: list[str] = []

    if nlp is not None and matcher is not None:
        doc = nlp(text.lower())
        seen: set[str] = set()
        for match_id, _start, _end in matcher(doc):
            label = nlp.vocab.strings[match_id]
            if label not in seen:
                seen.add(label)
                found.append(label)
        return found

    # Regex fallback
    seen = set()
    for label, pattern in _SYMPTOM_REGEX.items():
        if label not in seen and pattern.search(text):
            seen.add(label)
            found.append(label)
    return found
