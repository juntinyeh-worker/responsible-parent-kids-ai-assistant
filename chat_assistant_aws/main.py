"""FastAPI application — WebSocket audio proxy to Nova Sonic + static file server."""

import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import config
from session import NovaSonicSession
from logging_service import ConversationLogEntry, write_log

import os
logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")), format="%(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(json.dumps({
        "event": "startup",
        "deploy_env": config.deploy_env,
        "status": "ready",
    }))
    yield
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(title="Child Voice Tutor (Nova Sonic)", lifespan=lifespan, redirect_slashes=False)


# --- Models ---

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


@app.websocket("/api/ws/audio")
async def audio_websocket(ws: WebSocket):
    """Bidirectional audio streaming: browser ↔ this server ↔ Nova Sonic.

    Client sends:  {"type": "audio", "data": "<base64 PCM 16kHz>"}
                   {"type": "start"}
                   {"type": "stop"}
    Server sends:  {"type": "audio", "data": "<base64 PCM 24kHz>"}
                   {"type": "text", "role": "...", "content": "..."}
                   {"type": "event", ...}
    """
    await ws.accept()
    logger.info("WebSocket connected")

    session = NovaSonicSession(region=config.bedrock_region, voice_id=config.nova_voice_id)
    response_task = None

    try:
        logger.info("Starting Nova Sonic session...")
        await session.start()
        logger.info("Nova Sonic session started, starting audio input...")
        await session.start_audio_input()
        logger.info("Audio input started, ready for audio chunks")

        # Callbacks for Nova Sonic responses → forward to browser
        async def on_audio(b64_audio):
            try:
                await ws.send_json({"type": "audio", "data": b64_audio})
            except Exception:
                session.is_active = False

        async def on_text(role, text):
            try:
                await ws.send_json({"type": "text", "role": role, "content": text})
            except Exception:
                session.is_active = False

        async def on_event(evt):
            try:
                await ws.send_json({"type": "event", **evt})
            except Exception:
                session.is_active = False

        # Start reading Nova Sonic responses in background
        import asyncio
        response_task = asyncio.create_task(
            session.process_responses(on_audio, on_text, on_event)
        )

        # Read audio from browser and forward to Nova Sonic
        chunk_count = 0
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type", "")

            if msg_type == "audio":
                chunk_count += 1
                if chunk_count <= 3 or chunk_count % 100 == 0:
                    logger.info(f"Audio chunk #{chunk_count} received ({len(msg.get('data',''))} b64 chars)")
                await session.send_audio_chunk(msg["data"])
            elif msg_type == "stop":
                logger.info(f"Stop received after {chunk_count} chunks")
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        try:
            await session.end()
        except Exception:
            pass
        if response_task and not response_task.done():
            response_task.cancel()
        logger.info("Session cleaned up")


@app.post("/api/session/log")
async def session_log(body: SessionLogRequest):
    entry = ConversationLogEntry(**body.model_dump())
    await write_log(entry)
    return {"status": "ok"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(json.dumps({
        "event": "unhandled_exception",
        "error": str(exc),
        "path": str(request.url.path),
    }))
    return JSONResponse(status_code=500, content={"error": "Internal server error."})


if config.is_local:
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
