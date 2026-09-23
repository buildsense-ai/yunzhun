"""Application settings and NetEase (163/126) provider presets."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

# NetEase servers. All access requires an authorization code (授权码), not the
# web-login password. IMAP additionally requires the RFC 2971 ID command.
# qiye163 = 网易企业邮箱/校园邮 (e.g. SYSU student mail @mail2.sysu.edu.cn)
PROVIDERS: dict[str, dict] = {
    "163": {
        "imap_host": "imap.163.com",
        "imap_port": 993,
        "pop3_host": "pop.163.com",
        "pop3_port": 995,
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "requires_id": True,
    },
    "126": {
        "imap_host": "imap.126.com",
        "imap_port": 993,
        "pop3_host": "pop.126.com",
        "pop3_port": 995,
        "smtp_host": "smtp.126.com",
        "smtp_port": 465,
        "requires_id": True,
    },
    "qiye163": {
        "imap_host": "imaphz.qiye.163.com",
        "imap_port": 993,
        "pop3_host": "pophz.qiye.163.com",
        "pop3_port": 995,
        "smtp_host": "smtphz.qiye.163.com",
        "smtp_port": 465,
        "requires_id": True,
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="YUNZHUN_", extra="ignore")

    app_name: str = "Yunzhun Mail Gateway"
    api_key: str = "dev-key-change-me"
    db_url: str = f"sqlite:///{BASE_DIR / 'yunzhun.db'}"
    encryption_key: str = ""  # Fernet key; empty -> auto-generate .fernet.key

    sync_enabled: bool = True
    sync_interval_seconds: int = 120
    flag_refresh_window: int = 200

    # Jev (TypeSafe System One) semantic judgment layer.
    # provider "vercel" routes through Vercel AI Gateway (free tier, model typesafe-ai/jev);
    # "typesafe" calls api.typesafe.ai directly (jev-latest).
    jev_provider: str = "vercel"
    jev_api_key: str = ""       # TypeSafe direct key (or TYPESAFE_API_KEY env)
    vercel_gateway_key: str = ""  # AI Gateway key (or AI_GATEWAY_API_KEY env)
    jev_model: str = ""          # override; defaults per provider

    # Automated pulls
    download_dir: str = "./downloads"

    # Full pipeline (fetch body -> judge -> gated pull) running in background
    pipeline_enabled: bool = True
    pipeline_interval_seconds: int = 60
    pipeline_batch_limit: int = 20

    # LLM fallback extraction: runs only when Jev flags delivery-ish AND regex
    # found nothing. Route through the Vercel AI Gateway (OpenAI-compatible).
    llm_model: str = ""  # e.g. "openai/gpt-4.1-mini"; empty = fallback disabled
    llm_api_key: str = ""  # defaults to vercel_gateway_key / AI_GATEWAY_API_KEY

    # Optional webhook: POSTed after every pipeline pull that downloaded files
    webhook_url: str = ""

    # IMAP IDLE real-time push (falls back to polling when unsupported)
    idle_enabled: bool = True
    idle_folder: str = "INBOX"
    idle_heartbeat_seconds: int = 1500  # re-issue IDLE every 25min (RFC 2177 <29min)


@lru_cache
def get_settings() -> Settings:
    return Settings()
