"""Secure credential storage for Ticli.

Prefers the OS keychain (macOS Keychain, GNOME Keyring, Windows Credential Manager)
via the `keyring` library. Falls back to a chmod-600 JSON file if keyring is
unavailable.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "ticli"
FALLBACK_DIR = Path.home() / ".config" / SERVICE_NAME
FALLBACK_FILE = FALLBACK_DIR / "session.json"

try:
    import keyring
    # Verify the backend isn't the fail-open "null" backend
    _backend = keyring.get_keyring()
    _backend_name = type(_backend).__name__
    if "fail" in _backend_name.lower() or "null" in _backend_name.lower():
        keyring = None
        logger.debug("keyring backend is %s — falling back to file", _backend_name)
except Exception:
    keyring = None


def _ensure_fallback_dir() -> None:
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


def save_tokens(data: dict) -> None:
    """Persist OAuth tokens securely."""
    payload = json.dumps(data)

    if keyring is not None:
        try:
            keyring.set_password(SERVICE_NAME, "oauth_session", payload)
            # Remove any leftover plaintext file from previous runs
            _delete_fallback_file()
            return
        except Exception as e:
            logger.warning("keyring.set_password failed, falling back to file: %s", e)

    # Fallback: write to file with restrictive permissions
    _ensure_fallback_dir()
    FALLBACK_FILE.write_text(payload)
    os.chmod(FALLBACK_FILE, 0o600)


def load_tokens() -> Optional[dict]:
    """Load stored OAuth tokens. Returns None if nothing is stored."""
    # Try keychain first
    if keyring is not None:
        try:
            raw = keyring.get_password(SERVICE_NAME, "oauth_session")
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.debug("keyring.get_password failed: %s", e)

    # Fallback: read from file
    if FALLBACK_FILE.exists():
        try:
            return json.loads(FALLBACK_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to read fallback token file: %s", e)

    return None


def delete_tokens() -> None:
    """Remove stored OAuth tokens from all backends."""
    if keyring is not None:
        try:
            keyring.delete_password(SERVICE_NAME, "oauth_session")
        except Exception:
            pass
    _delete_fallback_file()


def _delete_fallback_file() -> None:
    """Remove the plaintext fallback file if it exists."""
    try:
        if FALLBACK_FILE.exists():
            # Overwrite before unlinking for slightly better security
            FALLBACK_FILE.write_bytes(b"\x00" * len(FALLBACK_FILE.read_bytes()))
            FALLBACK_FILE.unlink()
    except OSError:
        pass
