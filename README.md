> v0.9.5：修复普通HTTP/DDNS访问时crypto.randomUUID不可用导致登录请求无法发出的问题，保留Web8001/OneBot8080独立端口。[原因与修复](docs/21-http-request-id-fix.md)。

> v0.9.4：Web默认8001、OneBot默认8080，独立监听和协议隔离；共享同一游戏状态与机器人连接，统一启动停止。[双端口配置](docs/20-split-web-and-onebot-listeners.md)。

> v0.9.3：支持API_HOST网络监听，浏览器来源默认自动识别；内网IP、公网IP和DDNS无需逐个配置PUBLIC_ORIGIN。[端口与外部访问](docs/14-windows-deployment.md)。

> v0.9.2：修复玩家模型把隐藏状态误判为无对局的问题；提供同群公开执行回执，新增真实自然语言连续测试及聊天验收范围约束。[根因、修复与实测](docs/19-player-chat-context-and-verification.md)。

> v0.9.1：补齐游戏删除；管理员 Agent 可在同一会话跨空间查询、修改、创建和申请删除游戏，审批明确显示目标。[使用方式与删除范围](docs/18-game-deletion-and-cross-workspace-agent.md)。

> v0.9：统一对话、持久操作审批和会话级自动同意；Agent 可修改全局消息触发规则、指令前缀及助手说明，支持无需指令的玩家对话。[使用方式、权限边界和验收](docs/17-unified-conversation-and-platform-rules.md)。

> v0.8.1：修复方案模式错误调用和创建玩法入口；工具参数与真实命令目录绑定，预演禁止间接运行插件，任务完成增加实际回执核验。[约束设计与复测结果](docs/16-agent-execution-contracts.md)。

> v0.8：真实会话管理、连续追问与执行回执；新增模块预检、连续试玩、实际行动反馈和玩家参数修正。[专用 Agent 思路、使用方式与边界](docs/15-agent-workspace-and-specialist-loop.md)。

> 新增通用服务端扩展：Agent 可编写跨记录处理器、解析隐藏映射、返回受限的事务修改与分受众私聊消息，并接入 GM 人工流程。[协议与真实模型验收](docs/13-agent-server-extensions.md)。
>
> v0.7：持久 Agent 执行循环、Python/Node 代码插件、pip/npm 安装、测试反馈修复与动态工具注册。[运行方式和信任边界](docs/11-agent-harness-and-code-plugins.md)。

> v0.6：Agent 可创建和修改运行时模块、自定义计算函数及新交互，玩家 Agent 自动发现获授权功能。具体玩法均按需创建；[实现与扩展边界](docs/10-runtime-modules.md)。

> v0.5：新增由管理员配置的玩家 Agent、私聊身份隔离及玩家试运行。详见 [玩家参与设计与边界](docs/09-player-agent.md)。真实模型与真实 QQ 私聊尚未验收。

# MythWeaver

GitHub：[R0Y00/MythWeaver](https://github.com/R0Y00/MythWeaver)（私有仓库）。通过 Git 部署后，运行 `update-platform.bat` 可备份并更新；首次源码部署需要 Git、Python、Node.js 和 PostgreSQL 程序，详见部署文档。

迁移到另一台 Windows：先阅读 [Windows 部署与数据迁移](docs/14-windows-deployment.md)。发布包解压后运行 `setup-windows.bat`，再运行 `start-platform.bat`；无须 Docker。NapCat 和机器人账号不再写死，支持后续单独配置。

由管理员定义玩法的 AI 辅助多人互动游戏平台，以 QQ 为参与入口。管理员决定游戏有哪些概念、数据、规则、交互和功能；平台提供状态记录、权限、执行与 AI 辅助。

当前阶段：v0.9.5，包含玩家聊天修复、跨空间管理、双端口和 HTTP 登录兼容。Windows 原生运行，NapCat 直接接入，无 Docker。

本机入口：[http://127.0.0.1:8001](http://127.0.0.1:8001)（新安装默认；旧安装以 `.env` 为准，Web与OneBot端口必须不同）。管理员密码保存在 `.runtime/credentials.txt`（本机私有文件，已忽略版本管理）。

双击 `start-platform.bat` 启动平台与项目专用 PostgreSQL；`start-napcat.bat` 沿用现有 NapCat 自动登录脚本；`stop-platform.bat` 停止平台和项目数据库。

## 文档

1. [产品需求文档](docs/01-product-requirements.md)：角色权限、完整功能范围、首版边界、操作示例与验收标准。
2. [技术设计](docs/02-technical-design.md)：系统架构、Agent、规则引擎、数据模型、事务、API、部署与测试。
3. [QQ 接入选型](docs/03-qq-integration-decision.md)：NapCat 直连方案、SnowLuma 等备选方案比较及验证清单。
4. [本机运行与当前实现范围](docs/04-running-local.md)：启动、配置、QQ 指令、模型接口、测试和后续缺口。

5. [控制台升级说明](docs/05-console-upgrade.md)：参考来源、本次新增能力与验收结果。

6. [流程与管理执行](docs/06-workflows.md)：条件、人工节点、对象引用、Agent 查询与直接执行。
7. [首版验收跟踪](docs/07-acceptance-status.md)：A01–A16 当前状态与剩余缺口。

8. [管理 Agent 游戏控制](docs/08-agent-game-control.md)：25 项共享命令、迁移、退役恢复、流程接管与真实能力边界。

文档基线：2026-10-29，v0.4（需求与技术设计）。设计决策可调整，不代表已完成兼容性验证。

首版组合：NapCat → 平台自有 OneBot 11 接入模块 → Python 游戏后端 / Agent → PostgreSQL；Web 管理台使用 React / TypeScript。NapCat 通过反向 WebSocket 直接连接平台，不引入 NoneBot2 等中间机器人框架。

项目按非商业、熟人团、自托管场景设计。

管理员可以修改全部业务对象、规则、权限和 Agent 配置；Agent 的执行权限由服务端按实际操作者授权。QQ 接入可替换，游戏数据独立保存。

平台可以从空白游戏开始，不要求角色卡、战斗、背包、技能、骰子、剧情主线或阶段。管理员可创建自己的对象、交互和决定方式，按需选用已实现模块；未启用的模块不进入游戏界面、命令和 Agent 工具集。首版先验证通用能力，不预先建设一整套 RPG 功能。

当前已实现分区控制台、空白空间、自定义 JSON Schema 数据、动态表单、通用数据规则、条件分支、持久流程与局部人工等待、类型化对象引用、人工决定、参与权限、模板库、检查点恢复、QQ 诊断，以及带审批的连续对话助手、代码插件和平台规则管理。需求文档中的权限委托、模块安装、复杂规则、历史撤销/分支恢复等尚未全部实现，请以运行说明中的能力表为准。
