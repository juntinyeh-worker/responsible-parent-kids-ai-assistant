"""End-to-end tests: produce logs in S3 and access them via backend/frontend.

Uses the real sandbox S3 bucket and deployed stack to verify the full log pipeline.
Backend API is accessed via CloudFront (ALB is restricted to CF-only traffic).

Run with: pytest tests/e2e/test_s3_log_e2e.py -v
Requires AWS credentials with access to the sandbox account.
"""

import json
import uuid

import boto3
import pytest

BUCKET = "sandbox-rpkai-logs-256358067059"
REGION = "us-west-2"
LOG_PREFIX = "conversation-logs"
AUDIO_PREFIX = "voice-responses"
CLOUDFRONT_URL = "https://d1brrcdnejyoj8.cloudfront.net"

TEST_SESSION_ID = f"e2e-test-{uuid.uuid4().hex[:8]}"
TEST_DATE = "2026/04/21"


@pytest.fixture(scope="module")
def s3():
    return boto3.client("s3", region_name=REGION)


@pytest.fixture(scope="module")
def seed_logs(s3):
    """Write test log entries to S3 to simulate log production."""
    keys = []
    for turn in [1, 2]:
        entry = {
            "sessionId": TEST_SESSION_ID,
            "turnNumber": turn,
            "timestamp": f"2026-04-21T00:0{turn}:00Z",
            "userText": f"user message {turn}",
            "assistantText": f"assistant reply {turn}",
            "audioS3Key": f"{AUDIO_PREFIX}/{TEST_DATE}/{TEST_SESSION_ID}/{turn}.pcm",
        }
        key = f"{LOG_PREFIX}/server-turns/{TEST_DATE}/{TEST_SESSION_ID}/{turn}.json"
        s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(entry).encode(), ContentType="application/json")
        keys.append(key)

    # Upload fake audio
    audio_key = f"{AUDIO_PREFIX}/{TEST_DATE}/{TEST_SESSION_ID}/1.pcm"
    s3.put_object(Bucket=BUCKET, Key=audio_key, Body=b"\x00\x01" * 50, ContentType="audio/L16;rate=24000;channels=1")
    keys.append(audio_key)

    yield

    for k in keys:
        s3.delete_object(Bucket=BUCKET, Key=k)


class TestS3LogProduction:
    """Verify logs can be written to and read from S3."""

    def test_log_object_readable(self, s3, seed_logs):
        key = f"{LOG_PREFIX}/server-turns/{TEST_DATE}/{TEST_SESSION_ID}/1.json"
        data = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        assert data["sessionId"] == TEST_SESSION_ID
        assert data["userText"] == "user message 1"

    def test_audio_object_readable(self, s3, seed_logs):
        key = f"{AUDIO_PREFIX}/{TEST_DATE}/{TEST_SESSION_ID}/1.pcm"
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        assert len(body) == 100


class TestBackendAPIAccess:
    """Verify backend API endpoints via CloudFront."""

    @pytest.fixture
    def http(self):
        import httpx
        return httpx.Client(base_url=CLOUDFRONT_URL, timeout=15, follow_redirects=True)

    def test_health(self, http):
        r = http.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_list_sessions(self, http, seed_logs):
        r = http.get("/api/logs/sessions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_dates(self, http, seed_logs):
        r = http.get("/api/logs/dates")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_session_turns(self, http, seed_logs):
        r = http.get(f"/api/logs/sessions/{TEST_SESSION_ID}/turns")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestFrontendAccess:
    """Verify frontend pages are served via CloudFront."""

    @pytest.fixture
    def http(self):
        import httpx
        return httpx.Client(timeout=10)

    def test_index_page(self, http):
        r = http.get(CLOUDFRONT_URL)
        assert r.status_code == 200

    def test_chat_page(self, http):
        r = http.get(f"{CLOUDFRONT_URL}/chat.html")
        assert r.status_code == 200
