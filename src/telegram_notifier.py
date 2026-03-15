import logging
import time

from urllib3.exceptions import HTTPError

from src.models import Notification
from telepot import Bot  # type: ignore


logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Class that sends notifications to a Telegram user."""

    def __init__(self, bot: Bot):
        self.bot = bot

    def send_notification(
        self, telegramId: str, notification: Notification, parse_mode: str = "Markdown"
    ) -> None:
        last_error: Exception | None = None

        # Network calls to Telegram can transiently fail (e.g. RemoteDisconnected).
        for attempt in range(1, 4):
            try:
                self.bot.sendMessage(telegramId, notification.message, parse_mode=parse_mode)
                return
            except HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Telegram send failed (attempt %s/3): %s", attempt, exc
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Unexpected Telegram send failure (attempt %s/3): %s",
                    attempt,
                    exc,
                )

            if attempt < 3:
                time.sleep(2 * attempt)

        if last_error is not None:
            raise last_error
