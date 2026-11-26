"""Deterministic PII detectors used by ``pii`` rules."""

from __future__ import annotations

import re

PATTERNS = {
    "card_number": re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<!\w)\+?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\w)"),
    "street_address": re.compile(
        r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Court|Ct|Place|Pl)\b\.?"
    ),
}


def luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect(text: str, detectors: list[str]) -> list[tuple[str, str]]:
    """Return ``(detector, matched_value)`` pairs."""
    hits: list[tuple[str, str]] = []
    for name in detectors:
        pat = PATTERNS.get(name)
        if pat is None:
            continue
        for m in pat.finditer(text):
            val = m.group(0).strip()
            if name == "card_number" and not luhn_ok(val):
                continue
            if name == "phone" and sum(c.isdigit() for c in val) < 10:
                continue
            if name == "phone" and luhn_ok(val):  # a card number is not a phone number
                continue
            hits.append((name, val))
    return hits


def normalize(v: str) -> str:
    return re.sub(r"[^a-z0-9@.]", "", v.lower())
