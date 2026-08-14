#!/usr/bin/env python3
"""Credential loading and HTTP client for ZenTao Open Source REST API v1."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import ssl
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "http://127.0.0.1/zentao/api.php/v1"
DEFAULT_WEB_BASE = "http://127.0.0.1/zentao"
DEFAULT_ALLOWED_PROJECTS: tuple[int, ...] = ()
DEFAULT_TIMEOUT_SECONDS = 20.0
WRITE_CONFIRMATION_MODES = ("manual", "safe-auto")

_HTML_BREAK_RE = re.compile(r"(?i)<\s*(br|/p|/div|/li|/tr)\s*/?>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class ZentaoError(RuntimeError):
    """Safe, user-facing ZenTao connector failure."""


def default_credentials_path() -> Path:
    explicit = os.getenv("ZENTAO_CREDENTIALS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".config" / "codex" / "zentao-member-ops" / "credentials.json"


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_write_confirmation_mode(value: Any) -> str:
    mode = str(value or "manual").strip().lower()
    aliases = {
        "safe": "safe-auto",
        "risk-based": "safe-auto",
        "risk_based": "safe-auto",
        "auto": "safe-auto",
        "automatic": "safe-auto",
    }
    mode = aliases.get(mode, mode)
    if mode not in WRITE_CONFIRMATION_MODES:
        allowed = ", ".join(WRITE_CONFIRMATION_MODES)
        raise ZentaoError(f"write_confirmation_mode must be one of: {allowed}")
    return mode


def as_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) > 0:
        return int(value.strip())
    if isinstance(value, dict):
        for key in ("id", "project", "execution", "product", "value"):
            found = as_id(value.get(key))
            if found is not None:
                return found
    return None


def parse_allowed_projects(value: Any) -> tuple[int, ...]:
    if value is None or value == "":
        return DEFAULT_ALLOWED_PROJECTS
    if isinstance(value, str):
        parts: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        raise ZentaoError("allowed_project_ids must be a comma-separated string or list")
    result: list[int] = []
    for part in parts:
        parsed = as_id(str(part).strip())
        if parsed is None:
            raise ZentaoError(f"Invalid allowed project ID: {part!r}")
        if parsed not in result:
            result.append(parsed)
    return tuple(result)


def parse_source_roots(value: Any) -> dict[int, str]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ZentaoError("source_roots must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ZentaoError("source_roots must be an object keyed by project ID")
    result: dict[int, str] = {}
    for project_id, root in value.items():
        parsed = as_id(project_id)
        if parsed is None or not isinstance(root, str) or not root.strip():
            raise ZentaoError(f"Invalid source root mapping: {project_id!r}")
        result[parsed] = str(Path(root).expanduser())
    return result


def textify(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if "<" not in value and "&" not in value:
        return value
    value = _HTML_BREAK_RE.sub("\n", value)
    value = _HTML_TAG_RE.sub("", value)
    value = html.unescape(value)
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def normalize(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return str(value)
    if isinstance(value, dict):
        return {str(key): normalize(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item, depth + 1) for item in value]
    return textify(value)


def extract_collection(payload: Any, keys: Iterable[str]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict) and all(isinstance(item, dict) for item in value.values()):
            return list(value.values())
    nested = payload.get("data")
    if nested is not None and nested is not payload:
        return extract_collection(nested, keys)
    return []


def extract_entity(payload: Any, keys: Iterable[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ZentaoError("ZenTao returned an unexpected entity response")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    nested = payload.get("data")
    if isinstance(nested, dict):
        return extract_entity(nested, keys)
    if "id" in payload:
        return payload
    raise ZentaoError("ZenTao response did not contain the requested entity")


def select_fields(item: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: normalize(item[field]) for field in fields if field in item}


def _profile_value(profile: dict[str, Any], env_name: str, key: str, default: Any = "") -> Any:
    env = os.getenv(env_name)
    return env if env is not None and env != "" else profile.get(key, default)


def profile_summary(
    profile: dict[str, Any], *, active: bool, selected: bool
) -> dict[str, Any]:
    """Return connection metadata that is safe to expose through MCP status."""
    return {
        "active": active,
        "selected_by_running_process": selected,
        "api_base_url": profile.get("api_base_url"),
        "web_base_url": profile.get("web_base_url"),
        "account": profile.get("account"),
        "credentials_present": bool(
            profile.get("token") or (profile.get("account") and profile.get("password"))
        ),
        "allowed_project_ids": list(parse_allowed_projects(profile.get("allowed_project_ids"))),
        "http_basic_configured": bool(profile.get("http_basic_account")),
        "source_roots": {
            str(project_id): root
            for project_id, root in parse_source_roots(profile.get("source_roots", {})).items()
        },
        "write_confirmation_mode": parse_write_confirmation_mode(
            profile.get("write_confirmation_mode", "manual")
        ),
    }


@dataclass(frozen=True)
class ConnectorConfig:
    profile_name: str
    credentials_path: Path
    api_base: str
    web_base: str
    account: str
    password: str
    token: str
    allowed_project_ids: tuple[int, ...]
    timeout_seconds: float
    http_basic_account: str
    http_basic_password: str
    verify_tls: bool
    source_roots: dict[int, str]
    write_confirmation_mode: str = "manual"
    active_profile: str = "production"
    profile_selection_source: str = "credentials.active_profile"
    available_profiles: tuple[str, ...] = ()
    profile_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def credentials_configured(self) -> bool:
        return bool(self.token or (self.account and self.password))

    @property
    def transport_encrypted(self) -> bool:
        return self.api_base.lower().startswith("https://")

    @classmethod
    def load(cls) -> "ConnectorConfig":
        path = default_credentials_path()
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ZentaoError(f"Cannot read credentials file {path}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ZentaoError(f"Credentials file {path} must contain a JSON object")
            payload = loaded

        active_profile = str(payload.get("active_profile", "production") or "production")
        environment_profile = os.getenv("ZENTAO_PROFILE", "").strip()
        profile_name = environment_profile or active_profile
        selection_source = (
            "environment.ZENTAO_PROFILE"
            if environment_profile
            else "credentials.active_profile"
        )
        profiles = payload.get("profiles", {})
        available_profiles = tuple(
            sorted(str(name) for name, value in profiles.items() if isinstance(value, dict))
        ) if isinstance(profiles, dict) else ()
        if available_profiles and profile_name not in available_profiles:
            available = ", ".join(available_profiles)
            raise ZentaoError(
                f"Selected profile {profile_name!r} does not exist. Available profiles: {available}"
            )
        profile = profiles.get(profile_name, {}) if isinstance(profiles, dict) else {}
        if not isinstance(profile, dict):
            raise ZentaoError(f"Profile {profile_name!r} must be a JSON object")
        summaries = {
            str(name): profile_summary(
                value,
                active=str(name) == active_profile,
                selected=str(name) == profile_name,
            )
            for name, value in profiles.items()
            if isinstance(value, dict)
        } if isinstance(profiles, dict) else {}

        api_base = str(
            _profile_value(profile, "ZENTAO_API_BASE_URL", "api_base_url", DEFAULT_API_BASE)
        ).rstrip("/")
        web_base = str(
            _profile_value(profile, "ZENTAO_WEB_BASE_URL", "web_base_url", DEFAULT_WEB_BASE)
        ).rstrip("/")
        allowed = _profile_value(
            profile,
            "ZENTAO_ALLOWED_PROJECT_IDS",
            "allowed_project_ids",
            DEFAULT_ALLOWED_PROJECTS,
        )
        timeout = float(
            _profile_value(
                profile,
                "ZENTAO_TIMEOUT_SECONDS",
                "timeout_seconds",
                DEFAULT_TIMEOUT_SECONDS,
            )
        )
        if timeout <= 0 or timeout > 120:
            raise ZentaoError("timeout_seconds must be between 0 and 120")

        return cls(
            profile_name=profile_name,
            credentials_path=path,
            api_base=api_base,
            web_base=web_base,
            account=str(_profile_value(profile, "ZENTAO_ACCOUNT", "account", "")),
            password=str(_profile_value(profile, "ZENTAO_PASSWORD", "password", "")),
            token=str(_profile_value(profile, "ZENTAO_TOKEN", "token", "")),
            allowed_project_ids=parse_allowed_projects(allowed),
            timeout_seconds=timeout,
            http_basic_account=str(
                _profile_value(profile, "ZENTAO_HTTP_BASIC_ACCOUNT", "http_basic_account", "")
            ),
            http_basic_password=str(
                _profile_value(profile, "ZENTAO_HTTP_BASIC_PASSWORD", "http_basic_password", "")
            ),
            verify_tls=_as_bool(
                _profile_value(profile, "ZENTAO_VERIFY_TLS", "verify_tls", True), True
            ),
            source_roots=parse_source_roots(
                _profile_value(profile, "ZENTAO_SOURCE_ROOTS_JSON", "source_roots", {})
            ),
            write_confirmation_mode=parse_write_confirmation_mode(
                _profile_value(
                    profile,
                    "ZENTAO_WRITE_CONFIRMATION_MODE",
                    "write_confirmation_mode",
                    "manual",
                )
            ),
            active_profile=active_profile,
            profile_selection_source=selection_source,
            available_profiles=available_profiles,
            profile_summaries=summaries,
        )


@dataclass
class HttpResult:
    status: int
    payload: Any
    content_type: str = ""


class ZentaoClient:
    """Token-authenticated ZenTao client with optional outer HTTP Basic auth."""

    def __init__(self, config: ConnectorConfig | None = None) -> None:
        self.config = config or ConnectorConfig.load()
        self._token = self.config.token.strip()

    def _base_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "zentao-member-ops/1.3.0"}
        if self.config.http_basic_account:
            raw = f"{self.config.http_basic_account}:{self.config.http_basic_password}".encode()
            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode()}"
        return headers

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.config.api_base.lower().startswith("https://"):
            return None
        if self.config.verify_tls:
            return ssl.create_default_context()
        return ssl._create_unverified_context()  # noqa: SLF001 - explicit opt-out only.

    def _send(
        self,
        method: str,
        url: str,
        *,
        encoded: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        request_headers = self._base_headers()
        if headers:
            request_headers.update(headers)
        request = Request(url, data=encoded, headers=request_headers, method=method)
        try:
            with urlopen(
                request,
                timeout=self.config.timeout_seconds,
                context=self._ssl_context(),
            ) as response:
                status, raw = response.status, response.read()
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            status, raw = exc.code, exc.read()
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        except (URLError, TimeoutError, OSError) as exc:
            raise ZentaoError(f"Cannot reach ZenTao: {exc}") from exc

        if not raw:
            payload: Any = {}
        else:
            decoded = raw.decode("utf-8", errors="replace")
            try:
                payload = json.loads(decoded)
            except json.JSONDecodeError:
                payload = {"raw": decoded[:4000], "non_json": True}
        return HttpResult(status=status, payload=payload, content_type=content_type)

    def _api_http(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        token: str = "",
    ) -> HttpResult:
        url = f"{self.config.api_base}/{path.lstrip('/')}"
        if query:
            clean = {key: value for key, value in query.items() if value is not None and value != ""}
            if clean:
                url = f"{url}?{urlencode(clean, doseq=True)}"
        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers: dict[str, str] = {}
        if encoded is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if token:
            headers["Token"] = token
        return self._send(method, url, encoded=encoded, headers=headers)

    @staticmethod
    def _message(payload: Any) -> str:
        if not isinstance(payload, dict):
            return "request failed"
        message = payload.get("message") or payload.get("error") or payload.get("msg")
        if isinstance(message, dict):
            return json.dumps(normalize(message), ensure_ascii=False)
        return str(message or "request failed")

    def login(self) -> str:
        if not self.config.account or not self.config.password:
            raise ZentaoError(
                "ZenTao credentials are not configured. Run the plugin configure script or set "
                "ZENTAO_ACCOUNT and ZENTAO_PASSWORD."
            )
        result = self._api_http(
            "POST",
            "tokens",
            body={"account": self.config.account, "password": self.config.password},
        )
        payload = result.payload if isinstance(result.payload, dict) else {}
        nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        token = payload.get("token") or nested.get("token")
        if result.status >= 400 or not isinstance(token, str) or not token:
            raise ZentaoError(
                f"ZenTao authentication failed (HTTP {result.status}): {self._message(payload)}"
            )
        self._token = token
        return token

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        token = self._token or self.login()
        result = self._api_http(method, path, query=query, body=body, token=token)
        if result.status == 401 and self.config.account and self.config.password:
            self._token = ""
            result = self._api_http(method, path, query=query, body=body, token=self.login())
        if result.status >= 400:
            raise ZentaoError(
                f"ZenTao {method} {path} failed (HTTP {result.status}): "
                f"{self._message(result.payload)}"
            )
        if isinstance(result.payload, dict):
            business_status = str(
                result.payload.get("result") or result.payload.get("status") or ""
            ).lower()
            if result.payload.get("error") or business_status in {"fail", "failed", "error"}:
                raise ZentaoError(
                    f"ZenTao {method} {path} failed: {self._message(result.payload)}"
                )
        return result.payload

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, query=query)

    def write(self, method: str, path: str, body: dict[str, Any]) -> Any:
        if method not in {"POST", "PUT", "DELETE"}:
            raise ZentaoError(f"Unsupported write method: {method}")
        return self.request(method, path, body=body)

    def add_comment(self, object_type: str, object_id: int, comment: str) -> Any:
        """Use ZenTao's authenticated action/comment controller.

        Open Source 21.7.1 declares /comments in routes.php but does not ship a
        comments REST entry. The normal controller accepts the same token as a
        session and still checks action/comment plus object access.
        """
        if object_type not in {"bug", "story", "task"}:
            raise ZentaoError(f"Unsupported comment object type: {object_type}")
        token = self._token or self.login()
        route = f"action-comment-{object_type}-{object_id}.json"
        url = f"{self.config.web_base}/{route}"
        encoded = urlencode({"comment": comment}).encode("utf-8")
        headers = {
            "Token": token,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Referer": f"{self.config.web_base}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        result = self._send("POST", url, encoded=encoded, headers=headers)
        if result.status == 401 and self.config.account and self.config.password:
            self._token = ""
            headers["Token"] = self.login()
            result = self._send("POST", url, encoded=encoded, headers=headers)
        if result.status >= 400:
            raise ZentaoError(
                f"ZenTao comment failed (HTTP {result.status}): {self._message(result.payload)}"
            )
        payload = result.payload
        if isinstance(payload, dict):
            if payload.get("result") == "fail" or payload.get("status") == "fail":
                raise ZentaoError(f"ZenTao comment failed: {self._message(payload)}")
            return payload
        return {"status": "success", "response": normalize(payload)}

    def upload_file(
        self,
        source_path: Path,
        uid: str,
        *,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Upload one file through ZenTao's authenticated REST /files endpoint."""
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise ZentaoError(f"Attachment is not a file: {source_path}")
        size = source_path.stat().st_size
        if size <= 0 or size > max_bytes:
            raise ZentaoError(
                f"Attachment size must be between 1 and {max_bytes} bytes: {source_path.name}"
            )
        if not uid or len(uid) > 120:
            raise ZentaoError("Attachment uid is invalid")

        boundary = f"----zentao-member-ops-{uuid.uuid4().hex}"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="imgFile"; filename="{source_path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        encoded = prefix + source_path.read_bytes() + suffix
        url = f"{self.config.api_base}/files?{urlencode({'uid': uid})}"
        headers = {
            "Token": self._token or self.login(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        result = self._send("POST", url, encoded=encoded, headers=headers)
        if result.status == 401 and self.config.account and self.config.password:
            self._token = ""
            headers["Token"] = self.login()
            result = self._send("POST", url, encoded=encoded, headers=headers)
        payload = result.payload
        if result.status >= 400:
            raise ZentaoError(
                f"ZenTao attachment upload failed (HTTP {result.status}): "
                f"{self._message(payload)}"
            )
        if not isinstance(payload, dict) or (
            payload.get("result") == "fail"
            or payload.get("status") in {"fail", "error"}
        ):
            raise ZentaoError(f"ZenTao attachment upload failed: {self._message(payload)}")
        file_id = as_id(payload)
        if file_id is None and isinstance(payload.get("data"), dict):
            file_id = as_id(payload["data"])
        if file_id is None:
            raise ZentaoError("ZenTao attachment upload returned no file ID")
        nested_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return {
            "id": file_id,
            "name": source_path.name,
            "size": size,
            "url": normalize(payload.get("url") or nested_data.get("url", "")),
        }

    def download_file(
        self,
        file_id: int,
        target_path: Path,
        *,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Download one authorized ZenTao attachment to a connector-owned path."""
        if file_id <= 0:
            raise ZentaoError("file_id must be a positive integer")
        if max_bytes <= 0 or max_bytes > 100 * 1024 * 1024:
            raise ZentaoError("max_bytes must be between 1 and 104857600")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = target_path.with_name(f".{target_path.name}.partial")
        for attempt in range(2):
            token = self._token or self.login()
            headers = self._base_headers()
            headers["Token"] = token
            request = Request(
                f"{self.config.api_base}/files/{file_id}",
                headers=headers,
                method="GET",
            )
            try:
                with urlopen(
                    request,
                    timeout=self.config.timeout_seconds,
                    context=self._ssl_context(),
                ) as response:
                    content_type = response.headers.get("Content-Type", "")
                    content_disposition = response.headers.get("Content-Disposition", "")
                    lowered_type = content_type.lower()
                    if (
                        ("json" in lowered_type or "html" in lowered_type)
                        and "attachment" not in content_disposition.lower()
                    ):
                        response.read(min(max_bytes, 64 * 1024))
                        raise ZentaoError(
                            "ZenTao returned an error document instead of the requested attachment"
                        )
                    declared = response.headers.get("Content-Length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise ZentaoError(
                            f"Attachment exceeds the {max_bytes}-byte download limit"
                        )
                    total = 0
                    with partial_path.open("wb") as handle:
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_bytes:
                                raise ZentaoError(
                                    f"Attachment exceeds the {max_bytes}-byte download limit"
                                )
                            handle.write(chunk)
                    os.replace(partial_path, target_path)
                    return {
                        "file_id": file_id,
                        "path": str(target_path),
                        "size": total,
                        "content_type": content_type,
                    }
            except HTTPError as exc:
                raw = exc.read()
                if exc.code == 401 and attempt == 0 and self.config.account and self.config.password:
                    self._token = ""
                    continue
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    payload = {"raw": raw.decode("utf-8", errors="replace")[:1000]}
                raise ZentaoError(
                    f"ZenTao attachment download failed (HTTP {exc.code}): "
                    f"{self._message(payload)}"
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise ZentaoError(f"Cannot download ZenTao attachment: {exc}") from exc
            finally:
                if partial_path.exists():
                    partial_path.unlink()
        raise ZentaoError("ZenTao attachment authentication failed")
