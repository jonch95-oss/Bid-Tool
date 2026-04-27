from __future__ import annotations

import re
from difflib import SequenceMatcher


def norm_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_model(value: str | None) -> str:
    return norm_text(value).replace(" ", "")


def normalize_capacity(value: str | None) -> str:
    text = norm_text(value)
    if not text:
        return ""
    text = text.replace("gigabyte", "gb").replace("terabyte", "tb")
    text = text.replace(" ", "")
    return text


def normalize_color(value: str | None) -> str:
    text = norm_text(value)
    mapping = {
        "midnight black": "black",
        "space gray": "gray",
        "space grey": "gray",
        "phantom black": "black",
        "starlight": "white",
        "midnight": "black",
    }
    return mapping.get(text, text)


def normalize_grade(value: str | None) -> str:
    text = norm_text(value)
    grade_map = {
        "used like new": "A",
        "usedlikenew": "A",
        "likenew": "A",
        "like new": "A",
        "excellent": "A",
        "grade a": "A",
        "a": "A",
        "used very good": "B",
        "usedverygood": "B",
        "verygood": "B",
        "very good": "B",
        "grade b": "B",
        "b": "B",
        "used good": "C",
        "usedgood": "C",
        "good": "C",
        "grade c": "C",
        "c": "C",
        "used acceptable": "D",
        "usedacceptable": "D",
        "acceptable": "D",
        "grade d": "D",
        "d": "D",
    }
    return grade_map.get(text, text.upper())


def normalized_includes(left: str, right: str) -> bool:
    """
    Compare normalized strings with token containment fallback.

    This helps when one source returns "galaxy s22 ultra" and another returns
    "samsung galaxy s22 ultra 128gb".
    """
    left_n = norm_text(left)
    right_n = norm_text(right)
    if not left_n or not right_n:
        return False
    if left_n == right_n:
        return True
    left_tokens = left_n.split(" ")
    right_tokens = right_n.split(" ")
    return all(t in right_tokens for t in left_tokens) or all(t in left_tokens for t in right_tokens)


def fuzzy_ratio(a: str | None, b: str | None) -> float:
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()
