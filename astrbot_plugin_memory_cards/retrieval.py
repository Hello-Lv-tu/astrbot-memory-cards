"""Deterministic local relevance retrieval for memory cards."""

from __future__ import annotations

import re

from .models import MemoryNote

_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9][a-z0-9_-]*")
_RECALL_PHRASES = (
    "你还记得",
    "记得我",
    "了解我",
    "关于我",
    "我的喜好",
    "我的偏好",
)
_STOP_NGRAMS = {
    "一个",
    "一下",
    "一点",
    "什么",
    "关于",
    "可以",
    "还是",
    "我的",
    "怎么",
    "这个",
    "那个",
    "今天",
    "事情",
    "你还",
    "记得",
    "了解",
}


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


def _chinese_ngrams(text: str) -> set[str]:
    grams: set[str] = set()
    for run in _CHINESE_RUN.findall(text):
        for width in range(2, 5):
            for index in range(0, len(run) - width + 1):
                gram = run[index : index + width]
                if gram not in _STOP_NGRAMS:
                    grams.add(gram)
    return grams


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text))


def _score(query: str, note: MemoryNote) -> float:
    content = _normalize(note.content)
    score = 0.0
    if len(query) >= 2 and query in content:
        score += 10.0

    query_grams = _chinese_ngrams(query)
    content_grams = _chinese_ngrams(content)
    for gram in query_grams & content_grams:
        score += {2: 1.5, 3: 2.25, 4: 3.0}[len(gram)]

    score += 3.0 * len(_words(query) & _words(content))
    if note.category in query:
        score += 4.0
    return score


def _within_budget(
    notes: list[MemoryNote],
    *,
    max_notes: int,
    max_chars: int,
) -> list[MemoryNote]:
    selected: list[MemoryNote] = []
    used = 0
    for note in notes:
        if len(selected) >= max_notes:
            break
        size = len(note.content)
        if used + size > max_chars:
            continue
        selected.append(note)
        used += size
    return selected


def select_relevant_notes(
    query: str,
    notes: list[MemoryNote],
    *,
    minimum_score: float = 3.0,
    max_notes: int = 5,
    max_chars: int = 1500,
    recall_fallback_enabled: bool = True,
) -> list[MemoryNote]:
    normalized_query = _normalize(query)
    if not normalized_query or max_notes < 1 or max_chars < 1:
        return []

    scored = [
        (_score(normalized_query, note), note)
        for note in notes
    ]
    matched = [
        (score, note)
        for score, note in scored
        if score >= float(minimum_score)
    ]
    matched.sort(
        key=lambda item: (item[0], item[1].updated_at, item[1].id),
        reverse=True,
    )
    if matched:
        return _within_budget(
            [note for _, note in matched],
            max_notes=max_notes,
            max_chars=max_chars,
        )

    has_recall_intent = any(
        phrase in normalized_query for phrase in _RECALL_PHRASES
    )
    if not recall_fallback_enabled or not has_recall_intent:
        return []

    recent = sorted(
        notes,
        key=lambda note: (note.updated_at, note.id),
        reverse=True,
    )
    return _within_budget(
        recent,
        max_notes=min(max_notes, 3),
        max_chars=max_chars,
    )


def select_candidate_notes(
    content: str,
    category: str,
    notes: list[MemoryNote],
    *,
    max_notes: int = 6,
    minimum_score: float = 2.0,
) -> list[MemoryNote]:
    """Select deterministic same-scope candidates for model adjudication."""

    normalized_content = _normalize(content)
    if not normalized_content or max_notes < 1:
        return []
    normalized_category = str(category or "").strip()
    scored: list[tuple[float, MemoryNote]] = []
    for note in notes:
        score = _score(normalized_content, note)
        if normalized_category and note.category == normalized_category:
            score += 2.0
        if score >= float(minimum_score):
            scored.append((score, note))
    scored.sort(
        key=lambda item: (item[0], item[1].updated_at, item[1].id),
        reverse=True,
    )
    return [note for _, note in scored[:max_notes]]
