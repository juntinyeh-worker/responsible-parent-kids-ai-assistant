"""API key provider — env var (local) or Secrets Manager (AWS)."""

import time
from config import config


class CachedSecret:
    def __init__(self, value: str, ttl: float = 300.0):
        self.value = value
        self.fetched_at = time.time()
        self.ttl = ttl

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.fetched_at) >= self.ttl


_cache: CachedSecret | None = None


async def get_api_key() -> str:
    """Return the OpenAI API key. Prefers Secrets Manager in aws mode, falls back to env var."""
    if config.is_aws and config.secrets_name:
        return await _get_from_secrets_manager()

    if config.openai_api_key:
        return config.openai_api_key

    raise ValueError("No API key available. Set OPENAI_API_KEY or configure SECRETS_NAME.")


async def _get_from_secrets_manager() -> str:
    """Fetch API key from AWS Secrets Manager with 5-min cache."""
    global _cache
    if _cache and not _cache.is_expired:
        return _cache.value

    import boto3
    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=config.secrets_name)
    value = resp["SecretString"]
    _cache = CachedSecret(value)
    return value
