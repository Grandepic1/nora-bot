import logging
import sys
from typing import TextIO


class DebugLogger:
    def __init__(self, enabled: bool, stream: TextIO | None = None):
        self.enabled = enabled
        self._logger = logging.getLogger(f"nora.{id(self)}")
        self._logger.propagate = False
        self._logger.handlers.clear()
        self._logger.disabled = not enabled

        if enabled:
            handler = logging.StreamHandler(stream or sys.stderr)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s level=%(levelname)s %(message)s"
                )
            )
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.DEBUG)
        else:
            self._logger.setLevel(logging.CRITICAL + 1)

    @staticmethod
    def _format(event: str, fields: dict) -> str:
        parts = [f"event={event}"]

        for key, value in fields.items():
            parts.append(f"{key}={value!r}")

        return " ".join(parts)

    def event(self, event: str, **fields) -> None:
        if not self.enabled:
            return

        self._logger.debug(self._format(event, fields))

    def failure(self, event: str, error: BaseException, **fields) -> None:
        if not self.enabled:
            return

        fields["error_type"] = type(error).__name__
        self._logger.debug(self._format(event, fields))
