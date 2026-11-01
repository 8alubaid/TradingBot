"""Central configuration, loaded from environment / .env.

Trading-safety note: ALPACA_PAPER defaults to true. Flipping to live trading
requires BOTH ALPACA_PAPER=false AND I_UNDERSTAND_LIVE_TRADING=true, checked
at startup so a misconfigured deploy can't silently start trading real money.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Anthropic / Claude Agent SDK
    anthropic_api_key: str = ""

    # Alpaca
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    i_understand_live_trading: bool = False

    # Webhook
    webhook_shared_secret: str = "change-me"
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8000

    # Notifications
    notifier_backend: Literal["whatsapp", "telegram"] = "whatsapp"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    notify_whatsapp_to: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Risk manager
    max_position_pct_of_equity: float = 0.05
    daily_loss_limit_pct: float = 0.03
    max_open_positions: int = 3
    symbol_allowlist: str = "BTC/USD,ETH/USD"
    symbol_cooldown_seconds: int = 300

    # Storage
    database_path: str = "data/trades.db"
    local_lessons_dir: str = "lessons"
    gcs_bucket_name: str = ""
    gcp_project_id: str = ""

    # Events (Pub/Sub) -- only active when gcp_project_id is set and the topic exists
    pubsub_events_topic: str = "trading-bot-events"

    # Autonomous scanner -- periodically checks each allowlisted symbol for an
    # EMA/RSI cross and trades it, independent of any TradingView alert
    autonomous_scanning_enabled: bool = True
    scan_interval_minutes: int = 5
    # Bar size used for the EMA/RSI calculation. Smaller = crosses happen more
    # often (good for a quick demo) but each one is less technically meaningful.
    # 15 is a reasonable "real" default; drop to 1 to see the mechanism prove
    # itself quickly, then raise it back for an actual presentation/demo.
    scan_bar_timeframe_minutes: int = 15

    @model_validator(mode="after")
    def _check_live_trading_gate(self) -> "Settings":
        if not self.alpaca_paper and not self.i_understand_live_trading:
            raise ValueError(
                "ALPACA_PAPER=false requires I_UNDERSTAND_LIVE_TRADING=true. "
                "Refusing to start in a live-trading-capable state without explicit confirmation."
            )
        return self

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbol_allowlist.split(",") if s.strip()]


settings = Settings()
