import io
import logging

from aitrader.redaction import RedactingFilter, redact_text


def test_redact_text_masks_token_fields_in_urls_and_json():
    line = 'GET /local/kite/callback?request_token=abc123&status=success {"access_token": "tok999"}'
    out = redact_text(line)
    assert "abc123" not in out and "tok999" not in out
    assert "request_token=***" in out


def test_filter_masks_known_secret_values_in_formatted_records():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(["my-api-secret-value"]))
    log = logging.getLogger("redaction-test")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("secret is %s", "my-api-secret-value")
    log.removeHandler(handler)
    assert "my-api-secret-value" not in stream.getvalue()
    assert "***" in stream.getvalue()
