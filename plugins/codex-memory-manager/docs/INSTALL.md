> 当前 0.2.3：推荐从 [GitHub 个人市场](GITHUB-MARKETPLACE.md) 安装。新增全局记忆启动钩子，安装后需审核并信任；详见 [自动读取修复说明](RELEASE-0.2.2.md)。

# 自动安装与升级

本项目提供 Windows、Linux、WSL 和 macOS 安装入口，将本地记忆管理器注册为 Codex 插件。页面和插件共用同一个本地数据目录。

## 安装前准备

- Python 3.10 或更新版本。
- 已安装且支持 `codex plugin add` 的 Codex CLI。
- 使用与目标 Codex 客户端一致的操作系统用户及 `CODEX_HOME`。

安装器会检查依赖和插件命令，运行项目测试，并在检查失败时停止。它不下载 Python；缺少可用 CLI 时，默认通过 OpenAI 官方安装器补齐，`--check` 与 `--no-bootstrap-cli` 可禁止下载。不要求管理员权限，也不会整体覆写 `config.toml` 或自动批准钩子信任。

## Windows

解压或保留完整项目目录，双击根目录的 `install.cmd`。也可以在 PowerShell 中进入项目目录后执行：

```powershell
.\install.ps1
```

需要传参时：

```powershell
.\install.ps1 --check
.\install.ps1 --codex "C:\path\to\codex.exe"
.\install.ps1 --data-dir "D:\CodexMemory"
.\install.ps1 --reinstall
```

如果本机策略阻止 PowerShell 脚本，使用 `install.cmd`，或用已安装的 Python 直接运行核心安装器：

```powershell
python .\install.py
```

Windows 的 Python Launcher 可用时，也可执行 `py -3 .\install.py`。请确保选用的解释器为 Python 3.10 或更新版本。

## Linux、WSL 与 macOS

在项目根目录执行：

```sh
sh install.sh
```

或者直接使用 Python：

```sh
python3 install.py
python3 install.py --check
python3 install.py --codex /absolute/path/to/codex
python3 install.py --data-dir /absolute/path/to/memory-data
python3 install.py --reinstall
```

所有入口均将参数交给 `install.py` 处理，路径含空格时请加引号。

## 参数

| 参数 | 用途 |
| --- | --- |
| `--check` | 检查源码与 CLI 命令帮助，并执行 30 项测试；不创建安装副本、不注册市场、不安装插件。 |
| `--codex PATH` | 指定 Codex CLI 可执行文件。未指定时从当前环境查找。 |
| `--data-dir PATH` | 指定持久数据和插件安装目录。 |
| `--reinstall` | 显式请求刷新本插件；重复安装默认也会刷新。 |

数据目录按以下顺序确定：`--data-dir`、`CODEX_MEMORY_HOME` 环境变量、当前用户的 `~/.codex-memory-manager`。首次安装后请沿用该目录；选择另一个目录不会自动迁移原来的记忆库。

## 安装器执行的操作

1. 检查 Python 版本、项目文件以及 Codex 插件命令支持情况。
2. 运行项目单元测试；失败时停止安装。
3. 将插件复制到数据目录中的稳定位置 `marketplace/plugins/codex-memory-manager`，并生成本地市场配置。
4. 在安装副本的 `mcp.json` 写入实际 Python 解释器的绝对路径及 `CODEX_MEMORY_HOME`，使 MCP 与页面读写同一记忆库。
5. 使用官方命令注册本地市场并安装插件：

   ```text
   codex plugin marketplace add <数据目录>/marketplace --json
   codex plugin add codex-memory-manager@personal-memory-tools --json
   codex plugin list --marketplace personal-memory-tools --json
   ```

6. 核对列表中的插件名称、市场名称、版本、安装状态及启用状态，写入 `installation.json` 安装记录及 `open-manager.py` 页面启动脚本。

重复执行会通过 `plugin add` 刷新 `codex-memory-manager` 并确保启用，不先卸载插件。源码内容相同时复用稳定副本，内容变化时更新副本并备份旧代码，保留记忆数据。遇到同名但来源不同的市场时会停止，不覆盖原来源。安装完成后，源码目录可继续用于开发，已注册插件运行于数据目录中的安装副本。

