import sys
from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent.parent / "ledger"
sys.path.insert(0, str(LEDGER_DIR))

from _store import load_memory, save_memory  # noqa: E402
from _text_safety import safe_input, setup_utf8_io  # noqa: E402

STATUS_LABEL = {
    "candidate": "候选（还没到 2 次，不会出现在审查里）",
    "confirmed": "已确认",
    "declined": "已回绝（不会再被 AI 引用，但保留记录）",
}


def main():
    setup_utf8_io()
    memory_list = load_memory()

    if not memory_list:
        print("这个项目还没有任何模式记忆。")
        return

    print(f"共 {len(memory_list)} 条模式记忆：\n")
    for i, m in enumerate(memory_list, 1):
        rounds = "、".join(f"第{r}轮" for r in m["rounds"])
        label = STATUS_LABEL.get(m["status"], m["status"])
        print(f"  {i}. [{label}] {m['pattern']}")
        print(f"     出现 {m['occurrences']} 次：{rounds}")

    choice = safe_input(
        "\n输入编号删除对应记录（比如 1,3），直接回车不删除任何一条: "
    ).strip()

    if not choice:
        print("没有删除任何记录。")
        return

    try:
        indices = {int(x.strip()) for x in choice.split(",") if x.strip()}
    except ValueError:
        print("输入格式不对，没有删除任何记录。")
        return

    to_delete = {i for i in indices if 1 <= i <= len(memory_list)}
    if not to_delete:
        print("没有匹配到有效编号，没有删除任何记录。")
        return

    print("\n即将删除：")
    for i in sorted(to_delete):
        print(f"  - {memory_list[i - 1]['pattern']}")
    confirm = safe_input("\n确认删除吗？[回车]=确认，其他任意字符=取消: ").strip()
    if confirm != "":
        print("已取消，没有删除任何记录。")
        return

    remaining = [m for i, m in enumerate(memory_list, 1) if i not in to_delete]
    save_memory(remaining)
    print(f"\n已删除 {len(to_delete)} 条。")


if __name__ == "__main__":
    main()
