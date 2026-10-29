# Codex 规则与记忆管理器

当前版本：**0.2.0**。新增插件标识选择、网页记忆更新提醒，以及 Codex 首次使用时的全局读取规则确认。安装和调用失败的排查见[客户端加载说明](docs/CLIENT_LOADING.md)。

一个本地优先的个人记忆管理插件，提供中文可视化页面、SQLite 持久化、stdio MCP 工具和 Codex 技能。需要 Python 3.10+，无需安装第三方 Python 包、数据库或前端依赖。

## 从 GitHub 个人市场安装

在 Codex 插件管理的添加市场来源入口填写：

- 仓库：`https://github.com/yimeng02/codex-plugins`
- Git ref：`main`
- 市场名称：`Yimeng02`（`yimeng02`）
- 插件：`规则与记忆管理器`（`codex-memory-manager`）

源码版使用 PATH 中的 `python3`（3.10+），适用于当前 WSL/Linux/macOS 环境。Windows 原生运行时应先确认 `python3` 确实启动 Python；若环境仅有 `py` 或 `python`，可使用本机安装器生成正确的本地启动配置。该市场仓库公开，下载无需个人访问令牌。

也可通过官方 CLI 注册这个 Git 来源，然后在管理界面选择它并安装：

```text
codex plugin marketplace add yimeng02/codex-plugins --ref main
```

更新、私有仓库认证及原本地来源迁移见 [GitHub 个人市场说明](docs/GITHUB-MARKETPLACE.md)。本机安装脚本及参数见 [安装说明](docs/INSTALL.md)，版本变更见 [更新记录](CHANGELOG.md)。

## 首次在 Codex 使用与记忆更新

首次在 Codex 聊天中实际使用 memory-manager 时，会先查看首次配置状态，展示以下规则并询问是否加入生效的全局 AGENTS 文件：

```text
开始任务时，使用 memory-manager 读取全局启用记忆；
涉及具体项目、技能或插件时，按准确作用域和目标检索相关记忆。
仅应用与当前任务相关的内容；工具不可用时如实说明。
```

确认加入后才应用，保留已有内容并备份；选择暂不加入或稍后也会记录，后续不自动反复询问。后台连接插件不会自动写入，全局 override 存在时会在预览中说明实际生效的目标。网页不承担首次询问。

编辑插件范围的记忆时，可从“插件标识”选择框选择本机发现的插件，保存准确 ID；未在列表中的插件可手动填写。旧条目的显示名目标保留，选择规范标识并保存后才会更改。

网页顶部显示库版本和最近一次通过插件读取的版本。保存后如有更新，点击“复制重新读取指令”，在需要使用的 Codex 聊天中发送；插件以 `memory_refresh` 读取全局及当前任务指定范围，随后网页自动更新状态。复制不算读取，最近一次读取也不代表所有聊天同步。

详细步骤见 [0.2.0 使用与更新说明](docs/RELEASE-0.2.0.md)。

## 已实现的功能

- 分类：预置偏好、项目、技能、插件、纠错、知识分类；支持自定义分类、层级分类、改名和删除空分类。
- 条目：标题、正文、分类、作用域、目标、标签、优先级、启用状态，支持新增和编辑。
- 批量管理：多选启用、禁用、移动、移至回收站及恢复；整批操作事务执行。
- 检索：按文字、分类、作用域和状态筛选；MCP 可按准确作用域与目标读取。
- 历史：保留编辑版本，恢复历史内容时生成新版本。
- 迁移：JSON 导入导出，按 ID 合并，整批校验失败时回滚。
- 规则：预览、复制或下载启用条目的 Markdown；CLI 可以更新 Markdown 文件中的受管区块，保留区块外原文和备份。
- 数据：UI、CLI 与 MCP 共用同一 SQLite 数据库。页面不使用远程脚本、CDN或分析服务。

## 启动可视化页面

在本项目目录运行：

```bash
python3 run.py serve
```

