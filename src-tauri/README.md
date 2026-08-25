# 桌面发布

桌面安装包必须同时携带前端静态资源、后端源码和可再分发的 Python 3.12 运行时。仓库不提交运行时、依赖目录、用户数据库或 API 密钥。

在 Windows 构建机上准备一个许可允许再分发、包含 `python.exe` 的 Python 3.12 运行时目录，然后从仓库根目录执行：

```powershell
.\build-desktop.ps1 -PythonRuntimePath C:\path\to\python-3.12-runtime
```

脚本会在临时目录组装资源、安装 `backend/requirements.txt`、使用独立运行时验证 `app.main`，验证通过后才替换 `src-tauri/resources` 并构建 NSIS 安装包。`-StageOnly` 只暂存和验证资源，不构建安装包。

正式产物位于 `src-tauri/target/release/bundle/nsis/`。安装包内含 Python、后端和全部运行依赖；用户无需安装开发环境、数据库或配置环境变量。Windows 10/11 通常已自带 WebView2，缺失时安装器会自动下载。模型服务 API Key 由用户在应用设置页录入，不依赖系统环境变量。

Windows 发布只生成经过真实安装验证的 NSIS `-setup.exe`。当前资源树在 WiX 3 的 MSI 链接阶段不稳定，因此不将“同时生成 MSI”作为发布成功条件。

`src-tauri/build.rs` 会在 release 构建前再次检查 `python.exe` 与 `backend/app/main.py`，避免生成一个安装后无法启动的“空壳成功”安装包。
