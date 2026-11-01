"""Pluggable notifier: WhatsApp via Twilio Sandbox by default, Telegram Bot API as fallback.

Swap by setting NOTIFIER_BACKEND=whatsapp|telegram in .env -- no other code changes needed.
Falls back to a logging no-op notifier if the selected backend's credentials aren't set yet,
so local development doesn't require Twilio/Telegram to be configured.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from functools import lru_cache

import requests

from core.config import settings

logger = logging.getLogger("notify")


class Notifier(ABC):
    @abstractmethod
    def send(self, message: str) -> None: ...


class TwilioWhatsAppNotifier(Notifier):
    def __init__(self) -> None:
        from twilio.rest import Client

        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    def send(self, message: str) -> None:
        try:
            self._client.messages.create(
                from_=settings.twilio_whatsapp_from,
                to=f"whatsapp:{settings.notify_whatsapp_to}",
                body=message,
            )
        except Exception:
            logger.exception("Failed to send WhatsApp notification")


class TelegramNotifier(Notifier):
    def send(self, message: str) -> None:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": message},
                timeout=10,
            )
            if not resp.ok:
                logger.error(
                    "Telegram send failed: HTTP %s %s", resp.status_code, resp.text[:500]
                )
        except Exception:
            logger.exception("Failed to send Telegram notification")


class NullNotifier(Notifier):
    """Used when the selected backend's credentials aren't configured yet."""

    def send(self, message: str) -> None:
        logger.info("[notify:noop] %s", message.replace("\n", " | "))


@lru_cache(maxsize=1)
def get_notifier() -> Notifier:
    if settings.notifier_backend == "whatsapp":
        if settings.twilio_account_sid and settings.twilio_auth_token and settings.notify_whatsapp_to:
            return TwilioWhatsAppNotifier()
        logger.warning("WhatsApp notifier selected but Twilio credentials are incomplete; using no-op notifier")
        return NullNotifier()
    if settings.notifier_backend == "telegram":
        if settings.telegram_bot_token and settings.telegram_chat_id:
            return TelegramNotifier()
        logger.warning("Telegram notifier selected but credentials are incomplete; using no-op notifier")
        return NullNotifier()
    return NullNotifier()
