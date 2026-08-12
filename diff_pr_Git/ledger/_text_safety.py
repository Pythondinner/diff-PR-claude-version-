import functools
import re
import sys
import unicodedata

# 编码错误产生的孤立代理字符（我们已经踩过两次的那种 UnicodeEncodeError 的病根）
_SURROGATE_RE = re.compile("[\ud800-\udfff]")

# 常见的"看不见的乱码"：BOM、零宽空格/连接符，多来自网页/Word 复制粘贴
_INVISIBLE_CHARS = ("﻿", "​", "‌", "‍")


def setup_utf8_io():
    """在脚本入口调用一次：把 stdin/stdout 显式钉死成 UTF-8，
    不依赖 Windows 系统区域设置（这是根治，不是清洗）。"""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def sanitize_text(value):
    """兜底清洗：只处理"意料之外"的输入（用户粘贴带来的隐形字符、
    编码错误残留的孤立代理字符），不负责修正读取方式本身的错误。"""
    if not isinstance(value, str):
        return value
    value = _SURROGATE_RE.sub("", value)
    for ch in _INVISIBLE_CHARS:
        value = value.replace(ch, "")
    return unicodedata.normalize("NFC", value)


def sanitize(value):
    """递归清洗：dict/list 里嵌套的所有字符串都会被处理，
    用于清理整个 Hook payload 或 API 返回结果，而不只是单个字符串。"""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def clean_io(func):
    """装饰器：清洗被装饰函数的返回值。用在"数据进入系统的入口"——
    读 stdin、读用户输入、读外部 API 返回——而不是用来掩盖读取方式写错的问题。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return sanitize(func(*args, **kwargs))

    return wrapper


def safe_input(prompt=""):
    """input() 的安全版本：读取后立即清洗，替代裸用 input()。"""
    return sanitize_text(input(prompt))
