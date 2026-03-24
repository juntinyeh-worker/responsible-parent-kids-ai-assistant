"""Unit tests for conversation log writer (JSONL append + 20MB rotation)."""
import json
import pytest
from pathlib import Path
from logging_service import ConversationLogEntry, _write_local, _get_current_log_path, MAX_FILE_SIZE_BYTES


@pytest.fixture
def tmp_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("logging_service.config.local_log_dir", str(tmp_path))
    return tmp_path


def _make_entry(turn=1, client="client-1", session="sess-1"):
    return ConversationLogEntry(
        clientId=client, sessionId=session, turnNumber=turn,
        timestamp="2026-03-21T00:00:00Z",
        requestSummary="Hello", responseSummary="Hi there",
    )


@pytest.mark.asyncio
async def test_creates_first_log_file(tmp_log_dir):
    await _write_local(_make_entry())
    path = tmp_log_dir / "conversation_log_001.jsonl"
    assert path.exists()
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["clientId"] == "client-1"


@pytest.mark.asyncio
async def test_appends_to_same_file(tmp_log_dir):
    await _write_local(_make_entry(turn=1))
    await _write_local(_make_entry(turn=2))
    await _write_local(_make_entry(turn=3))
    path = tmp_log_dir / "conversation_log_001.jsonl"
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_rotates_when_exceeds_20mb(tmp_log_dir):
    # Create a file just under the limit
    first_file = tmp_log_dir / "conversation_log_001.jsonl"
    first_file.write_text("x" * (MAX_FILE_SIZE_BYTES))  # exactly 20MB

    await _write_local(_make_entry(turn=99))
    second_file = tmp_log_dir / "conversation_log_002.jsonl"
    assert second_file.exists()
    lines = second_file.read_text().strip().split("\n")
    assert len(lines) == 1


def test_get_current_log_path_creates_dir(tmp_log_dir):
    path = _get_current_log_path()
    assert path.name == "conversation_log_001.jsonl"
    assert path.parent.exists()


def test_get_current_log_path_returns_latest(tmp_log_dir):
    (tmp_log_dir / "conversation_log_001.jsonl").write_text("line\n")
    (tmp_log_dir / "conversation_log_002.jsonl").write_text("line\n")
    path = _get_current_log_path()
    assert path.name == "conversation_log_002.jsonl"
