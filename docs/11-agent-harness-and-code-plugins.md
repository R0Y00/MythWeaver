# v0.7：Agent 执行循环与代码插件

> 历史记录：以下描述 v0.7。v0.9 已取消界面的执行/规划/讨论切换；默认写入和运行代码需审批，可由管理员在会话中开启自动同意。以[统一对话与平台规则](17-unified-conversation-and-platform-rules.md)为准。

本轮参考 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的插件化思路与 [Agent API](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/README.md) 对执行、停止和状态的组织方式。结合本平台的持久状态、GM 介入、权限和消息渠道实现自己的执行层；没有安装 DSH 作为必需运行时，也没有宣称兼容 DSH 的插件 ABI。

## 已实现的执行闭环

“Agent 执行”成为管理助手的默认模式。管理员提交目标即授权本任务按目标调用工具；执行期间不用反复批准同一任务的安装、测试和调用。保留原有“管理规划”和只读讨论模式，旧规划预演不会安装或执行代码插件。

执行循环：读取状态 → 创建代码/模块 → 安装依赖 → 运行测试 → 观察错误 → 修改新版本 → 再测试 → 注册工具 → 实际调用 → 保存结果。

- 任务在后台执行，HTTP 返回任务编号；界面持续读取进度与工具回执。
- 每轮重新发现启用插件的工具，成功安装后同一个任务即可调用新工具。
- 最多24轮模型请求，每轮最多4个工具调用，单次执行任务15分钟上限。达到轮次上限可继续。
- 原始会话、每次调用的输入、输出、状态和进度保存在 PostgreSQL；模型上下文过大时压缩早期结果，保留完整日志与最近完整工具组。
- 可以停止正在执行的任务。插件子进程超时、输出过大或取消时终止进程树；当前进程内的线程执行器收到停止事件。
- 重启后的任务标记中断，可通过继续入口推进；结果不明的工具不会自动重演，先核对实际状态再发起新任务。
- 游戏命令仍通过统一命令服务，单批最多8项，保留原子提交、版本检查和实际回执。执行失败不等于之前的成功步骤被撤销。

## 真实代码插件

插件不是表达式别名。支持任意 Python 或 JavaScript 源码、标准库及 pip/npm 可安装的依赖，通过约定入口返回 JSON。可以执行循环、使用第三方库、调用网络或其他宿主能力；能力取决于插件代码及当前操作系统环境。

每个插件版本保存：

- 工作空间、插件标识、不可变修订号、源码文件和内容摘要。
- 工具名称、说明、输入输出 JSON Schema、入口函数。
- pip/npm 依赖规格、测试用例、执行超时。
- 安装状态、测试报告、是否启用。

Python 每版本使用独立 venv；Node 每版本使用独立 npm 依赖目录。通过包安装器安装依赖，未设置包白名单；支持包规格以及安装器支持的本地包/仓库来源。实际可安装性取决于网络、平台、编译工具和依赖兼容性。

没有将插件 import 到 API 进程中。Python/Node 子进程通过 stdin/stdout JSON 协议与平台交互，诊断写 stderr。输入输出经过 schema 校验，输出和执行时间有上限。

源码目录限制在对应版本目录，拒绝路径穿越、绝对路径、Windows 保留名称等；每次调用前核对源码与记录版本。只清理继承环境，未向插件传递数据库、模型和 OneBot 凭据。

**当前是可信本机代码执行，不是 OS 安全沙箱。** 独立进程和 venv 不能阻止恶意代码访问同一系统用户有权访问的文件、网络和进程；不应把“未继承密钥环境变量”理解为能阻止恶意插件读取宿主文件。符合暂不使用 Docker 的要求。本轮不提供对不可信第三方代码的强隔离承诺。

默认插件工具仅供管理员调用。玩家不能创建或安装插件，只能通过管理员开放的交互和 `player_enabled` 工具使用能力。插件本身必须是管理员信任的代码；玩家输入不能成为代码安装授权。

## 版本、测试和恢复

1. `plugin_draft` 保存源码与 manifest，不执行代码。
2. `plugin_install` 创建依赖环境、安装依赖并执行测试。
3. 所有测试通过后，版本进入 ready；需要启用时替换同标识的活动版本。
4. 新版本失败不会替换原活动版本；可查看报告，修复并创建新版本。
5. `plugin_state` 可停用或切换回已通过测试的旧版本。当前保留源码和环境供恢复，没有物理卸载/清理磁盘入口。

安装器不是数据库事务的一部分。包安装脚本、插件的网络/文件操作等外部副作用，不能被游戏检查点撤销。工具执行状态为 unknown 时不自动重试。

启用/停用插件使游戏定义版本变化，旧计划、待裁决事项和等待流程失效，避免继续用旧代码语义执行。尚未发送的私聊回复也会取消。

