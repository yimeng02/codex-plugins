"""Check packaged skills and a real MCP subprocess without changing memories."""

import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from . import __version__


REQUIRED_TOOLS = {"memory_snapshot", "memory_search", "memory_create", "memory_update", "memory_bulk", "memory_status", "memory_plugins", "memory_refresh", "memory_setup"}
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


class DiagnosticError(RuntimeError):
    pass


def host_path(value):
    value = str(value)
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", value):
        drive, remainder = ntpath.splitdrive(value)
        return Path("/mnt") / drive[0].lower() / remainder.lstrip("\\/").replace("\\", "/")
    if os.name == "nt":
        match = re.match(r"^/mnt/([A-Za-z])/(.*)$", value)
        if match:
            return Path(match[1].upper() + ":\\" + match[2].replace("/", "\\"))
    return Path(value).expanduser()


def is_foreign_windows_command(command, windows=None):
    is_windows = os.name == "nt" if windows is None else windows
    return not is_windows and bool(re.match(r"^[A-Za-z]:[\\/]", command))


def read_object(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise DiagnosticError("无法读取 JSON 配置 " + str(path) + "：" + str(error)) from error
    if not isinstance(data, dict):
        raise DiagnosticError("JSON 配置顶层应为对象：" + str(path))
    return data


def check_skill(plugin_root):
    path = Path(plugin_root) / "skills" / "memory-manager" / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise DiagnosticError("安装缓存缺少技能文件：" + str(path)) from error
    if not text.startswith("---") or not re.search(r"^name:\s*memory-manager\s*$", text, re.MULTILINE):
        raise DiagnosticError("技能文件缺少预期的 name: memory-manager")
    return str(path)


def _contained_path(value, root):
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
        path.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise DiagnosticError("MCP 路径必须保留在指定的插件目录内：" + str(value)) from error
    return path


def _portable_environment(environment, windows=None):
    windows = os.name == "nt" if windows is None else windows
    normalized = {}
    for key, value in environment.items():
        name = key.upper() if windows else key
        if name in ("PLUGIN_ROOT", "PLUGIN_DATA"):
            raise DiagnosticError("portable MCP env 不得覆盖保留变量：" + key)
        if name in normalized:
            raise DiagnosticError("portable MCP env 存在大小写重复的变量：" + key)
        normalized[name] = value
    return normalized


def _portable_launch_spec(root, config, plugin_data_root):
    # Match Codex's codex-mcp/src/agent_plugin_config.rs before trying to spawn.
    # A subprocess accepting a command does not imply the plugin loader accepts it.
    unknown = set(config) - {"$schema", "mcpServers"}
    if unknown:
        raise DiagnosticError("portable MCP 配置包含未知字段：" + ", ".join(sorted(unknown)))
    if config.get("$schema") != MCP_SCHEMA:
        raise DiagnosticError("portable MCP $schema 必须为 " + MCP_SCHEMA)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise DiagnosticError("portable MCP mcpServers 必须是对象")
    server = servers.get("memory-manager")
    if not isinstance(server, dict):
        raise DiagnosticError("mcp.json 未声明 memory-manager 服务")
    unknown = set(server) - {"type", "command", "args", "env", "cwd"}
    if unknown:
        raise DiagnosticError("portable MCP stdio 服务包含未知字段：" + ", ".join(sorted(unknown)))
    if server.get("type") != "stdio":
        raise DiagnosticError("memory-manager 的 portable MCP type 必须是 stdio")
    command, arguments, environment, cwd = _server_fields(server, "${PLUGIN_ROOT}")
    environment = _portable_environment(environment)
    bare = "/" not in command and "\\" not in command
    if os.name == "nt" and ntpath.splitdrive(command)[0]:
        bare = False
    relative = command.startswith("./") and bool(command[2:]) and "\\" not in command
    if "${PLUGIN_ROOT}" in command or "${PLUGIN_DATA}" in command or not (bare or relative):
        raise DiagnosticError("portable MCP command 只能使用裸命令名或插件包内的 ./ 路径；不能使用绝对路径或占位符命令")

    data_root = Path(plugin_data_root).expanduser().resolve() if plugin_data_root is not None else None
    values = [cwd] + arguments + list(environment.values())
    if data_root is None and any("${PLUGIN_DATA}" in value for value in values):
        raise DiagnosticError("配置引用了 ${PLUGIN_DATA}，请明确指定 plugin_data_root；它不是记忆数据库目录")
    replacements = {"${PLUGIN_ROOT}": str(root)}
    if data_root is not None:
        replacements["${PLUGIN_DATA}"] = str(data_root)

    def expand(value):
        return re.sub(r"\$\{PLUGIN_(?:ROOT|DATA)\}", lambda match: replacements[match[0]], value)

    cwd_root = None
    if cwd == "./" or (cwd.startswith("./") and cwd[2:] and "\\" not in cwd):
        cwd_root = root
    for placeholder, allowed_root in (("${PLUGIN_ROOT}", root), ("${PLUGIN_DATA}", data_root)):
        if cwd == placeholder or (cwd.startswith(placeholder + "/") and "\\" not in cwd):
            cwd_root = allowed_root
    if cwd_root is None:
        raise DiagnosticError("portable MCP cwd 只能省略，或使用包内 ./、${PLUGIN_ROOT}、${PLUGIN_DATA} 路径；不能使用 . 或任意绝对路径")
    resolved_cwd = _contained_path(expand(cwd), cwd_root)
    if relative:
        command = str(_contained_path(command, root))
    environment = {key: expand(value) for key, value in environment.items()}
    environment["PLUGIN_ROOT"] = str(root)
    if data_root is not None:
        environment["PLUGIN_DATA"] = str(data_root)
    return command, [expand(value) for value in arguments], environment, str(resolved_cwd)


def _server_fields(server, default_cwd):
    command = server.get("command")
    arguments = server.get("args", [])
    environment = server.get("env", {})
    cwd = server.get("cwd", default_cwd)
    if not isinstance(command, str) or not command.strip():
        raise DiagnosticError("MCP 缺少可执行命令")
    if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
        raise DiagnosticError("MCP args 必须是字符串数组")
    if not isinstance(environment, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items()):
        raise DiagnosticError("MCP env 必须是字符串映射")
    if not isinstance(cwd, str):
        raise DiagnosticError("MCP cwd 必须是字符串")
    return command, arguments, environment, cwd


def launch_spec(plugin_root, config_name="mcp.json", plugin_data_root=None):
    root = Path(plugin_root).resolve()
    config = read_object(root / config_name)
    if config_name == ".mcp.json":
        servers = config.get("mcpServers")
        server = servers.get("memory-manager") if isinstance(servers, dict) else None
        if not isinstance(server, dict):
            raise DiagnosticError(config_name + " 未声明 memory-manager 服务")
        command, arguments, environment, cwd = _server_fields(server, str(root))
    else:
        command, arguments, environment, cwd = _portable_launch_spec(root, config, plugin_data_root)
    if is_foreign_windows_command(command):
        raise DiagnosticError("检测到 Windows Python 命令正被 POSIX/WSL 环境使用：" + command + "。请在实际运行插件的环境重新安装，或为该执行环境配置对应的 Python；不要把 Windows 命令直接作为 Linux 命令启动。")
    if os.name == "nt" and command.startswith("/mnt/"):
        raise DiagnosticError("Windows 运行时收到 WSL 路径：" + command)

    if not Path(cwd).is_absolute():
        cwd = str(root / cwd)
    if not Path(cwd).is_dir():
        raise DiagnosticError("MCP 工作目录不存在：" + cwd)
    result_environment = os.environ.copy()
    result_environment.update(environment)
    return [command] + arguments, cwd, result_environment


def parse_probe_output(stdout):
    replies = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            reply = json.loads(line)
        except ValueError as error:
            raise DiagnosticError("MCP stdout 包含非 JSON 日志，客户端不能解析该协议流") from error
        if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
            raise DiagnosticError("MCP 返回了无效的 JSON-RPC 消息")
        identifier = reply.get("id")
        if identifier in (1, 2, 3):
            replies[identifier] = reply
    for identifier, label in ((1, "initialize"), (2, "tools/list"), (3, "tools/call")):
        reply = replies.get(identifier)
        if not reply:
            raise DiagnosticError("MCP 未完成 " + label)
        if "error" in reply:
            raise DiagnosticError("MCP " + label + " 返回协议错误：" + str(reply["error"]))
        if not isinstance(reply.get("result"), dict):
            raise DiagnosticError("MCP " + label + " 未返回 result 对象")
    tools = replies[2]["result"].get("tools")
    if not isinstance(tools, list):
        raise DiagnosticError("MCP tools/list 未返回工具数组")
    names = {row.get("name") for row in tools if isinstance(row, dict) and isinstance(row.get("name"), str)}
    missing = REQUIRED_TOOLS - names
    if missing:
        raise DiagnosticError("MCP 缺少必要工具：" + ", ".join(sorted(missing)))
    call = replies[3]["result"]
    if call.get("isError"):
        raise DiagnosticError("MCP 只读调用失败：" + str(call.get("content", []))[:1500])
    contents = call.get("content")
    if not isinstance(contents, list):
        raise DiagnosticError("MCP 只读调用没有返回内容")
    payload = None
    for block in contents:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                payload = json.loads(block.get("text", ""))
            except (ValueError, TypeError):
                continue
            break
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise DiagnosticError("memory_search 返回结构无效")
    return {"protocol": replies[1]["result"].get("protocolVersion"), "tool_count": len(names), "required_tools": sorted(REQUIRED_TOOLS), "read_probe": "passed"}


def probe_plugin(plugin_root, config_name="mcp.json", timeout=20, plugin_data_root=None):
    skill_path = check_skill(plugin_root)
    command, cwd, environment = launch_spec(plugin_root, config_name, plugin_data_root)
    resolved_command = command[0]
    if not Path(resolved_command).is_absolute():
        resolved_command = shutil.which(resolved_command, path=environment.get("PATH"))
        if resolved_command is None:
            raise DiagnosticError("MCP 裸命令未出现在有效 PATH 中：" + command[0])
    launch_command = [resolved_command] + command[1:]
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "memory-manager-doctor", "version": __version__}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "__memory_manager_healthcheck_" + uuid.uuid4().hex}}},
    ]
    request_text = "".join(json.dumps(request) + "\n" for request in requests)
    try:
        result = subprocess.run(launch_command, cwd=cwd, env=environment, input=request_text, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False, shell=False)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise DiagnosticError("MCP 进程无法启动或握手超时：" + str(error)) from error
    if result.returncode:
        raise DiagnosticError("MCP 进程退出码 " + str(result.returncode) + "：" + result.stderr.strip()[:2000])
    summary = parse_probe_output(result.stdout)
    summary.update(skill_path=skill_path, config=config_name, command=command[0], resolved_command=resolved_command, cwd=cwd, host=os.name)
    return summary
