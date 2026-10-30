> 当前 0.2.6 首次实际使用会说明记忆管理方法，并返回管理页面的可点击链接及运行状态。仍按保存的作用域与目标原值精确匹配；完整读取规则见[全局读取规则](GLOBAL-RULE.md)。

> 自动读取仍使用需审核信任的启动/版本刷新钩子，见 [启用说明](RELEASE-0.2.2.md)及 [0.2.3 诊断兼容更新](RELEASE-0.2.3.md)。下方保留历史加载故障的修复依据，旧版发行说明中的匹配选择不适用于当前版本。

# 已安装但客户端看不到或无法调用

## 询问用法与管理网页

在已加载插件的 Codex 聊天中说“使用 memory-manager，告诉我怎么使用和管理记忆”或“给我管理页面地址”。应调用 `memory_status(include_help=true)`，依据 `usage_guide` 说明用法，并以 `web_manager.url` 提供 Markdown 可点击链接。默认入口为[本地管理页面](http://127.0.0.1:8765/)，其他端口以实际返回为准。

页面链接、服务正在运行以及服务已验证是不同状态。结合 `running`、`verified` 和错误说明判断；未验证成功时应给出启动或诊断方法，不声称已经启动。已完成全局配置仍可查询用法，不需要再次同意或重写 AGENTS.md。后台 MCP 初始化只返回说明，首次使用引导应在 Codex 聊天中进行。

## 已定位的问题与 0.1.5 修复

旧安装器将当前 Python 的绝对路径写入 portable `mcp.json` 的 `command`。Codex portable parser 只接受裸命令名或插件包内 `./` 路径，因此可能发现 Skill，却拒绝 MCP 服务。0.1.3 的 subprocess 自检直接执行绝对 Python，遗漏了宿主配置格式校验；握手成功不能排除宿主拒绝配置。

Windows 安装器登记的本机市场路径不能直接当作 WSL 路径使用。例如来源是 Windows 绝对路径而当前 agent 为 WSL 时，CLI 可能报告市场目录缺少声明。GitHub 市场使用客户端自己管理的 Git 快照，避免依赖这个本机来源路径。

0.1.4 包含以下改动：

- 本机安装器用裸 Python 命令加子进程 PATH，固定运行时；兼容格式独立保留绝对 Python。
- 诊断先检查 portable schema、字段、command、cwd、路径边界和保留环境变量，再做 initialize、tools/list 和 memory_search 只读调用。
- GitHub 源码使用裸 `python3`、`${PLUGIN_ROOT}/run.py` 和 `${PLUGIN_ROOT}` cwd，不包含作者机器的 Python 路径。
- 显式数据目录优先；其次复用 CODEX_HOME 父目录中已有的 `.codex-memory-manager/memory.sqlite3`。MCP 未继承该环境变量时，从标准插件缓存位置确定所属配置目录；否则回退到用户默认库。不复制或迁移数据库。

配置问题已在源码和当前 CLI 中复现。具体桌面会话还有没有其他加载错误，仍以客户端日志及实际工具调用为准。

## 推荐：从 GitHub 市场安装

仓库：`https://github.com/yimeng02/codex-plugins`，ref：`main`，市场：`Yimeng02`（`yimeng02`），插件：`codex-memory-manager`。

完整步骤和更新命令见 [GitHub 市场说明](GITHUB-MARKETPLACE.md)。更新时保留仓库原有市场名和插件条目。

GitHub 版本要求实际 agent 执行环境中 `python3` 为 Python 3.10+。Windows 原生与 WSL 的 PATH、Python 和默认用户目录分别检查。当前已确认的 WSL 环境可运行 `/usr/bin/python3`。

## 诊断安装缓存

```text
doctor.cmd
doctor.cmd --data-dir "C:\实际数据目录"
doctor.cmd --plugin-root "C:\实际插件缓存目录"
```

Linux/WSL/macOS 使用 `python3 doctor.py`。从 GitHub 安装时，原回执可能仍指向旧市场缓存，应使用 `--plugin-root` 指定 CLI 或客户端返回的新缓存目录。

诊断不打印记忆正文。它查询随机关键词，不新增、编辑或删除条目；服务首次启动时可能初始化数据库。检查旧 0.1.3 缓存时，新诊断应拒绝旧绝对 portable command。

## 验收结果与范围

- 0.1.5 的 73 项隔离测试通过，包括 portable/legacy 实际 MCP 查询。
- 实际 Codex CLI 0.162.0-alpha.17.2 的 app-server 在临时配置与数据库中成功加载插件，`mcpServerStatus/list` 发现全部 14 个工具，包含 `memory_snapshot` 与 `memory_search`，`toolsError` 为 null。
- 这证明当前 Codex 宿主能解析并启动修复后的配置。现有桌面聊天的工具列表不会因源码提交自动刷新，还需在加载新版插件的新聊天中确认实际调用。

```text
使用 codex-memory-manager:memory-manager，实际调用 memory_snapshot，列出我的分类和记忆。
```

若新聊天仍无工具，检查实际 agent 环境、CODEX_HOME、插件版本和客户端 MCP 启动日志。技能可被提及仅证明技能已发现，不能证明服务已加载，也不能证明记忆库为空。

依据：[OpenAI 插件结构与市场](https://developers.openai.com/plugins/build/plugins)、[本地 stdio MCP](https://developers.openai.com/plugins/deploy/desktop-only)、[官方 portable parser 源码](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/agent_plugin_config.rs)。
