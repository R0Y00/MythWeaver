# 旧版解压部署接入 Git 更新

适用于已经通过 ZIP 安装、已有 `.env`、`.venv` 和游戏数据库，但目录内没有 `.git` 的 Windows 平台。只需接入一次；之后使用 `update-platform.bat`。

## 操作步骤

1. 在那台电脑安装 Git for Windows 和 Node.js。支持 Node.js 20.19+ 或 22.12+；推荐使用已安装的稳定版本。原平台 Python 环境须为 3.12+ x64。
2. 从官方仓库下载 [connect-git.bat](https://raw.githubusercontent.com/R0Y00/MythWeaver/main/connect-git.bat)，放到原平台目录，和 `start-platform.bat` 放在一起。注意保存为 `.bat`，不是 `.bat.txt`。
3. 双击 `connect-git.bat`。它获取官方接入工具和仓库，核对环境，备份原代码、配置及数据库，然后在**原路径**接入 Git、安装所需依赖、构建网页。完成后进入服务器窗口。
4. 以后双击 `update-platform.bat`，不再下载更新包。网页刷新即可，无需另装客户端。

例如原平台在 `E:\QQBot\MythWeaver`，下载文件应放到 `E:\QQBot\MythWeaver\connect-git.bat`。不要先删旧目录，也不用重新执行首次安装。

接入前可关闭原服务器窗口；工具也会通过该安装自己的停止逻辑停止平台并备份数据库。NapCat 独立运行，不会重新登录或重新生成其配置。操作期间暂停使用平台。

## 保留与备份

- `.env` 的管理员密码、数据库连接、模型加密密钥、OneBot Token 和端口均保留；不生成新密码。
- 原数据库目录、游戏数据、插件源码及依赖环境、NapCat 文件保留原位置；不做跨库导入，不清空 QQ 绑定。
- 仓库中的源代码更新为官方版本。原目录中与仓库同名的自定义源码会先备份，再替换，**不会自动合并源码改动**。
- 原目录额外文件保留，并通过本机 `.git/info/exclude` 排除。不会被接入工具上传；该工具只下载仓库，没有 push 操作。
- 后续上游新增文件如与这些本地文件重名，更新会停止并提示冲突，不直接覆盖。

备份和报告位于 `.runtime\git-adoption\时间戳\`，包括原代码 `code\`、原配置 `previous.env`、`report.json`。数据库逻辑备份的路径记在报告中，通常在 `.runtime\backups\`。这些文件含私密数据，只留在本机。

接入成功后可在目录执行 `git status --short`，正常应无输出；`git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'` 应返回 `origin/main`。

## 失败时

- 缺 Git、Node.js 或端口冲突：在替换代码前停止。按提示处理后再运行。
- 下载或数据库备份失败：不替换旧代码。
- 替换代码或 Git 核对失败：尝试从本次备份恢复原代码；数据库不回退。异常中断或恢复失败时保留报告，不自动继续覆盖。
- 依赖安装或网页构建失败：代码已经接入 Git，保留待完成标记。修复网络/环境问题后，**再次运行 `connect-git.bat`** 继续安装，不重做初始化。重试前会再次停止和备份；如果此时改了源码，会拒绝覆盖。
- 接入成功但启动失败：按服务器错误处理，之后运行 `start-platform.bat`；不需要重新迁移。

如果旧接入脚本曾出现 `console.py: error: unrecognized arguments`，参数中夹杂乱码或 `pause`，可能是运行中的批处理被 Git 替换后，Windows 从错误字节位置继续读取。接入/更新入口已修复：子程序返回后在同一条预解析命令中退出；批处理提示改用 ASCII，Git 工作区批处理统一 CRLF。

可以先在原平台目录打开 PowerShell，绕过批处理启动：

```powershell
& ".\.venv\Scripts\python.exe" ".\scripts\console.py"
```

如果 Git 已接入成功，关闭服务器窗口后在该目录执行 `git pull --ff-only` 获取此次启动脚本修复，再正常启动。此处只是获取批处理修复的补救操作；日常更新仍用 `update-platform.bat` 安装依赖、构建和备份。若 Git 提示有本地修改，先保留并检查，不要强制 reset。

极老版本如果网页端口仍为 8080 且未配置 ONEBOT_PORT，新版双端口会冲突。工具会提前提示，在原 `.env` 中明确分开，例如 `API_PORT=8001`、`ONEBOT_PORT=8080`，然后重试。工具不会暗改你的公网映射或 NapCat 地址。

目录已有 Git 仓库或属于其他仓库时，不会强制重建、reset 或删除 Git 元数据。原版本缺必要运行文件、路径含不安全的目录联接、文件与目录重名时也会停止。

## 实现与验证

入口 `connect-git.bat` 下载官方 `scripts/adopt_git.py`，使用旧安装的虚拟环境运行；接入程序仅依赖 Python 标准库，备份使用旧安装自己的运行逻辑，安装阶段使用新进程加载新代码。目标分支为官方 `main`。

部署与 Git 专项 **39 项测试通过**，包含本地 Git 仓库的接入与后续更新、文件和配置保留、失败恢复、构建失败后重试、路径与端口校验。还在独立临时 PostgreSQL 实例中实际完成旧目录接入、Python 依赖安装和网页构建；确认原数据库内容保持一致，并将逻辑备份恢复到另一临时数据库核对。日常平台未启动，未操作真实 QQ。

这不是跨机器数据库迁移或强制回滚工具。接入后首次正常启动仍会执行平台自身的数据库版本迁移；代码备份不能代替数据库备份。


## 更新时 Git fetch 返回 128

128 是 Git 的通用失败码，不等于已经确认是网络或权限问题。新版更新脚本会展示 Git 原始错误，并保存到 `.runtime/update-error.log`；获取更新失败时平台尚未停止，代码与数据库不会被更新流程覆盖。

旧版只展示 Python traceback 时，在部署目录的 PowerShell 中执行 `git fetch --prune origin`，查看其原始错误。该命令只获取远端引用，不合并代码；网络恢复后重新运行 `update-platform.bat`。不要先执行 `git reset --hard`，也不要关闭 SSL 验证或把未知代理配置全部清空。需要协助时只提供 Git 错误，不提供 `.env` 或凭据。

不要通过直接覆盖受 Git 管理的 `scripts/update.py` 来升级更新器，否则会触发本地修改保护。先解决 fetch 的具体问题，正常更新就会带入新更新器。
