"""FastAPI application — WebSocket audio proxy to Nova Sonic + static file server."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import config
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from logging_service import (
    ConversationLogEntry,
    ServerTurnLog,
    upload_voice_response,
    write_log,
    write_server_turn_log,
)
from pydantic import BaseModel
from rate_limit import rate_limiter
from session import NovaSonicSession

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")), format="%(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        json.dumps(
            {
                "event": "startup",
                "deploy_env": config.deploy_env,
                "status": "ready",
            }
        )
    )
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    cleanup_task.cancel()
    logger.info(json.dumps({"event": "shutdown"}))


async def _periodic_cleanup():
    """Purge stale rate-limiter entries every 5 minutes to prevent memory leaks."""
    while True:
        await asyncio.sleep(300)
        rate_limiter.cleanup()


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

    # Per-session state for server-side logging
    session_id = session.prompt_name  # unique per session
    turn_number = 0
    current_user_text = ""
    current_assistant_text = ""
    current_audio_chunks: list[str] = []
    current_role = "ASSISTANT"
    is_speculative = False

    async def flush_turn():
        """Log the completed turn's text I/O and upload audio to S3."""
        nonlocal turn_number, current_user_text, current_assistant_text, current_audio_chunks
        if not current_user_text and not current_assistant_text and not current_audio_chunks:
            return

        turn_number += 1
        audio_key = await upload_voice_response(session_id, turn_number, current_audio_chunks)
        await write_server_turn_log(
            ServerTurnLog(
                sessionId=session_id,
                turnNumber=turn_number,
                timestamp=datetime.now(timezone.utc).isoformat(),
                userText=current_user_text,
                assistantText=current_assistant_text,
                audioS3Key=audio_key,
            )
        )
        current_user_text = ""
        current_assistant_text = ""
        current_audio_chunks = []

    try:
        logger.info("Starting Nova Sonic session...")
        await session.start()
        logger.info("Nova Sonic session started, starting audio input...")
        await session.start_audio_input()
        logger.info("Audio input started, ready for audio chunks")

        # Callbacks for Nova Sonic responses → forward to browser + capture for logging
        async def on_audio(b64_audio):
            nonlocal current_audio_chunks
            current_audio_chunks.append(b64_audio)
            try:
                await ws.send_json({"type": "audio", "data": b64_audio})
            except Exception:
                session.is_active = False

        async def on_text(role, text):
            nonlocal current_user_text, current_assistant_text
            if not is_speculative:
                if role == "USER":
                    current_user_text += text
                else:
                    current_assistant_text += text
            try:
                await ws.send_json({"type": "text", "role": role, "content": text})
            except Exception:
                session.is_active = False

        async def on_event(evt):
            nonlocal current_role, is_speculative
            if "speculative" in evt:
                is_speculative = evt["speculative"]
            if evt.get("type") == "contentStart":
                current_role = evt.get("role", current_role)
            if evt.get("type") == "contentEnd" and evt.get("role") == "ASSISTANT" and not is_speculative:
                await flush_turn()
            try:
                await ws.send_json({"type": "event", **evt})
            except Exception:
                session.is_active = False

        # Start reading Nova Sonic responses in background
        response_task = asyncio.create_task(session.process_responses(on_audio, on_text, on_event))

        # Read audio from browser and forward to Nova Sonic
        chunk_count = 0
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type", "")

            if msg_type == "audio":
                chunk_count += 1
                if chunk_count <= 3 or chunk_count % 100 == 0:
                    logger.info(f"Audio chunk #{chunk_count} received ({len(msg.get('data', ''))} b64 chars)")
                await session.send_audio_chunk(msg["data"])
            elif msg_type == "stop":
                logger.info(f"Stop received after {chunk_count} chunks")
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Flush any remaining turn data
        await flush_turn()
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


@app.get("/api/audio/{session_id}/{turn_number}")
async def get_audio(session_id: str, turn_number: int, date: str | None = None):
    """Retrieve a stored voice response PCM file for replay.

    Query params:
        date: YYYY/MM/DD (defaults to today)
    Returns raw PCM audio (16-bit, 24kHz, mono).
    """
    date_str = date or datetime.now(timezone.utc).strftime("%Y/%m/%d")

    if config.is_aws and config.s3_log_bucket:
        from logging_service import _get_s3_client, get_voice_response_s3_key

        key = get_voice_response_s3_key(session_id, turn_number, date_str)
        try:
            s3 = _get_s3_client()
            obj = s3.get_object(Bucket=config.s3_log_bucket, Key=key)
            pcm_data = obj["Body"].read()
            return Response(content=pcm_data, media_type="audio/L16;rate=24000;channels=1")
        except Exception:
            return JSONResponse(status_code=404, content={"error": "Audio not found"})
    else:
        from pathlib import Path

        local_path = Path(config.local_log_dir) / config.s3_audio_prefix / date_str / session_id / f"{turn_number}.pcm"
        if local_path.exists():
            return Response(content=local_path.read_bytes(), media_type="audio/L16;rate=24000;channels=1")
        return JSONResponse(status_code=404, content={"error": "Audio not found"})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        json.dumps(
            {
                "event": "unhandled_exception",
                "error": str(exc),
                "path": str(request.url.path),
            }
        )
    )
    return JSONResponse(status_code=500, content={"error": "Internal server error."})


if config.is_local:
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
