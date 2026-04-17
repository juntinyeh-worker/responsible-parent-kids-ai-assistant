"""Conversation log writer — append to single JSONL file with 20MB rotation.

Local (Phase 1): writes to ./logs/conversation_log_001.jsonl
AWS (Phase 2): writes to S3 (stub)
"""

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


def _get_current_log_path() -> Path:
    """Find the current log file, or create a new one if the latest exceeds 20MB."""
    log_dir = Path(config.local_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Find existing log files sorted by number
    existing = sorted(log_dir.glob("conversation_log_*.jsonl"))

    if not existing:
        return log_dir / "conversation_log_001.jsonl"

    latest = existing[-1]
    if latest.exists() and latest.stat().st_size >= MAX_FILE_SIZE_BYTES:
        # Rotate: extract number and increment
        stem = latest.stem  # e.g. "conversation_log_001"
        num = int(stem.split("_")[-1])
        return log_dir / f"conversation_log_{num + 1:03d}.jsonl"

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
