"""Environment-aware application configuration."""

import os

from dotenv import load_dotenv

load_dotenv()

VALID_DEPLOY_ENVS = ("local", "aws")


class AppConfig:
    def __init__(self):
        self.deploy_env: str = os.getenv("DEPLOY_ENV", "local")
        if self.deploy_env not in VALID_DEPLOY_ENVS:
            raise ValueError(f"Invalid DEPLOY_ENV='{self.deploy_env}'. Must be one of: {VALID_DEPLOY_ENVS}")

        self.local_log_dir: str = os.getenv("LOCAL_LOG_DIR", "./logs")
        self.s3_log_bucket: str | None = os.getenv("S3_LOG_BUCKET")
        self.s3_log_prefix: str = os.getenv("S3_LOG_PREFIX", "conversation-logs")
        self.s3_audio_prefix: str = os.getenv("S3_AUDIO_PREFIX", "voice-responses")

        # Bedrock / Nova Sonic config
        self.bedrock_region: str = os.getenv("BEDROCK_REGION", "us-east-1")
        self.nova_voice_id: str = os.getenv("NOVA_VOICE_ID", "tiffany")

    @property
    def is_local(self) -> bool:
        return self.deploy_env == "local"

    @property
    def is_aws(self) -> bool:
        return self.deploy_env == "aws"


config = AppConfig()
