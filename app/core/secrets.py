"""Encryption at rest for per-project delivery secrets (DVB-306 / S1).

RELAY_SECRETS_KEYS holds one or more Fernet keys separated by commas. The first one
encrypts; all of them decrypt, so a key can be rotated by prepending the new key,
re-saving the secrets (or just waiting for the next edit) and later dropping the old
one. Outside development the setting is mandatory (see Settings); in development a
fixed, clearly dev-only key is used when none is configured so local data survives
restarts.

Plaintext secrets never leave this module except to the provider factory; APIs and
the admin only ever see `has_<name>` flags.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.settings.config import Settings, settings

SECRET_FIELDS = ("smtp_password", "brevo_api_key")

_DEV_ONLY_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"butter-relay development-only secrets key").digest()
)


class SecretsKeyError(ValueError):
    pass


def _cipher(config: Settings) -> MultiFernet:
    raw = [k.strip() for k in config.relay_secrets_keys.split(",") if k.strip()]
    if not raw:
        if config.environment != "development":
            raise SecretsKeyError("RELAY_SECRETS_KEYS must be set outside development")
        raw = [_DEV_ONLY_KEY.decode()]
    try:
        return MultiFernet([Fernet(key.encode()) for key in raw])
    except ValueError as exc:
        raise SecretsKeyError("RELAY_SECRETS_KEYS has an invalid Fernet key") from exc


def encrypt_secrets(values: dict[str, Any], config: Settings = settings) -> str | None:
    cleaned = {k: v for k, v in values.items() if k in SECRET_FIELDS and v}
    if not cleaned:
        return None
    return _cipher(config).encrypt(json.dumps(cleaned).encode()).decode()


def decrypt_secrets(token: str | None, config: Settings = settings) -> dict[str, Any]:
    if not token:
        return {}
    try:
        data = json.loads(_cipher(config).decrypt(token.encode()))
    except InvalidToken as exc:
        raise SecretsKeyError(
            "stored delivery secrets cannot be decrypted with RELAY_SECRETS_KEYS"
        ) from exc
    return data if isinstance(data, dict) else {}


def rotate_secrets(token: str | None, config: Settings = settings) -> str | None:
    """Re-encrypts with the first (newest) key."""
    if not token:
        return None
    return _cipher(config).rotate(token.encode()).decode()


def secret_flags(token: str | None, config: Settings = settings) -> dict[str, bool]:
    values = decrypt_secrets(token, config) if token else {}
    return {f"has_{name}": bool(values.get(name)) for name in SECRET_FIELDS}
