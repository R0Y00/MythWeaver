# 在另一台 Windows 部署

当前提供 Windows x64 原生部署包，不用 Docker，不安装系统数据库服务。平台默认仅本机访问；本次不包含公网发布、自动开机服务或 Linux 部署。

## 新机器安装

1. 安装 [Python 3.12 或以上 x64](https://www.python.org/downloads/windows/)。本轮实际测试版本为 Python 3.12；安装时启用 Python Launcher 或加入 PATH，也可手工指定 Python 路径。
2. 解压 `MythWeaver-Windows.zip` 到可写目录，例如 `D:\MythWeaver`。不要放在 Program Files 等需要管理员写权限的目录，不要在 ZIP 中直接运行。
3. 双击 `setup-windows.bat`。它会新建 `.venv`、联网安装 `requirements.lock` 中的依赖、生成随机管理员密码与独立密钥、初始化项目专用 PostgreSQL。
4. 双击 `start-platform.bat`，等待提示就绪后自动打开管理台；密码见 `.runtime\credentials.txt`。
5. 在“模型管理”填写自己的服务商、密钥和模型，分配管理规划、讨论助手和玩家 Agent。新安装不含原机器的模型密钥或游戏数据。

启动窗口就是服务器终端，会持续显示运行日志。保持窗口打开；按 Ctrl+C 正常停止，关闭窗口也会结束 API 和项目 PostgreSQL 进程。使用 Windows Job Object 将进程树绑定到终端拥有者，不安装独立后台守护或系统服务。单独通过 `start-napcat.bat` 打开的 NapCat 仍由其自己的窗口管理。

### 使用 Git 更新

客户端是浏览器，前端资源由同一个服务器提供；服务端更新并构建界面后，玩家刷新网页即可。浏览器不需要单独安装或更新。当前服务默认仅本机访问，远程玩家网页访问并未随 Git 更新自动开放。

仓库只上传代码，保留现有 `.gitignore`，不要提交 `.env`、`.runtime`、NapCat 登录态或数据库。源码仓库为私有的 [R0Y00/MythWeaver](https://github.com/R0Y00/MythWeaver)。另一台机器需登录有权限的 GitHub 账号；机器自己的配置、游戏、数据库和 NapCat 环境保持本机私有。

首次获取源码：

```powershell
git clone https://github.com/R0Y00/MythWeaver.git
```

仓库不包含 PostgreSQL 程序、前端构建产物或本机数据。从源码新部署时先准备 PostgreSQL 程序，再运行 `setup-windows.bat`，安装脚本会构建网页。已有 ZIP 部署不能在原目录重复执行 clone 或直接运行更新脚本；应先完整备份，再迁移为有远程跟踪分支的 Git 部署，不能强制覆盖现有数据。

采用 Git clone 部署的机器安装 Git；首次从源码构建界面还需要 Node.js 和 PostgreSQL 程序（可复用初装包里的 PostgreSQL 程序路径）。在部署目录双击 `update-platform.bat`：

1. 检查工作区干净、有远程跟踪分支，fetch 上游，确认可以 fast-forward。没有新提交时不重启；有本地代码修改或分叉时拒绝，不强制 reset。
2. 停止已有服务器终端，启动数据库并做备份，再快进代码。
3. Python 锁文件变化时安装依赖；前端变化时执行 npm ci 和构建。配置、数据、NapCat 和插件环境不通过 Git 覆盖。
4. 更新完成后，同一个窗口进入服务器终端；关闭窗口即停止服务，不留独立后台实例。

更新报告在 `.runtime\updates\时间戳\report.json`，包含原/新提交及备份路径。失败时停止，不自动降级数据库；按报告排查再恢复，不声称代码回退能够撤销数据库迁移。ZIP 解压目录不能直接 git pull，应先明确建立 Git 部署及跟踪分支；不要用强制 checkout 覆盖有本地修改的目录。

验证：Git 更新规划的本地远程仓库测试覆盖上游获取、工作区保护、分叉拒绝和无变化，共4项通过。服务器终端在隔离部署实例实际启动后，强制结束终端拥有进程，API 18080 与数据库 15543 均退出，原开发实例保持正常。完整远程仓库更新发布流程仍需在你的实际仓库上接入验收。

标准发布包已包含前端构建产物及 PostgreSQL 17 程序，不需要自行安装 PostgreSQL 或构建界面。Python 依赖首次安装需要网络。若 PostgreSQL 提示缺少 Microsoft Visual C++ 运行库，请安装 [Microsoft 官方 x64 运行库](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。

### 下载慢时更换镜像

新版安装默认使用清华 PyPI 镜像，失败后单独重试阿里云镜像，不修改全局 pip 设置。可以指定另一镜像重跑，已安装依赖会保留：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 -PipIndexUrl https://mirrors.aliyun.com/pypi/simple/
```

或恢复官方源并关闭自动后备：

```powershell
& .\scripts\setup.ps1 -PipIndexUrl https://pypi.org/simple -PipFallbackIndexUrl ''
```

成功使用的源保存到本项目 `.runtime\package-sources.json`，后续 Python 代码插件安装也使用它。也可手工编辑其中的 `pip_index_url` 为不带账号密码的 HTTPS 地址；不改变模型 API 地址。镜像同步可能存在延迟，找不到锁定版本时换源，不自动降低依赖版本。[阿里云镜像说明](https://developer.aliyun.com/mirror/pypi)。

已经拿到旧版部署包、不方便重新下载时，在项目目录执行下列命令即可先完成依赖安装，再重跑原安装脚本：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.lock --index-url https://mirrors.aliyun.com/pypi/simple/ --timeout 20 --retries 1
```

这只解决 Python 包下载；整个发布 ZIP 或 Python 安装程序下载慢，需要另外调整文件传输方式。

Node.js 不是预构建界面的启动前提，但运行 Node 插件需要安装 Node.js。若从源码重新构建前端，需要 [Vite 所要求的 Node.js 20.19+ 或 22.12+](https://vite.dev/guide/)。

带空格目录可用。中文目录会自动为 PostgreSQL 原生工具转换 Windows 8.3 短路径；若目标卷没有可用短路径，会明确提示改用英文目录，本程序不会修改系统短文件名设置。建议使用英文目录降低环境差异。

高级首次安装示例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 `
  -PythonExe "C:\Python312\python.exe" -ApiPort 18080 -DbPort 15543
```

安装前先确认端口空闲。安装脚本不会覆盖已有 `.env`；端口和路径参数仅在生成新配置时写入。已有安装若调整 API 端口，修改 `API_PORT` 后重启；v0.9.3 起来源默认自动识别，无需随端口修改 `PUBLIC_ORIGIN`。若主动配置固定来源，需自行同步；数据库监听端口还需与 `.runtime\pgdata\postgresql.conf` 及 `DATABASE_URL` 一致。

## 配置 QQ（可稍后进行）

### 调整 Web 端口和外部转发

v0.9.3 起默认自动跟随浏览器当前访问地址，不要求登记公网 IP、DDNS 域名或外部端口。在项目根目录 `.env` 设置：

```dotenv
API_PORT=8001
API_HOST=0.0.0.0
ONEBOT_PORT=8080
ONEBOT_HOST=127.0.0.1
```

删除旧的 `PUBLIC_ORIGIN` 一行即可采用自动模式，也可以填写 `PUBLIC_ORIGIN=auto`。以前误填的 `http://0.0.0.0:8001` 会兼容为自动模式。只有明确填写实际固定来源时才限制为那个来源。

`API_HOST` 默认 `127.0.0.1`（仅本机），`0.0.0.0` 允许其他机器连接；它是监听地址，不是浏览器访问地址。Windows 防火墙放行 TCP8001，路由器映射外部 TCP 端口到平台电脑的内网 IP:8001。外部端口可以不同，例如公网28080 → 内网8001；浏览器访问 `http://你的DDNS域名:28080`。同时可以通过内网IP:8001访问，无需切换配置。修改后重启平台。

自动模式要求写操作来自当前站点本身，不是允许其他任意网站跨站调用管理接口。登录密码和请求校验仍生效。HTTPS访问自动使用Secure cookie。反向代理需保留浏览器访问时的Host，并通过受信任的代理链传递协议信息；同机反代可保持环回监听。路由器普通TCP映射不需要这些反代设置。对外管理建议使用HTTPS。

v0.9.4 起Web/API和OneBot端口独立。Web默认8001，OneBot默认8080。同机NapCat继续连接 `ws://127.0.0.1:8080/channels/onebot/v11/ws`，token不变。Web端口修改不影响NapCat。Web端口不接收OneBot，OneBot端口不提供网页和管理API。旧安装如果API_PORT仍是8080，需改为8001或其他空闲端口；两端口相同会拒绝启动并提示。数据库55432不需要公网映射。详见[双端口说明](20-split-web-and-onebot-listeners.md)。

平台可以不启动 NapCat。首次安装的 `ONEBOT_BOT_ID=0` 表示未配置实际账号。

1. 在目标机自行安装 NapCat，登录机器人 QQ。发布包不包含 NapCat 安装程序或任何 QQ 登录状态。
2. 在 `.env` 设置实际账号及启动脚本路径，例如：

```dotenv
ONEBOT_BOT_ID=123456789
NAPCAT_SCRIPT=D:\Tools\NapCat\napcat.quick.bat
```

3. 首次登录生成协议配置后，执行：

```powershell
.venv\Scripts\python.exe scripts\local.py configure-napcat
```

若 NapCat 目录结构不同，明确传入其 OneBot 配置 JSON 路径：

```powershell
.venv\Scripts\python.exe scripts\local.py configure-napcat --napcat-config "D:\Tools\NapCat\config\onebot11_123456789.json"
```

只修改名为 `mythweaver-local` 的反向 WebSocket 连接，保留其他连接，首次修改前备份原文件。连接地址使用配置的 API 端口和目标机新生成的 Token。重新启动平台及 NapCat，再在“连接与诊断”检查状态。`start-napcat.bat` 使用 `.env` 中的路径，旧机器原路径仍有兼容回退。

## 迁移原机器数据

不要复制 `.venv`、pgdata 或插件运行环境。数据库通过 PostgreSQL 逻辑备份迁移；[pg_restore 支持从归档重建数据库](https://www.postgresql.org/docs/17/app-pgrestore.html)。

在旧机器数据库运行时，导出**私有迁移包**：

```powershell
.venv\Scripts\python.exe scripts\windows.py export-data --output "D:\Transfer\MythWeaver-private.zip"
```

其中含 public schema 的游戏数据、插件源码、模型加密凭据，以及解密模型凭据所需的 SESSION_SECRET。它不包含旧机器的管理员密码、数据库密码或 OneBot Token。**迁移包没有额外 ZIP 加密，应按私密备份保管，不要当作发布包分享。** 发布程序包与私有迁移包是两个明确分开的命令。

目标机器先完成首次安装，然后停止目标平台：

```powershell
.venv\Scripts\python.exe scripts\local.py stop
.venv\Scripts\python.exe scripts\windows.py import-data "D:\Transfer\MythWeaver-private.zip"
```

导入会启动目标机的项目数据库，在新建的 `mythweaver_import_*` 数据库恢复，成功后才切换 `.env`；不覆盖原数据库。恢复使用 `--clean` 的范围仅为此次新建的暂存数据库。失败时原配置保持不变，失败暂存库保留供排查，不会删除运行库。

导入保留目标机器的管理员密码、数据库和 OneBot 密码，只迁移模型加密所需 SESSION_SECRET。旧 `.env` 副本在 `.runtime\migration\import-*\previous.env`。新的 SESSION_SECRET 会使旧网页登录会话失效，需要重新登录。

为避免迁移后直接发消息，导入清除 QQ 绑定，取消待投递消息，停用插件，并保留插件源码。检查 `.runtime\import-report.json` 后，可在管理台重新安装插件，或显式重建迁移前启用的可信插件：

```powershell
.venv\Scripts\python.exe scripts\windows.py rebuild-plugins
.venv\Scripts\python.exe scripts\local.py start
```

重建会真实执行源插件代码、安装依赖和测试；只对自己信任的备份执行。原插件依赖若是旧机器绝对路径，需要修改为目标机可用的依赖再安装。重建失败不会被报告为全部完成。最后在管理台检查数据与规则、重新绑定实际 QQ，再开放玩家使用。

## 从源码生成发布包

先构建前端，再运行：

```powershell
.venv\Scripts\python.exe scripts\windows.py package --output .runtime\releases\MythWeaver-Windows.zip
```

按白名单打包代码、脚本、文档、示例、前端产物及 PostgreSQL 的 bin/lib/share 与根目录许可文件。不打包 `.env`、数据库、备份、缓存、插件环境、Node modules、Python 虚拟环境、NapCat 或 QQ 登录态。提供发布文件 SHA-256 与包内文件哈希清单；目标文件存在时拒绝覆盖。

若没有 PostgreSQL 程序，使用 `--without-postgres` 构建精简包。目标机需从 [PostgreSQL 官方 Windows 下载入口](https://www.postgresql.org/download/windows/)取得二进制包，通过 `setup.ps1 -PostgresZip "D:\Downloads\postgresql.zip"` 解压，或 `-PostgresBin "D:\PostgreSQL\17\bin"` 指定程序路径。外部 PostgreSQL 程序仍配合项目内独立数据目录运行，不会接管系统服务数据库。

## 本轮验证记录

在本机解压发布包到全新 `.runtime\Windows 部署 验证` 目录，使用新的虚拟环境、新 PostgreSQL 集群和 18080/15543 端口：

- 首次安装、锁定依赖联网安装、无 QQ 启动、中文空格路径、网页与管理员登录通过。
- 安装并调用真实测试插件；导出私有测试数据包后，先改变目标机加密密钥，再导入，验证源模型凭据仍可解密。
- 恢复到新数据库、原库保留、QQ 绑定清除、旧消息取消、插件重建及调用、迁移后登录共 9 项检查通过。
- 完整平台回归 186 项通过；随后新增的非法迁移包及运行中导入保护两项通过（部署专项8项全部通过）。迁移流程额外修正了 public schema 恢复冲突，并再次完成真实导入验证。
- 测试实例随后停止，原开发实例不受影响。没有向真实 QQ 发送消息，没有使用真实模型 API 验证该迁移包。

这是同一台 Windows 上的独立目录、独立环境部署演练，尚未在另一台物理 Windows 或干净虚拟机上测试。发布包仍依赖目标机 Python、系统运行库和首次联网安装环境。
