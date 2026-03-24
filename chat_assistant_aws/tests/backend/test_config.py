"""Unit tests for config module."""
import os
import pytest


def test_config_loads_local_mode(monkeypatch):
    monkeypatch.setenv("DEPLOY_ENV", "local")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Re-import to pick up new env
    from config import AppConfig
    cfg = AppConfig()
    assert cfg.is_local is True
    assert cfg.is_aws is False
    assert cfg.openai_api_key == "sk-test"


def test_config_rejects_invalid_deploy_env(monkeypatch):
    monkeypatch.setenv("DEPLOY_ENV", "invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from config import AppConfig
    with pytest.raises(ValueError, match="Invalid DEPLOY_ENV"):
        AppConfig()


def test_config_requires_api_key_in_local(monkeypatch):
    monkeypatch.setenv("DEPLOY_ENV", "local")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from config import AppConfig
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        AppConfig()
