"""Ephemeral token generation via OpenAI Realtime API.

Tries the GA endpoint first (/v1/realtime/client_secrets with gpt-realtime),
falls back to the beta endpoint (/v1/realtime/sessions with gpt-4o-realtime-preview).
"""

import logging
import os
import httpx
from pydantic import BaseModel
from api_secrets import get_api_key
from prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# GA endpoint (Aug 2025+)
GA_URL = "https://api.openai.com/v1/realtime/client_secrets"
GA_MODEL = "gpt-4o-mini-realtime"
GA_TRANSCRIPTION_MODEL = "gpt-realtime-transcription"

# Beta endpoint (fallback)
BETA_URL = "https://api.openai.com/v1/realtime/sessions"
BETA_MODEL = "gpt-4o-realtime-preview"
BETA_TRANSCRIPTION_MODEL = "whisper-1"

# Allow override via env var
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "")


class EphemeralTokenResponse(BaseModel):
    sessionToken: str
    expiresAt: int
    sessionId: str
    model: str = ""
    isGA: bool = True  # True = use /v1/realtime/calls, False = use /v1/realtime?model=...


class SessionCreationError(Exception):
    """Raised when OpenAI session creation fails."""
    pass


async def create_session() -> EphemeralTokenResponse:
    """Create an ephemeral session token. Tries GA, falls back to beta."""
    api_key = await get_api_key()

    # Try GA first, then beta
    if REALTIME_MODEL:
        # User explicitly set a model — use GA endpoint
        return await _try_endpoint(api_key, GA_URL, REALTIME_MODEL, GA_TRANSCRIPTION_MODEL, ga=True)

    try:
        return await _try_endpoint(api_key, GA_URL, GA_MODEL, GA_TRANSCRIPTION_MODEL, ga=True)
    except SessionCreationError as e:
        logger.warning(f"GA endpoint failed ({e}), trying beta endpoint...")
        return await _try_endpoint(api_key, BETA_URL, BETA_MODEL, BETA_TRANSCRIPTION_MODEL, ga=False)


async def _try_endpoint(
    api_key: str, url: str, model: str, transcription_model: str, ga: bool
) -> EphemeralTokenResponse:
    """Try creating a session with the given endpoint configuration."""

    if ga:
        payload = {
            "session": {
                "type": "realtime",
                "model": model,
                "audio": {"output": {"voice": "alloy"}},
                "instructions": SYSTEM_PROMPT,
                "input_audio_transcription": {"model": transcription_model},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.6,
                    "prefix_padding_ms": 500,
                    "silence_duration_ms": 1000,
                },
            }
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        # Beta format — no "session" wrapper, different field names
        payload = {
            "model": model,
            "voice": "alloy",
            "instructions": SYSTEM_PROMPT,
            "input_audio_transcription": {"model": transcription_model},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "realtime=v1",
        }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=10.0)

    if response.status_code != 200:
        logger.error(f"OpenAI session creation failed: status={response.status_code}, body={response.text}")
        raise SessionCreationError(f"OpenAI returned {response.status_code}: {response.text}")

    data = response.json()

    # GA response: { "client_secret": { "value": "...", "expires_at": ... }, "id": "..." }
    # Beta response: { "client_secret": { "value": "...", "expires_at": ... }, "id": "..." }
    client_secret = data.get("client_secret", {})

    token = client_secret.get("value", "")
    expires = client_secret.get("expires_at", 0)
    sess_id = data.get("id", "")

    if not token:
        raise SessionCreationError(f"No token in response: {data}")

    logger.info(f"Session created via {'GA' if ga else 'beta'} endpoint, model={model}, id={sess_id}")

    return EphemeralTokenResponse(sessionToken=token, expiresAt=expires, sessionId=sess_id, model=model, isGA=ga)
