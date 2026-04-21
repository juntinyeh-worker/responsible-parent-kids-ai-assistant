"""Environment-aware application configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

VALID_DEPLOY_ENVS = ("local", "aws")


class AppConfig:
    def __init__(self):
        self.deploy_env: str = os.getenv("DEPLOY_ENV", "local")
        if self.deploy_env not in VALID_DEPLOY_ENVS:
            raise ValueError(
                f"Invalid DEPLOY_ENV='{self.deploy_env}'. Must be one of: {VALID_DEPLOY_ENVS}"
            )

        self.local_log_dir: str = os.getenv("LOCAL_LOG_DIR", "./logs")
        self.s3_log_bucket: str | None = os.getenv("S3_LOG_BUCKET")
        self.s3_log_prefix: str = os.getenv("S3_LOG_PREFIX", "conversation-logs")
        self.s3_audio_prefix: str = os.getenv("S3_AUDIO_PREFIX", "voice-responses")
        self.secrets_name: str | None = os.getenv("SECRETS_NAME")

        # In local mode, OPENAI_API_KEY must be set
        self.openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
        if self.deploy_env == "local" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is required when DEPLOY_ENV=local. "
                "Copy .env.example to .env and set your key."
            )

    @property
    def is_local(self) -> bool:
        return self.deploy_env == "local"

    @property
    def is_aws(self) -> bool:
        return self.deploy_env == "aws"


config = AppConfig()
