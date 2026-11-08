"""JSON logging setup."""

import io
import json
import logging

from roost.observability.logs import configure_logging


def test_noisy_libraries_log_only_warnings_and_lines_are_json() -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    stream = io.StringIO()
    try:
        configure_logging("INFO", stream)
        logging.getLogger("httpx2").info("HTTP Request: GET https://huggingface.co/...")
        logging.getLogger("huggingface_hub.utils._http").info("download step")
        logging.getLogger("roost.api").info("Kiosk ready")
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["message"] for line in lines] == ["Kiosk ready"]
    assert lines[0]["level"] == "INFO"
