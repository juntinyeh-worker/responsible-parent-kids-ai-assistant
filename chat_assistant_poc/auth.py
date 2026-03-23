"""Cognito OAuth2 callback — exchanges auth code for tokens, sets session cookie."""

import os
import logging
import httpx
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()

COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET", "")
COGNITO_REDIRECT_URI = os.getenv("COGNITO_REDIRECT_URI", "")


@router.get("/auth/callback")
@router.get("/auth/callback/")
async def auth_callback(code: str = ""):
    """Exchange Cognito authorization code for tokens and set session cookie."""
    if not code:
        return RedirectResponse("/")

    if not all([COGNITO_DOMAIN, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET]):
        logger.warning("Cognito not configured, skipping auth")
        return RedirectResponse("/")

    token_url = f"https://{COGNITO_DOMAIN}/oauth2/token"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": COGNITO_CLIENT_ID,
                "client_secret": COGNITO_CLIENT_SECRET,
                "code": code,
                "redirect_uri": COGNITO_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        logger.error(f"Token exchange failed: {resp.status_code} {resp.text}")
        return RedirectResponse("/")

    tokens = resp.json()
    id_token = tokens.get("id_token", "")

    response = RedirectResponse("/")
    # Flag cookie for CloudFront Function auth check
    response.set_cookie("cvt-authed", "1", secure=True, samesite="lax", max_age=3600)
    # Full token for backend API auth
    response.set_cookie("cvt-session", id_token, httponly=True, secure=True, samesite="lax", max_age=3600)
    return response


@router.get("/auth/logout")
async def logout():
    """Clear session cookies and redirect to login."""
    response = RedirectResponse("/")
    response.delete_cookie("cvt-authed")
    response.delete_cookie("cvt-session")
    return response
