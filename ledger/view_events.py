import json
import sys

from _store import events_file_for
from _text_safety import setup_utf8_io

setup_utf8_io()

MAX_STR_LEN = 300


def truncate(obj):
    if isinstance(obj, str) and len(obj) > MAX_STR_LEN:
        return obj[:MAX_STR_LEN] + f"...[还有 {len(obj) - MAX_STR_LEN} 字符省略]"
    if isinstance(obj, dict):
        return {k: truncate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [truncate(v) for v in obj]
    return obj


def main():
    # 默认看当前所在项目的记录；也可以传一个项目路径，看别的项目
    project_path = sys.argv[1] if len(sys.argv) > 1 else None
    events_file = events_file_for(project_path)
    if not events_file.exists():
        print(f"还没有记录：{events_file}")
        return
    with events_file.open("r", encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]
    for i, line in enumerate(lines, 1):
        record = json.loads(line)
        print(f"--- 第 {i} 条 | {record['logged_at']} | {record['record_type']} ---")
        print(json.dumps(truncate(record), ensure_ascii=False, indent=2))
        print()


if __name__ == "__main__":
    main()
