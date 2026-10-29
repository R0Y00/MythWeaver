# v0.9.5：HTTP 地址登录时的 randomUUID 报错

症状：通过公网IP或DDNS的HTTP地址打开登录页面，点击登录显示 `crypto.randomUUID is not a function`。

这是前端请求标识生成失败，并非服务器判断管理员密码错误。此前所有API请求都直接调用 `crypto.randomUUID()`；该接口依赖浏览器安全上下文，在普通HTTP公网/内网地址下可能不存在。浏览器在调用fetch之前已经抛出异常，登录请求并没有发到服务器。本机localhost访问时不一定能复现。

修复：共用 `requestId()` 生成非认证用途的幂等请求编号。存在randomUUID时沿用；HTTP下使用可用的getRandomValues构造UUID；缺少Web Crypto的旧环境采用随机请求编号后备。管理员密码、会话签名、OneBot token仍由原有后端校验，不使用这个编号作为安全令牌。玩家试运行的请求编号也改用同一函数。

更新包含预构建界面，覆盖后重启平台并Ctrl+F5刷新页面。配置文件、密码及8001/8080双端口无须因此修改。

验证脚本 `node scripts/test_http_frontend.mjs` 执行真实前端源码，模拟缺少randomUUID的HTTP环境及缺少crypto的旧环境，检查UUID格式、各1000个编号无重复，以及登录确实发起fetch；另检查HTTPS实现和玩家试运行请求编号。TypeScript与生产构建通过。没有向用户公网地址提交真实密码，没有启动本机平台或数据库。
