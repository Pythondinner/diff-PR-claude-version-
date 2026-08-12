import sys
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent
LEDGER_DIR = BRAIN_DIR.parent / "ledger"
sys.path.insert(0, str(LEDGER_DIR))
sys.path.insert(0, str(BRAIN_DIR))

from _store import append_record, load_records  # noqa: E402
from _text_safety import safe_input, setup_utf8_io  # noqa: E402
from review import find_intent_snapshots, find_reviewed_snapshot_ids  # noqa: E402

PREVIEW_COUNT = 3


def preview(snapshot):
    round_number = snapshot["payload"].get("round_number")
    round_label = f"[第 {round_number} 轮] " if round_number else ""
    intents = snapshot["payload"]["confirmed_intents"]
    shown = "; ".join(intents[:PREVIEW_COUNT])
    more = f"（还有 {len(intents) - PREVIEW_COUNT} 条）" if len(intents) > PREVIEW_COUNT else ""
    return f"{round_label}{shown}{more}"


def main():
    setup_utf8_io()
    records = load_records()
    snapshots = find_intent_snapshots(records)
    reviewed_ids = find_reviewed_snapshot_ids(records)
    pending = [s for s in snapshots if s["id"] not in reviewed_ids]

    if not pending:
        print("没有待审查的需求快照，不需要清理。")
        return

    print(f"待审查的需求快照，共 {len(pending)} 条（不调用大模型，只是列出来给你挑，看的是你当初确认过的需求原文）：\n")
    for i, s in enumerate(pending, 1):
        print(f"  {i}. [{s['logged_at']}] {preview(s)}")

    choice = safe_input(
        "\n输入要清除的编号（比如 1,3），或输入 all 清除全部，直接回车不清除任何一条: "
    ).strip().lower()

    if not choice:
        print("没有清除任何记录。")
        return

    if choice == "all":
        targets = pending
    else:
        try:
            indices = {int(x.strip()) for x in choice.split(",") if x.strip()}
        except ValueError:
            print("输入格式不对，没有清除任何记录。")
            return
        targets = [pending[i - 1] for i in sorted(indices) if 1 <= i <= len(pending)]

    if not targets:
        print("没有匹配到有效编号，没有清除任何记录。")
        return

    print("\n即将清除：")
    for s in targets:
        print(f"  - {preview(s)}")
    confirm = safe_input("\n确认清除吗？[回车]=确认，其他任意字符=取消: ").strip()
    if confirm != "":
        print("已取消，没有清除任何记录。")
        return

    for s in targets:
        append_record("review_result", {
            "intent_snapshot_id": s["id"],
            "round_number": s["payload"].get("round_number"),
            "reviewed_change_ids": [],
            "ai_verdict": None,
            "confirmed_verdict": {"overall": "skipped", "summary": "用户批量清除，未经 DeepSeek 审查"},
            "confirmation_method": "bulk_skipped",
        })
    print(f"\n已清除 {len(targets)} 条，全程没有调用任何大模型。")


if __name__ == "__main__":
    main()
