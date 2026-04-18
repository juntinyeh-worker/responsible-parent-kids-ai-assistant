"""Conversation log writer — append to single JSONL file with 20MB rotation.

Local (Phase 1): writes to ./logs/conversation_log_001.jsonl
AWS (Phase 2): writes to S3 (stub)

Also provides server-side turn logging (text I/O from Nova Sonic)
and voice response audio upload to S3 for replay.
"""

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import config
from pydantic import BaseModel

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client("s3")
    return _s3_client


class ConversationLogEntry(BaseModel):
    clientId: str
    sessionId: str
    turnNumber: int
    timestamp: str
    requestSummary: str
    responseSummary: str


class ServerTurnLog(BaseModel):
    """Server-side log entry capturing text I/O directly from Nova Sonic events."""

    sessionId: str
    turnNumber: int
    timestamp: str
    userText: str
    assistantText: str
    audioS3Key: str | None = None


def _get_current_log_path(prefix: str = "conversation_log") -> Path:
    """Find the current log file, or create a new one if the latest exceeds 20MB."""
    log_dir = Path(config.local_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(log_dir.glob(f"{prefix}_*.jsonl"))

    if not existing:
        return log_dir / f"{prefix}_001.jsonl"

    latest = existing[-1]
    if latest.exists() and latest.stat().st_size >= MAX_FILE_SIZE_BYTES:
        stem = latest.stem
        num = int(stem.split("_")[-1])
        return log_dir / f"{prefix}_{num + 1:03d}.jsonl"

    return latest


async def write_log(entry: ConversationLogEntry) -> None:
    """Append a log entry. Non-blocking — failures are logged, not raised."""
    try:
        if config.is_local:
            await _write_local(entry)
        elif config.is_aws:
            await _write_s3(entry)
    except Exception as e:
        logger.error(f"Failed to write conversation log: {e}")


async def _write_local(entry: ConversationLogEntry) -> None:
    """Append log entry as a single JSON line to the current JSONL file."""
    log_path = _get_current_log_path()
    line = json.dumps(entry.model_dump(), ensure_ascii=False) + "\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
    logger.info(f"Appended conversation log to {log_path} ({log_path.stat().st_size} bytes)")


async def _write_s3(entry: ConversationLogEntry) -> None:
    """Write log entry to S3 as individual JSON file."""
    s3 = _get_s3_client()
    timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    key = f"{config.s3_log_prefix}/{timestamp}/{entry.clientId}/{entry.sessionId}/{entry.turnNumber}.json"
    body = json.dumps(entry.model_dump(), ensure_ascii=False)
    s3.put_object(
        Bucket=config.s3_log_bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(f"Wrote conversation log to s3://{config.s3_log_bucket}/{key}")


async def write_server_turn_log(entry: ServerTurnLog) -> None:
    """Write server-side turn log (text I/O captured from Nova Sonic)."""
    try:
        if config.is_local:
            log_path = _get_current_log_path(prefix="server_turn_log")
            line = json.dumps(entry.model_dump(), ensure_ascii=False) + "\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
            logger.info(f"Server turn log → {log_path}")
        elif config.is_aws and config.s3_log_bucket:
            s3 = _get_s3_client()
            timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
            key = f"{config.s3_log_prefix}/server-turns/{timestamp}/{entry.sessionId}/{entry.turnNumber}.json"
            s3.put_object(
                Bucket=config.s3_log_bucket,
                Key=key,
                Body=json.dumps(entry.model_dump(), ensure_ascii=False).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"Server turn log → s3://{config.s3_log_bucket}/{key}")
    except Exception as e:
        logger.error(f"Failed to write server turn log: {e}")


async def upload_voice_response(session_id: str, turn_number: int, audio_chunks: list[str]) -> str | None:
    """Concatenate base64 PCM audio chunks and store as a file. Returns the S3 key or local path."""
    if not audio_chunks:
        return None
    try:
        pcm_data = b"".join(base64.b64decode(chunk) for chunk in audio_chunks)
        timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        relative_path = f"{config.s3_audio_prefix}/{timestamp}/{session_id}/{turn_number}.pcm"

        if config.is_aws and config.s3_log_bucket:
            s3 = _get_s3_client()
            s3.put_object(
                Bucket=config.s3_log_bucket,
                Key=relative_path,
                Body=pcm_data,
                ContentType="audio/L16;rate=24000;channels=1",
            )
            logger.info(f"Voice response ({len(pcm_data)} bytes) → s3://{config.s3_log_bucket}/{relative_path}")
            return relative_path
        else:
            local_dir = Path(config.local_log_dir) / config.s3_audio_prefix / timestamp / session_id
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / f"{turn_number}.pcm"
            local_path.write_bytes(pcm_data)
            logger.info(f"Voice response ({len(pcm_data)} bytes) → {local_path}")
            return str(local_path)
    except Exception as e:
        logger.error(f"Failed to upload voice response: {e}")
        return None


def get_voice_response_s3_key(session_id: str, turn_number: int, date_str: str) -> str:
    """Build the S3 key for a stored voice response."""
    return f"{config.s3_audio_prefix}/{date_str}/{session_id}/{turn_number}.pcm"
