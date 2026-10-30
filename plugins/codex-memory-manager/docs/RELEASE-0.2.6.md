# 0.2.6 任务执行中与上下文压缩后的记忆刷新

## 更新后的行为

| 检查点 | 读取行为 |
| --- | --- |
| SessionStart：启动、恢复、清空、派生 | 重新准备启用的全局记忆 |
| SessionStart(source="compact")：自动或手动压缩后 | 不受库版本去重影响，在下一次模型请求前重新加载全局记忆 |
| UserPromptSubmit：用户消息 | 无本聊天记录、库版本变化或插件版本变化时刷新 |
| PreToolUse：受支持的工具调用前 | 检查上述版本条件，有变化时为本聊天准备最新全局内容 |
| PostToolUse：受支持的工具执行后 | 捕获工具运行期间的记忆更新，供模型下一次续行使用 |

版本未变化时，用户消息和工具检查点不重复注入全部正文。压缩后的 SessionStart 必须重新读取，因为聊天上下文可能已压缩，而资料库版本没有变化。

自动正文只包含启用、未删除的全局条目；项目、技能和插件只给出可用目标索引，继续按当前任务及 scope/target 原值精确检索。内容超出自动预算时明确标记遗漏；不代表全部内容已经加载。准备记录只说明提供给宿主的版本，实际遵循仍须核对。

## 安装与审核

从已配置的 Yimeng02 个人市场更新，或运行：

```sh
codex plugin marketplace upgrade yimeng02
codex plugin add codex-memory-manager@yimeng02
```

完全退出并重启 Codex，在本地新聊天验证。需要更新页面进程时也应重启。沿用原资料库，不重新创建数据库，不改写实际全局 AGENTS 文件或重置首次配置决定。

本版新增的 PreToolUse、PostToolUse 必须审核准确的定义后启用；安装或更新不等于信任。两个钩子都同步运行插件内的 memory_context.py，最多 5 秒，读取现有资料库、检查库及插件版本，并写入普通聊天准备记录；不保存提示词、工具输入输出，不读聊天历史，不修改记忆条目，不阻止或改写原工具。原 SessionStart 和 UserPromptSubmit 的具体定义保持不变。使用本机安装器时运行时和数据目录会按原有逻辑固定到实际环境，审核时以宿主显示的命令为准。

## 边界与故障处理

更新在下一个受支持检查点进入模型上下文，不是网页保存后立即中断模型生成。正在执行的长命令不会中途使用新规则；工具执行后可在模型续行时读取。已执行的操作不会被记忆更新自动撤销。托管工具、特殊工具路径可能不触发本地工具钩子；用户消息和 SessionStart 仍可补充读取，需要立即确认时可调用 memory_refresh。

不额外注册 PreCompact/PostCompact 来输出正文：当前官方文档没有为这两种事件定义可注入正文的 additionalContext 合同。压缩后的正文注入使用明确支持的 SessionStart(source="compact")，不能把日志或成功退出当作已经加载。客户端未发出事件、钩子未信任或被禁用、运行时错误时，如实报告未完成，不伪造成功。

依据：[OpenAI Docs · SessionStart](https://learn.chatgpt.com/docs/hooks#sessionstart)、[PreToolUse](https://learn.chatgpt.com/docs/hooks#pretooluse)、[PostToolUse](https://learn.chatgpt.com/docs/hooks#posttooluse)、[工具覆盖范围](https://learn.chatgpt.com/docs/hooks#tool-coverage)。
