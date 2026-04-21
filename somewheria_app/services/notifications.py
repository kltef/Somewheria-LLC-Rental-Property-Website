import datetime
import html
import json
import re
import smtplib
from email.message import EmailMessage

from .console import get_console_logger


class NotificationService:
    def __init__(self, config, analytics) -> None:
        self.config = config
        self.analytics = analytics
        self.console = get_console_logger("notify")

    def send_email(self, subject: str, body: str, to: str | None = None) -> bool:
        app_password = self._email_password()
        if not app_password:
            self.console.warning("EMAIL_APP_PASSWORD is not configured; skipping email '%s'", subject)
            return False

        recipient = (to or self.config.email_recipient or "").strip()
        if not recipient or "@" not in recipient:
            self.console.warning("No valid recipient for email '%s' (to=%r); skipping.", subject, to)
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.email_sender
        message["To"] = recipient
        message.set_content(body)
        message.add_alternative(self._html_email_body(subject, body), subtype="html")

        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(self.config.email_sender, app_password)
                server.send_message(message)
            self.console.info("Sent email '%s' to %s", subject, recipient)
            return True
        except Exception as exc:
            self.console.error("Failed to send email '%s': %s", subject, exc)
            return False

    def _html_email_body(self, subject: str, body: str) -> str:
        escaped_subject = html.escape(subject)
        body_lines = [line.strip() for line in body.splitlines() if line.strip()]
        intro = html.escape(body_lines[0]) if body_lines else "There is a new update from Somewheria."
        details = "".join(
            f"<p style=\"margin:0 0 12px;font-size:14px;line-height:1.65;color:#5a4439;\">{html.escape(line)}</p>"
            for line in body_lines[1:]
        )
        if not details:
            details = (
                "<p style=\"margin:0 0 12px;font-size:14px;line-height:1.65;color:#5a4439;\">"
                "Open the dashboard or logs for the latest details."
                "</p>"
            )

        return f"""
<html>
  <body style="margin:0;padding:24px;font-family:Arial,sans-serif;background:#f7f1ea;color:#352118;">
    <div style="max-width:600px;margin:0 auto;background:#fffaf5;border-radius:24px;padding:30px;box-shadow:0 18px 36px rgba(62,42,32,0.14);border:1px solid #eedfd2;">
      <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:#efe1d4;color:#7a6257;font-size:11px;letter-spacing:2px;text-transform:uppercase;">
        Somewheria LLC
      </div>
      <h2 style="margin:16px 0 8px;font-size:24px;color:#3e2a20;">{escaped_subject}</h2>
      <p style="margin:0 0 18px;font-size:14px;line-height:1.65;color:#5a4439;">{intro}</p>
      <div style="background:#f7ede2;border:1px solid #e7d7c8;border-radius:18px;padding:18px 20px;">
        {details}
      </div>
      <div style="margin-top:20px;padding-top:16px;border-top:1px solid #f1e6db;">
        <p style="margin:0;font-size:12px;color:#7a6257;">This notification was sent automatically by the Somewheria management site.</p>
        <p style="margin:8px 0 0;font-size:12px;color:#7a6257;">Ekberg Properties admin tools</p>
      </div>
    </div>
  </body>
</html>
"""

    def _email_password(self) -> str:
        import os

        return os.getenv("EMAIL_APP_PASSWORD", "")

    def log_and_notify_error(self, subject: str, error_message: str) -> None:
        self.analytics.record_error()
        self.console.error("%s: %s", subject, error_message)
        self.send_email(subject, error_message)

    def notify_image_edit(self, image_urls: list[str]) -> None:
        self.send_email("Image Edited Notification", "The following image(s) have been edited:\n" + "\n".join(image_urls))

    def log_site_change(self, user_email: str, action: str, extra: dict | None = None) -> None:
        try:
            entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "user": user_email or "anonymous",
                "action": action,
                "extra": extra or {},
            }
            with self.config.change_log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.console.error("Failed to record site change '%s': %s", action, exc)

    def read_logs(self) -> list[dict]:
        entries = []
        if not self.config.log_file.exists():
            return entries
        ansi_escape = re.compile(r"\x1B\[[0-9;]*[mK]")
        with self.config.log_file.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if "|" in line:
                    pipe_parts = line.split("|", 3)
                    if len(pipe_parts) == 4:
                        timestamp, level, component, message = pipe_parts
                        message = f"[{component}] {message}"
                    else:
                        timestamp, level, message = "", "", line
                else:
                    legacy_parts = line.split(":", 2)
                    if len(legacy_parts) == 3:
                        timestamp, level, message = legacy_parts
                    else:
                        timestamp, level, message = "", "", line
                if level == "WARN":
                    level = "WARNING"
                if level == "CRIT":
                    level = "CRITICAL"
                entries.append(
                    {
                        "timestamp": timestamp or "Unknown",
                        "level": level,
                        "message": ansi_escape.sub("", message),
                    }
                )
        return list(reversed(entries[-500:]))
