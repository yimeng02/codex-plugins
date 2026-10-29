# 从 GitHub 个人市场安装

本插件发布到已有的 GitHub 插件仓库，与禅道插件共用 `yimeng02` 市场。GitHub 源用于让 Codex 下载和更新插件，记忆数据库继续保存在本机。

| 项目 | 填写内容 |
| --- | --- |
| 仓库 | `https://github.com/yimeng02/codex-plugins` |
| 分支 / ref | `main` |
| 市场标识 | `yimeng02` |
| 市场显示名称 | Yimeng02 |
| 插件标识 | `codex-memory-manager` |
| 完整安装标识 | `codex-memory-manager@yimeng02` |
| 插件目录 | `plugins/codex-memory-manager` |
| 插件显示名称 | 规则与记忆管理器 |
| 本版 | `0.2.0` |

填写仓库 URL，不填写 `raw.githubusercontent.com` 链接、某个文件的浏览页或 ZIP 下载链接。市场文件位于仓库根目录下的 `.agents/plugins/marketplace.json`；本插件条目的 `source.path` 为 `./plugins/codex-memory-manager`，相对于仓库根目录解析。Codex 从该插件目录发现 `plugin.json`、`mcp.json` 和 `skills/`。已有市场名称和禅道插件条目保持不变，只向现有市场追加本插件。[官方打包说明](https://developers.openai.com/plugins/build/plugins)

## 安装前确认 Python

GitHub 版本通过 `python3` 命令启动本地 MCP，需要目标 Codex 运行环境的 PATH 中有 Python 3.10 或更高版本。当前目标聊天使用 WSL/POSIX 运行环境，已确认其解释器为 `/usr/bin/python3`。在与桌面客户端相同的执行环境中运行：

```text
python3 -c "import sys; print(sys.executable); print(sys.version); assert sys.version_info >= (3, 10)"
```

Windows 原生环境中的 `py -3` 或 `python` 能运行，不代表 `python3` 命令也可用；请确认上面的命令成功，再重新启动客户端以读取更新后的 PATH。在另一台 Windows 原生客户端使用时，需要为该环境提供可运行的 `python3` 命令，或使用其支持的本机 MCP 运行时配置方式选择实际解释器。本插件不会自动修改系统 Python 配置。Windows Python 路径不能直接作为 WSL 内的解释器命令。

## 在桌面插件管理中添加

1. 打开 Codex 的插件管理，在个人市场源的添加入口填写仓库 URL `https://github.com/yimeng02/codex-plugins`；如果有分支输入，填写 `main`。已经添加该仓库时，直接刷新现有来源。
2. 选择“Yimeng02”来源，找到“规则与记忆管理器”或 `codex-memory-manager`，执行安装并启用。该来源中的禅道插件继续保留。
3. 完全退出并重新打开桌面客户端，在本地新聊天中完成下方的工具验收。

界面没有 GitHub 源添加入口时，可在同一环境、同一 `CODEX_HOME` 中使用官方 CLI 注册来源，再回到桌面插件管理安装。不要在 Windows CLI 中注册后，假定 WSL 的客户端配置已同步。

```text
codex plugin marketplace list --json
codex plugin marketplace add yimeng02/codex-plugins --ref main --json
```

也可通过 CLI 安装，随后仍须在桌面新聊天中验收：

```text
codex plugin add codex-memory-manager@yimeng02 --json
codex plugin list --marketplace yimeng02 --json
```

已经注册正确 GitHub 来源时，使用 `codex plugin marketplace upgrade yimeng02 --json` 刷新即可，不必重新添加。[官方 CLI 命令](https://learn.chatgpt.com/docs/developer-commands#codex-plugin)

本机独立源码包中的 `install.cmd` 会管理它自己的 `personal-memory-tools` 本地市场；它与这里的 `yimeng02` GitHub 市场是两种分发入口。从 GitHub 安装无需运行该安装器，也不要求删除旧本地市场。若旧的 `codex-memory-manager@personal-memory-tools` 实例仍启用，请在插件管理中停用该旧实例，再验证新来源实例，避免重复加载同名 MCP 服务。保留原有数据库。

## 已有同名本地市场时

先用 `codex plugin marketplace list --json` 核对 `yimeng02` 的实际来源。如果已指向 `yimeng02/codex-plugins`，刷新该来源即可；若同名来源指向其他位置，部分已有缓存或客户端版本会返回 `already added from a different source`。

出现该错误时停止重复安装，保留 `list` 输出并运行现有安装的 `doctor.cmd`，先核对当前来源、插件缓存路径和数据目录，再通过客户端来源管理处理冲突。本说明不会要求预先删除市场、插件或数据库，也不要手动修改缓存中的版本号来绕过冲突。[Codex 来源登记实现](https://github.com/openai/codex/blob/main/codex-rs/core-plugins/src/marketplace_add.rs)

## 下载访问与认证

`yimeng02/codex-plugins` 是公开仓库，下载不需要私有仓库凭据。Codex 中连接 GitHub 插件和桌面市场的本机 Git 下载是两条独立通道；连接状态不能替代实际下载验证。

遇到下载失败时，先检查同一运行环境能否读取仓库：

```text
git ls-remote https://github.com/yimeng02/codex-plugins.git HEAD
```

若失败，按错误检查网络、代理或 Git 配置；公开仓库下载不应先要求创建访问令牌。如果未来另接私有来源，才按该来源要求配置本机 Git 凭据。不要把访问令牌写进仓库 URL、插件配置、命令示例或提交记录。

## 验收与故障定位

在安装后的本地新聊天中发送：

```text
使用 codex-memory-manager:memory-manager，实际调用 memory_snapshot，列出我的分类和记忆。
```

验收分为三个独立层次：

| 层次 | 证据 |
| --- | --- |
| 市场分发 | 插件管理能看到 GitHub 来源，下载得到 `0.2.0` |
| 本机 MCP | 对实际缓存目录的初始化、工具列表和只读调用通过 |
| 桌面聊天 | 新聊天实际获得并调用 `memory_snapshot` 或 `memory_search` |

技能出现在列表中、CLI 显示 installed/enabled，以及本机 MCP 自检通过，都不能替代最后一项。GitHub 分发提供一致的安装入口；仍缺少工具时，应继续检查桌面的 MCP 启动错误、Python PATH、执行环境和启用配置。[本地 stdio 插件运行要求](https://developers.openai.com/plugins/deploy/desktop-only)

从 GitHub 安装不会生成本地安装器的回执。诊断时应使用 `codex plugin add ... --json` 返回的实际 `installedPath`，避免旧回执指向上一份缓存。在插件目录 `plugins/codex-memory-manager` 运行：

```text
python3 doctor.py --plugin-root "<实际 installedPath>"
```

不要原样使用占位符。`doctor.py` 会进行 MCP 只读探测；数据库尚不存在时，服务会在配置的数据目录初始化数据库。更多说明见 [客户端加载诊断](CLIENT_LOADING.md)。

## 更新与数据

发布新版源码时同步插件目录内的 `plugin.json`、兼容声明和 Python 包版本，并提交至 `main`；保留根市场里的现有条目。用户在桌面插件管理刷新市场来源后执行更新；CLI 可先刷新 Git 市场，再安装最新版本：

```text
codex plugin marketplace upgrade yimeng02 --json
codex plugin add codex-memory-manager@yimeng02 --json
```

更新完成后，检查实际安装版本并在本地新聊天再次调用工具。市场刷新和聊天工具加载分别验收。

显式 `CODEX_MEMORY_HOME` 优先。未指定时，优先复用 `CODEX_HOME` 父目录中已有的 `.codex-memory-manager/memory.sqlite3`；MCP 子进程未继承该变量时，从标准安装位置 `CODEX_HOME/plugins/cache/<市场>/codex-memory-manager/<版本>` 确定所属配置目录。没有对应旧库时回退到当前用户默认目录。路径选择不会合并、复制或替换数据库；如要使用其他库，请明确设置数据目录。

Windows 与 WSL 的主目录不同；UI、诊断和 MCP 必须指向同一个数据目录，才能看到同一批记忆。运行可视化页面可使用与该运行环境匹配的启动脚本，或在同一执行环境、同一数据目录下运行 `python3 run.py serve`；Windows 可使用实际可用的 Python 启动器。

GitHub 仓库只保存插件源码、技能、市场声明和文档。不要提交 `memory.sqlite3`、其 WAL/SHM 文件、记忆导出 JSON、`installation.json`、本机 Codex 配置、访问令牌或个人数据目录。升级代码不要求迁移数据库；跨环境迁移使用管理页面的显式 JSON 导入导出。

## 自动检查

发布仓库根目录的 `.github/workflows/codex-memory-manager.yml` 在 Ubuntu 和 Windows 的 Python 3.10、3.13 上运行标准库 `unittest`，工作目录为 `plugins/codex-memory-manager`。独立源码包的 `.github/workflows/check.yml` 是这份仓库工作流的来源，发布时复制到仓库根目录并使用上述名称，不放在插件子目录里。测试使用临时目录和临时数据库，安装器/CLI 下载逻辑通过 mock 校验；不执行实际安装、CLI bootstrap 或读取个人记忆。GitHub Actions 完成 checkout 和 Python 环境准备需要正常访问其托管服务。

CI 通过代表源码测试通过。桌面插件管理下载和新聊天实际调用仍需在目标客户端验证。
