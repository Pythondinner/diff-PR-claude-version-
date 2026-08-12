import json
import sys
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LEDGER_DIR))

from _store import load_records  # noqa: E402


def read_cwd_from_stdin():
    raw = sys.stdin.buffer.read().decode("utf-8")
    if not raw.strip():
        return None
    try:
        return json.loads(raw).get("cwd")
    except json.JSONDecodeError:
        return None


def count_pending(records):
    last_snapshot_index = -1
    for i, r in enumerate(records):
        if r["record_type"] == "intent_snapshot":
            last_snapshot_index = i
    pending_messages = sum(
        1 for r in records[last_snapshot_index + 1:]
        if r["record_type"] == "user_prompt_submit"
    )

    last_review_index = -1
    for i, r in enumerate(records):
        if r["record_type"] == "review_result":
            last_review_index = i
    pending_changes = sum(
        1 for r in records[last_review_index + 1:]
        if r["record_type"] == "post_tool_use"
    )

    snapshots = [r for r in records if r["record_type"] == "intent_snapshot"]
    reviewed_ids = {
        r["payload"]["intent_snapshot_id"]
        for r in records
        if r["record_type"] == "review_result"
    }
    pending_reviews = sum(1 for s in snapshots if s["id"] not in reviewed_ids)

    return pending_messages, pending_changes, pending_reviews


REMIND_EVERY = 5


def main():
    cwd = read_cwd_from_stdin()
    records = load_records(project_path=cwd)
    pending_messages, pending_changes, pending_reviews = count_pending(records)

    # 固定节奏：待确认对话数正好是 REMIND_EVERY 的倍数才提示一次，
    # 不是"只要有积压就每轮都提示"——避免刷屏，同时保持"只提醒、不自动审查"。
    if pending_messages == 0 or pending_messages % REMIND_EVERY != 0:
        return

    parts = [f"{pending_messages} 条新对话"]
    if pending_changes:
        parts.append(f"{pending_changes} 次代码改动")
    if pending_reviews:
        parts.append(f"{pending_reviews} 条需求待审查")

    # 这里故意用 json.dumps 默认的 ensure_ascii=True，让中文变成 \uXXXX 转义、
    # 输出的字节全是 ASCII——绕开这台机器上反复出现的"stdout 不是 UTF-8"编码坑，
    # 不用再给这个脚本额外调 setup_utf8_io()。
    message = "[监控 Agent] " + "，".join(parts) + "——想核查随时运行 python check.py"
    print(json.dumps({"systemMessage": message}))


if __name__ == "__main__":
    main()
