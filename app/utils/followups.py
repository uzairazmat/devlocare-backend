"""
Generic empathetic follow-up prompts used when the classifier's confidence on
the combined user input is below the configured threshold (UC-01 stateful
chat). The assistant picks one at random and asks the patient to elaborate;
the question is appended to ``symptom_logs.chat_history`` so the UI can
render the conversation as a single thread.

Kept condition-agnostic on purpose — the classifier hasn't settled on a
diagnosis yet, so any disease-specific prompt would risk anchoring the user.
"""
from __future__ import annotations

import random

FOLLOWUP_QUESTIONS: tuple[str, ...] = (
    "To give you the safest advice, could you describe your symptoms in a bit "
    "more detail?",
    "Are there any other symptoms you are experiencing right now?",
    "How long have you been feeling this way, and has it changed over time?",
    "Could you tell me where in your body you are feeling these symptoms?",
    "On a scale from mild to severe, how would you describe what you're "
    "feeling?",
    "Have you noticed anything that seems to make the symptoms better or "
    "worse?",
    "Is there anything else — even something small — that you think I should "
    "know to help you better?",
)


def pick_followup_question(exclude: list[str] | None = None) -> str:
    """
    Return one random question from the pool.

    ``exclude`` lets callers avoid repeating questions already asked in the
    same conversation. If every question has already been used we fall back
    to the full pool so the chat never stalls.
    """
    pool = FOLLOWUP_QUESTIONS
    if exclude:
        used = set(exclude)
        remaining = tuple(q for q in pool if q not in used)
        if remaining:
            pool = remaining
    return random.choice(pool)
