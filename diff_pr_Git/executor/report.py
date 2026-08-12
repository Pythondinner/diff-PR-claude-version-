import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent.parent / "ledger"
sys.path.insert(0, str(LEDGER_DIR))

from _store import load_memory, load_records, sanitize_project_path  # noqa: E402
from _text_safety import setup_utf8_io  # noqa: E402

REPORTS_ROOT = Path(__file__).resolve().parent / "reports"


def report_file_for(project_path=None):
    project_path = project_path or Path.cwd()
    folder = REPORTS_ROOT / sanitize_project_path(project_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "report.md"

STATUS_LABEL = {
    "matched": "完全匹配",
    "partial": "部分匹配",
    "missing": "没做到",
    "skipped": "已跳过（用户认为不需要审查）",
}
NEEDS_ATTENTION_STATUSES = {"partial", "missing"}


def file_link(path_str):
    return "file:///" + path_str.replace("\\", "/")


def collect_reviews(records):
    by_id = {r["id"]: r for r in records}
    reviews = []
    for r in records:
        if r["record_type"] != "review_result":
            continue
        payload = r["payload"]
        verdict = payload.get("confirmed_verdict") or payload.get("verdict") or {}

        changes = []
        seen_files = set()
        for change_id in payload.get("reviewed_change_ids", []):
            change = by_id.get(change_id)
            if not change:
                continue
            cp = change["payload"]
            file_path = cp.get("tool_input", {}).get("file_path")
            tool_name = cp.get("tool_name", "?")
            if file_path and file_path not in seen_files:
                seen_files.add(file_path)
                changes.append({"file_path": file_path, "tool_name": tool_name})

        reviews.append({
            "logged_at": r["logged_at"],
            "round_number": payload.get("round_number"),
            "overall": verdict.get("overall", "未知"),
            "summary": verdict.get("summary", ""),
            "restated_intents": verdict.get("restated_intents", []),
            "per_intent": verdict.get("per_intent", []),
            "matched_pattern_ids": verdict.get("matched_pattern_ids", []),
            "changes": changes,
        })
    reviews.sort(key=lambda x: x["logged_at"], reverse=True)
    return reviews


def render_review(review, memory_by_id):
    label = STATUS_LABEL.get(review["overall"], review["overall"])
    round_label = f"第 {review['round_number']} 轮 — " if review["round_number"] else ""
    lines = [f"### {round_label}{review['logged_at']} — {label}"]

    if review["summary"]:
        lines.append(f"\n{review['summary']}")

    if review["restated_intents"]:
        lines.append("\n**AI 理解到的需求：**")
        for item in review["restated_intents"]:
            lines.append(f"- {item}")

    if review["matched_pattern_ids"]:
        lines.append("\n**引用了历史模式：**")
        for pid in review["matched_pattern_ids"]:
            m = memory_by_id.get(pid)
            if m:
                rounds = "、".join(f"第{r}轮" for r in m["rounds"])
                lines.append(f"- {m['pattern']}（出现在 {rounds}）")

    lines.append("\n**逐条判断：**")
    for item in review["per_intent"]:
        item_label = STATUS_LABEL.get(item.get("status"), item.get("status"))
        lines.append(f"- [{item_label}] {item.get('intent')}")
        lines.append(f"  - 理由: {item.get('reason')}")

    if review["changes"]:
        lines.append(
            "\n**涉及的代码改动**"
            "（链接打开的是文件*现在*的样子，不是审查那一刻的历史 diff——"
            "要看精确的历史改动，用 `python ledger/view_events.py` 查）："
        )
        for c in review["changes"]:
            name = Path(c["file_path"]).name
            lines.append(f"- [{name}]({file_link(c['file_path'])})（{c['tool_name']}）")

    return "\n".join(lines)


def main():
    setup_utf8_io()
    records = load_records()
    reviews = collect_reviews(records)
    memory_by_id = {m["id"]: m for m in load_memory()}

    if not reviews:
        print("还没有任何审查结果，先跑 brain/review.py 生成审查结果。")
        return

    counts = Counter(r["overall"] for r in reviews)
    needs_attention = [r for r in reviews if r["overall"] in NEEDS_ATTENTION_STATUSES]
    archived = [r for r in reviews if r["overall"] not in NEEDS_ATTENTION_STATUSES]

    lines = [
        "# Claude Code 需求核验报告",
        "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"共 {len(reviews)} 次审查："
        f"{counts.get('matched', 0)} 完全匹配、"
        f"{counts.get('partial', 0)} 部分匹配、"
        f"{counts.get('missing', 0)} 没做到、"
        f"{counts.get('skipped', 0)} 已跳过",
    ]

    if needs_attention:
        lines.append("")
        lines.append("## 需要关注")
        for r in needs_attention:
            lines.append("")
            lines.append(render_review(r, memory_by_id))

    if archived:
        lines.append("")
        lines.append("## 已完全匹配 / 已跳过（存档）")
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>展开查看</summary>")
        for r in archived:
            lines.append("")
            lines.append(render_review(r, memory_by_id))
        lines.append("")
        lines.append("</details>")

    report_file = report_file_for()
    report_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"报告已生成: {report_file}，"
        f"共 {len(reviews)} 条审查记录（{len(needs_attention)} 条需要关注）。"
    )


if __name__ == "__main__":
    main()
