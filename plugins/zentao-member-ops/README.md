# ZenTao Member Ops

`zentao-member-ops` 是面向 Codex 的禅道开源版 21.7.1 插件。它按当前登录成员的实时分组与权限动态提供 MCP 工具，用于读取和操作项目、产品、执行、Bug、需求与任务，并以“先预览、再确认、写后强制回读、最后展示真实禅道界面截图”的方式保护写入。

当前版本：`1.2.0`

## 主要能力

- 首次配置时，必须由用户提供禅道地址、成员账号、成员密码和明确授权的项目 ID；存在外层 HTTP Basic 时再额外收集对应账号与密码。
- 依据账号实际分组和权限动态开放项目、Bug、需求、任务、评论、指派和状态流转等 MCP 工具，不模拟管理员权限。
- 所有写入先生成一次性预览和确认令牌；默认 `manual` 模式下必须获得用户明确确认。
- `safe-auto` 仅允许用户主动配置后自动确认低风险评论；创建、上传、指派、字段或状态变更及高风险操作仍需用户确认。
- 写入后立即清理身份、项目和产品缓存，并绕过缓存回读权威结果，减少“写入成功但缓存未更新”的误判。
- 每次成功写入后，必须打开已登录的真实禅道结果页，刷新并核对对象与变更，再把真实页面截图展示给用户。
- 开发者成员默认建立每日 07:00 新增 Bug 扫描，持久保存游标、重试队列以及可由其他 Codex 会话直接接手的 JSON/Markdown 解决方案包。
- 插件只获取 Bug 与需求上下文；分析、方案、源码处理和验证由当前 Codex 结合实际项目完成，不会自动把分析结论评论到禅道。

## 安全边界

- 凭据保存在插件仓库之外的 `~/.config/codex/zentao-member-ops/credentials.json`，文件权限限制为当前用户可读写。
- 仓库不包含禅道地址、账号、密码、Token、HTTP Basic 凭据、项目 ID、源码路径或业务数据。
- 项目白名单默认为空，必须由用户明确提供；插件不会自动扩展访问范围。
- 不提供删除类 MCP 工具。
- 解决方案包与扫描状态保存在账号私有运行目录中，不提交到 Git。
- 定时扫描不会修改源码，也不会准备或执行禅道写入。
- 真实截图不得包含登录页、密码、Token、HTTP Basic 弹窗或密码管理器界面。截图失败时只重试截图，不重复执行已经成功的写入。

## 环境要求

- 支持个人插件市场的 Codex
- Python 3.11 或更高版本
- 禅道开源版 21.7.1 REST API v1
- 若需写后截图，Codex 必须能够访问一个已经通过禅道登录及外层 HTTP Basic 的浏览器会话

## 推荐安装方式：公开插件市场源

任何人都可以添加公开市场源并安装：

```bash
codex plugin marketplace add yimeng02/codex-plugins --ref main
codex plugin add zentao-member-ops@yimeng02
```

安装或更新后请新建 Codex 任务，使新版本的 Skill 和 MCP 服务生效。

更新市场源与插件：

```bash
codex plugin marketplace upgrade yimeng02
codex plugin add zentao-member-ops@yimeng02
```

## 可选安装方式：个人本地市场

```bash
git clone https://github.com/yimeng02/zentao-member-ops ~/plugins/zentao-member-ops
codex plugin add zentao-member-ops@personal
```

若个人市场尚未登记该插件，可在 `~/.agents/plugins/marketplace.json` 的 `plugins` 数组中加入：

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

## 首次配置

首次连接前，Codex 必须先向用户收集以下信息，不能猜测、沿用未知旧配置或静默使用默认值：

1. 禅道地址，可提供 `my.html` 首页地址或 REST API v1 地址；
2. 禅道成员账号；
3. 禅道成员密码，通过隐藏提示输入；
4. 用户明确授权的项目 ID 列表；
5. 可选：外层 HTTP Basic 账号和密码；
6. 可选：对应项目源码目录、TLS 特例和写入确认模式。

进入插件目录后执行：

```bash
python3 scripts/configure.py set \
  --profile production \
  --api-base https://zentao.example.com/zentao/my.html \
  --account YOUR_ZENTAO_ACCOUNT \
  --allowed-project-ids 101,102 \
  --test
```

命令会以隐藏方式询问禅道密码。不要把密码放进命令行、聊天截图、日志、源码或 Git。`--web-base` 可省略，脚本会从禅道地址推导网页根地址。

若站点还有外层 HTTP Basic：

