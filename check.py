import json
import msvcrt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ledger"))
sys.path.insert(0, str(ROOT / "ledger" / "hooks"))

from _store import load_records  # noqa: E402
from _text_safety import safe_input, setup_utf8_io  # noqa: E402
from remind import count_pending  # noqa: E402

STEPS = [
    ("确认需求意图", ROOT / "ledger" / "confirm_intent.py"),
    ("审查代码是否匹配需求", ROOT / "brain" / "review.py"),
    ("生成汇总报告", ROOT / "executor" / "report.py"),
]

START_COMMANDS = {"y", "核查"}
POLL_SECONDS = 2
HOOKS_DIR = ROOT / "ledger" / "hooks"


def hook_config():
    return {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python "{HOOKS_DIR / "log_user_prompt.py"}"',
                        "timeout": 15,
                    }
                ]
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Edit|Write|NotebookEdit",
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python "{HOOKS_DIR / "log_tool_use.py"}"',
                        "timeout": 15,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python "{HOOKS_DIR / "remind.py"}"',
                        "timeout": 15,
                    }
                ]
            }
        ],
    }


def merge_hooks(existing, new):
    existing.setdefault("hooks", {})
    for event, entries in new.items():
        existing["hooks"].setdefault(event, [])
        existing_commands = {
            h.get("command")
            for group in existing["hooks"][event]
            for h in group.get("hooks", [])
        }
        for entry in entries:
            entry_commands = {h.get("command") for h in entry.get("hooks", [])}
            if entry_commands & existing_commands:
                continue  # 已经配置过了，不重复加
            existing["hooks"][event].append(entry)
    return existing


def is_connected(project_path):
    """这个项目是不是已经接过监控 Hook 了。"""
    settings_path = project_path / ".claude" / "settings.json"
    if not settings_path.exists():
        return False
    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    existing_hooks = existing.get("hooks", {})
    for event, entries in hook_config().items():
        existing_commands = {
            h.get("command")
            for group in existing_hooks.get(event, [])
            for h in group.get("hooks", [])
        }
        wanted_commands = {
            h.get("command") for entry in entries for h in entry.get("hooks", [])
        }
        if not wanted_commands.issubset(existing_commands):
            return False
    return True


def connect_project(project_path):
    """把监控接到这个项目——写/合并 Hook 配置，不复制任何代码。"""
    settings_path = project_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        existing = {}

    merged = merge_hooks(existing, hook_config())
    settings_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"这个项目还没接过监控，已经帮你接上：{settings_path}")
    print("从现在开始新发生的对话/改动才会被记录——这之前已经聊过的内容不会被补录。")
    print(
        "如果这个项目里已经有一个正在运行的 Claude Code 会话，它可能不会自动发现这份新配置："
        "在那个会话里敲一下 /hooks 重新加载，或者直接重开一次，确认生效之后再继续。\n"
    )


def resolve_project_path():
    """开场问一句要锁定监控哪个项目文件夹——必须显式输入，不设默认值。
    不接受直接回车：误按回车会在没意识到的情况下锁定错项目（比如当前所在目录
    刚好不是你想查的那个），所以强制要求每次都明确打一遍路径。"""
    while True:
        entered = safe_input("请输入要监控的项目文件夹完整路径: ").strip().strip('"')
        if not entered:
            print("没有输入路径，不会用默认值，请重新输入。")
            continue
        path = Path(entered).resolve()
        if not path.is_dir():
            print(f"这个路径不存在：{path}，请重新输入。")
            continue
        return path


def snapshot(project_path):
    records = load_records(project_path=project_path)
    pending_messages, pending_changes, pending_reviews = count_pending(records)
    total_messages = sum(1 for r in records if r["record_type"] == "user_prompt_submit")
    total_changes = sum(1 for r in records if r["record_type"] == "post_tool_use")
    return (total_messages, total_changes, pending_messages, pending_changes, pending_reviews)


def format_line(s):
    total_messages, total_changes, pending_messages, pending_changes, pending_reviews = s
    return (
        f"累计 {total_messages} 条对话 / {total_changes} 次改动  |  "
        f"待处理: {pending_messages} 条新对话, {pending_changes} 次改动, {pending_reviews} 条待审查"
    )


def wait_for_space(seconds):
    """非阻塞地等一段时间，期间只要按下空格键就立刻返回 True。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b" ":
                return True
        time.sleep(0.05)
    return False


def watch_until_space(project_path):
    """实时刷新状态（不调用大模型），按空格键就返回，把控制权交还给 main() 的主循环。
    这个函数本身不退出程序——退出程序只能靠 Ctrl+C，在 main() 的最外层统一捕获。"""
    last = None
    while True:
        current = snapshot(project_path)
        if current != last:
            print(format_line(current))
            last = current
        if wait_for_space(POLL_SECONDS):
            print()
            return


def run_checks(project_path):
    for label, script in STEPS:
        print(f"\n===== {label} ({script.relative_to(ROOT)}) =====", flush=True)
        result = subprocess.run([sys.executable, "-u", str(script)], cwd=str(project_path))
        if result.returncode != 0:
            print(f"\n{label} 出错了（退出码 {result.returncode}），停在这一步。回到监控状态。")
            return


def main():
    setup_utf8_io()
    sys.stdout.reconfigure(line_buffering=True)

    project_path = resolve_project_path()

    if not is_connected(project_path):
        connect_project(project_path)

    print(f"锁定监控项目：{project_path}")
    print("监控已启动，会一直开着——除非按 Ctrl+C，否则不会自己退出。")
    print("不调用大模型，只是每几秒重新数一遍；准备核查了按空格键。\n")

    try:
        while True:
            watch_until_space(project_path)

            records = load_records(project_path=project_path)
            pending_messages, _pending_changes, pending_reviews = count_pending(records)
            if pending_messages == 0 and pending_reviews == 0:
                print("现在没有待处理的内容，继续监控...\n")
                continue

            choice = safe_input(
                f"要现在开始核查吗？输入 {'/'.join(START_COMMANDS)} 开始（会调用 DeepSeek），"
                "直接回车或输入别的内容就先不查，回到监控: "
            ).strip().lower()
            if choice not in START_COMMANDS:
                print("先不查，回到监控...\n")
                continue

            run_checks(project_path)
            print("\n核查结束，回到监控...\n")
    except KeyboardInterrupt:
        print("\n已退出监控。")


if __name__ == "__main__":
    main()