然后访问 [本地管理页面](http://127.0.0.1:8765)。停止服务使用 Ctrl+C。端口被占用时运行 `python3 run.py serve --port 8766`，访问对应端口。

Windows 原生环境可双击 `start.cmd`，或运行 `py -3 run.py serve`。Linux/WSL/macOS 可运行 `sh start.sh`。以下示例的 python3 可替换为实际可用的 Python 启动器。

页面初次打开为空条目库，分类已预置。点击“新增分类”或“新增记忆”开始。普通删除进入回收站；非空分类须先移动条目和子分类，避免误删。

## 数据存放与跨环境使用

默认数据库：当前 Python 用户主目录下 `.codex-memory-manager/memory.sqlite3`。此位置独立于插件安装缓存，因此重新安装或更新源码不会覆盖数据库。

显式 `CODEX_MEMORY_HOME` 优先。未指定时，优先复用 `CODEX_HOME` 父目录中已有的 `.codex-memory-manager/memory.sqlite3`；MCP 子进程未继承 `CODEX_HOME` 时，从标准安装缓存位置确定所属 Codex 配置目录。否则使用当前用户默认库。这样 WSL 与 Windows 使用同一 Codex 配置时可以沿用旧记忆，不移动或复制数据库。

自定义数据库时，在子命令之前指定 --db：

```bash
python3 run.py --db /absolute/path/memory.sqlite3 serve
python3 run.py --db /absolute/path/memory.sqlite3 mcp
```

也可以在启动环境设置 `CODEX_MEMORY_HOME` 指向数据目录。UI 和 MCP 必须使用相同目录；Windows Python 与 WSL Python 的默认主目录不同，混用时应明确指定路径。不要将运行中的 SQLite 文件直接当作迁移包，使用界面的 JSON 导出功能。

每条记忆有两个独立维度：

| 维度 | 用途 |
| --- | --- |
| 分类 | 自己组织内容，可新增和修改，也可以有子分类 |
| 作用域 | global / project / skill / plugin，决定导出或检索的适用对象 |

global 的 target 为空；其余作用域需要明确 target。项目推荐完整路径，技能和插件推荐准确名称。优先级是本管理器中的显示和导出排序，不表示可以越过 Codex 的指令层级。

## 本机安装器

需要 Python 3.10+，以及支持 `codex plugin add` 的 Codex CLI。在项目根目录执行：

```powershell
# Windows，也可以直接双击 install.cmd
.\install.ps1
```

```sh
# Linux / WSL / macOS
sh install.sh
```

也可直接运行 `python install.py`（Windows）或 `python3 install.py`（Linux / WSL / macOS）。安装器检查依赖和项目测试，建立稳定的本地市场副本，通过 Codex 官方命令注册并安装 `codex-memory-manager@personal-memory-tools`，最后检查插件启用状态。

| 参数 | 用途 |
| --- | --- |
| `--check` | 检查源码、CLI 命令帮助并运行项目测试；不创建安装副本、不注册市场、不安装插件。 |
| `--codex PATH` | 指定 Codex CLI 路径。 |
| `--data-dir PATH` | 指定持久数据及安装目录。 |
| `--reinstall` | 显式请求刷新本插件；重复安装默认也会刷新。 |

例如：`python3 install.py --check`，或 `.\install.ps1 --data-dir "D:\CodexMemory"`。

默认数据目录为 `~/.codex-memory-manager`，也可通过 `CODEX_MEMORY_HOME` 或 `--data-dir` 指定。安装后的 MCP 和页面使用同一数据目录；升级时沿用此目录，再次运行安装脚本即可。重复执行会刷新本插件并确保启用；源码相同时复用稳定副本，源码变化时备份旧代码，始终保留记忆数据。安装器不会先卸载插件；遇到同名但来源不同的市场会停止，不覆盖原来源。

安装成功后，按脚本输出使用 Python 运行数据目录中的 `open-manager.py` 启动页面，并完全退出、重新打开目标 Codex 客户端。安装器会生成 `installation.json` 供排查。默认不自动启动页面、不下载 Python；缺少 CLI 时允许补齐 OpenAI 官方 CLI，`--check` 或 `--no-bootstrap-cli` 可禁止下载。不需要管理员权限，也不整体覆写 `config.toml`。

Windows 与 WSL 的 Codex 配置可能不同；请在目标客户端对应的环境中安装，并确认使用同一 `CODEX_HOME`。CLI 不支持插件命令时会明确失败。详细步骤及验证方式见 [安装与升级](docs/INSTALL.md)。

### 手动注册（排查使用）

以下为手动注册参考。自动安装生成的本地市场位于 `<数据目录>/marketplace`；排查自动安装时应使用该实际路径。官方参考：[Codex 插件命令](https://learn.chatgpt.com/docs/developer-commands#codex-plugin)、[构建插件](https://learn.chatgpt.com/docs/plugins/build/plugins)。

已提供 portable `plugin.json`、`mcp.json`、`skills/` 和本地 marketplace 入口。本项目目录同时作为 marketplace 根目录。

先确认 Codex 实际执行环境可运行 `python3`。如果 Windows 原生环境只有 `python`，可在自己的源码副本将 mcp.json 中 command 改为 `python`。portable command 不能是 Python 的绝对路径，也不能把命令与参数拼在一起；本机安装器会以裸命令加子进程 PATH 固定实际 Python。

在安装客户端所在环境运行：

```text
codex plugin marketplace add /absolute/path/codex-memory-manager
```

重启桌面客户端，在插件目录中选择“个人规则与记忆工具”来源，安装 `codex-memory-manager`，然后在新聊天中测试：

```text
使用 memory-manager，列出我的分类和记忆。
新增一个“交付规范”分类。
将“完成后报告实际验证结果”保存为全局记忆，分类为交付规范。
搜索项目 /absolute/path/project 的启用记忆。
```

插件安装与客户端加载分别验收：配置和独立握手通过之后，还需在目标客户端新聊天中看到真实的工具调用。

若所在客户端尚不支持本地 marketplace，可使用受支持的 MCP 配置方式接入绝对路径 `python3 /absolute/path/codex-memory-manager/run.py mcp`，不要直接覆盖原有配置。启动 UI 时仍需使用同一数据库。

## 保存与实际生效

管理器保存的是你自建的显式记忆库。它不会读取或改写 Codex 内部记忆数据库，不会扫描聊天记录，也不会自动修改其他插件、技能或全局指令。

“已保存”“已启用”“已被当前会话读取”“已发布到规则文件”是不同状态。启用条目可被检索和导出，不能据此声称所有聊天已经自动加载。新会话应确认实际指令来源。

对于需要稳定加载的少量规则，可以发布到实际生效的 AGENTS.md。下面命令仅预览，不写文件：

```bash
python3 run.py publish --file /absolute/path/project/AGENTS.md --scope project --target /absolute/path/project
```

检查输出的 content 和 sha256。明确应用时运行：

```text
python3 run.py publish --file /absolute/path/project/AGENTS.md --scope project --target /absolute/path/project --apply --expected-sha256 <刚才的sha256>
```

只替换 `<!-- codex-memory-manager:begin -->` 与 `<!-- codex-memory-manager:end -->` 之间的内容；不存在时追加区块；原文件在同目录备份。标记不完整、重复、目标文件发生变化时拒绝覆盖。发布后再在目标项目的新会话验证加载。

不要把全部知识条目都发布到 AGENTS.md。检查实际 CODEX_HOME、项目目录、AGENTS.override.md 和加载预算。发布到普通 Markdown 或技能说明文件不会自动赋予其 AGENTS.md 的加载方式。

## CLI 和 MCP

```bash
python3 run.py state
python3 run.py tool memory_search --json '{"scope":"global","target":""}'
python3 run.py tool memory_render --json '{"scope":"global"}'
```

复杂正文可用 `--input parameters.json` 代替 --json，避免命令行转义。MCP 提供分类 CRUD、记忆 CRUD、批量操作、历史恢复、导入导出、检索和 Markdown 预览，不提供任意文件读写或脚本执行工具。

## 验证与打包

```bash
python3 -m unittest discover -s tests -v
python3 package.py
```

打包结果为 `dist/codex-memory-manager-0.2.0.zip`，排除运行数据和 Python 缓存。隔离测试覆盖 CRUD、批量事务、HTTP、MCP、宿主配置规则、数据库路径及安装器；MCP 新增插件目录、读取状态、刷新与首次配置工具；实际验证结果见本版更新记录。GitHub Actions 在 Ubuntu/Windows 与 Python 3.10/3.13 上执行相同测试。桌面新聊天是否拿到工具仍以真实调用为准。

## 参考依据

- [OpenAI 插件打包与本地安装](https://developers.openai.com/plugins/build/plugins)
- [OpenAI 本地 MCP 插件](https://developers.openai.com/plugins/deploy/desktop-only)
- [Codex Skills 加载机制](https://learn.chatgpt.com/docs/build-skills)
- [Codex AGENTS.md 加载机制](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
