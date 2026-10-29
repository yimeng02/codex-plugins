# 修复安装时“未找到 Codex CLI”

适用于安装器 0.1.2。0.1.1 仅查询 PATH，因此已安装桌面应用或刚安装 CLI、尚未刷新 PATH 时，也可能错误退出。报错本身只能说明安装器没有在 PATH 中找到 CLI，不能确定电脑上没有安装。

## 直接重试

Windows 关闭上一次的安装窗口，重新双击项目中的 `install.cmd`。窗口顶部应显示 `Codex Memory Manager Installer 0.1.2`。使用更新后的整个项目目录，不要只复制 install.py；检测逻辑位于 `memory_manager/installer_support.py`。

新版会依次尝试显式指定的位置、CODEX_CLI_PATH、PATH、官方独立安装目录、npm 用户目录、standalone current 及桌面应用随附的 CLI。每个找到的程序都会检查版本和插件命令能力，不会把桌面应用的图形界面程序当作 CLI。旧路径失效或旧版 CLI 不支持命令时，会继续查找其他候选。

Windows 的常见位置包括：

```text
%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe
%APPDATA%\npm\codex.cmd
%CODEX_HOME%\packages\standalone\current\bin\codex.exe
```

没有设置 CODEX_HOME 时使用当前用户的 `.codex`。桌面应用位置通过当前用户的安装包与进程位置查询，不接管 WindowsApps 权限，也不修改桌面应用安装文件。

## 确实缺少 CLI 时

默认自动从 OpenAI 的官方入口下载并运行当前用户的 Codex CLI 安装器：

- Windows：[官方 PowerShell 安装器](https://chatgpt.com/codex/install.ps1)
- Linux/WSL/macOS：[官方 shell 安装器](https://chatgpt.com/codex/install.sh)

当前官方入口转向 releases.openai.com。下载需要网络，遵循系统代理与 TLS 校验。脚本只接受预期的 OpenAI HTTPS 安装入口；失败时显示具体错误，不将失败报告为安装成功。无需另行安装 Node.js/npm。

CLI 安装成功后，安装器在同一窗口重新检测并继续安装记忆管理插件，不依赖当前窗口刷新 PATH。若官方安装器遇到代理、网络、目录冲突或企业策略限制，修复显示的具体问题后重试。

CLI 的安装位置、更新与下载验证由官方安装器处理；记忆管理器不会为解决该错误删除数据库、调整 WindowsApps 权限或绕过 Codex 的权限设置。

## 只检查，或使用离线 CLI

在项目目录打开终端：

```text
install.cmd --check
install.cmd --no-bootstrap-cli
install.cmd --codex "C:\实际目录\codex.exe"
```

PowerShell 可使用 `./install.ps1`，Linux/WSL/macOS 使用 `sh install.sh`，参数相同。

- `--check`：检查源码、可用 CLI 和测试，不注册市场、不安装插件，也不下载 CLI；任何检查失败时即停止。
- `--no-bootstrap-cli`：只使用电脑上现有的 CLI，仍执行正常插件安装；找不到可用程序时明确报错。
- `--codex`：严格使用该程序，路径错误或命令不兼容时停止，不擅自替换它或另装 CLI。
- `--data-dir`：与此前相同，指定 UI、MCP 共用的数据目录。

## 验证成功

只有命令行实际返回已安装、已启用，且名称、市场、版本均匹配时，安装器才输出“安装验证通过”，并写入 installation.json。之后重启相同用户、相同 CODEX_HOME 的 Codex 桌面客户端，在新聊天里调用 memory-manager。

可视化页面不依赖 CLI。需要先编辑记忆时，可以直接运行 `start.cmd`。这不表示插件已经完成安装。

本修复新增了发现与补齐逻辑的回归用例，涵盖 PATH 外的安装位置、失效路径、旧 CLI 回退、只检查不下载及官方安装器调用。当前开发环境无法创建本地进程，尚未实际运行测试或完成 Windows 安装验收。

依据：[OpenAI 官方 CLI 安装说明](https://developers.openai.com/codex/cli)、[Windows CLI 部署说明](https://learn.chatgpt.com/docs/enterprise/windows-deployment#deploy-codex-cli-on-windows)。
