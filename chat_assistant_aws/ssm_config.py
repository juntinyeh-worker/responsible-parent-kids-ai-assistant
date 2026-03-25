"""SSM Parameter Store config loader with caching.

Provides a reusable pattern for overriding hardcoded defaults with SSM params.
Only active when DEPLOY_ENV=aws; returns None in local mode.
"""

import logging
import time
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[str, float]] = {}
DEFAULT_TTL = 300.0  # 5 minutes


def get_ssm_param(name: str, ttl: float = DEFAULT_TTL) -> Optional[str]:
    """Fetch an SSM parameter by name. Returns None if not found or in local mode."""
    if not config.is_aws:
        return None

    # Check cache
    if name in _cache:
        value, fetched_at = _cache[name]
        if (time.time() - fetched_at) < ttl:
            return value

    try:
        import boto3
        client = boto3.client("ssm", region_name=config.bedrock_region)
        resp = client.get_parameter(Name=name, WithDecryption=True)
        value = resp["Parameter"]["Value"]
        _cache[name] = (value, time.time())
        logger.info(f"Loaded SSM param: {name}")
        return value
    except Exception as e:
        logger.warning(f"SSM param '{name}' not available: {e}")
        return None