```bash
python3 scripts/configure.py set \
  --profile production \
  --api-base https://zentao.example.com/zentao/my.html \
  --account YOUR_ZENTAO_ACCOUNT \
  --allowed-project-ids 101 \
  --http-basic-account YOUR_BASIC_ACCOUNT \
  --test
```

外层 HTTP Basic 密码同样通过隐藏提示输入。配置完成后检查脱敏状态和连通性：

```bash
python3 scripts/configure.py status
python3 scripts/diagnose.py
```

## 源码映射

把已授权的禅道项目映射到正确的本地源码树：

```bash
python3 scripts/configure.py map-source \
  --profile production \
  --project-id 101 \
  --path /path/to/project/source
```

没有有效映射时，插件仍会保存完整 Bug 证据，但源码相关结论必须标记为低置信度、受阻或需要补充信息。

## 使用方式

安装并新建 Codex 任务后，可以直接提出自然语言请求，例如：

- “检查当前禅道账号、成员分组、可用 MCP 工具和可见项目。”
- “列出项目 101 最近新增的 Bug，并读取 Bug 详情、历史、评论和附件。”
- “结合项目源码分析 Bug 123，给出根因、修改步骤、风险和验证方案，不写回禅道。”
- “列出项目 101 已生成的 Bug 解决方案包，并读取 Bug 123 的完整交接包。”
- “预览把 Bug 123 指派给开发成员，但先不要执行。”
- “预览关闭 Bug 123，等我确认后再执行；成功后展示真实禅道结果页截图。”

MCP 的 `connection_status` 会返回脱敏配置状态、当前确认策略、项目范围及首次配置要求。可用工具会随账号分组和禅道实时权限变化。

## 写入与截图流程

1. Codex 读取最新对象、项目范围和当前成员权限。
2. 调用对应的 `prepare_*` 工具生成完整预览，不执行写入。
3. 向用户展示目标、字段、风险和影响，并取得符合策略的明确确认。
4. 使用一次性确认令牌调用 `execute_confirmed_write`。
5. MCP 重新校验权限、对象版本和确认策略后执行一次写入。
6. MCP 清理本地缓存，并绕过缓存回读权威对象；若回读失败，单独报告刷新问题，不把已完成写入说成失败。
7. Codex 打开返回的 `ui_verification.web_url`，使用已登录浏览器刷新真实禅道页面。
8. 核对页面中的对象 ID 及字段、状态、评论、指派或创建结果，截图并在会话中展示。

API JSON、预览内容、生成的 HTML、模拟页面和登录页截图都不能替代真实禅道页面截图。浏览器会话不可用时，应报告“写入已完成、截图待验证”，恢复登录后只补截图。

## 开发者每日 Bug 解决方案包

开发者账号首次成功交互连接后，Skill 会检查实时角色与 Bug 读取权限，并为符合条件的账号建立一个本地时区每日 07:00 的 Codex heartbeat：`禅道开发者每日新增 Bug 方案包`。

每次运行会：

1. 使用账号私有持久状态扫描所有已授权且可见的项目；
2. 获取新增 Bug 的完整详情、历史、评论、附件元数据和内嵌图片；
3. 只检查正确映射的源码树；
4. 形成明确的根因、置信度、实现步骤、候选文件、风险和验证方案；
5. 保存权威 JSON 与 Markdown 交接包；
6. 只有在方案包落盘成功后才从重试队列移除 Bug。

其他 Codex 会话可通过 `list_bug_solution_packages` 和 `get_bug_solution_package` 直接获取交接包，无需复制本地路径。自动化只在 Codex 自动化运行机制实际触发时执行，并非仅保持窗口打开就持续轮询。

## 常见问题

- 安装后找不到 Skill 或 MCP：新建 Codex 任务，旧任务不会自动加载新的插件版本。
- 返回 401/登录失败：先确认禅道成员密码；若页面有额外认证，再配置外层 HTTP Basic 账号与密码。
- 写入成功但列表仍是旧数据：优先检查 `cache_refresh.authoritative_read`，再用对应读取工具传入 `refresh=true`。
- 写后无法截图：确认浏览器已经完成禅道登录和外层 HTTP Basic；不要为了截图再次执行写入。
- Bug 分析没有源码证据：检查项目 ID 是否在白名单内，并使用 `map-source` 配置正确源码目录。
- 使用明文 HTTP：插件会明确提示传输未加密；生产环境建议使用受信任的 HTTPS 或安全内网链路。

## 开发与验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile mcp/server.py mcp/zentao_client.py scripts/configure.py scripts/diagnose.py
```

版本记录见 [CHANGELOG.md](CHANGELOG.md)。
