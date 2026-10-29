# v0.9.4：Web 与 OneBot 使用独立端口

配置示例：

```dotenv
API_HOST=0.0.0.0
API_PORT=8001
ONEBOT_HOST=127.0.0.1
ONEBOT_PORT=8080
```

`API_PORT` 只控制网页及管理 API。`ONEBOT_PORT` 只控制 NapCat 的反向 WebSocket。不再需要跟着 Web 端口调整 NapCat。

- 网页：`http://部署电脑IP:8001`，或将路由器外部端口转发到部署电脑内网IP:8001。
- 同机 NapCat：`ws://127.0.0.1:8080/channels/onebot/v11/ws`，token 与账号校验不变。
- `API_HOST` 默认环回；需要其他机器访问网页时才设置 `0.0.0.0`。
- `ONEBOT_HOST` 默认环回。同机 NapCat 无需将8080暴露到公网；NapCat 在其他机器时，可主动设为 `0.0.0.0` 并配置可达地址。
- 仍支持自动识别网页访问来源；`PUBLIC_ORIGIN` 可以不设置，或设为 `auto`。

两端口必须不同，冲突或被占用时会提示错误，不自动改动你的端口。旧安装的 `API_PORT=8080` 请改为8001或其他空闲端口，或明确为OneBot选择其他端口。Web、OneBot、数据库也不能占用同一端口。

新安装默认Web8001、OneBot8080。旧 `.env` 不会被安装或更新脚本覆盖。更新后调整配置并重启 `start-platform.bat`；单纯刷新浏览器不能改变服务器监听端口。

## 启动和停止

生产启动入口改为 `python -m mythweaver.server`，由原有启动脚本调用。单个Uvicorn服务监听两个socket，按实际接收端口分配接口；共享同一个FastAPI应用、数据库连接池、OneBot对象、任务执行器和消息泵。没有另开第二套游戏后端或数据库，不会重复初始化及重复消费消息。

- Web端口不接受OneBot WebSocket。
- OneBot端口不提供网页或管理API；仅提供受token/账号校验的WebSocket和进程健康检查 `GET /health`。
- 管理状态仍通过Web端口 `/api/status` 读取同一个OneBot连接状态。
- 启动就绪和控制台巡检同时检查Web数据库健康及OneBot监听状态。
- PID记录仍为 `.runtime/api.pid`，停止脚本验证进程归属并兼容原来的单端口启动命令。
- 关闭服务器控制台仍结束Web、OneBot及项目数据库。NapCat自己的进程不由此停止。

直接使用 `uvicorn mythweaver.api:create_app` 是旧的单应用开发入口，不提供生产双监听与端口分配；请使用新的启动脚本。

## 验证

使用真实的两个临时TCP socket运行同一个服务，验证：网页可访问、OneBot端口无管理API、授权WebSocket在专属端口可连接、Web端口拒绝OneBot、错误token拒绝、共享一个机器人对象、只初始化一次并只启动一个消息泵、关闭后两个端口均不再监听。

部署测试同时验证NapCat配置URL不随Web端口变化、启动脚本只创建一个服务进程、两个健康检查都成功后才返回就绪、停止脚本兼容新旧进程。测试未启动日常平台或项目数据库，未发送QQ消息。
