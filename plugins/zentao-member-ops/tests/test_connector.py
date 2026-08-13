#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MCP_ROOT = Path(__file__).resolve().parents[1] / "mcp"
SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(MCP_ROOT))
sys.path.insert(0, str(SCRIPTS_ROOT))

from configure import build_parser, set_profile  # noqa: E402
from policy import (  # noqa: E402
    BUG_ACTIONS,
    STORY_ACTIONS,
    CapabilityResolver,
    ConfirmationTickets,
    sanitize_body,
)
from server import ZentaoMemberTools  # noqa: E402
from zentao_client import (  # noqa: E402
    ConnectorConfig,
    HttpResult,
    ZentaoClient,
    ZentaoError,
    parse_write_confirmation_mode,
)


def group(
    account: str,
    privileges: dict[str, list[str]],
    *,
    group_id: int = 1,
    name: str = "研发",
    role: str = "dev",
) -> dict:
    return {
        "id": group_id,
        "name": name,
        "role": role,
        "accounts": {account: account},
        "privs": {
            module: {method: True for method in methods}
            for module, methods in privileges.items()
        },
    }


def complete_solution_analysis() -> dict:
    return {
        "symptom_summary": "Opening the camera crashes the application.",
        "reproduction_conditions": "Use the affected build and open Camera once.",
        "evidence": ["Bug steps contain the failing entry path.", "logcat is attached."],
        "inspected_attachments": ["file 21: logcat"],
        "source_findings": ["Camera startup is the candidate fault boundary."],
        "root_cause": "The current evidence points to an unchecked camera startup failure.",
        "confidence": "medium",
        "impact_and_risks": ["Camera remains unavailable until the process restarts."],
        "recommended_solution": "Handle startup failure and preserve a diagnostic error.",
        "implementation_steps": ["Guard camera startup.", "Add a failure-path test."],
        "candidate_files": ["camera/startup/CameraController.java"],
        "verification_plan": ["Reproduce before the fix.", "Run camera startup regression."],
        "open_questions": ["Confirm the exact device build fingerprint."],
        "handoff_instructions": "Re-read the package evidence, confirm the source mapping, then ask before editing source.",
        "analysis_status": "ready",
    }


class FakeClient:
    def __init__(
        self,
        account: str,
        groups: list[dict],
        *,
        projects: list[dict] | None = None,
        bugs: list[dict] | None = None,
        bug_detail: dict | None = None,
        source_roots: dict[int, str] | None = None,
        products: list[dict] | None = None,
        story_detail: dict | None = None,
        task_detail: dict | None = None,
        write_confirmation_mode: str = "manual",
        account_role: str = "dev",
    ) -> None:
        self.account = account
        self.groups = groups
        self.projects = projects if projects is not None else [{"id": 101, "name": "Local test"}]
        self.bugs = bugs if bugs is not None else []
        self.bug_detail = bug_detail
        self.products = products if products is not None else [{"id": 7, "name": "Local product"}]
        self.story_detail = story_detail
        self.task_detail = task_detail
        self.account_role = account_role
        self.execution_detail = {"id": 2, "project": 101, "name": "Local sprint"}
        self.downloads: list[tuple[int, Path]] = []
        self.uploads: list[tuple[Path, str]] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.gets: list[tuple[str, dict]] = []
        self.project_gets = 0
        self.config = SimpleNamespace(
            account=account,
            token="",
            profile_name="local-test",
            api_base="http://127.0.0.1/zentao/api.php/v1",
            web_base="http://127.0.0.1/zentao",
            allowed_project_ids=(101,),
            credentials_configured=True,
            transport_encrypted=False,
            credentials_path=Path("/tmp/fake-zentao-credentials.json"),
            source_roots=source_roots or {},
            write_confirmation_mode=write_confirmation_mode,
        )

    def get(self, path: str, query: dict | None = None):
        self.gets.append((path, dict(query or {})))
        if path == "user":
            return {
                "profile": {
                    "account": self.account,
                    "realname": self.account,
                    "role": {"code": self.account_role, "name": self.account_role},
                    "admin": False,
                }
            }
        if path == "groups":
            return self.groups
        if path == "projects":
            self.project_gets += 1
            return {"projects": self.projects}
        if path == "projects/101":
            return {"id": 101, "products": self.products}
        if path == "products":
            return {"products": self.products}
        if path == "projects/101/bugs":
            return {"bugs": self.bugs, "total": len(self.bugs)}
        if path == "projects/101/stories":
            return {"stories": [], "total": 0}
        if path.startswith("bugs/") and self.bug_detail is not None:
            return self.bug_detail
        if path.startswith("stories/") and self.story_detail is not None:
            return self.story_detail
        if path.startswith("tasks/") and self.task_detail is not None:
            return self.task_detail
        if path == "executions/2":
            return self.execution_detail
        raise AssertionError(f"Unexpected fake GET {path} {query}")

    def download_file(self, file_id: int, target: Path):
        self.downloads.append((file_id, target))
        return {"file_id": file_id, "path": str(target), "size": 12, "content_type": "image/png"}

    def upload_file(self, source_path: Path, uid: str, *, max_bytes: int):
        self.uploads.append((source_path, uid))
        return {"id": 801, "name": source_path.name, "size": source_path.stat().st_size}

    def write(self, method: str, path: str, body: dict):
        self.writes.append((method, path, body))
        return {"id": 901, "ok": True}

    def add_comment(self, object_type: str, object_id: int, comment: str):
        self.writes.append(("COMMENT", f"{object_type}/{object_id}", {"comment": comment}))
        return {"id": 902, "ok": True}


