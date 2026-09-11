import io
import unittest

from app.debug_logging import DebugLogger


class DebugLoggerTests(unittest.TestCase):
    def test_disabled_logger_emits_nothing(self):
        output = io.StringIO()
        logger = DebugLogger(False, stream=output)

        logger.event("test.event", value="visible")
        logger.failure("test.failure", RuntimeError("secret"))

        self.assertEqual(output.getvalue(), "")

    def test_enabled_logger_emits_safe_key_value_fields(self):
        output = io.StringIO()
        logger = DebugLogger(True, stream=output)

        logger.event("test.event", duration_ms=12)
        logger.failure("test.failure", RuntimeError("secret"))

        rendered = output.getvalue()
        self.assertIn("event=test.event", rendered)
        self.assertIn("duration_ms=12", rendered)
        self.assertIn("event=test.failure", rendered)
        self.assertIn("error_type='RuntimeError'", rendered)
        self.assertNotIn("secret", rendered)


if __name__ == "__main__":
    unittest.main()
