from __future__ import annotations

import logging
import re
from typing import Iterable

_FIELD_RE = re.compile(
    r"(?i)(access_token|api_secret|request_token|enctoken|checksum)(['\"]?\s*[:=]\s*['\"]?)([^'\"&\s,}]+)"
)


def redact_text(text: str) -> str:
    return _FIELD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 4]

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return redact_text(text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        return True


def configure_logging(secrets: Iterable[str]) -> None:
    """Root handler with redaction. Run uvicorn with log_config=None so its loggers propagate here."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = RedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