## 启动页面与验证插件

安装完成后，按安装器打印的命令运行数据目录中的 `open-manager.py`。默认目录示例：

```powershell
python "$env:USERPROFILE\.codex-memory-manager\open-manager.py"
```

```sh
python3 "$HOME/.codex-memory-manager/open-manager.py"
```

使用自定义数据目录时替换相应路径。请使用 Python 运行此文件；是否能直接双击 `.py` 文件取决于操作系统的文件关联。安装过程默认不自动启动页面。

完全退出并重新打开目标 Codex 客户端，再执行 `codex plugin list --marketplace personal-memory-tools --json` 检查本插件。CLI 显示启用后，还应在新会话中调用插件检索工具，并确认读取到页面中保存的条目。只有完成这一步，才算验证了实际客户端的插件加载和数据共享。

`installation.json` 记录本次安装信息，可用于排查解释器、安装目录及 Codex CLI 选择问题。文件存在不等于目标客户端已经加载插件。

## 更新

1. 保留当前数据目录，更新或替换项目源码。
2. 在新源码目录再次执行相同安装入口，并沿用原来的 `--data-dir` 或 `CODEX_MEMORY_HOME`。
3. 按安装器输出确认更新结果，然后重新打开 Codex 客户端。

无需手动删除记忆库。重复运行安装器会刷新本插件；也可添加 `--reinstall` 表达刷新意图。

## 手动安装与故障排查

优先运行 `--check`。如果当前 CLI 不支持 `plugin add`，安装器会明确报错；请安装支持该命令的 Codex CLI，再重试。脚本不会通过改写其他插件配置来绕过缺失命令。

若已生成安装副本，需要手动排查注册步骤，可使用上面的三条官方命令。`<数据目录>/marketplace` 必须换成实际路径；不要将尖括号原样输入终端。尚未生成安装副本时，请先修复检查结果并运行完整安装。

Windows 与 WSL 通常拥有不同的用户目录和 Codex 配置。WSL 中安装成功不代表 Windows 客户端已安装，反之亦然。请在目标客户端使用的环境中运行安装器，并确认 `CODEX_HOME` 一致。`--codex` 用于选定命令路径，不会自动切换操作系统用户或 Codex 配置目录。

如插件可用而页面看不到同一批条目，检查页面启动命令和安装记录是否指向同一数据目录。升级或重装后仍显示旧工具时，完全退出并重新打开 Codex，再用新会话验证。

官方参考：[Codex 插件命令](https://learn.chatgpt.com/docs/developer-commands#codex-plugin)、[构建插件](https://learn.chatgpt.com/docs/plugins/build/plugins)。

## 验证与自动读取

安装器检查缓存中的技能、MCP 握手和只读调用；CLI 登记成功仍需在目标客户端验证。0.2.2 另通过隔离配置中的真实 Codex 宿主验证：审核测试钩子后，发送“你好”的首个模型请求已包含全局记忆，且未包含其他范围正文。测试使用本地模拟模型，不代表实际模型已遵循每条偏好。

升级后完全退出并重启 Codex，按提示审核 `SessionStart` 和 `UserPromptSubmit`。CLI 可使用 `/hooks` 检查；未信任、被策略禁用或客户端不支持的钩子不会自动执行。原资料库继续使用，无需开启 Codex 内置记忆或改写全局 AGENTS。
> **0.1.2 安装修复：** 不再要求提前把 CLI 加入 PATH。安装器会查找桌面应用和常见用户安装位置；仍无可用 CLI 时，默认运行 OpenAI 官方安装器。`--check` 与新增的 `--no-bootstrap-cli` 不下载。本文旧版“需手动安装 CLI”或“不下载 Codex”的描述由此更新，详细步骤见 [CLI 找不到的修复说明](CLI_NOT_FOUND.md)。

> **0.1.3 验收更新：** CLI 的 installed/enabled 不代表桌面会话工具已加载。新版增加兼容声明与实际安装缓存的 MCP 进程自检，并提供 `doctor.cmd` 检查旧版安装。步骤见[客户端加载说明](CLIENT_LOADING.md)。

