"""Local SQLite storage for explicitly managed memories (standard library only)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .catalog import PLUGIN_NAME


class StoreError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


SCOPES = {"global", "project", "skill", "plugin"}
ENTRY_FIELDS = {"title", "content", "category_id", "scope", "target", "match_mode", "plugin_id",
                "tags", "priority", "enabled"}
IMPORT_FIELDS = ENTRY_FIELDS | {"id", "deleted", "created_at", "updated_at", "version"}
INITIAL_CATEGORIES = (
    ("preferences", "偏好"), ("projects", "项目"), ("skills", "技能"),
    ("plugins", "插件"), ("corrections", "纠错"), ("knowledge", "知识"),
)
MAX_ENTRIES = 10_000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _text(value, field, *, empty=False, limit=1_000_000):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit:
        raise StoreError(f"{field} 必须是{'非空' if not empty else ''}字符串，长度不能超过 {limit}")
    return value


def _identifier(value, field="id"):
    value = _text(value, field, limit=128)
    if any(ord(char) < 32 for char in value):
        raise StoreError(f"{field} 含有无效字符")
    return value


def _object(value, name):
    if not isinstance(value, dict):
        raise StoreError(f"{name} 必须是对象")
    return value


def _plugin_id(value):
    value = _text(value, "plugin_id", limit=64)
    if not PLUGIN_NAME.fullmatch(value):
        raise StoreError("plugin_id 必须是准确插件标识，仅支持小写字母、数字、单连字符和点，不能包含市场名或版本")
    return value


class Store:
    """Each operation uses its own connection and an atomic transaction.

    ``snapshot`` includes disabled and deleted entries; ``select_entries`` defaults
    to enabled, non-deleted entries. Nothing reads Codex's private memory files.
    """

    def __init__(self, db_path, *, timeout=15):
        self._timeout = timeout
        self._keeper = None
        if str(db_path) == ":memory:":
            self.db_path = f"file:codex_memory_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._keeper = self._connect()
        else:
            path = Path(db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(path)
            self._uri = False
        with self._transaction() as conn:
            schema = """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    parent_id TEXT REFERENCES categories(id),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                    category_id TEXT NOT NULL REFERENCES categories(id),
                    scope TEXT NOT NULL, target TEXT NOT NULL, tags TEXT NOT NULL,
                    match_mode TEXT NOT NULL DEFAULT 'exact',
                    plugin_id TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL, enabled INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL REFERENCES entries(id),
                    version INTEGER NOT NULL, saved_at TEXT NOT NULL,
                    entry_json TEXT NOT NULL, UNIQUE(entry_id, version)
                );
                CREATE INDEX IF NOT EXISTS entries_category ON entries(category_id);
                CREATE INDEX IF NOT EXISTS versions_entry ON versions(entry_id, version);
            """
            # executescript() commits implicitly; individual statements preserve
            # the initialization transaction and serialize concurrent first use.
            for statement in schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            # Existing memories keep their exact matching semantics. Adding the
            # columns does not edit entries, history, or the memory revision.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(entries)")}
            if "match_mode" not in columns:
                conn.execute("ALTER TABLE entries ADD COLUMN match_mode TEXT NOT NULL DEFAULT 'exact'")
            if "plugin_id" not in columns:
                conn.execute("ALTER TABLE entries ADD COLUMN plugin_id TEXT NOT NULL DEFAULT ''")
            initialized = conn.execute("SELECT value FROM metadata WHERE key = 'revision'").fetchone()
            if initialized is None:
                conn.execute("INSERT INTO metadata(key,value) VALUES ('revision','0')")
                now = _now()
                conn.executemany(
                    "INSERT OR IGNORE INTO categories(id,name,parent_id,created_at,updated_at) VALUES (?,?,NULL,?,?)",
                    [(key, name, now, now) for key, name in INITIAL_CATEGORIES],
                )

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self._timeout, uri=self._uri)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        return conn

    @contextmanager
    def _transaction(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _bump(conn):
        conn.execute("UPDATE metadata SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'")

    @staticmethod
    def _entry(row):
        data = dict(row)
        data["tags"] = json.loads(data["tags"])
        data["enabled"] = bool(data["enabled"])
        data["deleted"] = bool(data["deleted"])
        return data

    def _get(self, conn, entry_id):
        _identifier(entry_id)
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise StoreError("记忆条目不存在", 404)
        return self._entry(row)

    @staticmethod
    def _categories(conn):
        return {row["id"]: dict(row) for row in conn.execute("SELECT * FROM categories ORDER BY created_at,id")}

    @staticmethod
    def _check_graph(categories):
        # Validate every reference first: walking a child may visit a parent
        # whose own missing parent has not appeared in iteration order yet.
        for category in categories.values():
            parent = category.get("parent_id")
            if parent is not None and parent not in categories:
                raise StoreError("父分类不存在")
        for key, category in categories.items():
            parent = category.get("parent_id")
            seen = {key}
            while parent is not None:
                if parent in seen:
                    raise StoreError("分类不能形成循环")
                seen.add(parent)
                parent = categories[parent].get("parent_id")

    @staticmethod
    def _validate_entry(data, categories, *, importing=False):
        _object(data, "记忆条目")
        unexpected = set(data) - (IMPORT_FIELDS if importing else ENTRY_FIELDS)
        if unexpected:
            raise StoreError("不支持的条目字段: " + ", ".join(sorted(unexpected)))
        entry = {
            "title": "", "content": "", "category_id": "preferences", "scope": "global",
            "target": "", "match_mode": "exact", "plugin_id": "", "tags": [], "priority": 50, "enabled": True,
        }
        entry.update(data)
        entry["title"] = _text(entry["title"], "title", limit=500).strip()
        _text(entry["content"], "content", empty=True)
        _identifier(entry["category_id"], "category_id")
        if entry["category_id"] not in categories:
            raise StoreError("条目分类不存在")
        if not isinstance(entry["scope"], str) or entry["scope"] not in SCOPES:
            raise StoreError("scope 必须是 global、project、skill 或 plugin")
        _text(entry["target"], "target", empty=True, limit=4096)
        _text(entry["plugin_id"], "plugin_id", empty=True, limit=64)
        if not isinstance(entry["match_mode"], str) or entry["match_mode"] not in {"exact", "plugin_id"}:
            raise StoreError("match_mode 必须是 exact 或 plugin_id")
        if entry["match_mode"] == "plugin_id":
            if entry["scope"] != "plugin":
                raise StoreError("插件标识匹配仅适用于 plugin 作用域")
            entry["plugin_id"] = _plugin_id(entry["plugin_id"])
            if entry["target"]:
                raise StoreError("插件标识匹配不使用 target，请清空 target 后填写 plugin_id")
            entry["target"] = ""
        elif entry["scope"] == "global":
            # A leftover target after changing scope must not hide a global
            # memory from consumers selecting scope=global, target="".
            entry["target"] = ""
        elif not entry["target"].strip():
            raise StoreError("项目、技能和插件记忆必须指定 target")
        if entry["match_mode"] == "exact":
            entry["plugin_id"] = ""
        if not isinstance(entry["tags"], list) or len(entry["tags"]) > 100:
            raise StoreError("tags 必须是最多 100 项的字符串数组")
        tags = []
        for tag in entry["tags"]:
            tag = _text(tag, "tag", limit=100).strip()
            if tag not in tags:
                tags.append(tag)
        entry["tags"] = tags
        if type(entry["priority"]) is not int or not 0 <= entry["priority"] <= 100:
            raise StoreError("priority 必须是 0 到 100 的整数")
        if type(entry["enabled"]) is not bool:
            raise StoreError("enabled 必须是布尔值")
        if importing:
            if "id" in entry:
                _identifier(entry["id"])
            if "deleted" in entry and type(entry["deleted"]) is not bool:
                raise StoreError("deleted 必须是布尔值")
            if "version" in entry and (type(entry["version"]) is not int or entry["version"] < 1):
                raise StoreError("version 必须是正整数")
            for key in ("created_at", "updated_at"):
                if key in entry:
                    _text(entry[key], key, limit=64)
                    try:
                        datetime.fromisoformat(entry[key].replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise StoreError(f"{key} 必须是 ISO 8601 时间") from exc
        return entry

    @staticmethod
    def _save(conn, entry):
        fields = ("id", "title", "content", "category_id", "scope", "target", "match_mode", "plugin_id", "tags", "priority",
                  "enabled", "deleted", "created_at", "updated_at", "version")
        values = [json.dumps(entry[key], ensure_ascii=False) if key == "tags" else entry[key] for key in fields]
        conn.execute(
            "INSERT INTO entries (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ") "
            "ON CONFLICT(id) DO UPDATE SET " + ",".join(key + "=excluded." + key for key in fields if key != "id"),
            values,
        )
        conn.execute("INSERT INTO versions(entry_id,version,saved_at,entry_json) VALUES (?,?,?,?)",
                     (entry["id"], entry["version"], entry["updated_at"], json.dumps(entry, ensure_ascii=False)))

    def snapshot(self):
        with self._transaction() as conn:
            return {
                "categories": list(self._categories(conn).values()),
                "entries": [self._entry(row) for row in conn.execute("SELECT * FROM entries ORDER BY priority DESC,updated_at DESC,id")],
                "revision": int(conn.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]),
                "application": self._application_status(conn),
            }

    @staticmethod
    def _application_status(conn):
        metadata = {row["key"]: row["value"] for row in conn.execute(
            "SELECT key,value FROM metadata WHERE key IN "
            "('revision','last_read_revision','last_read_at','onboarding_decision')"
        )}
        revision = int(metadata["revision"])
        last_read = metadata.get("last_read_revision")
        last_read = int(last_read) if last_read is not None else None
        return {
            "revision": revision,
            "last_read_revision": last_read,
            "last_read_at": metadata.get("last_read_at"),
            "needs_refresh": revision > 0 and (last_read is None or last_read < revision),
            "onboarding_decision": metadata.get("onboarding_decision", "pending"),
        }

    def application_status(self):
        """Report the latest explicit plugin read, not every chat's state."""
        with self._transaction() as conn:
            return self._application_status(conn)

    def refresh(self, contexts=None):
        """Read relevant enabled memories and acknowledge this database revision.

        Global memories are always included; exact targets and plugin identifiers
        are distinct context selectors. Validation, the read, and receipt are atomic so a
        concurrent edit cannot be incorrectly acknowledged as already read.
        """
        contexts = [] if contexts is None else contexts
        if not isinstance(contexts, list) or len(contexts) > MAX_ENTRIES:
            raise StoreError("contexts 必须是最多 10000 项的对象数组")
        selected_contexts = set()
        selected_plugins = set()
        for context in contexts:
            _object(context, "context")
            if set(context) not in ({"scope", "target"}, {"scope", "plugin_id"}):
                raise StoreError("context 必须包含 scope 和 target，或 scope='plugin' 与 plugin_id；两种目标不能混用")
            scope = context["scope"]
            if not isinstance(scope, str) or scope not in SCOPES - {"global"}:
                raise StoreError("context scope 必须是 project、skill 或 plugin")
            if "plugin_id" in context:
                if scope != "plugin":
                    raise StoreError("plugin_id context 仅适用于 plugin 作用域")
                selected_plugins.add(_plugin_id(context["plugin_id"]))
            else:
                target = _text(context["target"], "context target", limit=4096)
                selected_contexts.add((scope, target))
        with self._transaction() as conn:
            entries = []
            for row in conn.execute(
                "SELECT * FROM entries WHERE enabled=1 AND deleted=0 "
                "ORDER BY priority DESC,updated_at DESC,id"
            ):
                if (row["scope"] == "global"
                        or (row["match_mode"] == "exact" and (row["scope"], row["target"]) in selected_contexts)
                        or (row["match_mode"] == "plugin_id" and row["plugin_id"] in selected_plugins)):
                    entries.append(self._entry(row))
            available_targets = [dict(row) for row in conn.execute(
                "SELECT scope,target,match_mode,plugin_id,COUNT(*) AS count FROM entries "
                "WHERE enabled=1 AND deleted=0 AND scope!='global' "
                "GROUP BY scope,target,match_mode,plugin_id ORDER BY scope,match_mode,target,plugin_id"
            )]
            revision = int(conn.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0])
            conn.executemany(
                "INSERT INTO metadata(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [("last_read_revision", str(revision)), ("last_read_at", _now())],
            )
            return {
                "entries": entries,
                "revision": revision,
                "available_targets": available_targets,
                "application": self._application_status(conn),
                "note": "仅记录本次插件已读取的记忆库版本；不代表所有 Codex 聊天已自动应用。其他聊天需要重新读取相关记忆。",
            }

    def set_onboarding_decision(self, decision):
        """Remember a user decision without changing memories or their revision."""
        if not isinstance(decision, str) or decision not in {"configured", "declined", "deferred"}:
            raise StoreError("decision 必须是 configured、declined 或 deferred")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO metadata(key,value) VALUES ('onboarding_decision',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (decision,),
            )
            return self._application_status(conn)

    def category_create(self, data):
        _object(data, "分类")
        if set(data) - {"name", "parent_id"}:
            raise StoreError("分类仅支持 name 和 parent_id")
        now = _now()
        category = {"id": "cat_" + uuid.uuid4().hex, "name": _text(data.get("name"), "name", limit=100).strip(),
                    "parent_id": data.get("parent_id"), "created_at": now, "updated_at": now}
        if category["parent_id"] is not None:
            _identifier(category["parent_id"], "parent_id")
        with self._transaction() as conn:
            categories = self._categories(conn)
            categories[category["id"]] = category
            self._check_graph(categories)
            conn.execute("INSERT INTO categories(id,name,parent_id,created_at,updated_at) VALUES (?,?,?,?,?)",
                         tuple(category[key] for key in ("id", "name", "parent_id", "created_at", "updated_at")))
            self._bump(conn)
        return category

    def category_update(self, category_id, patch):
        _identifier(category_id)
        _object(patch, "分类修改")
        if set(patch) - {"name", "parent_id"}:
            raise StoreError("分类仅支持 name 和 parent_id")
        with self._transaction() as conn:
            categories = self._categories(conn)
            if category_id not in categories:
                raise StoreError("分类不存在", 404)
            category = categories[category_id]
            if "name" in patch:
                category["name"] = _text(patch["name"], "name", limit=100).strip()
            if "parent_id" in patch:
                if patch["parent_id"] is not None:
                    _identifier(patch["parent_id"], "parent_id")
                category["parent_id"] = patch["parent_id"]
            self._check_graph(categories)
            category["updated_at"] = _now()
            conn.execute("UPDATE categories SET name=?,parent_id=?,updated_at=? WHERE id=?",
                         (category["name"], category["parent_id"], category["updated_at"], category_id))
            self._bump(conn)
            return category

    def category_delete(self, category_id):
        _identifier(category_id)
        with self._transaction() as conn:
            if category_id not in self._categories(conn):
                raise StoreError("分类不存在", 404)
            if conn.execute("SELECT 1 FROM categories WHERE parent_id=?", (category_id,)).fetchone() or conn.execute(
                "SELECT 1 FROM entries WHERE category_id=?", (category_id,)
            ).fetchone():
                raise StoreError("分类非空，请先移动条目和子分类", 409)
            conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
            self._bump(conn)
        return {"deleted": category_id}

    def entry_create(self, data):
        with self._transaction() as conn:
            entry = self._validate_entry(data, self._categories(conn))
            if conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] >= MAX_ENTRIES:
                raise StoreError("条目数量不能超过 10000", 409)
            now = _now()
            entry.update(id="mem_" + uuid.uuid4().hex, deleted=False, created_at=now, updated_at=now, version=1)
            self._save(conn, entry)
            self._bump(conn)
            return entry

    def entry_update(self, entry_id, patch):
        _object(patch, "条目修改")
        if set(patch) - ENTRY_FIELDS:
            raise StoreError("存在不可修改的条目字段")
        with self._transaction() as conn:
            current = self._get(conn, entry_id)
            if current["deleted"]:
                raise StoreError("请先恢复已删除条目", 409)
            editable = {key: current[key] for key in ENTRY_FIELDS}
            editable.update(patch)
            if patch.get("match_mode") == "plugin_id" and "target" not in patch:
                editable["target"] = ""
            current.update(self._validate_entry(editable, self._categories(conn)))
            current.update(updated_at=_now(), version=current["version"] + 1)
            self._save(conn, current)
            self._bump(conn)
            return current

    def entry_delete(self, entry_id):
        return self.bulk({"ids": [entry_id], "action": "delete"})["entries"][0]

    def bulk(self, data):
        _object(data, "批量操作")
        if set(data) - {"ids", "action", "category_id"}:
            raise StoreError("批量操作包含不支持的字段")
        ids, action = data.get("ids"), data.get("action")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_ENTRIES:
            raise StoreError("ids 必须是包含 1 到 10000 个条目 id 的数组")
        for entry_id in ids:
            _identifier(entry_id)
        ids = list(dict.fromkeys(ids))
        if not isinstance(action, str) or action not in {"enable", "disable", "move", "delete", "restore"}:
            raise StoreError("不支持的批量操作")
        with self._transaction() as conn:
            entries = [self._get(conn, entry_id) for entry_id in ids]
            if action == "move":
                _identifier(data.get("category_id"), "category_id")
                if data["category_id"] not in self._categories(conn):
                    raise StoreError("目标分类不存在")
            if action in {"enable", "disable", "move"} and any(entry["deleted"] for entry in entries):
                raise StoreError("请先恢复已删除条目", 409)
            now = _now()
            for entry in entries:
                if action in {"enable", "disable"}:
                    entry["enabled"] = action == "enable"
                elif action == "move":
                    entry["category_id"] = data["category_id"]
                elif action in {"delete", "restore"}:
                    entry["deleted"] = action == "delete"
                entry.update(updated_at=now, version=entry["version"] + 1)
                self._save(conn, entry)
            self._bump(conn)
            return {"entries": entries, "count": len(entries)}

    def history(self, entry_id):
        with self._transaction() as conn:
            self._get(conn, entry_id)
            return [{"version_id": row["id"], "version": row["version"], "saved_at": row["saved_at"],
                     "entry": json.loads(row["entry_json"])} for row in conn.execute(
                         "SELECT * FROM versions WHERE entry_id=? ORDER BY version DESC", (entry_id,))]

    def restore_version(self, entry_id, version_id):
        if type(version_id) is not int and not (isinstance(version_id, str) and version_id.isdecimal()):
            raise StoreError("version_id 必须是历史版本 id")
        if len(str(version_id)) > 18:
            raise StoreError("version_id 无效")
        with self._transaction() as conn:
            current = self._get(conn, entry_id)
            row = conn.execute("SELECT entry_json FROM versions WHERE entry_id=? AND id=?", (entry_id, version_id)).fetchone()
            if row is None:
                raise StoreError("历史版本不存在", 404)
            old = json.loads(row["entry_json"])
            restored = self._validate_entry({key: old[key] for key in ENTRY_FIELDS if key in old}, self._categories(conn))
            current.update(restored)
            current.update(deleted=old["deleted"], updated_at=_now(), version=current["version"] + 1)
            self._save(conn, current)
            self._bump(conn)
            return current

    def export_data(self):
        snapshot = self.snapshot()
        return {"format": "codex-memory-manager", "schema_version": 2,
                "categories": snapshot["categories"], "entries": snapshot["entries"]}

    def import_data(self, payload):
        _object(payload, "导入数据")
        if set(payload) - {"format", "schema_version", "categories", "entries"}:
            raise StoreError("导入数据包含不支持的字段")
        if payload.get("format", "codex-memory-manager") != "codex-memory-manager":
            raise StoreError("导入文件格式不匹配")
        if type(payload.get("schema_version", 1)) is not int or payload.get("schema_version", 1) not in {1, 2}:
            raise StoreError("不支持的导入文件版本")
        category_items, entry_items = payload.get("categories", []), payload.get("entries", [])
        if not isinstance(category_items, list) or not isinstance(entry_items, list):
            raise StoreError("categories 和 entries 必须是数组")
        if len(category_items) > MAX_ENTRIES or len(entry_items) > MAX_ENTRIES:
            raise StoreError("导入项目过多")
        with self._transaction() as conn:
            categories = self._categories(conn)
            category_ids, entry_ids = set(), set()
            imported_categories, imported_entries = [], []
            now = _now()
            for data in category_items:
                _object(data, "导入分类")
                if set(data) - {"id", "name", "parent_id", "created_at", "updated_at"}:
                    raise StoreError("导入分类包含不支持的字段")
                key = _identifier(data.get("id"))
                if key in category_ids:
                    raise StoreError("导入分类 id 重复")
                category_ids.add(key)
                category = {"id": key, "name": _text(data.get("name"), "name", limit=100).strip(),
                            "parent_id": data.get("parent_id"),
                            "created_at": categories.get(key, {}).get("created_at", now), "updated_at": now}
                if category["parent_id"] is not None:
                    _identifier(category["parent_id"], "parent_id")
                categories[key] = category
                imported_categories.append(category)
            self._check_graph(categories)
            existing_ids = {row[0] for row in conn.execute("SELECT id FROM entries")}
            for data in entry_items:
                entry = self._validate_entry(data, categories, importing=True)
                entry_id = entry.get("id", "mem_" + uuid.uuid4().hex)
                if entry_id in entry_ids:
                    raise StoreError("导入条目 id 重复")
                entry_ids.add(entry_id)
                current = self._get(conn, entry_id) if entry_id in existing_ids else None
                entry.update(id=entry_id, deleted=entry.get("deleted", False),
                             created_at=current["created_at"] if current else entry.get("created_at", now),
                             updated_at=now, version=current["version"] + 1 if current else 1)
                imported_entries.append(entry)
            if len(existing_ids | entry_ids) > MAX_ENTRIES:
                raise StoreError("条目数量不能超过 10000", 409)
            # Defer references so child categories may precede their parents.
            conn.execute("PRAGMA defer_foreign_keys = ON")
            for category in imported_categories:
                conn.execute(
                    "INSERT INTO categories(id,name,parent_id,created_at,updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name,parent_id=excluded.parent_id,updated_at=excluded.updated_at",
                    tuple(category[key] for key in ("id", "name", "parent_id", "created_at", "updated_at")),
                )
            for entry in imported_entries:
                self._save(conn, entry)
            if imported_categories or imported_entries:
                self._bump(conn)
            return {"categories": len(imported_categories), "entries": len(imported_entries)}

    def select_entries(self, filters=None):
        filters = {} if filters is None else _object(filters, "筛选条件")
        if set(filters) - {"scope", "target", "plugin_id", "match_mode", "query", "enabled"}:
            raise StoreError("不支持的筛选条件")
        scope = filters.get("scope")
        if scope is not None and (not isinstance(scope, str) or scope not in SCOPES):
            raise StoreError("scope 无效")
        target = filters.get("target")
        if target is not None:
            _text(target, "target", empty=True, limit=4096)
        plugin_id = filters.get("plugin_id")
        if plugin_id is not None:
            _text(plugin_id, "plugin_id", empty=True, limit=64)
            if plugin_id:
                _plugin_id(plugin_id)
        if target and plugin_id:
            raise StoreError("target 与 plugin_id 不能同时为非空；请选择精确匹配或插件标识匹配")
        match_mode = filters.get("match_mode")
        if match_mode is not None and (not isinstance(match_mode, str) or match_mode not in {"exact", "plugin_id"}):
            raise StoreError("match_mode 必须是 exact 或 plugin_id")
        query = _text(filters.get("query", ""), "query", empty=True, limit=4096).casefold().strip()
        enabled = filters.get("enabled", True)
        if enabled is not None and type(enabled) is not bool:
            raise StoreError("enabled 必须是布尔值或 null")
        with self._transaction() as conn:
            selected = []
            for row in conn.execute("SELECT * FROM entries WHERE deleted=0 ORDER BY priority DESC,updated_at DESC,id LIMIT 10000"):
                entry = self._entry(row)
                if scope is not None and entry["scope"] != scope:
                    continue
                if match_mode is not None and entry["match_mode"] != match_mode:
                    continue
                if plugin_id and (entry["match_mode"] != "plugin_id" or entry["plugin_id"] != plugin_id):
                    continue
                if target is not None and not plugin_id and (entry["match_mode"] != "exact" or entry["target"] != target):
                    continue
                if enabled is not None and entry["enabled"] != enabled:
                    continue
                haystack = "\n".join([entry["title"], entry["content"], *entry["tags"]]).casefold()
                if query and query not in haystack:
                    continue
                selected.append(entry)
            return selected
