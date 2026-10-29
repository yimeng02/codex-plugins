"""Consent-gated setup of the global memory reader instruction."""

import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from .catalog import resolve_codex_home
from .store import StoreError


BEGIN_READER = "<!-- codex-memory-manager:reader:begin -->"
END_READER = "<!-- codex-memory-manager:reader:end -->"
READER_RULE = """开始任务时，使用 memory-manager 读取全局启用记忆；
涉及具体项目、技能或插件时，按准确作用域和目标检索相关记忆。
仅应用与当前任务相关的内容；工具不可用时如实说明。
"""
FIRST_USE_PROMPT = "是否在全局 AGENTS.md 中加入记忆读取规则，让新聊天主动读取适用记忆？可选择加入、暂不加入或稍后。"


def global_agents_path(codex_home=None):
    home = Path(codex_home) if codex_home is not None else resolve_codex_home()
    home = home.expanduser().absolute()
    override = home / "AGENTS.override.md"
    if override.is_symlink():
        raise StoreError("全局 AGENTS.override.md 是符号链接，请先检查实际指令文件")
    if override.is_file() and override.read_bytes().strip():
        return override
    return home / "AGENTS.md"


def reader_document(original):
    if original.count(BEGIN_READER) != original.count(END_READER) or original.count(BEGIN_READER) > 1:
        raise StoreError("记忆读取规则标记不完整或重复，请先检查全局指令文件")
    block = BEGIN_READER + "\n" + READER_RULE.rstrip() + "\n" + END_READER
    if BEGIN_READER in original:
        start, end = original.index(BEGIN_READER), original.index(END_READER)
        if end < start:
            raise StoreError("记忆读取规则标记顺序无效")
        return original[:start] + block + original[end + len(END_READER):]
    separator = "\n\n" if original and not original.endswith("\n\n") else ""
    return original + separator + block + "\n"


def setup_global_rule(store, action="preview", confirmed=False, expected_sha256=None, expected_path=None, codex_home=None):
    if action not in {"preview", "apply", "decline", "defer"}:
        raise StoreError("首次配置 action 必须是 preview、apply、decline 或 defer")
    if not isinstance(confirmed, bool):
        raise StoreError("confirmed 必须是布尔值")
    if expected_sha256 is not None and not isinstance(expected_sha256, str):
        raise StoreError("expected_sha256 必须是预览返回的字符串")
    if expected_path is not None and not isinstance(expected_path, str):
        raise StoreError("expected_path 必须是预览返回的 path 字符串")
    if action in {"decline", "defer"}:
        decision = "declined" if action == "decline" else "deferred"
        return {"applied": False, "application": store.set_onboarding_decision(decision), "note": "已记录选择，未修改全局指令；以后可主动调用 memory_setup。"}
    if action == "apply" and confirmed is not True:
        raise StoreError("请先向用户展示预览并获得明确同意，再设置 confirmed=true；当前未写入全局指令")
    if action == "apply" and expected_sha256 is None:
        raise StoreError("应用需要最近一次 preview 返回的 expected_sha256")
    if action == "apply" and expected_path is None:
        raise StoreError("应用需要最近一次 preview 返回的 path 作为 expected_path")
    path = global_agents_path(codex_home)
    if action == "apply" and expected_path != str(path):
        raise StoreError("生效的全局指令文件已变化，请重新预览后再应用", 409)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise StoreError("全局指令目标必须是普通文件，不能是符号链接或目录")
    original_bytes = path.read_bytes() if path.is_file() else b""
    digest = hashlib.sha256(original_bytes).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise StoreError("全局指令已发生变化，请重新预览后再应用", 409)
    try:
        original = original_bytes.decode("utf-8")
    except UnicodeError as error:
        raise StoreError("全局指令不是 UTF-8 文本，请先检查原文件") from error
    newline = "\r\n" if b"\r\n" in original_bytes else "\n"
    updated = reader_document(original.replace("\r\n", "\n")).replace("\n", newline)
    result = {"path": str(path), "sha256": digest, "rule": READER_RULE, "content": updated, "applied": False, "already_configured": updated.encode("utf-8") == original_bytes, "prompt": FIRST_USE_PROMPT, "application": store.application_status()}
    if action == "preview":
        return result
    # This is the only branch that may create a directory or write AGENTS.
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    temporary = None
    try:
        if updated.encode("utf-8") != original_bytes:
            if path.exists():
                with tempfile.NamedTemporaryFile(prefix=path.name + ".memory-reader-backup-", suffix=".md", dir=path.parent, delete=False) as output:
                    output.write(original_bytes)
                    backup = output.name
            with tempfile.NamedTemporaryFile(prefix=path.name + ".memory-reader-", dir=path.parent, delete=False) as output:
                temporary = output.name
                output.write(updated.encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
            if path.exists():
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            # Re-resolve the effective file as an override may have appeared.
            if global_agents_path(codex_home) != path or path.is_symlink():
                raise StoreError("生效的全局指令文件已变化，请重新预览", 409)
            current = path.read_bytes() if path.exists() else b""
            if hashlib.sha256(current).hexdigest() != digest:
                raise StoreError("全局指令已发生变化，请重新预览", 409)
            os.replace(temporary, path)
            temporary = None
        result.update(applied=True, backup=backup, note="全局读取规则已配置；请在新聊天验证加载。记忆正文仍由插件按需读取。")
        try:
            result["application"] = store.set_onboarding_decision("configured")
        except (StoreError, OSError, sqlite3.Error) as error:
            # The file write has happened: never report it as an untouched file.
            result.update(application_recorded=False, warning="规则已写入，首次配置状态记录失败：" + str(error))
        return result
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
