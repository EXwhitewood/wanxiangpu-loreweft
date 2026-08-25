# 开发指南 / Development Guide

## 环境

- Windows 10/11（当前主要开发与发布目标）
- Python 3.12
- Node.js 与 npm
- Rust stable
- Tauri 2 所需 Windows 构建工具和 WebView2

## 后端

```powershell
py -m pip install -r backend/requirements-dev.txt
py -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

`requirements-dev.txt` 包含完全锁定的运行依赖与测试依赖。只构建桌面发行资源时使用 `backend/requirements.txt`，避免把测试工具打进安装包。

如需自定义开发配置，可复制 `backend/.env.example` 为本机 `.env`。真实 `.env`、数据库和 API Key 不得提交。

## 前端

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

前端开发地址为 `http://127.0.0.1:5173`，默认代理到 `http://127.0.0.1:8000`。

## 验证

```powershell
py -m compileall -q backend/app
py -m pytest -q backend/tests
npm --prefix frontend test
npm --prefix frontend run build
cargo test --manifest-path src-tauri/Cargo.toml
```

端到端测试需要本地前后端已启动：

```powershell
npm --prefix frontend run test:e2e
```

## 数据安全

- 使用测试数据库运行自动化，不对真实创作库执行写测试；
- 提交日志和截图前去除作品正文、路径、用户名和凭据；
- 数据模型或迁移变更必须验证 `PRAGMA integrity_check` 和 `PRAGMA foreign_key_check`；
- 修改 AI 生成路径时必须保留“不得静默覆盖用户正文”的服务端门禁。
