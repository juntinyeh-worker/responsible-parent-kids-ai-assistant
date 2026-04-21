"""Conversation log writer — append to single JSONL file with 20MB rotation.

Local (Phase 1): writes to ./logs/conversation_log_001.jsonl
AWS (Phase 2): writes to S3

Also provides server-side turn logging (text I/O from client-reported transcripts).
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


class ServerTurnLog(BaseModel):
    """Server-side log entry capturing text I/O reported by the client."""

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
    """Write server-side turn log (text I/O reported by client)."""
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


async def list_sessions() -> list[dict]:
    """List all logged sessions with metadata. Returns [{sessionId, firstTimestamp, turnCount}]."""
    sessions: dict[str, dict] = {}
    try:
        if config.is_local:
            log_dir = Path(config.local_log_dir)
            for f in sorted(log_dir.glob("server_turn_log_*.jsonl")):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    entry = json.loads(line)
                    sid = entry["sessionId"]
                    if sid not in sessions:
                        sessions[sid] = {"sessionId": sid, "firstTimestamp": entry["timestamp"], "turnCount": 0}
                    sessions[sid]["turnCount"] += 1
        elif config.is_aws and config.s3_log_bucket:
            s3 = _get_s3_client()
            prefix = f"{config.s3_log_prefix}/server-turns/"
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=config.s3_log_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    parts = obj["Key"].split("/")
                    if len(parts) >= 7 and parts[-1].endswith(".json"):
                        sid = parts[-2]
                        if sid not in sessions:
                            sessions[sid] = {
                                "sessionId": sid,
                                "firstTimestamp": obj["LastModified"].isoformat(),
                                "turnCount": 0,
                            }
                        sessions[sid]["turnCount"] += 1
    except Exception as e:
        logger.error(f"Failed to list sessions: {e}")
    return sorted(sessions.values(), key=lambda s: s["firstTimestamp"], reverse=True)


async def list_log_dates() -> list[str]:
    """List all dates that have logs. Returns ['YYYY/MM/DD', ...] sorted newest first."""
    dates: set[str] = set()
    try:
        if config.is_aws and config.s3_log_bucket:
            s3 = _get_s3_client()
            prefix = f"{config.s3_log_prefix}/server-turns/"
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=config.s3_log_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    parts = obj["Key"].split("/")
                    if len(parts) >= 7:
                        dates.add(f"{parts[2]}/{parts[3]}/{parts[4]}")
        elif config.is_local:
            log_dir = Path(config.local_log_dir)
            for f in sorted(log_dir.glob("server_turn_log_*.jsonl")):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    ts = json.loads(line).get("timestamp", "")
                    if ts:
                        dates.add(ts[:10].replace("-", "/"))
    except Exception as e:
        logger.error(f"Failed to list log dates: {e}")
    return sorted(dates, reverse=True)


async def list_sessions_by_date(date_str: str) -> list[dict]:
    """List sessions for a specific date. Returns [{sessionId, turnCount, firstTimestamp}]."""
    sessions: dict[str, dict] = {}
    try:
        if config.is_aws and config.s3_log_bucket:
            s3 = _get_s3_client()
            prefix = f"{config.s3_log_prefix}/server-turns/{date_str}/"
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=config.s3_log_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    parts = obj["Key"].split("/")
                    if len(parts) >= 7 and parts[-1].endswith(".json"):
                        sid = parts[-2]
                        if sid not in sessions:
                            sessions[sid] = {
                                "sessionId": sid,
                                "date": date_str,
                                "firstTimestamp": obj["LastModified"].isoformat(),
                                "turnCount": 0,
                            }
                        sessions[sid]["turnCount"] += 1
        elif config.is_local:
            log_dir = Path(config.local_log_dir)
            target = date_str.replace("/", "-")
            for f in sorted(log_dir.glob("server_turn_log_*.jsonl")):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    entry = json.loads(line)
                    if entry.get("timestamp", "").startswith(target):
                        sid = entry["sessionId"]
                        if sid not in sessions:
                            sessions[sid] = {
                                "sessionId": sid,
                                "date": date_str,
                                "firstTimestamp": entry["timestamp"],
                                "turnCount": 0,
                            }
                        sessions[sid]["turnCount"] += 1
    except Exception as e:
        logger.error(f"Failed to list sessions for {date_str}: {e}")
    return sorted(sessions.values(), key=lambda s: s["firstTimestamp"], reverse=True)


async def list_session_turns(session_id: str) -> list[dict]:
    """List all turns for a given session. Returns [{turnNumber, timestamp, userText, assistantText, audioS3Key}]."""
    turns: list[dict] = []
    try:
        if config.is_local:
            log_dir = Path(config.local_log_dir)
            for f in sorted(log_dir.glob("server_turn_log_*.jsonl")):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    entry = json.loads(line)
                    if entry["sessionId"] == session_id:
                        turns.append(entry)
        elif config.is_aws and config.s3_log_bucket:
            s3 = _get_s3_client()
            prefix = f"{config.s3_log_prefix}/server-turns/"
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=config.s3_log_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    parts = obj["Key"].split("/")
                    if len(parts) >= 7 and parts[-2] == session_id and parts[-1].endswith(".json"):
                        body = s3.get_object(Bucket=config.s3_log_bucket, Key=obj["Key"])["Body"].read()
                        turns.append(json.loads(body))
    except Exception as e:
        logger.error(f"Failed to list turns for {session_id}: {e}")
    return sorted(turns, key=lambda t: t.get("turnNumber", 0))
