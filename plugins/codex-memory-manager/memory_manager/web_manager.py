"""Discover this library's local management page without starting a server."""

from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

from . import __version__
from .diagnostics import host_path


DEFAULT_PORT = 8765
DEFAULT_URL = "http://127.0.0.1:8765/"
MAX_RECORD_BYTES = 16_384


def registry_path(store):
    if getattr(store, "_uri", False):
        return None
    database = Path(store.db_path).expanduser().resolve()
    return database.with_name(database.name + ".web.json")


def _read_record(path):
    if path is None or path.is_symlink():
        return None
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_RECORD_BYTES:
            return None
        record = json.loads(raw.decode("utf-8"))
        return record if isinstance(record, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def register_server(store, port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("网页服务端口必须是 1 到 65535 的整数")
    record = {
        "service": "codex-memory-manager", "version": __version__,
        "instance_id": uuid.uuid4().hex, "pid": os.getpid(),
        "database": str(Path(store.db_path).expanduser().resolve()),
        "platform": os.name, "port": port,
        "url": f"http://127.0.0.1:{port}/",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    path = registry_path(store)
    if path is None:
        return record
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as output:
            temporary = output.name
            json.dump(record, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return record


def unregister_server(store, record):
    path = registry_path(store)
    current = _read_record(path)
    if current is not None and current.get("instance_id") == record.get("instance_id"):
        try:
            path.unlink()
        except OSError:
            pass


def _start_command(store, port):
    if getattr(store, "_uri", False):
        return None
    command = [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py"),
               "--db", str(Path(store.db_path).expanduser().resolve()),
               "serve", "--port", str(port)]
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _valid_record(store, record):
    if not isinstance(record, dict) or record.get("service") != "codex-memory-manager":
        return False
    port, identifier = record.get("port"), record.get("instance_id")
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
        return False
    if record.get("platform") not in {"posix", "nt"} or not isinstance(record.get("database"), str):
        return False
    try:
        return host_path(record["database"]).resolve() == Path(store.db_path).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return False


def _probe(record, timeout):
    # Use only the fixed loopback host and validated port. Never follow redirects
    # or request a URL supplied by the record or by an HTTP response.
    connection = http.client.HTTPConnection("127.0.0.1", record["port"], timeout=timeout)
    try:
        connection.request("GET", "/api/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            return False
        health = json.loads(body.decode("utf-8"))
        return (isinstance(health, dict) and health.get("service") == "codex-memory-manager"
                and health.get("instance_id") == record["instance_id"])
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def get_manager_status(store, timeout=0.5):
    result = {
        "url": DEFAULT_URL, "running": None, "verified": False, "status": "unknown",
        "message": "未发现本资料库的页面运行记录；这是默认地址，尚未验证服务已启动。",
        "start_command": _start_command(store, DEFAULT_PORT),
    }
    record = _read_record(registry_path(store))
    if not _valid_record(store, record):
        return result
    port = record["port"]
    result.update(url=f"http://127.0.0.1:{port}/",
                  start_command=_start_command(store, port),
                  version=record.get("version"))
    if _probe(record, timeout):
        result.update(running=True, verified=True, status="running",
                      message="已验证本资料库的管理页面正在运行，可打开链接管理记忆。")
    elif record["platform"] != os.name:
        result.update(status="unverified",
                      message="已找到另一个执行环境启动的页面地址；当前环境无法验证，请在启动页面的本机浏览器打开链接。")
    else:
        result.update(running=False, status="unavailable",
                      message="已记录页面地址，但未验证到本资料库的页面服务；请先启动或检查页面进程。")
    return result
