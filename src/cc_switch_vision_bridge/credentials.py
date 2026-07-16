from __future__ import annotations

import os

import keyring

SERVICE_NAME = "CCSwitchVisionBridge"
ACCOUNT_NAME = "vision-api-key"


def get_api_key() -> str:
    env_key = os.environ.get("CCSVB_VISION_API_KEY", "").strip()
    if env_key:
        return env_key
    value = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    if not value:
        raise RuntimeError(
            "Vision API key is not configured. Run install.ps1 again or set "
            "CCSVB_VISION_API_KEY for this process."
        )
    return value


def set_api_key(value: str) -> None:
    value = value.strip()
    if not value:
        raise ValueError("API key cannot be empty")
    keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, value)


def delete_api_key() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except keyring.errors.PasswordDeleteError:
        pass

