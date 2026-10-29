# 0.1.3 客户端加载与 MCP 验收

- 增加 .codex-plugin/plugin.json、.mcp.json 兼容声明，同时保留 portable plugin.json 和 mcp.json。
- 技能补充中文显示元数据和本地 MCP 依赖说明。
- 安装器为 portable、compatibility 两种格式写入同一 Python 与共享数据目录。
- 对实际安装缓存执行 MCP initialize、tools/list 和 memory_search 只读调用；检查失败时不报告完整验收成功。
- 安装回执分开记录 CLI 登记、本机 MCP 自检、待确认的桌面会话加载状态。
- 新增 doctor.py/doctor.cmd，允许检查现有 0.1.2 安装，不需要先重装。
- 新增包装配置、协议异常和真实子进程集成用例。
- 技能明确区分技能发现与工具挂载，避免对“工具不可用”给出未经证实的重连建议。

用户日志确认此前 0.1.2 安装测试通过（1 项跳过）且 CLI 登记成功。当前会话仍无记忆 MCP 工具，具体加载原因尚需实际诊断输出确认。开发会话执行工具不可用，本版新增用例尚未在开发会话执行。

操作步骤见 [客户端加载说明](CLIENT_LOADING.md)。
