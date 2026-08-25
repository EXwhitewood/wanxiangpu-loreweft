# Windows 桌面构建 / Windows Desktop Build

> 构建或发布更新前，请先备份真实用户数据，并完成本文列出的源码、安装器、签名和升级验证。

桌面安装包由 Tauri 2、前端静态资源、后端源码和可再分发的 Python 3.12 运行时组成。仓库不会提交 Python 运行时、依赖目录、用户数据库或私钥。

## 本地构建

准备一个许可允许再分发、包含 `python.exe` 的 Python 3.12 运行时目录：

```powershell
.\build-desktop.ps1 -PythonRuntimePath C:\path\to\python-runtime
```

脚本会在隔离目录安装锁定的后端依赖，删除字节码缓存和无用 console launcher，拒绝数据库、日志、密钥与本机构建路径进入候选，并验证 `app.main` 可导入后才构建 NSIS 安装器。

NSIS 安装器带有安装前的 fail-closed 检查：仅关闭当前安装目录内的 bundled Python，并确认旧 `.exe`、`.dll`、`.pyd` 全部解除占用后才覆盖文件。可先运行发行与安装器 fixture：

```powershell
cargo test --manifest-path src-tauri\Cargo.toml
.\scripts\test-release-pipeline.ps1
.\src-tauri\installer\test-installer-preflight.ps1
```

## 修改版必须更换身份

公开分发 Fork 前必须修改：

- `src-tauri/tauri.conf.json` 的 `productName` 和 `identifier`；
- 前端产品名称与图标；
- Updater 仓库、签名公钥和私钥；
- 安装包名称和发布说明。

修改版不得使用万象谱官方 Updater 私钥、发布通道或签名声明。官方配置中的 Updater 公钥可以公开，但它只验证官方更新，不会授权 Fork 发布更新。

## Fork 的 Updater 密钥

Fork 维护者应自行生成密钥：

```powershell
.\frontend\node_modules\.bin\tauri.cmd signer generate -w C:\secure\path\updater.key
```

私钥和密码只能存放在受控构建环境中；仅把公钥写入 Fork 的 `tauri.conf.json`。不要把密钥路径、私钥或密码写入 `.env`、源码、日志、Issue 或 Release。

## 官方 Release

`prepare-github-release.ps1` 会先验证 Tauri Updater 签名、候选卫生与 Authenticode 状态，再在临时目录生成安装器、`.sig` 和 `latest.json`；只有全部通过才原子替换 upload-ready 目录。官方 Release 还必须执行匿名 URL、哈希、安装启动和跨版本更新验证。
