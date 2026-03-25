"""Unit tests for session creation."""

from unittest.mock import AsyncMock, patch

import pytest
from prompts import SYSTEM_PROMPT
from session import create_session


def test_system_prompt_contains_safety_keywords():
    """Property 3: Child safety prompt injection — concrete check."""
    assert "Traditional Chinese" in SYSTEM_PROMPT or "繁體中文" in SYSTEM_PROMPT
    assert "five sentences" in SYSTEM_PROMPT
    assert "violence" in SYSTEM_PROMPT
    assert "politics" in SYSTEM_PROMPT
    assert "religion" in SYSTEM_PROMPT
    assert "adult topics" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_create_session_returns_token():
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {
        "client_secret": {"value": "eph_test123", "expires_at": 9999999999},
        "id": "sess_test",
    }
    mock_response.text = ""

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("session.httpx.AsyncClient", return_value=mock_client),
        patch("session.get_api_key", return_value="sk-test"),
    ):
        result = await create_session()

    assert result.sessionToken == "eph_test123"
    assert result.expiresAt == 9999999999
    assert result.sessionId == "sess_test"
