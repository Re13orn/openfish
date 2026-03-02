"""Authorization helpers."""

from src.config import AppConfig


def is_allowed_user(config: AppConfig, telegram_user_id: str) -> bool:
    return telegram_user_id in config.allowed_telegram_user_ids
