"""Expo Push Notification service for the mobile API layer.

Sends push notifications to registered Expo push tokens. No APNs/FCM
credentials are required — Expo's push API handles delivery on both platforms.

Push tokens are stored per-user in push_tokens.json as {email: [token, ...]}.
Tokens follow the format "ExponentPushToken[...]" or "ExpoPushToken[...]".
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import requests

from .console import get_console_logger

if TYPE_CHECKING:
    from ..config import AppConfig

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_MAX_BATCH_SIZE = 100
_TOKEN_PREFIXES = ("ExponentPushToken[", "ExpoPushToken[")


def _is_valid_expo_token(token: str) -> bool:
    return isinstance(token, str) and any(token.startswith(p) for p in _TOKEN_PREFIXES)


class PushNotificationService:
    def __init__(self, config: "AppConfig", storage) -> None:
        self.config = config
        self.storage = storage
        self.logger = get_console_logger("push_notifications")

    def _load_tokens(self) -> dict[str, list[str]]:
        data = self.storage.load_json_file(self.config.push_tokens_file, {}, expected_type=dict)
        return data if isinstance(data, dict) else {}

    def _save_tokens(self, tokens: dict[str, list[str]]) -> None:
        self.storage.save_json_file(self.config.push_tokens_file, tokens)

    def register_token(self, email: str, token: str) -> None:
        if not email or not _is_valid_expo_token(token):
            self.logger.warning("Ignored invalid push token registration for %s", email)
            return
        email = email.lower()
        tokens = self._load_tokens()
        user_tokens = tokens.get(email, [])
        if token not in user_tokens:
            user_tokens.append(token)
            tokens[email] = user_tokens
            self._save_tokens(tokens)

    def remove_token(self, email: str, token: str) -> None:
        email = email.lower()
        tokens = self._load_tokens()
        user_tokens = tokens.get(email, [])
        if token in user_tokens:
            user_tokens.remove(token)
            tokens[email] = user_tokens
            self._save_tokens(tokens)

    def send_to_user(
        self,
        email: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> bool:
        if not email:
            return False
        all_tokens = self._load_tokens()
        user_tokens = all_tokens.get(email.lower(), [])
        if not user_tokens:
            return False
        thread = threading.Thread(
            target=self._send_batch,
            args=(email.lower(), user_tokens, title, body, data or {}),
            daemon=True,
        )
        thread.start()
        return True

    def _send_batch(
        self,
        email: str,
        tokens: list[str],
        title: str,
        body: str,
        data: dict,
    ) -> None:
        messages = [
            {"to": token, "title": title, "body": body, "data": data}
            for token in tokens
            if _is_valid_expo_token(token)
        ]
        if not messages:
            return

        for i in range(0, len(messages), _MAX_BATCH_SIZE):
            batch = messages[i : i + _MAX_BATCH_SIZE]
            try:
                resp = requests.post(
                    _EXPO_PUSH_URL,
                    json=batch,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                self._handle_receipts(email, tokens, result.get("data", []))
            except Exception as exc:
                self.logger.warning("Expo push send failed for %s: %s", email, exc)

    def _handle_receipts(self, email: str, tokens: list[str], receipts: list[dict]) -> None:
        for token, receipt in zip(tokens, receipts):
            if isinstance(receipt, dict) and receipt.get("status") == "error":
                details = receipt.get("details", {})
                if isinstance(details, dict) and details.get("error") == "DeviceNotRegistered":
                    self.logger.info("Removing unregistered push token for %s", email)
                    self.remove_token(email, token)
