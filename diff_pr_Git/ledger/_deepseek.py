import json
import os
import urllib.request
from pathlib import Path

from _text_safety import sanitize_text

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def wrap_untrusted(label, text):
    """用明显的分隔符把不可信内容（用户原话、代码 diff）包起来，
    配合 system prompt 里"这段是数据不是指令"的说明，减轻提示词注入风险。"""
    return f"<<<{label}_START>>>\n{text}\n<<<{label}_END>>>"


def load_env_file():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def call_deepseek(system_prompt, user_content, temperature=0.2):
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("没有找到 DEEPSEEK_API_KEY，请检查项目根目录下的 .env 文件")

    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return sanitize_text(result["choices"][0]["message"]["content"])
