"""Unit tests for S3 log production (mocked with moto)."""

import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

# Ensure the app root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def s3_env(monkeypatch):
    """Set up AWS-mode config with a mocked S3 bucket."""
    monkeypatch.setenv("DEPLOY_ENV", "aws")
    monkeypatch.setenv("S3_LOG_BUCKET", "test-log-bucket")
    monkeypatch.setenv("S3_LOG_PREFIX", "conversation-logs")
    monkeypatch.setenv("S3_AUDIO_PREFIX", "voice-responses")


@pytest.fixture
def s3_setup():
    """Create mocked S3 bucket and inject client into logging_service."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-log-bucket")

        # Reload config with env vars set
        import importlib
        import config as config_mod
        importlib.reload(config_mod)

        import logging_service
        importlib.reload(logging_service)
        logging_service._s3_client = s3

        yield s3, logging_service


async def test_write_s3_creates_object(s3_setup):
    s3, ls = s3_setup
    entry = ls.ConversationLogEntry(
        clientId="c1", sessionId="s1", turnNumber=1,
        timestamp="2026-04-21T00:00:00Z", requestSummary="hi", responseSummary="hello",
    )
    await ls.write_log(entry)
    objs = s3.list_objects_v2(Bucket="test-log-bucket", Prefix="conversation-logs/")
    assert objs["KeyCount"] == 1
    body = json.loads(s3.get_object(Bucket="test-log-bucket", Key=objs["Contents"][0]["Key"])["Body"].read())
    assert body["sessionId"] == "s1"


async def test_write_server_turn_log_s3(s3_setup):
    s3, ls = s3_setup
    entry = ls.ServerTurnLog(
        sessionId="sess-abc", turnNumber=1,
        timestamp="2026-04-21T00:00:00Z", userText="hello", assistantText="hi there",
    )
    await ls.write_server_turn_log(entry)
    objs = s3.list_objects_v2(Bucket="test-log-bucket", Prefix="conversation-logs/server-turns/")
    assert objs["KeyCount"] == 1
    body = json.loads(s3.get_object(Bucket="test-log-bucket", Key=objs["Contents"][0]["Key"])["Body"].read())
    assert body["userText"] == "hello"


async def test_upload_voice_response_s3(s3_setup):
    import base64
    s3, ls = s3_setup
    chunks = [base64.b64encode(b"\x00" * 100).decode()]
    key = await ls.upload_voice_response("sess-abc", 1, chunks)
    assert key and "voice-responses" in key
    assert len(s3.get_object(Bucket="test-log-bucket", Key=key)["Body"].read()) == 100


async def test_list_sessions_s3(s3_setup):
    s3, ls = s3_setup
    for turn in [1, 2]:
        s3.put_object(
            Bucket="test-log-bucket",
            Key=f"conversation-logs/server-turns/2026/04/21/sess-x/{turn}.json",
            Body=json.dumps({"sessionId": "sess-x", "turnNumber": turn}).encode(),
        )
    sessions = await ls.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == "sess-x"
    assert sessions[0]["turnCount"] == 2


async def test_list_session_turns_s3(s3_setup):
    s3, ls = s3_setup
    entry = {"sessionId": "sess-y", "turnNumber": 1, "userText": "hey", "assistantText": "yo"}
    s3.put_object(
        Bucket="test-log-bucket",
        Key="conversation-logs/server-turns/2026/04/21/sess-y/1.json",
        Body=json.dumps(entry).encode(),
    )
    turns = await ls.list_session_turns("sess-y")
    assert len(turns) == 1
    assert turns[0]["userText"] == "hey"
