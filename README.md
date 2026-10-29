# yimeng02 Codex Plugins

这是 `yimeng02` 的公开 Codex 插件市场源，也是其中插件的唯一源码仓库。其他人添加该市场源后即可安装插件。

## 添加市场源并安装

```bash
codex plugin marketplace add yimeng02/codex-plugins --ref main
codex plugin add zentao-member-ops@yimeng02
```

安装后请新建 Codex 任务，使插件提供的 Skill 和 MCP 服务生效。禅道地址、账号、密码、项目范围和源码路径均由每位使用者保存在自己的电脑中，不包含在本仓库。

## 更新

```bash
codex plugin marketplace upgrade yimeng02
codex plugin add zentao-member-ops@yimeng02
```

## ZenTao Member Ops

当前版本：`1.2.0`

主要能力：

- 首次配置时主动收集禅道地址、成员账号、隐藏输入的密码和明确授权的项目 ID；
- 按账号实时分组和权限动态提供项目、Bug、需求、任务及状态流转 MCP 工具；
- 所有写入默认先预览并等待用户确认，写入后清理缓存并强制回读；
- 每次成功写入后，要求展示已登录的真实禅道结果页截图；
- 开发者成员默认每日 07:00 扫描新增 Bug，生成可跨 Codex 会话交接的完整解决方案包；
- Bug 与需求分析由当前 Codex 完成，不自动把方案评论到禅道。

完整安装、首次配置、使用方式、安全模型及故障排查请参阅[插件说明](plugins/zentao-member-ops/README.md)，版本记录见[更新日志](plugins/zentao-member-ops/CHANGELOG.md)。

`zentao-member-ops` 的权威源码位于本仓库的 [`plugins/zentao-member-ops`](plugins/zentao-member-ops)；原独立仓库已归档，不再作为源码或市场源。

## Codex 规则与记忆管理器

当前版本：`0.2.2`。提供中文本地管理页面和 MCP 工具，可自定义分类、编辑记忆、批量启停或移动、回收恢复、查看历史及导入导出。数据保存在使用者本机，插件升级时保留。

在 Codex 插件管理中选择 **Yimeng02** 市场并安装 **规则与记忆管理器**，或运行：

```bash
codex plugin marketplace upgrade yimeng02
codex plugin add codex-memory-manager@yimeng02
```

当前 GitHub 版本需要 agent 环境 PATH 中有 Python 3.10+ 的 `python3`；Windows 钩子默认使用 `py -3`，本机安装器可固定运行命令与数据目录。安装后在本地新聊天中调用 `codex-memory-manager:memory-manager`。

[插件说明](plugins/codex-memory-manager/README.md) · [个人市场安装与更新](plugins/codex-memory-manager/docs/GITHUB-MARKETPLACE.md) · [加载故障排查](plugins/codex-memory-manager/docs/CLIENT_LOADING.md) · [更新记录](plugins/codex-memory-manager/CHANGELOG.md)

记忆管理器 0.2.2 为每个新聊天在首轮回复前准备全局记忆，库版本变化后自动刷新。使用前需在 Codex 审核并信任两个启动/消息钩子；精确匹配、插件标识匹配与原资料库均保留。[本版说明](plugins/codex-memory-manager/docs/RELEASE-0.2.2.md)。
