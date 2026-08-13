import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent
DATA_ROOT = HUB_DIR / "data" / "projects"


def sanitize_project_path(path_str):
    """把项目文件夹路径变成安全的目录名。可读前缀部分照抄 Claude Code 自己
    ~/.claude/projects/ 底下用的那套（非字母数字字符全部换成短横线），但这个规则
    对中文之类的非 ASCII 字符没有区分度——不同的中文项目名可能被压成同一串短横线，
    数据会撞在一起。所以额外加一段基于完整原始路径算出来的短 hash 后缀保证唯一性，
    可读前缀负责"人眼能不能看出个大概"，hash 后缀负责"绝对不会撞"，两者分工。"""
    resolved = str(Path(path_str).resolve())
    readable = re.sub(r"[^a-zA-Z0-9]", "-", resolved)
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def events_file_for(project_path=None):
    """project_path 不给的话，用当前运行这个脚本时所在的文件夹（Path.cwd()）。
    每个项目的数据存在自己独立的子目录里，互不干扰。"""
    project_path = project_path or Path.cwd()
    folder = DATA_ROOT / sanitize_project_path(project_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "events.jsonl"


def load_records(project_path=None):
    events_file = events_file_for(project_path)
    if not events_file.exists():
        return []
    records = []
    with events_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_record(record_type, payload, project_path=None):
    events_file = events_file_for(project_path)
    record = {
        "id": str(uuid.uuid4()),
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "record_type": record_type,
        "payload": payload,
    }
    with events_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def memory_file_for(project_path=None):
    """Brain 的模式记忆，跟 events.jsonl 存在同一个项目分区目录下，
    但这个文件是可变的（次数会累加），不是追加写的账本。"""
    project_path = project_path or Path.cwd()
    folder = DATA_ROOT / sanitize_project_path(project_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "memory.json"


def load_memory(project_path=None):
    memory_file = memory_file_for(project_path)
    if not memory_file.exists():
        return []
    with memory_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_memory(memory_list, project_path=None):
    memory_file = memory_file_for(project_path)
    with memory_file.open("w", encoding="utf-8") as f:
        json.dump(memory_list, f, ensure_ascii=False, indent=2)
