"""Minimisation helpers for data that leaves the database (API responses, stored
provider errors, logs) -- DVB-208 / audit finding M-05.

Recipients are personal data and provider responses can echo addresses, API keys or
message content, so anything shown outside the authenticated admin is masked and
anything stored from a provider is redacted and truncated.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Brevo keys (xkeysib-/xsmtpsib-), relay keys (brk_), bearer tokens and any long opaque
# token-looking run of characters.
_SECRET = re.compile(
    r"(?i)\b(?:xkeysib|xsmtpsib)-[A-Za-z0-9-]+|\bbrk_[A-Za-z0-9_-]+|"
    r"(?i:bearer)\s+[A-Za-z0-9._~+/=-]+|\b[A-Za-z0-9_-]{32,}\b"
)

ERROR_MAX_CHARS = 300


def mask_email(address: str | None) -> str | None:
    """"ana.souza@example.com" -> "a***@example.com"; non-addresses pass through masked."""
    if not address:
        return address
    local, sep, domain = address.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


def redact(text: str | None, limit: int = ERROR_MAX_CHARS) -> str | None:
    """Masks e-mail addresses, removes secret-looking tokens and truncates."""
    if text is None:
        return None
    cleaned = _SECRET.sub("[redacted]", text)
    cleaned = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned
