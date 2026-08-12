import json
import sys
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LEDGER_DIR))
from _store import append_record  # noqa: E402
from _text_safety import clean_io  # noqa: E402


@clean_io
def read_stdin_json():
    raw = sys.stdin.buffer.read().decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def append_event(record_type, payload):
    append_record(record_type, payload, project_path=payload.get("cwd"))
