# 贡献方式 / Contributing

万象谱欢迎问题反馈、文档改进和代码贡献。小型修复可以直接提交 Pull Request；涉及数据模型、生成工作流、安装更新或大范围界面调整时，请先创建 Issue 对齐范围和兼容性要求。

Loreweft welcomes bug reports, documentation improvements, and code contributions. Small fixes may be submitted directly. Please open an issue first for database, generation-workflow, installer/updater, or broad UI changes.

## 问题反馈

提交 Issue 前请：

1. 确认问题可以在最新公开版本复现；
2. 写明版本、操作系统、复现步骤、预期结果和实际结果；
3. 对日志和截图进行脱敏；
4. 不上传 API Key、访问令牌、Updater 私钥、真实数据库或未公开作品全文。

安全漏洞不要公开披露，按 [SECURITY.md](SECURITY.md) 处理。

## Fork 与修改版

Fork、修改、从源码构建和再分发须遵守 [AGPL-3.0-only](LICENSE) 与 [TRADEMARKS.md](TRADEMARKS.md)。开源许可证允许商业使用，但分发或联网提供修改版时必须履行相应的源码提供义务。面向他人发布的修改版必须使用独立的名称、主图标、应用 identifier、更新地址和签名密钥。

## Pull Request 要求

提交前请确认：

1. 改动范围清晰，不混入无关格式化或生成产物；
2. 后端至少通过 `py -m compileall -q backend/app`，前端改动通过对应测试与构建；
3. 测试使用临时 SQLite 或临时目录，不写入真实用户数据库、作品或语料；
4. 不包含 API Key、访问令牌、Updater 私钥、个人路径或未公开内容；
5. 新依赖的许可证与来源已经记录，必要时更新 `THIRD_PARTY_NOTICES.md`；
6. 你有权提交这些内容，并同意贡献内容按本仓库的 `AGPL-3.0-only` 许可证发布。

提交贡献不授予项目名称、Logo、应用图标或官方签名的商标使用权。
