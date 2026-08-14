# ZenTao 21.7.1 operation reference

Use `get_operation_schema` as the live source of truth because the logged-in member's groups determine which rows are available.

Every prepared write includes `confirmation_policy`. `manual` requires explicit later user confirmation for every write. `safe-auto` can policy-confirm only a low-risk comment; creation, assignment, edits, workflow state changes, attachments, container creation, and destructive actions always remain human-confirmed. After execution, inspect `cache_refresh`; use the corresponding read tool with `refresh=true` if the immediate authoritative read failed.

Every successful execution also returns `ui_verification`. Open its authenticated `web_url`, refresh the real ZenTao page, compare the visible object and changed result with the authoritative read, capture a PNG, and show it in the Codex conversation. Do not substitute API JSON, previews, generated HTML, or mock screenshots. If the UI session is unavailable, preserve the successful write result, report screenshot verification as pending, and retry only the UI capture after login is restored.

Read-only analysis tools are also group-derived: `scan_new_bugs`, `get_bug_analysis_context`, and `download_bug_attachment` exist only when the current account can read Bugs. Attachment download is bound to file IDs referenced by the scoped Bug and writes only to an isolated local cache, never to ZenTao. `get_project_source_mapping` resolves an allowed project to a local source directory.

Scheduled and handoff tools are Bug-read-derived. `scan_new_bugs_scheduled` and `complete_scheduled_bug` update only connector-private cursor/retry state. `save_bug_solution_package` writes only private JSON/Markdown after refetching the scoped Bug. `list_bug_solution_packages` and `get_bug_solution_package` are read-only. None of these are ZenTao mutations or substitutes for the normal source/ZenTao confirmation flow.

When `bug.create` is available, `prepare_create_bug_with_attachments` may bind 1–10 local files to a Bug creation preview. Files must live under the connector staging directory reported by `get_attachment_staging_directory` or the mapped project source root. The preview includes each path, size, and SHA-256. Execution rechecks the hashes, uploads the files, then creates the Bug with a shared ZenTao upload UID. The preview explicitly reports the partial-side-effect risk if attachment upload succeeds but Bug creation fails.

| Object | Action | Important fields |
|---|---|---|
| Bug | `assign` | `assignedTo` required; optional `comment`, `mailto` |
| Bug | `confirm` | optional `assignedTo`, `pri`, `type`, `deadline`, `comment` |
| Bug | `resolve` | `resolution` required; optional `resolvedBuild`, `assignedTo`, `comment` |
| Bug | `close` / `activate` | optional `comment`; close is marked destructive |
| Bug | `update` | only the fields returned by `get_operation_schema` |
| Requirement | `assign` | `assignedTo` required; optional `comment` |
| Requirement | `review` | Requirement must already be `reviewing`; `result`, current `pri`, and current `estimate` are required. ZenTao 21.7.1 may require the exact preview's conditional `story.edit` status fallback. |
| Requirement | `close` | `closedReason` required; optional `duplicateStory`, `comment` |
| Requirement | `activate` / `change` / `update` | use the live schema; preserve current values when not changing them |
| Task | `assign` | `assignedTo` required; optional `left`, `comment` |
| Task | `start` / `pause` | use `consumed`, `left`, `realStarted`, and `comment` as applicable |
| Task | `restart` | `consumed` and `left` required |
| Task | `finish` | `realStarted`, `finishedDate`, and `currentConsumed` required; both timestamps must use `YYYY-MM-DD HH:MM:SS`, and completion cannot be earlier than start |
| Task | `close` / `activate` / `update` | use the live schema; close is marked destructive |
| All three | `comment` | nonblank `comment` required; requires `action.comment` |

Creation also requires an allowed `project_id`. Bug and requirement creation require a product linked to that project. Task creation requires an execution belonging to that project.

Container creation is separately role-gated:

| Object | Required privilege | Required fields |
|---|---|---|
| Product | `product.create` | `name`, `code` |
| Project | `project.create` | `name`, `code`, `begin`, `end`, non-empty visible `products` |
| Execution/iteration | `execution.create` | allowed `project_id` plus `project`, `name`, `code`, `begin`, `end` |

Use `get_container_creation_schema` as the live field source. `prepare_create_container` is read-only and returns the same signed, expiring confirmation token as other writes. Project creation does not auto-expand the allow list.

The connector intentionally does not expose delete, arbitrary batch mutation, or raw HTTP requests.
