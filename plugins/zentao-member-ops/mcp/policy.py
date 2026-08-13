#!/usr/bin/env python3
"""Role capability resolution and two-phase write confirmation policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from zentao_client import ZentaoClient, ZentaoError, normalize


@dataclass(frozen=True)
class ActionSpec:
    module: str
    privilege: str
    method: str
    path: str
    allowed_fields: tuple[str, ...]
    required_fields: tuple[str, ...] = ()
    destructive: bool = False


BUG_ACTIONS: dict[str, ActionSpec] = {
    "assign": ActionSpec(
        "bug", "assignTo", "POST", "bugs/{id}/assign", ("assignedTo", "mailto", "comment"), ("assignedTo",)
    ),
    "confirm": ActionSpec(
        "bug",
        "confirm",
        "POST",
        "bugs/{id}/confirm",
        ("assignedTo", "mailto", "comment", "pri", "type", "status", "deadline"),
    ),
    "resolve": ActionSpec(
        "bug",
        "resolve",
        "POST",
        "bugs/{id}/resolve",
        ("resolution", "resolvedBuild", "resolvedDate", "duplicateBug", "assignedTo", "uid", "comment"),
        ("resolution",),
    ),
    "close": ActionSpec("bug", "close", "POST", "bugs/{id}/close", ("comment",), destructive=True),
    "activate": ActionSpec(
        "bug", "activate", "POST", "bugs/{id}/active", ("assignedTo", "uid", "openedBuild", "comment")
    ),
    "update": ActionSpec(
        "bug",
        "edit",
        "PUT",
        "bugs/{id}",
        (
            "title",
            "project",
            "execution",
            "openedBuild",
            "assignedTo",
            "pri",
            "severity",
            "type",
            "story",
            "resolvedBy",
            "resolvedBuild",
            "closedBy",
            "resolution",
            "product",
            "plan",
            "task",
            "module",
            "steps",
            "mailto",
            "keywords",
            "deadline",
        ),
    ),
    "comment": ActionSpec("action", "comment", "COMMENT", "bugs/{id}", ("comment",), ("comment",)),
}

STORY_ACTIONS: dict[str, ActionSpec] = {
    "assign": ActionSpec("story", "assignTo", "POST", "stories/{id}/assign", ("assignedTo", "comment"), ("assignedTo",)),
    "review": ActionSpec(
        "story",
        "review",
        "POST",
        "stories/{id}/review",
        ("reviewedDate", "result", "closedReason", "pri", "estimate", "comment"),
        ("result", "pri", "estimate"),
    ),
    "close": ActionSpec(
        "story",
        "close",
        "POST",
        "stories/{id}/close",
        ("closedReason", "duplicateStory", "comment"),
        ("closedReason",),
        destructive=True,
    ),
    "activate": ActionSpec("story", "activate", "POST", "stories/{id}/active", ("assignedTo", "status", "comment")),
    "change": ActionSpec(
        "story",
        "change",
        "POST",
        "stories/{id}/change",
        ("reviewer", "comment", "executions", "bugs", "cases", "tasks", "reviewedBy", "title", "spec", "verify"),
    ),
    "update": ActionSpec(
        "story",
        "edit",
        "PUT",
        "stories/{id}",
        (
            "title",
            "product",
            "parent",
            "reviewer",
            "type",
            "plan",
            "module",
            "source",
            "sourceNote",
            "category",
            "pri",
            "estimate",
            "mailto",
            "keywords",
            "stage",
            "notifyEmail",
            "status",
            "needNotReview",
        ),
    ),
    "comment": ActionSpec("action", "comment", "COMMENT", "stories/{id}", ("comment",), ("comment",)),
}

TASK_ACTIONS: dict[str, ActionSpec] = {
    "assign": ActionSpec(
        "task", "assignTo", "POST", "tasks/{id}/assignto", ("assignedTo", "comment", "left"), ("assignedTo",)
    ),
    "start": ActionSpec("task", "start", "POST", "tasks/{id}/start", ("assignedTo", "consumed", "left", "comment", "realStarted")),
    "pause": ActionSpec("task", "pause", "POST", "tasks/{id}/pause", ("comment",)),
    "restart": ActionSpec(
        "task",
        "restart",
        "POST",
        "tasks/{id}/restart",
        ("assignedTo", "realStarted", "consumed", "left", "comment"),
        ("consumed", "left"),
    ),
    "finish": ActionSpec(
        "task",
        "finish",
        "POST",
        "tasks/{id}/finish",
        ("assignedTo", "realStarted", "finishedDate", "currentConsumed", "comment"),
        ("realStarted", "finishedDate", "currentConsumed"),
    ),
    "close": ActionSpec("task", "close", "POST", "tasks/{id}/close", ("comment",), destructive=True),
    "activate": ActionSpec("task", "activate", "POST", "tasks/{id}/active", ("assignedTo", "left", "comment")),
    "update": ActionSpec(
        "task",
        "edit",
        "PUT",
        "tasks/{id}",
        (
            "name",
            "type",
            "desc",
            "assignedTo",
            "pri",
            "estimate",
            "left",
            "consumed",
            "story",
            "parent",
            "execution",
            "module",
            "closedReason",
            "status",
            "estStarted",
            "deadline",
            "mailto",
        ),
    ),
    "comment": ActionSpec("action", "comment", "COMMENT", "tasks/{id}", ("comment",), ("comment",)),
}

CREATE_SPECS: dict[str, ActionSpec] = {
    "bug": ActionSpec(
        "bug",
        "create",
        "POST",
        "bugs",
        (
            "product",
            "title",
            "project",
            "execution",
            "openedBuild",
            "assignedTo",
            "pri",
            "module",
            "severity",
            "type",
            "story",
            "task",
            "mailto",
            "keywords",
            "steps",
            "deadline",
        ),
        ("product", "title", "pri", "severity", "type", "openedBuild"),
    ),
    "story": ActionSpec(
        "story",
        "create",
        "POST",
        "stories",
        (
            "product",
            "title",
            "spec",
            "verify",
            "module",
            "reviewer",
            "type",
            "parent",
            "source",
            "sourceNote",
            "category",
            "pri",
            "estimate",
            "mailto",
            "keywords",
            "status",
            "plan",
            "branch",
        ),
        ("product", "title", "spec", "pri", "category"),
    ),
    "task": ActionSpec(
        "task",
        "create",
        "POST",
        "executions/{execution_id}/tasks",
        (
            "name",
            "type",
            "assignedTo",
            "estimate",
            "story",
            "project",
            "module",
            "pri",
            "desc",
            "estStarted",
            "deadline",
            "mailto",
        ),
        ("name", "assignedTo", "type", "estStarted", "deadline"),
    ),
}

CONTAINER_CREATE_SPECS: dict[str, ActionSpec] = {
    "product": ActionSpec(
        "product",
        "create",
        "POST",
        "products",
        (
            "program",
            "line",
            "name",
            "code",
            "PO",
            "QD",
            "RD",
            "type",
            "desc",
            "acl",
            "whitelist",
        ),
        ("name", "code"),
    ),
    "project": ActionSpec(
        "project",
        "create",
        "POST",
        "projects",
        (
            "name",
            "code",
            "begin",
            "end",
            "products",
            "multiple",
            "acl",
            "whitelist",
            "PM",
            "model",
            "parent",
        ),
        ("name", "code", "begin", "end", "products"),
    ),
    "execution": ActionSpec(
        "execution",
        "create",
        "POST",
        "projects/{project_id}/executions",
        (
            "project",
            "name",
            "code",
            "begin",
            "end",
            "lifetime",
            "desc",
            "days",
            "percent",
            "parent",
            "acl",
            "PO",
            "PM",
            "QD",
            "RD",
            "whitelist",
            "products",
            "plans",
        ),
        ("project", "name", "code", "begin", "end"),
    ),
}

ACTION_SETS = {"bug": BUG_ACTIONS, "story": STORY_ACTIONS, "task": TASK_ACTIONS}


@dataclass
class Identity:
    account: str
    realname: str
    role_code: str
    role_name: str
    admin: bool
    groups: list[dict[str, Any]]
    privileges: dict[str, set[str]]

    def has(self, module: str, privilege: str) -> bool:
        return self.admin or privilege in self.privileges.get(module, set())

    def allowed_actions(self, object_type: str) -> list[str]:
        specs = ACTION_SETS[object_type]
        actions = [name for name, spec in specs.items() if self.has(spec.module, spec.privilege)]
        # ZenTao Open Source 21.7.1's REST story-review entry omits the
        # hidden current status field. A safe review therefore needs
        # story.edit as a compatibility fallback if the primary endpoint
        # leaves the status empty.
        if object_type == "story" and "review" in actions and not self.has("story", "edit"):
            actions.remove("review")
        return actions

    def can_create(self, object_type: str) -> bool:
        spec = CREATE_SPECS[object_type]
        return self.has(spec.module, spec.privilege)

    def can_create_container(self, object_type: str) -> bool:
        spec = CONTAINER_CREATE_SPECS[object_type]
        return self.has(spec.module, spec.privilege)

    def summary(self) -> dict[str, Any]:
        relevant = {module: sorted(methods) for module, methods in self.privileges.items() if module in {"action", "bug", "story", "task"}}
        return {
            "account": self.account,
            "realname": self.realname,
            "role": {"code": self.role_code, "name": self.role_name},
            "admin": self.admin,
            "groups": self.groups,
            "capabilities": relevant,
            "allowed_actions": {
                object_type: self.allowed_actions(object_type) for object_type in ACTION_SETS
            },
            "can_create": {
                object_type: self.can_create(object_type) for object_type in CREATE_SPECS
            },
            "can_create_container": {
                object_type: self.can_create_container(object_type)
                for object_type in CONTAINER_CREATE_SPECS
            },
        }


class CapabilityResolver:
    def __init__(self, client: ZentaoClient) -> None:
        self.client = client

    def resolve(self) -> Identity:
        user = self.client.get("user")
        if not isinstance(user, dict) or not isinstance(user.get("profile"), dict):
            raise ZentaoError("ZenTao /user response did not contain a profile")
        profile = user["profile"]
        account = str(profile.get("account") or self.client.config.account)
        role = profile.get("role", {})
        role_code = str(role.get("code", "")) if isinstance(role, dict) else str(role)
        role_name = str(role.get("name", role_code)) if isinstance(role, dict) else str(role)
        groups_payload = self.client.get("groups")
        privileges: dict[str, set[str]] = {}
        own_groups: list[dict[str, Any]] = []
        if isinstance(groups_payload, list):
            for group in groups_payload:
                if not isinstance(group, dict):
                    continue
                accounts = group.get("accounts", {})
                if not isinstance(accounts, dict) or account not in accounts:
                    continue
                own_groups.append(
                    {
                        "id": group.get("id"),
                        "name": group.get("name"),
                        "role": group.get("role"),
                    }
                )
                privs = group.get("privs", {})
                if not isinstance(privs, dict):
                    continue
                for module, methods in privs.items():
                    if isinstance(methods, dict):
                        privileges.setdefault(str(module), set()).update(
                            str(method) for method, enabled in methods.items() if enabled
                        )
        return Identity(
            account=account,
            realname=str(profile.get("realname", "")),
            role_code=role_code,
            role_name=role_name,
            admin=bool(profile.get("admin")),
            groups=own_groups,
            privileges=privileges,
        )


def sanitize_body(data: Any, spec: ActionSpec) -> dict[str, Any]:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ZentaoError("data must be an object")
    unknown = sorted(set(data) - set(spec.allowed_fields))
    if unknown:
        raise ZentaoError(f"Unsupported fields for this action: {', '.join(unknown)}")

    def clean(value: Any, field: str) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > 20000:
                raise ZentaoError(f"Field {field} exceeds 20000 characters")
            return value
        if isinstance(value, list):
            if len(value) > 100:
                raise ZentaoError(f"Field {field} contains too many values")
            if any(not isinstance(item, (str, int, float, bool)) and item is not None for item in value):
                raise ZentaoError(f"Field {field} may contain only scalar values")
            return value
        raise ZentaoError(f"Field {field} has an unsupported value type")

    result = {field: clean(value, field) for field, value in data.items()}
    missing = [field for field in spec.required_fields if field not in result or result[field] in (None, "", [])]
    if missing:
        raise ZentaoError(f"Missing required fields: {', '.join(missing)}")
    if "comment" in result and not str(result["comment"]).strip():
        raise ZentaoError("comment must not be blank")
    return result


class ConfirmationTickets:
    """Issue short-lived, signed, single-use previews for every write."""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or os.urandom(32)
        self._used_nonces: set[str] = set()

    @staticmethod
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def issue(self, operation: dict[str, Any], ttl_seconds: int = 600) -> tuple[str, int]:
        ttl = min(max(int(ttl_seconds), 60), 1800)
        expires_at = int(time.time()) + ttl
        payload = {
            "v": 1,
            "exp": expires_at,
            "nonce": uuid.uuid4().hex,
            "operation": normalize(operation),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return f"{self._b64(raw)}.{self._b64(signature)}", expires_at

    def verify_and_consume(self, token: str) -> dict[str, Any]:
        try:
            encoded, encoded_signature = token.split(".", 1)
            raw = self._unb64(encoded)
            signature = self._unb64(encoded_signature)
        except (ValueError, TypeError) as exc:
            raise ZentaoError("Invalid confirmation token") from exc
        if self._b64(raw) != encoded or self._b64(signature) != encoded_signature:
            raise ZentaoError("Invalid confirmation token signature")
        expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ZentaoError("Invalid confirmation token signature")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ZentaoError("Invalid confirmation token payload") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ZentaoError("Unsupported confirmation token version")
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ZentaoError("Confirmation token expired; prepare the operation again")
        nonce = str(payload.get("nonce", ""))
        if not nonce or nonce in self._used_nonces:
            raise ZentaoError("Confirmation token was already used")
        operation = payload.get("operation")
        if not isinstance(operation, dict):
            raise ZentaoError("Confirmation token has no operation")
        self._used_nonces.add(nonce)
        return operation
