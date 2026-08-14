#!/usr/bin/env python3
"""Store ZenTao profiles outside the plugin without printing secrets."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is expected.
    tomllib = None  # type: ignore[assignment]


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = PLUGIN_ROOT / "mcp"
sys.path.insert(0, str(MCP_ROOT))

from zentao_client import (  # noqa: E402
    DEFAULT_ALLOWED_PROJECTS,
    DEFAULT_API_BASE,
    DEFAULT_WEB_BASE,
    WRITE_CONFIRMATION_MODES,
    ConnectorConfig,
    ZentaoClient,
    ZentaoError,
    default_credentials_path,
    parse_allowed_projects,
    parse_source_roots,
    parse_write_confirmation_mode,
)


def codex_config_path() -> Path:
    codex_home = os.getenv("CODEX_HOME", "").strip()
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "config.toml"


def read_credentials(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"active_profile": "production", "profiles": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Credentials file must contain an object")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        payload["profiles"] = {}
    return payload


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="credentials-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def derive_web_base(api_base: str) -> str:
    marker = "/api.php/v1"
    return api_base.split(marker, 1)[0].rstrip("/") if marker in api_base else DEFAULT_WEB_BASE


def normalized_api_base(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.endswith("/my.html"):
        value = value[: -len("/my.html")]
    if value.endswith("/zentao"):
        value += "/api.php/v1"
    return value


def env_value(env: dict[str, Any], names: tuple[str, ...], default: Any = "") -> Any:
    for name in names:
        value = env.get(name)
        if value not in (None, ""):
            return value
    return default


def import_mcp(args: argparse.Namespace) -> dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("Python 3.11 or newer is required")
    config_path = codex_config_path()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = config.get("mcp_servers", {})
    server = servers.get(args.name, {}) if isinstance(servers, dict) else {}
    if not isinstance(server, dict):
        raise ValueError(f"MCP server {args.name!r} was not found in {config_path}")
    env = server.get("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"MCP server {args.name!r} has no environment configuration")
    api_base = normalized_api_base(
        str(
            env_value(
                env,
                ("ZENTAO_API_BASE_URL", "ZENTAO_API_BASE", "ZENTAO_BASE_URL", "ZENTAO_URL"),
                DEFAULT_API_BASE,
            )
        )
    )
    account = str(env_value(env, ("ZENTAO_ACCOUNT", "ZENTAO_USERNAME"), ""))
    password = str(env_value(env, ("ZENTAO_PASSWORD",), ""))
    token = str(env_value(env, ("ZENTAO_TOKEN",), ""))
    if not token and not (account and password):
        raise ValueError(f"MCP server {args.name!r} does not contain usable ZenTao credentials")
    allowed = parse_allowed_projects(
        env_value(env, ("ZENTAO_ALLOWED_PROJECT_IDS",), DEFAULT_ALLOWED_PROJECTS)
    )
    return {
        "api_base_url": api_base,
        "web_base_url": str(
            env_value(env, ("ZENTAO_WEB_BASE_URL",), derive_web_base(api_base))
        ).rstrip("/"),
        "account": account,
        "password": password,
        "token": token,
        "allowed_project_ids": list(allowed),
        "timeout_seconds": float(env_value(env, ("ZENTAO_TIMEOUT_SECONDS",), 20)),
        "verify_tls": True,
        "write_confirmation_mode": parse_write_confirmation_mode(
            env_value(env, ("ZENTAO_WRITE_CONFIRMATION_MODE",), "manual")
        ),
    }


def set_profile(args: argparse.Namespace) -> dict[str, Any]:
    password = getpass.getpass("ZenTao password: ")
    if not password:
        raise ValueError("ZenTao password is required for first configuration")
    basic_account = args.http_basic_account or ""
    basic_password = getpass.getpass("Outer HTTP Basic password (blank if unused): ") if basic_account else ""
    if basic_account and not basic_password:
        raise ValueError("Outer HTTP Basic password is required when its account is provided")
    api_base = normalized_api_base(args.api_base)
    return {
        "api_base_url": api_base,
        "web_base_url": (args.web_base or derive_web_base(api_base)).rstrip("/"),
        "account": args.account,
        "password": password,
        "allowed_project_ids": list(parse_allowed_projects(args.allowed_project_ids)),
        "timeout_seconds": args.timeout,
        "http_basic_account": basic_account,
        "http_basic_password": basic_password,
        "verify_tls": not args.no_verify_tls,
        "write_confirmation_mode": parse_write_confirmation_mode(args.write_confirmation_mode),
    }


def save_profile(path: Path, profile_name: str, profile: dict[str, Any]) -> None:
    payload = read_credentials(path)
    payload["active_profile"] = profile_name
    payload.setdefault("profiles", {})[profile_name] = profile
    atomic_write(path, payload)


def safe_status(path: Path) -> dict[str, Any]:
    payload = read_credentials(path)
    profiles = payload.get("profiles", {})
    result_profiles: dict[str, Any] = {}
    if isinstance(profiles, dict):
        for name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            result_profiles[str(name)] = {
                "api_base_url": profile.get("api_base_url"),
                "web_base_url": profile.get("web_base_url"),
                "account": profile.get("account"),
                "credentials_present": bool(
                    profile.get("token") or (profile.get("account") and profile.get("password"))
                ),
                "allowed_project_ids": profile.get("allowed_project_ids", []),
                "http_basic_configured": bool(profile.get("http_basic_account")),
                "source_roots": parse_source_roots(profile.get("source_roots", {})),
                "write_confirmation_mode": parse_write_confirmation_mode(
                    profile.get("write_confirmation_mode", "manual")
                ),
            }
    return {
        "credentials_file": str(path),
        "exists": path.exists(),
        "active_profile": payload.get("active_profile"),
        "profiles": result_profiles,
    }


def test_profile(profile_name: str, path: Path) -> dict[str, Any]:
    old_profile = os.environ.get("ZENTAO_PROFILE")
    old_path = os.environ.get("ZENTAO_CREDENTIALS_FILE")
    os.environ["ZENTAO_PROFILE"] = profile_name
    os.environ["ZENTAO_CREDENTIALS_FILE"] = str(path)
    try:
        client = ZentaoClient(ConnectorConfig.load())
        user = client.get("user")
        profile = user.get("profile", {}) if isinstance(user, dict) else {}
        return {
            "ok": True,
            "account": profile.get("account") if isinstance(profile, dict) else None,
            "role": profile.get("role") if isinstance(profile, dict) else None,
            "api_base_url": client.config.api_base,
        }
    except ZentaoError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if old_profile is None:
            os.environ.pop("ZENTAO_PROFILE", None)
        else:
            os.environ["ZENTAO_PROFILE"] = old_profile
        if old_path is None:
            os.environ.pop("ZENTAO_CREDENTIALS_FILE", None)
        else:
            os.environ["ZENTAO_CREDENTIALS_FILE"] = old_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, default=default_credentials_path())
    sub = parser.add_subparsers(dest="command", required=True)

    import_parser = sub.add_parser("import-mcp", help="Import credentials from an existing Codex MCP entry")
    import_parser.add_argument("--name", default="zentao")
    import_parser.add_argument("--profile", default="production")
    import_parser.add_argument("--test", action="store_true")

    set_parser = sub.add_parser("set", help="Interactively store a profile")
    set_parser.add_argument("--profile", default="production")
    set_parser.add_argument(
        "--api-base",
        required=True,
        help="ZenTao home/my.html URL or REST API v1 URL supplied by the user",
    )
    set_parser.add_argument(
        "--web-base",
        help="ZenTao web base URL; omitted means derive it from --api-base",
    )
    set_parser.add_argument("--account", required=True)
    set_parser.add_argument(
        "--allowed-project-ids",
        required=True,
        help="Comma-separated project IDs explicitly authorized by the user",
    )
    set_parser.add_argument("--timeout", type=float, default=20)
    set_parser.add_argument("--http-basic-account")
    set_parser.add_argument("--no-verify-tls", action="store_true")
    set_parser.add_argument(
        "--write-confirmation-mode",
        choices=WRITE_CONFIRMATION_MODES,
        default="manual",
    )
    set_parser.add_argument("--test", action="store_true")

    map_parser = sub.add_parser("map-source", help="Map an allowed ZenTao project ID to a local source directory")
    map_parser.add_argument("--profile", default="production")
    map_parser.add_argument("--project-id", type=int, required=True)
    map_parser.add_argument("--path", type=Path, required=True)

    confirmation_parser = sub.add_parser(
        "set-confirmation",
        help="Set a profile's write confirmation policy without changing credentials",
    )
    confirmation_parser.add_argument("--profile", default="production")
    confirmation_parser.add_argument("--mode", choices=WRITE_CONFIRMATION_MODES, required=True)

    sub.add_parser("status", help="Show configuration without secrets")
    test_parser = sub.add_parser("test", help="Test an existing profile")
    test_parser.add_argument("--profile", default="production")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = args.credentials_file.expanduser()
    try:
        if args.command == "status":
            result = safe_status(path)
        elif args.command == "test":
            result = test_profile(args.profile, path)
        elif args.command == "set-confirmation":
            payload = read_credentials(path)
            profiles = payload.setdefault("profiles", {})
            profile = profiles.get(args.profile)
            if not isinstance(profile, dict):
                raise ValueError(f"Profile {args.profile!r} does not exist")
            previous_mode = parse_write_confirmation_mode(
                profile.get("write_confirmation_mode", "manual")
            )
            profile["write_confirmation_mode"] = parse_write_confirmation_mode(args.mode)
            atomic_write(path, payload)
            result = {
                "saved": True,
                "profile": args.profile,
                "previous_mode": previous_mode,
                "write_confirmation_mode": profile["write_confirmation_mode"],
                "restart_required": True,
                **safe_status(path),
            }
        elif args.command == "map-source":
            source_path = args.path.expanduser().resolve()
            if not source_path.is_dir():
                raise ValueError(f"Source path is not a directory: {source_path}")
            payload = read_credentials(path)
            profiles = payload.setdefault("profiles", {})
            profile = profiles.get(args.profile)
            if not isinstance(profile, dict):
                raise ValueError(f"Profile {args.profile!r} does not exist")
            allowed = parse_allowed_projects(profile.get("allowed_project_ids"))
            if args.project_id not in allowed:
                raise ValueError(f"Project {args.project_id} is outside profile {args.profile!r}'s allow list")
            roots = parse_source_roots(profile.get("source_roots", {}))
            roots[args.project_id] = str(source_path)
            profile["source_roots"] = {str(key): value for key, value in sorted(roots.items())}
            atomic_write(path, payload)
            result = {
                "saved": True,
                "profile": args.profile,
                "project_id": args.project_id,
                "source_root": str(source_path),
                **safe_status(path),
            }
        else:
            profile = import_mcp(args) if args.command == "import-mcp" else set_profile(args)
            save_profile(path, args.profile, profile)
            result = {"saved": True, "profile": args.profile, **safe_status(path)}
            if args.test:
                result["test"] = test_profile(args.profile, path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not (isinstance(result, dict) and result.get("ok") is False) else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
