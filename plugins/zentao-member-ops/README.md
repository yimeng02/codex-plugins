# ZenTao Member Ops

`zentao-member-ops` is a Codex plugin for ZenTao Open Source 21.7.1. It derives available MCP tools from the logged-in member's live groups and privileges, supports scoped project, Bug, story, and task operations, and requires a preview before every write.

## Safety model

- Credentials are stored outside the plugin in `~/.config/codex/zentao-member-ops/credentials.json` with user-only permissions.
- The repository contains no ZenTao account, password, token, private endpoint, authorized project ID, source path, or business data.
- The project allow list is empty by default and must be configured explicitly.
- `manual` mode requires user confirmation for every ZenTao write.
- `safe-auto` may automatically confirm only an explicitly requested low-risk comment. Status changes, assignment, creation, uploads, and destructive actions still require user confirmation.
- Bug and requirement analysis is performed by the active Codex session. The plugin retrieves context but does not automatically post analysis back to ZenTao.

## Requirements

- Codex with personal plugin marketplace support
- Python 3.11 or newer
- ZenTao Open Source 21.7.1 REST API v1

## Install in the personal marketplace

Clone this repository to the standard personal plugin source path:

```bash
git clone https://github.com/yimeng02/zentao-member-ops ~/plugins/zentao-member-ops
```

Add the following entry to the `plugins` array in `~/.agents/plugins/marketplace.json` if it is not already present:

```json
{
  "name": "zentao-member-ops",
  "source": {
    "source": "local",
    "path": "./plugins/zentao-member-ops"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Productivity"
}
```

Install or refresh the plugin, then start a new Codex task:

```bash
codex plugin add zentao-member-ops@personal
```

## Configure

Credentials are entered interactively and never written into this repository:

```bash
python3 scripts/configure.py set \
  --profile production \
  --api-base http://127.0.0.1/zentao/api.php/v1 \
  --web-base http://127.0.0.1/zentao \
  --account YOUR_ZENTAO_ACCOUNT \
  --allowed-project-ids 101,102 \
  --test
```

Optional outer HTTP Basic authentication:

```bash
python3 scripts/configure.py set \
  --profile production \
  --api-base https://zentao.example.com/zentao/api.php/v1 \
  --web-base https://zentao.example.com/zentao \
  --account YOUR_ZENTAO_ACCOUNT \
  --allowed-project-ids 101 \
  --http-basic-account YOUR_BASIC_ACCOUNT \
  --test
```

Map an authorized project to a local source tree:

```bash
python3 scripts/configure.py map-source \
  --profile production \
  --project-id 101 \
  --path /path/to/project/source
```

Inspect configuration without exposing secrets:

```bash
python3 scripts/configure.py status
python3 scripts/diagnose.py
```

## Validation

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## License

MIT — see [LICENSE](LICENSE).
