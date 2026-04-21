"""FastAPI application — session gateway and static file server."""

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import router as auth_router
from config import config
from logging_service import (
    ConversationLogEntry,
    ServerTurnLog,
    list_log_dates,
    list_session_turns,
    list_sessions,
    list_sessions_by_date,
    write_log,
    write_server_turn_log,
)
from rate_limit import rate_limiter
from session import SessionCreationError, create_session

# Structured JSON logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(json.dumps({"event": "startup", "deploy_env": config.deploy_env, "status": "ready"}))
    yield
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(title="Child Voice Tutor", lifespan=lifespan, redirect_slashes=False)
app.include_router(auth_router)


# --- Models ---


class SessionCreateRequest(BaseModel):
    clientId: str


class SessionLogRequest(BaseModel):
    clientId: str
    sessionId: str
    turnNumber: int
    timestamp: str
    requestSummary: str
    responseSummary: str


# --- Routes ---


@app.get("/api/health")
async def health():
    return {"status": "healthy"}


@app.post("/api/session/create")
async def session_create(body: SessionCreateRequest, request: Request):
    start = time.time()
    session_id = ""

    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        logger.info(json.dumps({"sessionId": "", "duration": round(time.time() - start, 3), "status": "rate_limited", "clientIp": client_ip}))
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    try:
        result = await create_session()
        session_id = result.sessionId
        logger.info(json.dumps({"sessionId": session_id, "duration": round(time.time() - start, 3), "status": "success", "clientId": body.clientId}))
        return result.model_dump()
    except SessionCreationError:
        logger.info(json.dumps({"sessionId": session_id, "duration": round(time.time() - start, 3), "status": "error", "clientId": body.clientId}))
        raise HTTPException(status_code=500, detail="Failed to create session.")


@app.post("/api/session/log")
async def session_log(body: SessionLogRequest):
    entry = ConversationLogEntry(**body.model_dump())
    await write_log(entry)
    # Also write as ServerTurnLog for the log browsing feature
    await write_server_turn_log(
        ServerTurnLog(
            sessionId=body.sessionId,
            turnNumber=body.turnNumber,
            timestamp=body.timestamp,
            userText=body.requestSummary,
            assistantText=body.responseSummary,
        )
    )
    return {"status": "ok"}


# --- Log Browsing API ---


@app.get("/api/logs/sessions")
async def get_log_sessions():
    return await list_sessions()


@app.get("/api/logs/dates")
async def get_log_dates():
    return await list_log_dates()


@app.get("/api/logs/dates/{year}/{month}/{day}/sessions")
async def get_sessions_by_date(year: str, month: str, day: str):
    return await list_sessions_by_date(f"{year}/{month}/{day}")


@app.get("/api/logs/sessions/{session_id}/turns")
async def get_session_turns(session_id: str):
    return await list_session_turns(session_id)


# --- Global exception handler ---


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(json.dumps({"event": "unhandled_exception", "error": str(exc), "path": str(request.url.path)}))
    return JSONResponse(status_code=500, content={"error": "Internal server error."})


# --- Static file serving ---

if config.is_local:
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