class PolicyTests(unittest.TestCase):
    def test_write_confirmation_mode_rejects_unbounded_auto(self) -> None:
        self.assertEqual(parse_write_confirmation_mode(None), "manual")
        self.assertEqual(parse_write_confirmation_mode("auto"), "safe-auto")
        with self.assertRaisesRegex(ZentaoError, "manual, safe-auto"):
            parse_write_confirmation_mode("all-auto")

    def test_capability_resolver_uses_only_current_accounts_groups(self) -> None:
        own = group("dev1", {"bug": ["browse", "resolve"]})
        someone_else = group(
            "qa1",
            {"bug": ["close", "delete"], "story": ["create"]},
            group_id=2,
            name="测试",
        )
        identity = CapabilityResolver(FakeClient("dev1", [own, someone_else])).resolve()
        self.assertTrue(identity.has("bug", "resolve"))
        self.assertFalse(identity.has("bug", "close"))
        self.assertFalse(identity.has("story", "create"))
        self.assertEqual([item["id"] for item in identity.groups], [1])

    def test_confirmation_ticket_is_signed_short_lived_and_single_use(self) -> None:
        tickets = ConfirmationTickets(secret=b"x" * 32)
        operation = {"mode": "action", "object_type": "bug", "object_id": 8}
        token, _ = tickets.issue(operation, 60)
        self.assertEqual(tickets.verify_and_consume(token), operation)
        with self.assertRaisesRegex(ZentaoError, "already used"):
            tickets.verify_and_consume(token)

        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaisesRegex(ZentaoError, "signature"):
            ConfirmationTickets(secret=b"x" * 32).verify_and_consume(tampered)

        with patch("policy.time.time", return_value=1000):
            expiring, _ = ConfirmationTickets(secret=b"y" * 32).issue(operation, 60)
        with patch("policy.time.time", return_value=1061):
            with self.assertRaisesRegex(ZentaoError, "expired"):
                ConfirmationTickets(secret=b"y" * 32).verify_and_consume(expiring)

    def test_body_schema_blocks_unknown_and_missing_fields(self) -> None:
        with self.assertRaisesRegex(ZentaoError, "Unsupported fields"):
            sanitize_body({"resolution": "fixed", "surprise": True}, BUG_ACTIONS["resolve"])
        with self.assertRaisesRegex(ZentaoError, "Missing required"):
            sanitize_body({}, BUG_ACTIONS["resolve"])
        self.assertEqual(
            sanitize_body({"resolution": "fixed", "comment": "done"}, BUG_ACTIONS["resolve"]),
            {"resolution": "fixed", "comment": "done"},
        )
        with self.assertRaisesRegex(ZentaoError, "pri, estimate"):
            sanitize_body({"result": "pass"}, STORY_ACTIONS["review"])
        self.assertEqual(
            sanitize_body(
                {"result": "pass", "pri": 2, "estimate": 8},
                STORY_ACTIONS["review"],
            ),
            {"result": "pass", "pri": 2, "estimate": 8},
        )


