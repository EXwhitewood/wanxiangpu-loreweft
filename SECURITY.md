# 安全政策 / Security Policy

## 支持范围

公开 Beta 阶段只保证最新发布版本和当前 `main` 源码接受安全修复。旧测试版可能要求先升级后再处理。

During public beta, security fixes target the latest release and current `main` source. Older beta builds may need to upgrade first.

## 私密报告漏洞

优先使用 GitHub 仓库的 Private Vulnerability Reporting。若该入口不可用，请创建一个不包含漏洞细节的普通 Issue，请求维护者提供私密联系方式；不要公开 PoC、密钥、真实作品或可直接利用的细节。

Prefer GitHub Private Vulnerability Reporting. If it is unavailable, open a public issue without exploit details and ask the maintainer for a private channel. Do not publish proofs of concept, credentials, real manuscripts, or immediately exploitable details.

报告应包括：受影响版本、影响范围、最小复现条件、可能的数据风险和建议修复方向。请在维护者确认修复和披露时间前保持私密。

## 不要提交

- API Key、访问令牌或 Updater 私钥；
- 用户数据库、项目归档或未发布作品；
- 包含个人路径、账户名或第三方凭据的完整日志；
- 针对第三方模型服务的账号或计费问题。
