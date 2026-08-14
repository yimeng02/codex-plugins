# yimeng02 Codex Plugins

这是 `yimeng02` 的公开 Codex 插件市场源，其他人添加该市场源后即可安装其中的插件。

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