数据库保存完整源码及 manifest。运行目录丢失时启动检查会停用缺失环境，可从已记录版本重新安装重建环境。数据库备份不包含整套 venv/node_modules；依赖应使用固定版本或保留可恢复安装源。

游戏检查点恢复不回滚插件安装状态或外部副作用；正在执行的 Agent 任务需通过停止入口处理。已发送 QQ 消息同样不能随数据库恢复撤回。

## 最小代码插件

Python 文件 `tools.py`：

```python
def calculate(data):
    return sum(i * i for i in range(data["n"]))
```

manifest：

```json
{
  "runtime": "python",
  "requirements": [],
  "tools": {
    "calculate": {
      "description": "计算0到n之前整数的平方和",
      "entry": "tools.py:calculate",
      "input_schema": {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 0}},
        "required": ["n"],
        "additionalProperties": false
      },
      "output_schema": {"type": "integer"},
      "player_enabled": false
    }
  },
  "tests": [{"tool": "calculate", "input": {"n": 5}, "expected": 30}]
}
```

插件标识如 `calculator`，成功启用后动态注册 `ext_calculator__calculate`。Node 入口使用 `export function calculate(data)` 或 async 函数。

第三方库可以作为依赖安装后在代码中 import。现成的其他平台插件通常需要适配以上 manifest 和入口协议；不是任何框架的插件包都能不经适配直接运行。

## 接入现有交互

新增 `plugin` 数据效果：

```json
{
  "type_key": "custom_record",
  "id_from": "target",
  "field": "computed_value",
  "operation": "plugin",
  "plugin_key": "calculator",
  "tool": "calculate",
  "expression": {"object": {"n": {"path": "current.n"}}}
}
```

插件返回值成为目标字段的新值。调用前检查目标归属、流程锁及玩家工具授权，返回后检查记录 schema 和引用。数据库写入仍在原事务中。具有外部副作用的插件无法获得与数据库相同的回滚保证。

玩家行动的这一结算阶段已移入可取消的后台线程，避免慢插件阻塞 QQ/WebSocket 的事件循环。

## API 与管理台

所有以下接口均要求已有管理员认证和写请求校验：

- `POST /api/games/{id}/agent/tasks`：提交 `{prompt}`，启动执行任务。
- `GET /api/games/{id}/agent/tasks/{run}`：查看任务和实际工具回执。
- `POST /api/games/{id}/agent/tasks/{run}/resume`：提交 `{prompt}` 继续。
- `POST /api/games/{id}/agent/runs/{run}/cancel`：停止运行中的任务。
- `GET/POST /api/games/{id}/plugins`：列版本/保存草稿，POST 接受 `{key,manifest,files}`。
- `GET /api/games/{id}/plugins/{release}`：查看源码与完整报告。
- `POST /api/games/{id}/plugins/install`：`{id,activate}`。
- `POST /api/games/{id}/plugins/state`：`{id,enabled}`。

“游戏控制台 → 代码插件”可查看版本及报告，启用、停用、回退。任务详情展示源代码提交、安装、失败、修复和实际调用回执。

## 验证与剩余范围

测试实际运行 Python/Node 插件，并用本地构造的 wheel/npm tarball 验证真实 pip/npm 安装；不依赖模拟安装成功。模型修复循环使用模拟服务商，运行插件与安装依赖使用真实进程。

还验证了版本失败保护与回退、源码校验、路径约束、环境变量隔离、超时与取消、玩家目标授权、跨空间隔离、环境重建、任务继续与上下文压缩。

真实模型的编写/调试质量、公共包仓库网络安装、任意第三方插件兼容性和真实 QQ 私聊尚未完成端到端验收。浏览器自动化当前关闭，界面通过 TypeScript 与构建检查，不宣称完成视觉验收。当前也没有强 OS 沙箱、插件磁盘卸载、通用外部副作用补偿或自动处理 unknown 调用的能力。


### 本轮本机验收记录

完整回归157项通过；随后动态工具注册名修正的3项定向回归通过（含1项新增用例）。TypeScript检查和Vite构建通过。

已在独立的“本机 · Agent 扩展验证”空间，通过真实HTTP保存并安装`local_statistics`插件，实际调用`ext_local_statistics__summarize`对`[2,4,6,8]`返回count=4、sum=20、mean=5、median=5。真实API进程重启后插件保持ready/启用，实际工具调用再次通过。这个空间没有绑定QQ，未调用真实模型。

备份`.runtime/backups/20261029-123721.dump`已恢复至独立数据库`mythweaver_restore_20261029_123725_af93d2`，检查5个空间、44条事件、2个流程、1个代码插件及引用完整性；运行库未被覆盖。当前运行库尚无真实模型驱动的harness任务，任务循环证据来自隔离集成测试。