class DynamicToolTests(unittest.TestCase):
    def test_connection_status_declares_required_first_configuration_inputs(self) -> None:
        client = FakeClient("", [])
        client.config.credentials_configured = False
        tools = ZentaoMemberTools(client=client)
        status = tools.connection_status({})

        self.assertFalse(status["ok"])
        contract = status["first_configuration"]
        self.assertTrue(contract["must_ask_user_before_configuring"])
        required = " ".join(contract["required_inputs"])
        self.assertIn("site address", required)
        self.assertIn("member account", required)
        self.assertIn("password", required)
        self.assertIn("project IDs", required)
        self.assertIn("Ask the user", status["next_step"])

    def test_limited_member_gets_no_write_tools(self) -> None:
        client = FakeClient("limited", [group("limited", {"my": ["index"]}, name="受限")])
        names = {item["name"] for item in ZentaoMemberTools(client=client).tool_definitions()}
        self.assertEqual(
            names,
            {
                "connection_status",
                "who_am_i",
                "list_my_work",
                "get_developer_daily_automation_spec",
            },
        )
        self.assertFalse(any("delete" in name for name in names))

    def test_daily_automation_requires_developer_role_and_live_bug_read(self) -> None:
        privileges = {"project": ["browse"], "bug": ["browse", "view"]}
        developer = ZentaoMemberTools(
            client=FakeClient("dev1", [group("dev1", privileges)])
        ).get_developer_daily_automation_spec({})
        self.assertTrue(developer["eligible"])
        self.assertEqual(developer["kind"], "heartbeat")
        self.assertEqual(developer["schedule"]["hour"], 7)
        self.assertIn("不要修改源码", developer["prompt"])
        self.assertIn("不要准备或执行任何禅道", developer["prompt"])

        tester = ZentaoMemberTools(
            client=FakeClient(
                "qa1",
                [group("qa1", privileges, name="测试", role="qa")],
                account_role="qa",
            )
        ).get_developer_daily_automation_spec({})
        self.assertFalse(tester["eligible"])

    def test_developer_gets_bug_analysis_and_only_permitted_writes(self) -> None:
        privileges = {
            "project": ["browse", "view"],
            "bug": ["browse", "view", "resolve"],
            "action": ["comment"],
        }
        client = FakeClient("dev1", [group("dev1", privileges)])
        definitions = ZentaoMemberTools(client=client).tool_definitions()
        by_name = {item["name"]: item for item in definitions}
        self.assertIn("scan_new_bugs", by_name)
        self.assertIn("scan_new_bugs_scheduled", by_name)
        self.assertIn("save_bug_solution_package", by_name)
        self.assertIn("get_bug_solution_package", by_name)
        self.assertIn("list_bug_solution_packages", by_name)
        self.assertIn("get_bug_analysis_context", by_name)
        self.assertIn("download_bug_attachment", by_name)
        self.assertIn("prepare_bug_action", by_name)
        self.assertIn("execute_confirmed_write", by_name)
        self.assertNotIn("prepare_story_action", by_name)
        self.assertNotIn("prepare_task_action", by_name)
        self.assertFalse(any("delete" in name for name in by_name))
        actions = by_name["prepare_bug_action"]["inputSchema"]["properties"]["action"]["enum"]
        self.assertEqual(set(actions), {"resolve", "comment"})
        self.assertFalse(by_name["execute_confirmed_write"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["execute_confirmed_write"]["annotations"]["destructiveHint"])
        self.assertFalse(by_name["save_bug_solution_package"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["save_bug_solution_package"]["annotations"]["destructiveHint"])

    def test_container_creation_tools_follow_member_privileges(self) -> None:
        privileges = {
            "product": ["all", "create"],
            "project": ["browse", "create"],
        }
        client = FakeClient("pm1", [group("pm1", privileges, name="项目经理")])
        by_name = {
            item["name"]: item
            for item in ZentaoMemberTools(client=client).tool_definitions()
        }
        self.assertIn("list_products", by_name)
        self.assertIn("get_container_creation_schema", by_name)
        self.assertIn("prepare_create_container", by_name)
        self.assertIn("execute_confirmed_write", by_name)
        container_types = by_name["prepare_create_container"]["inputSchema"]["properties"]["object_type"]["enum"]
        self.assertEqual(set(container_types), {"product", "project"})
        self.assertNotIn("execution", container_types)


class BugAnalysisTests(unittest.TestCase):
    def _client(self) -> FakeClient:
        privileges = {"project": ["browse"], "bug": ["browse", "view"]}
        bug_detail = {
            "id": 12,
            "project": 101,
            "title": "Camera crash",
            "steps": '<p>Open camera</p><img src="/zentao/file-read-22.png">',
            "files": {
                "21": {"id": 21, "title": "logcat", "extension": "txt", "size": 10},
            },
            "actions": [
                {"id": 1, "actor": "qa1", "action": "opened", "comment": "first comment"},
                {"id": 2, "actor": "dev1", "action": "assigned", "comment": ""},
            ],
        }
        bugs = [
            {"id": 12, "project": 101, "title": "Camera crash", "assignedTo": {"account": "dev1"}},
            {"id": 11, "project": 101, "title": "Audio issue", "assignedTo": {"account": "qa1"}},
            {"id": 10, "project": 101, "title": "Old issue", "assignedTo": {"account": "dev1"}},
        ]
        return FakeClient("dev1", [group("dev1", privileges)], bugs=bugs, bug_detail=bug_detail)

    def test_scan_initializes_then_returns_new_bugs(self) -> None:
        tools = ZentaoMemberTools(client=self._client())
        baseline = tools.scan_new_bugs({"project_id": 101})
        self.assertTrue(baseline["baseline_initialized"])
        self.assertEqual(baseline["next_after_bug_id"], 12)
        self.assertEqual(baseline["new_bugs"], [])

        found = tools.scan_new_bugs({"project_id": 101, "after_bug_id": 10})
        self.assertEqual([item["id"] for item in found["new_bugs"]], [11, 12])
        self.assertEqual(found["next_after_bug_id"], 12)

        assigned = tools.scan_new_bugs(
            {"project_id": 101, "after_bug_id": 10, "scope": "assigned_to_me"}
        )
        self.assertEqual([item["id"] for item in assigned["new_bugs"]], [12])

    def test_context_extracts_comments_attachments_and_embedded_images(self) -> None:
        tools = ZentaoMemberTools(client=self._client())
        context = tools.get_bug_analysis_context({"bug_id": 12})
        self.assertEqual(context["project_id"], 101)
        self.assertEqual([item["id"] for item in context["comments"]], [1])
        self.assertEqual([item["id"] for item in context["attachments"]], [21])
        self.assertEqual(context["embedded_images"][0]["file_id"], 22)
        self.assertEqual(context["downloadable_file_ids"], [21, 22])
        contract = context["analysis_contract"]
        self.assertEqual(contract["delivery"], "codex_conversation_only")
        self.assertIn("ask the user", contract["source_change_policy"].lower())
        self.assertIn("do not prepare or write", contract["zentao_writeback_policy"].lower())
        self.assertTrue(
            any("do not turn the analysis into a zentao comment" in step.lower()
                for step in contract["recommended_next_steps"])
        )

    def test_scheduled_scan_persists_zero_baseline_then_detects_first_bug(self) -> None:
        client = self._client()
        client.bugs = []
        with tempfile.TemporaryDirectory() as directory:
            tools = ZentaoMemberTools(client=client, state_root=Path(directory))
            baseline = tools.scan_new_bugs_scheduled({"project_id": 101})
            self.assertEqual(baseline["next_after_bug_id"], 0)
            self.assertEqual(baseline["pending_bug_ids"], [])

            client.bugs = [
                {"id": 1, "project": 101, "title": "First Bug", "assignedTo": {"account": "dev1"}}
            ]
            detected = tools.scan_new_bugs_scheduled({"project_id": 101})
            self.assertEqual(detected["pending_bug_ids"], [1])
            repeated = tools.scan_new_bugs_scheduled({"project_id": 101})
            self.assertEqual(repeated["pending_bug_ids"], [1])
        self.assertEqual(client.writes, [])

    def test_solution_package_is_complete_cross_session_and_clears_pending(self) -> None:
        client = self._client()
        with tempfile.TemporaryDirectory() as directory:
            tools = ZentaoMemberTools(client=client, state_root=Path(directory))
            tools.scan_new_bugs_scheduled({"project_id": 101})
            client.bugs.insert(
                0,
                {"id": 13, "project": 101, "title": "New camera crash", "assignedTo": {"account": "dev1"}},
            )
            client.bug_detail = {
                **client.bug_detail,
                "id": 13,
                "title": "New camera crash",
                "status": "active",
                "customEvidence": "preserve arbitrary scoped Bug fields",
            }
            detected = tools.scan_new_bugs_scheduled({"project_id": 101})
            self.assertEqual(detected["pending_bug_ids"], [13])
            with self.assertRaisesRegex(ZentaoError, "solution package"):
                tools.complete_scheduled_bug({"project_id": 101, "bug_id": 13})

            saved = tools.save_bug_solution_package(
                {"bug_id": 13, "analysis": complete_solution_analysis()}
            )
            self.assertTrue(saved["handoff_ready"])
            package_path = Path(saved["package_path"])
            markdown_path = Path(saved["markdown_path"])
            self.assertTrue(package_path.is_file())
            self.assertTrue(markdown_path.is_file())

            handoff = tools.get_bug_solution_package({"project_id": 101, "bug_id": 13})
            package = handoff["package"]
            self.assertEqual(package["bug_context"]["bug"]["customEvidence"], "preserve arbitrary scoped Bug fields")
            self.assertEqual([item["id"] for item in package["bug_context"]["comments"]], [1])
            self.assertEqual([item["id"] for item in package["bug_context"]["attachments"]], [21])
            self.assertEqual(package["analysis"]["analysis_status"], "ready")
            self.assertIn("# Bug #13 解决方案包", handoff["markdown"])
            listed = tools.list_bug_solution_packages({"project_id": 101})
            self.assertEqual([item["bug_id"] for item in listed["packages"]], [13])

            completed = tools.complete_scheduled_bug({"project_id": 101, "bug_id": 13})
            self.assertEqual(completed["pending_bug_ids"], [])
        self.assertEqual(client.writes, [])

    def test_solution_package_rejects_incomplete_analysis_without_writing(self) -> None:
        client = self._client()
        analysis = complete_solution_analysis()
        del analysis["verification_plan"]
        with tempfile.TemporaryDirectory() as directory:
            tools = ZentaoMemberTools(client=client, state_root=Path(directory))
            with self.assertRaisesRegex(ZentaoError, "verification_plan"):
                tools.save_bug_solution_package({"bug_id": 12, "analysis": analysis})
            self.assertEqual(list(Path(directory).rglob("solution-package.json")), [])
        self.assertEqual(client.writes, [])

    def test_attachment_download_is_bound_to_bug_references(self) -> None:
        client = self._client()
        tools = ZentaoMemberTools(client=client)
        result = tools.download_bug_attachment({"bug_id": 12, "file_id": 21})
        self.assertEqual(result["download"]["file_id"], 21)
        self.assertEqual(client.downloads[0][0], 21)
        with self.assertRaisesRegex(ZentaoError, "not referenced"):
            tools.download_bug_attachment({"bug_id": 12, "file_id": 999})

    def test_prepared_write_aborts_if_item_changes(self) -> None:
        client = self._client()
        client.groups[0]["privs"]["bug"]["resolve"] = True
        client.bug_detail.update({"status": "active", "lastEditedDate": "2026-01-01 10:00:00"})
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_bug_action(
            {
                "bug_id": 12,
                "action": "resolve",
                "data": {"resolution": "fixed", "comment": "draft"},
            }
        )
        self.assertTrue(prepared["requires_user_confirmation"])
        self.assertEqual(client.writes, [])

        client.bug_detail["lastEditedDate"] = "2026-01-01 10:01:00"
        with self.assertRaisesRegex(ZentaoError, "changed after preparation"):
            tools.execute_confirmed_write(
                {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
            )
        self.assertEqual(client.writes, [])

    def test_prepared_write_aborts_if_privilege_is_revoked(self) -> None:
        client = self._client()
        client.groups[0]["privs"]["bug"]["resolve"] = True
        client.bug_detail.update({"status": "active", "lastEditedDate": "2026-01-01 10:00:00"})
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_bug_action(
            {"bug_id": 12, "action": "resolve", "data": {"resolution": "fixed"}}
        )
        client.groups[0]["privs"]["bug"]["resolve"] = False
        with self.assertRaisesRegex(ZentaoError, "no longer has"):
            tools.execute_confirmed_write(
                {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
            )
        self.assertEqual(client.writes, [])

    def test_empty_close_uses_transport_sentinel_and_rejects_false_success(self) -> None:
        client = self._client()
        client.groups[0]["privs"]["bug"]["close"] = True
        client.bug_detail.update(
            {
                "status": "resolved",
                "resolution": "fixed",
                "lastEditedDate": "2026-01-01 10:00:00",
            }
        )
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_bug_action(
            {"bug_id": 12, "action": "close", "data": {}}
        )
        self.assertEqual(prepared["preview"]["body"], {"comment": ""})
        self.assertIn("does not create a comment", prepared["preview"]["transport_note"])

        with self.assertRaisesRegex(ZentaoError, "not verified as successful"):
            tools.execute_confirmed_write(
                {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
            )
        self.assertEqual(client.writes[0][0:2], ("POST", "bugs/12/close"))
        self.assertEqual(client.writes[0][2], {"comment": ""})

    def test_close_reports_success_only_after_closed_status_is_observed(self) -> None:
        client = self._client()
        client.groups[0]["privs"]["bug"]["close"] = True
        client.bug_detail.update(
            {
                "status": "resolved",
                "resolution": "fixed",
                "lastEditedDate": "2026-01-01 10:00:00",
            }
        )
        original_write = client.write

        def close_write(method: str, path: str, body: dict):
            result = original_write(method, path, body)
            client.bug_detail.update(
                {"status": "closed", "lastEditedDate": "2026-01-01 10:01:00"}
            )
            return result

        client.write = close_write  # type: ignore[method-assign]
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_bug_action(
            {"bug_id": 12, "action": "close", "data": {}}
        )
        executed = tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(executed["postcondition"]["actual_status"], "closed")
        self.assertTrue(executed["postcondition"]["verified"])
        screenshot = executed["ui_verification"]
        self.assertTrue(screenshot["required"])
        self.assertEqual(screenshot["status"], "pending_real_ui_screenshot")
        self.assertEqual(screenshot["object_type"], "bug")
        self.assertEqual(screenshot["object_id"], 12)
        self.assertEqual(
            screenshot["web_url"], "http://127.0.0.1/zentao/bug-view-12.html"
        )
        self.assertIn("never repeat the write", screenshot["instruction"])


class ConfirmationPolicyAndCacheTests(unittest.TestCase):
    @staticmethod
    def _client(
        mode: str,
        *,
        extra_bug_privileges: list[str] | None = None,
        status: str = "active",
    ) -> FakeClient:
        bug_privileges = ["browse", "view", *(extra_bug_privileges or [])]
        privileges = {
            "project": ["browse", "view"],
            "bug": bug_privileges,
            "action": ["comment"],
        }
        return FakeClient(
            "dev1",
            [group("dev1", privileges)],
            bug_detail={
                "id": 12,
                "project": 101,
                "title": "Confirmation policy test",
                "status": status,
                "lastEditedDate": "2026-01-01 10:00:00",
            },
            write_confirmation_mode=mode,
        )

    def test_manual_requires_user_confirmation_even_for_low_risk_comment(self) -> None:
        client = self._client("manual")
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_bug_action(
            {
                "bug_id": 12,
                "action": "comment",
                "data": {"comment": "Verified locally."},
            }
        )
        self.assertTrue(prepared["requires_user_confirmation"])
        self.assertFalse(prepared["auto_confirmation_allowed"])
        self.assertEqual(prepared["confirmation_policy"]["risk_level"], "low")
        with self.assertRaisesRegex(ZentaoError, "not eligible for automatic confirmation"):
            tools.execute_confirmed_write(
                {
                    "confirmation_token": prepared["confirmation_token"],
                    "approval": "policy",
                }
            )
        self.assertEqual(client.writes, [])

        prepared_without_source = tools.prepare_bug_action(
            {
                "bug_id": 12,
                "action": "comment",
                "data": {"comment": "Second preview."},
            }
        )
        with self.assertRaisesRegex(ZentaoError, "approval must be user or policy"):
            tools.execute_confirmed_write(
                {"confirmation_token": prepared_without_source["confirmation_token"]}
            )
        self.assertEqual(client.writes, [])

    def test_safe_auto_policy_confirms_only_low_risk_comment_and_refreshes(self) -> None:
        client = self._client("safe-auto")
        tools = ZentaoMemberTools(client=client)
        tools.list_projects({})
        prepared = tools.prepare_bug_action(
            {
                "bug_id": 12,
                "action": "comment",
                "data": {"comment": "Verified locally."},
            }
        )
        self.assertFalse(prepared["requires_user_confirmation"])
        self.assertTrue(prepared["auto_confirmation_allowed"])
        executed = tools.execute_confirmed_write(
            {
                "confirmation_token": prepared["confirmation_token"],
                "approval": "policy",
            }
        )
        self.assertEqual(client.writes[0][0], "COMMENT")
        self.assertEqual(executed["approval"], "policy")
        self.assertTrue(executed["cache_refresh"]["authoritative_read"]["ok"])
        detail_queries = [
            query for path, query in client.gets if path == "bugs/12"
        ]
        self.assertTrue(any("_mcp_refresh" in query for query in detail_queries))
        project_queries = [query for path, query in client.gets if path == "projects"]
        self.assertTrue(any("_mcp_refresh" in query for query in project_queries))
        self.assertTrue(
            executed["cache_refresh"]["local_caches_invalidated"]["visible_projects"]
        )
        project_gets_after_refresh = client.project_gets
        tools.list_projects({})
        self.assertEqual(client.project_gets, project_gets_after_refresh)

    def test_safe_auto_still_blocks_risky_status_change_without_user(self) -> None:
        client = self._client(
            "safe-auto", extra_bug_privileges=["close"], status="resolved"
        )
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_bug_action(
            {"bug_id": 12, "action": "close", "data": {}}
        )
        self.assertTrue(prepared["requires_user_confirmation"])
        self.assertEqual(prepared["confirmation_policy"]["risk_level"], "high")
        with self.assertRaisesRegex(ZentaoError, "not eligible for automatic confirmation"):
            tools.execute_confirmed_write(
                {
                    "confirmation_token": prepared["confirmation_token"],
                    "approval": "policy",
                }
            )
        self.assertEqual(client.writes, [])

    def test_read_tools_expose_refresh_and_processing_contracts(self) -> None:
        client = self._client("manual")
        tools = ZentaoMemberTools(client=client)
        by_name = {item["name"]: item for item in tools.tool_definitions()}
        self.assertIn("refresh", by_name["get_bug"]["inputSchema"]["properties"])
        self.assertIn("refresh", by_name["list_bugs"]["inputSchema"]["properties"])
        bug = tools.get_bug({"bug_id": 12, "refresh": True})
        self.assertTrue(bug["cache_refreshed"])
        self.assertTrue(bug["processing_contract"]["no_automatic_writeback"])
        context = tools.get_bug_analysis_context({"bug_id": 12})
        contract = context["analysis_contract"]
        self.assertIn("current Codex instance", contract["codex_role"])
        self.assertIn("remind the customer", contract["post_verification_policy"])



class StoryScopeTests(unittest.TestCase):
    def test_product_story_is_scoped_through_allowed_project_product(self) -> None:
        privileges = {"project": ["browse"], "story": ["browse", "view"]}
        client = FakeClient(
            "po1",
            [group("po1", privileges, name="产品经理")],
            story_detail={"id": 31, "product": 7, "title": "Allowed product story"},
        )
        result = ZentaoMemberTools(client=client).get_story({"story_id": 31})
        self.assertEqual(result["project_id"], 101)
        self.assertEqual(result["story"]["id"], 31)

    def test_product_story_outside_allowed_project_is_rejected(self) -> None:
        privileges = {"project": ["browse"], "story": ["browse", "view"]}
        client = FakeClient(
            "po1",
            [group("po1", privileges, name="产品经理")],
            story_detail={"id": 32, "product": 999, "title": "Other product story"},
        )
        with self.assertRaisesRegex(ZentaoError, "Cannot prove"):
            ZentaoMemberTools(client=client).get_story({"story_id": 32})

    def test_review_pass_requires_current_values_and_verifies_active_status(self) -> None:
        privileges = {
            "project": ["browse"],
            "story": ["browse", "view", "review", "edit"],
        }
        client = FakeClient(
            "po1",
            [group("po1", privileges, name="产品经理")],
            story_detail={
                "id": 31,
                "product": 7,
                "title": "Reviewable story",
                "status": "reviewing",
                "pri": 2,
                "estimate": 8,
                "lastEditedDate": None,
            },
        )
        original_write = client.write

        def review_write(method: str, path: str, body: dict):
            result = original_write(method, path, body)
            client.story_detail.update(
                {"status": "active", "lastEditedDate": "2026-01-01 10:01:00"}
            )
            return result

        client.write = review_write  # type: ignore[method-assign]
        tools = ZentaoMemberTools(client=client)
        with self.assertRaisesRegex(ZentaoError, "pri, estimate"):
            tools.prepare_story_action(
                {"story_id": 31, "action": "review", "data": {"result": "pass"}}
            )
        prepared = tools.prepare_story_action(
            {
                "story_id": 31,
                "action": "review",
                "data": {"result": "pass", "pri": 2, "estimate": 8},
            }
        )
        executed = tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(executed["postcondition"]["actual_status"], "active")
        self.assertTrue(executed["postcondition"]["verified"])

    def test_review_is_hidden_without_edit_compatibility_privilege(self) -> None:
        privileges = {
            "project": ["browse"],
            "story": ["browse", "view", "review"],
        }
        client = FakeClient("reviewer", [group("reviewer", privileges)])
        tools = ZentaoMemberTools(client=client)
        actions = tools.identity().allowed_actions("story")
        self.assertNotIn("review", actions)

    def test_review_rejects_story_that_was_not_submitted(self) -> None:
        privileges = {
            "project": ["browse"],
            "story": ["browse", "view", "review", "edit"],
        }
        client = FakeClient(
            "po1",
            [group("po1", privileges)],
            story_detail={
                "id": 31,
                "product": 7,
                "title": "Draft story",
                "status": "draft",
                "pri": 2,
                "estimate": 8,
            },
        )
        with self.assertRaisesRegex(ZentaoError, "only while its status is reviewing"):
            ZentaoMemberTools(client=client).prepare_story_action(
                {
                    "story_id": 31,
                    "action": "review",
                    "data": {"result": "pass", "pri": 2, "estimate": 8},
                }
            )

    def test_review_repairs_empty_zentao_2171_status_with_previewed_fallback(self) -> None:
        privileges = {
            "project": ["browse"],
            "story": ["browse", "view", "review", "edit"],
        }
        client = FakeClient(
            "po1",
            [group("po1", privileges)],
            story_detail={
                "id": 31,
                "product": 7,
                "title": "Reviewable story",
                "status": "reviewing",
                "pri": 2,
                "estimate": 8,
                "lastEditedDate": None,
            },
        )

        def review_write(method: str, path: str, body: dict):
            client.writes.append((method, path, body))
            if method == "POST" and path == "stories/31/review":
                client.story_detail["status"] = ""
                return {"id": 31, "status": "", "finalResult": ""}
            if method == "PUT" and path == "stories/31":
                client.story_detail["status"] = body["status"]
                return {"id": 31, "status": body["status"]}
            raise AssertionError(f"Unexpected fake write {method} {path}")

        client.write = review_write  # type: ignore[method-assign]
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_story_action(
            {
                "story_id": 31,
                "action": "review",
                "data": {"result": "pass", "pri": 2, "estimate": 8},
            }
        )
        self.assertIn("conditional_follow_up", prepared["preview"])
        executed = tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(client.writes[1], ("PUT", "stories/31", {"status": "reviewing"}))
        self.assertEqual(executed["postcondition"]["actual_status"], "reviewing")
        self.assertTrue(executed["compatibility_repair"]["executed"])

class ContainerCreationTests(unittest.TestCase):
    def _client(self) -> FakeClient:
        privileges = {
            "product": ["all", "view", "create"],
            "project": ["browse", "view", "create"],
        }
        return FakeClient("pm1", [group("pm1", privileges, name="项目经理")])

    def test_project_creation_is_previewed_and_scope_is_not_auto_expanded(self) -> None:
        client = self._client()
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_create_container(
            {
                "object_type": "project",
                "data": {
                    "name": "MCP workflow validation",
                    "code": "mcp-validation",
                    "begin": "2026-08-12",
                    "end": "2026-08-31",
                    "products": [7],
                    "model": "scrum",
                    "PM": "pm1",
                    "acl": "private",
                },
            }
        )
        self.assertTrue(prepared["requires_user_confirmation"])
        self.assertTrue(prepared["preview"]["requires_scope_update_after_create"])
        self.assertEqual(client.writes, [])

        executed = tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(client.writes[0][0:2], ("POST", "projects"))
        self.assertEqual(executed["created_id"], 901)
        self.assertTrue(executed["scope_update_required"])
        self.assertNotIn(901, client.config.allowed_project_ids)
        self.assertEqual(
            executed["ui_verification"]["web_url"],
            "http://127.0.0.1/zentao/project-view-901.html",
        )

    def test_container_creation_aborts_if_permission_is_revoked(self) -> None:
        client = self._client()
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_create_container(
            {
                "object_type": "product",
                "data": {"name": "Validation product", "code": "validation-product"},
            }
        )
        client.groups[0]["privs"]["product"]["create"] = False
        with self.assertRaisesRegex(ZentaoError, "no longer has"):
            tools.execute_confirmed_write(
                {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
            )
        self.assertEqual(client.writes, [])

    def test_execution_creation_uses_project_nested_endpoint(self) -> None:
        client = self._client()
        client.groups[0]["privs"]["execution"] = {"create": True}
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_create_container(
            {
                "object_type": "execution",
                "project_id": 101,
                "data": {
                    "project": 101,
                    "name": "MCP sprint",
                    "code": "mcp-sprint",
                    "begin": "2026-08-12",
                    "end": "2026-08-31",
                    "products": [7],
                },
            }
        )
        self.assertEqual(prepared["preview"]["path"], "projects/101/executions")
        tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(client.writes[0][0:2], ("POST", "projects/101/executions"))

    def test_project_creation_invalidates_project_visibility_cache(self) -> None:
        client = self._client()
        tools = ZentaoMemberTools(client=client)
        tools.list_projects({})
        self.assertEqual(client.project_gets, 1)
        prepared = tools.prepare_create_container(
            {
                "object_type": "project",
                "data": {
                    "name": "MCP workflow validation",
                    "code": "mcp-validation",
                    "begin": "2026-08-12",
                    "end": "2026-08-31",
                    "products": [7],
                },
            }
        )
        tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        tools.list_projects({})
        self.assertEqual(client.project_gets, 2)


class ItemCreationTests(unittest.TestCase):
    def test_task_creation_expands_execution_path_before_preview_and_write(self) -> None:
        privileges = {
            "project": ["browse", "view"],
            "task": ["browse", "view", "create"],
        }
        client = FakeClient("pm1", [group("pm1", privileges, name="项目经理")])
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_create_item(
            {
                "object_type": "task",
                "project_id": 101,
                "execution_id": 2,
                "data": {
                    "name": "Fix brightness zero",
                    "type": "devel",
                    "assignedTo": "dev1",
                    "estimate": 4,
                    "story": 31,
                    "estStarted": "2026-08-13",
                    "deadline": "2026-08-15",
                },
            }
        )
        self.assertEqual(prepared["preview"]["path"], "executions/2/tasks")
        self.assertNotIn("{", prepared["preview"]["path"])

        tools.execute_confirmed_write(
            {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(client.writes[0][0:2], ("POST", "executions/2/tasks"))


class TaskWorkflowTests(unittest.TestCase):
    @staticmethod
    def _client() -> FakeClient:
        privileges = {
            "project": ["browse", "view"],
            "task": ["browse", "view", "start", "finish"],
        }
        return FakeClient(
            "dev1",
            [group("dev1", privileges)],
            task_detail={
                "id": 1,
                "project": 101,
                "execution": 2,
                "name": "Fix brightness zero",
                "status": "wait",
                "estimate": 4,
                "consumed": 0,
                "left": 4,
                "assignedTo": "dev1",
                "lastEditedDate": "2026-08-13 14:08:23",
            },
        )

    def test_task_start_rejects_http_success_without_doing_status(self) -> None:
        client = self._client()
        tools = ZentaoMemberTools(client=client)
        prepared = tools.prepare_task_action(
            {
                "task_id": 1,
                "action": "start",
                "data": {"consumed": 0, "left": 4, "realStarted": "2026-08-13"},
            }
        )
        with self.assertRaisesRegex(ZentaoError, "expected doing"):
            tools.execute_confirmed_write(
                {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
            )

    def test_task_start_and_finish_verify_workflow_statuses(self) -> None:
        client = self._client()
        original_write = client.write

        def workflow_write(method: str, path: str, body: dict):
            result = original_write(method, path, body)
            if path == "tasks/1/start":
                client.task_detail.update(
                    {
                        "status": "doing",
                        "realStarted": "2026-08-13",
                        "lastEditedDate": "2026-08-13 14:20:00",
                    }
                )
            elif path == "tasks/1/finish":
                client.task_detail.update(
                    {
                        "status": "done",
                        "consumed": 4,
                        "left": 0,
                        "finishedDate": "2026-08-13 14:30:00",
                        "lastEditedDate": "2026-08-13 14:30:00",
                    }
                )
            return result

        client.write = workflow_write  # type: ignore[method-assign]
        tools = ZentaoMemberTools(client=client)
        start = tools.prepare_task_action(
            {
                "task_id": 1,
                "action": "start",
                "data": {"consumed": 0, "left": 4, "realStarted": "2026-08-13"},
            }
        )
        started = tools.execute_confirmed_write(
            {"confirmation_token": start["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(started["postcondition"]["actual_status"], "doing")

        finish = tools.prepare_task_action(
            {
                "task_id": 1,
                "action": "finish",
                "data": {
                    "realStarted": "2026-08-13 14:20:00",
                    "finishedDate": "2026-08-13 14:30:00",
                    "currentConsumed": 4,
                },
            }
        )
        finished = tools.execute_confirmed_write(
            {"confirmation_token": finish["confirmation_token"], "approval": "user"}
        )
        self.assertEqual(finished["postcondition"]["actual_status"], "done")

    def test_task_finish_rejects_date_only_values_before_confirmation(self) -> None:
        tools = ZentaoMemberTools(client=self._client())
        with self.assertRaisesRegex(ZentaoError, "YYYY-MM-DD HH:MM:SS"):
            tools.prepare_task_action(
                {
                    "task_id": 1,
                    "action": "finish",
                    "data": {
                        "realStarted": "2026-08-13",
                        "finishedDate": "2026-08-13",
                        "currentConsumed": 4,
                    },
                }
            )

    def test_task_finish_rejects_completion_before_start(self) -> None:
        tools = ZentaoMemberTools(client=self._client())
        with self.assertRaisesRegex(ZentaoError, "must not be earlier"):
            tools.prepare_task_action(
                {
                    "task_id": 1,
                    "action": "finish",
                    "data": {
                        "realStarted": "2026-08-13 14:30:00",
                        "finishedDate": "2026-08-13 14:20:00",
                        "currentConsumed": 4,
                    },
                }
            )


class ClientResponseTests(unittest.TestCase):
    def test_http_200_business_failure_is_rejected(self) -> None:
        config = ConnectorConfig(
            profile_name="test",
            credentials_path=Path("/tmp/unused.json"),
            api_base="http://127.0.0.1/zentao/api.php/v1",
            web_base="http://127.0.0.1/zentao",
            account="user",
            password="password",
            token="token",
            allowed_project_ids=(101,),
            timeout_seconds=20,
            http_basic_account="",
            http_basic_password="",
            verify_tls=True,
            source_roots={},
        )
        client = ZentaoClient(config)
        with patch.object(
            client,
            "_api_http",
            return_value=HttpResult(
                200,
                {"result": "fail", "message": {"project": ["required"]}},
            ),
        ):
            with self.assertRaisesRegex(ZentaoError, "required"):
                client.write("POST", "projects/101/executions", {"project": 101})


class FirstConfigurationTests(unittest.TestCase):
    def test_set_command_requires_site_address_and_project_scope(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["set", "--account", "developer", "--allowed-project-ids", "101"]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["set", "--api-base", "https://zentao.example.com/zentao/my.html", "--account", "developer"]
            )

    def test_set_profile_rejects_blank_member_password(self) -> None:
        args = SimpleNamespace(
            api_base="https://zentao.example.com/zentao/my.html",
            web_base=None,
            account="developer",
            allowed_project_ids="101",
            timeout=20,
            http_basic_account=None,
            no_verify_tls=False,
            write_confirmation_mode="manual",
        )
        with patch("configure.getpass.getpass", return_value=""):
            with self.assertRaisesRegex(ValueError, "password is required"):
                set_profile(args)

    def test_set_profile_derives_web_url_without_exposing_password(self) -> None:
        args = SimpleNamespace(
            api_base="https://zentao.example.com/zentao/my.html",
            web_base=None,
            account="developer",
            allowed_project_ids="101,102",
            timeout=20,
            http_basic_account=None,
            no_verify_tls=False,
            write_confirmation_mode="manual",
        )
        with patch("configure.getpass.getpass", return_value="secret"):
            profile = set_profile(args)
        self.assertEqual(
            profile["api_base_url"],
            "https://zentao.example.com/zentao/api.php/v1",
        )
        self.assertEqual(profile["web_base_url"], "https://zentao.example.com/zentao")
        self.assertEqual(profile["allowed_project_ids"], [101, 102])


class BugAttachmentCreationTests(unittest.TestCase):
    @staticmethod
    def _client() -> FakeClient:
        privileges = {
            "project": ["browse", "view"],
            "bug": ["browse", "view", "create"],
        }
        return FakeClient("qa1", [group("qa1", privileges, name="测试")])

    @staticmethod
    def _data() -> dict:
        return {
            "product": 7,
            "title": "Black-box attachment validation",
            "pri": 2,
            "severity": 2,
            "type": "codeerror",
            "openedBuild": ["trunk"],
            "assignedTo": "dev1",
            "steps": "Reproduce and inspect the attached log.",
        }

    def test_confirmed_bug_creation_uploads_hash_bound_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attachment = root / "logcat.txt"
            attachment.write_text("fatal exception\n", encoding="utf-8")
            client = self._client()
            tools = ZentaoMemberTools(client=client)
            with patch.object(tools, "_upload_staging_root", return_value=root):
                prepared = tools.prepare_create_bug_with_attachments(
                    {
                        "project_id": 101,
                        "data": self._data(),
                        "attachment_paths": [str(attachment)],
                    }
                )
                self.assertEqual(client.uploads, [])
                self.assertEqual(client.writes, [])
                metadata = prepared["preview"]["attachments"][0]
                self.assertEqual(metadata["size"], attachment.stat().st_size)
                self.assertEqual(len(metadata["sha256"]), 64)

                executed = tools.execute_confirmed_write(
                    {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
                )
                self.assertEqual(client.uploads[0][0], attachment)
                self.assertEqual(client.writes[0][0:2], ("POST", "bugs"))
                self.assertEqual(executed["uploaded_attachments"][0]["id"], 801)

    def test_attachment_change_after_preview_blocks_all_remote_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attachment = root / "logcat.txt"
            attachment.write_text("before\n", encoding="utf-8")
            client = self._client()
            tools = ZentaoMemberTools(client=client)
            with patch.object(tools, "_upload_staging_root", return_value=root):
                prepared = tools.prepare_create_bug_with_attachments(
                    {
                        "project_id": 101,
                        "data": self._data(),
                        "attachment_paths": [str(attachment)],
                    }
                )
                attachment.write_text("after!\n", encoding="utf-8")
                with self.assertRaisesRegex(ZentaoError, "changed after preparation"):
                    tools.execute_confirmed_write(
                        {"confirmation_token": prepared["confirmation_token"], "approval": "user"}
                    )
                self.assertEqual(client.uploads, [])
                self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()
