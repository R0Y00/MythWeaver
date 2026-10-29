# QQ 接入选型与决策记录

日期：2026-10-29 · 版本：v0.2 · 决策编号：ADR-001 · 状态：已确定 NapCat 直连平台，待实现和真实账号验证。

## 1. 结论

用户已明确本项目不商用，并确定首版使用 NapCat 直接接入平台。平台自行实现 OneBot 11 接入模块，不引入 NoneBot2、Koishi 或 AstrBot 等中间机器人框架。SnowLuma 作为后续替换候选。

首版链路：

```text
QQ → NapCat → OneBot 11 反向 WebSocket
                         ↓
             MythWeaver 自有接入模块（API 进程内）
                         ↓
             应用服务 / Agent / 规则引擎
                         ↓
                    PostgreSQL

Web 管理工作台 → MythWeaver API → 同一应用服务
```

NapCat 负责连接 QQ 并提供消息接口；OneBot 规定消息和 API 格式；平台自有模块负责会话、消息解析、指令路由和收发。游戏规则和状态仍由独立领域模块负责。[NapCat 项目](https://github.com/NapNeko/NapCatQQ)、[OneBot 定义](https://onebot.dev/)

本项目按非商业自托管实施，当前不安排收费运营或官方机器人接入；许可证作为依赖资料留存，部署使用官方发行包，通过 OneBot 对接，不修改或打包分发 NapCat 代码。未来用途发生变化时再重新评估。[许可证原文](https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE)

## 2. 分层选型，避免混淆

| 层 | 解决的问题 | 候选 |
| --- | --- | --- |
| QQ 接入端 | 登录/官方鉴权、接收事件、发送消息 | NapCat、QQ 官方 Bot API、LLBot、Lagrange |
| 消息协议 | 应用和接入端如何交换事件/API | OneBot 11；其他方案可有 Satori/Milky/官方 API |
| 平台接入模块 | 会话、路由、命令、协议转换 | 首版自行实现；NoneBot2、Koishi、AstrBot 不作为依赖 |
| 产品业务 | 世界状态、规则、权限、审批、恢复 | MythWeaver 自己实现 |
| 模型编排 | 意图解析、工具调用、内容生成 | MythWeaver Agent + 可替换模型 Provider |

本项目选择 NapCat 作为 QQ 协议端，由自己的平台直接消费 OneBot 事件及调用 API。

## 3. QQ 接入候选比较

以下“适用性”是基于本项目需求的设计判断；不等同于实际稳定性测量，也不承诺具体账号可获得全部能力。

| 候选 | 已核实的定位 | 对本项目的判断 | 决策 |
| --- | --- | --- | --- |
| NapCat | 基于 NTQQ 的协议端；提供 OneBot 11 接口及 WS 连接配置 | 适合熟人群收发和常规机器人交互；需验证客户端/协议兼容 | 首版采用，直连平台 |
| SnowLuma | 独立 NTQQ 互操作运行时，提供 OneBot 11、WebUI、SDK、MCP；作者文档标明仍在早期开发 | 能接同一业务架构，但接口与配置变化需额外跟进；SDK/MCP 不是本平台首版的必需能力 | 后续替换候选，首版仍用 NapCat |
| QQ 官方机器人 | 腾讯提供的 Bot 平台与 API；接入权限需以平台当前账户和文档为准 | 群事件、私聊、主动通知、身份体系不能直接假定与个人号相同 | 当前不实施，用途变化时再评估 |
| LLBot / LuckyLilliaBot | 当前旧 LLOneBot 地址重定向到该项目，项目声明支持 OneBot 11、Satori、Milky | 可作为替换接入端候选；相同协议仍须验证消息段、错误和重连语义 | 第二候选，不首版同时维护两套 |
| Lagrange.Core | C# 的 NTQQ 协议实现 | 若未来有特定部署约束，可独立验证；目前没有本项目账号上的比较证据 | 暂不采用，不宣称已停更或更稳定 |

来源：[NapCat](https://github.com/NapNeko/NapCatQQ)、[NapCat 网络配置源码](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/config/config.ts)、[腾讯机器人文档](https://github.com/tencent-connect/bot-docs/blob/main/docs/README.md)、[LLBot](https://github.com/LLOneBot/LuckyLilliaBot)、[Lagrange.Core](https://github.com/LagrangeDev/Lagrange.Core)。

SnowLuma 的定位与早期开发说明分别见[项目仓库](https://github.com/SnowLuma/SnowLuma)和[介绍文档](https://snowluma.github.io/en/docs/guide/introduction)。它本身是接入运行时，不能简单理解为 NapCat 的 GUI。两者的真实稳定性尚未在本项目账号上对比；选择 NapCat 是控制首版集成范围的工程判断，不是已测得的性能或稳定性排名。

非官方接入应把客户端变更、登录失效、账号限制和连接故障视为需要验证的运行风险，而不是对账号长期可用性的保证。平台状态放在自己的数据库，QQ 离线时 GM 仍可通过 Web 继续主持和修复。

官方文档仓库中的主体准入信息可能落后于实际开放平台；本次未登录开发者账户，不能确认该账号当前获准的群场景、人数、审核要求、频控或主动消息权限。实施时以实际账户控制台及当时官方文档为准，不在本设计中写死这些限制。

## 4. 应用框架比较

| 框架 | 主要价值 | 本项目取舍 |
| --- | --- | --- |
| NoneBot2 | Python 异步框架、插件和适配器；已有 OneBot 与 QQ 适配器 | 不采用：首版无明确需要复用的插件，平台自行处理消息 |
| Koishi | TypeScript 机器人框架、插件与配置界面 | 团队选择全 TypeScript 时可重新评估；目前不为 QQ 网关单独引入第二套后端语言 |
| AstrBot | 集成 IM、模型、工具、插件与 WebUI 的 AI 机器人平台 | 适合快速体验 AI 聊天；本产品仍须另做游戏事务、版本、权限和审批，首版不把领域状态交给其会话系统 |
| 自有 OneBot 11 模块 | 完全控制传输层，直接调用平台应用服务 | 采用：API 挂载反向 WS 入口，显式实现会话、echo、解析与可靠性 |

来源：[NoneBot2](https://nonebot.dev/docs/)、[OneBot 适配器](https://github.com/nonebot/adapter-onebot)、[QQ 适配器](https://github.com/nonebot/adapter-qq)、[Koishi](https://koishi.chat/zh-CN/)、[AstrBot](https://github.com/AstrBotDevs/AstrBot)。

选择直接接入的依据是 MythWeaver 已经需要自己的 API、Agent、权限和任务系统，首版没有必须复用的机器人插件；再叠加一套机器人框架收益有限。相应代价是平台必须自己维护 OneBot 会话、消息解析、请求关联和异常恢复，并通过契约测试验证。

已有平台接入能力也不自动提供定义管理、状态事务、幂等、可选人工决定和分支恢复，这些由 MythWeaver 核心实现。具体游戏有哪些对象与机制由管理员决定，QQ 接入端不预设玩法。

## 5. 为什么首版选 OneBot 11

NapCat 公开 API 文档按 OneBot 11 提供接口，平台自有模块按该协议实现所需的事件与 API 子集，不依赖第三方机器人适配器，也不要求首版覆盖 NapCat 的全部扩展接口。[NapCat API](https://napneko.github.io/api/)

业务层只接收标准化消息结构，不存 OneBot 专有格式为核心领域数据。未来接 QQ 官方 API、Satori、Milky 或其他聊天平台时，新增 adapter，并显式声明 capability。

首版只依赖群文本、@、基础消息段和文字审批；不将协议扩展接口、消息撤回、历史消息拉取、可点击审批按钮或好友私聊作为所有渠道的必备功能。

## 6. 反向 WebSocket 连接方案

平台 API 进程直接监听 `/channels/onebot/v11/ws`，NapCat 主动连接，采用 Universal 全双工连接收事件和调用 API。该路径是反向 WebSocket；NapCat 的 WebUI 管理端口不作为 OneBot 地址。[连接文档](https://napneko.github.io/NapCatQQ-Desktop/docs/connections/)

示例仅用于说明拓扑，不是已测试配置：

```text
同机开发：NapCat → ws://127.0.0.1:8080/channels/onebot/v11/ws
容器网络：NapCat → ws://api:8080/channels/onebot/v11/ws
跨机部署：NapCat → wss://实际域名/channels/onebot/v11/ws
```

路径为本平台统一约定，端口和域名按部署配置设置；容器中的 127.0.0.1 指容器自身。网络仅对必要来源开放；两端设置同一 OneBot Token；不把 Token 写入日志和群消息。

平台自行实现的接入契约：

- Token、bot_id 和会话账号校验；事件、生命周期、心跳与 API 响应分流。
- 请求携带唯一 echo，按 echo 对应回包，处理并发、乱序、超时和 retcode 错误。
- 首版 API 单进程持有会话，Worker 通过数据库 outbox 委托 API 投递器发送；后续多实例须先实现会话路由。
- 连接替换和断线清理；发送状态不确定时不盲目重发，游戏写入通过幂等命令去重。
- 消息段解析、@、引用、命令匹配和有界队列；不在协议模块内结算游戏或信任玩家声明的权限。

## 7. 管理员能力与 QQ 框架的关系

管理员能否修改世界，不由 NapCat 决定。协议端只运输消息；真正的全量修改能力由业务 schema、管理工具、权限系统、事务和恢复体系提供。

必须同时有两条入口：

- QQ：快捷查询、行动、裁决、明确的管理命令和结果通知。
- Web：任意业务对象编辑、规则与策略配置、批量差异预览、权限、历史分支、备份恢复和扩展管理。

Agent 通过同一命令服务实现自然语言管理，不能有一条绕过事务的“AI 直写数据库”捷径。新增管理功能时必须同时提供机器可调用的工具/API，避免管理员只在网页里能做、Agent 永远做不了。

## 8. M0 验证清单

| 项目 | 验证内容 | 通过条件 |
| --- | --- | --- |
| 运行形态 | 非商业自托管，NapCat 与平台独立运行 | 无中间机器人框架，连接直接到平台 API |
| 版本组合 | QQ/协议端/适配器/运行时版本 | 固定版本并记录，可重建 |
| 基础消息 | 群文本、@、引用、长消息、特殊字符和消息段 | 不丢 actor/群/消息标识，不误触发控制段 |
| 直连接口 | Token、账号校验、心跳、echo 并发乱序、retcode 和会话替换 | 请求与回包准确关联，旧连接不影响新连接 |
| 指令 | 通用查询、管理员自定义交互、可选人工决定、管理命令 | 按当前游戏定义和真实身份执行，不依赖战斗或骰子模块 |
| 去重 | 重复事件、重连后重复投递 | 同一行动提交一次，相同文本的新消息仍能执行 |
| 断线 | 平台 API 重启、协议端重启、登录失效、网络中断 | 明确报告离线；已入队任务可恢复；不声称补齐未收到消息 |
| 回复失败 | 限流、超时、权限失效、回执丢失 | 不重复游戏结算，投递状态可查 |
| 秘密投递 | GM 信息、个人秘密、公开战报 | 未确认私聊可用时改用 Web，不泄露到群 |
| 协议边界 | 声明首版支持的 OneBot 事件/API，未知类型显式处理 | 规则和状态不依赖 NapCat 私有消息结构 |
| 运维 | 管理端口、Token、备份、更新回退 | 核心状态独立，故障后可用 Web 管理 |

每项记录“通过/失败/未验证”、版本、复现步骤与日志引用。当前均未执行真实 QQ 集成测试。QQ 官方机器人不在本轮验证范围内。

## 9. 重新选型的触发条件

- 明确收费、对外分发或运营模式超出当前许可：优先官方接入或获得明确授权。
- NapCat 在实际环境中持续出现不可接受的登录/消息故障：对相同测试样本验证 LLBot/其他接入端。
- 后续出现必须复用的机器人插件或团队技术栈变化：再评估是否引入机器人框架，另作独立 ADR。
- 官方机器人已覆盖实际需要的群交互：优先考虑迁移，迁移身份需显式绑定，不能直接把官方不透明 ID 当 QQ 号。
- 消息吞吐超过单机设计：先实现独立接入模块的会话路由，再扩展 API/Worker 与数据库协调策略，不因吞吐问题重写游戏规则。

## 10. 来源与证据边界

本次查阅项目原始仓库、作者文档和腾讯文档；未用第三方推荐文章作为技术结论依据。检索日期为 2026-10-29，页面与仓库分支会变化。实施时保存实际依赖版本和许可证文本，不以本文代替授权判断。

没有进行账号登录、安装 NapCat、开通官方机器人或发送 QQ 消息。推荐建立在公开接口与本项目需求的匹配分析上，稳定性、实际准入与费用须另行验证。
