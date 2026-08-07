# 安全政策 / Security Policy

## 支持范围

公开 Beta 阶段只为最新发布版本提供安全更新。旧版本可能需要先升级到当前版本。

During public beta, security fixes target the latest published release. Older builds may be required to upgrade first.

## 私密报告漏洞

请优先使用本仓库的 GitHub Private Vulnerability Reporting。若该入口不可用，请创建一个不包含漏洞细节的普通 Issue，请求维护者提供私密联系方式。

报告建议包括：

- 受影响版本和 Windows 环境；
- 影响范围与可能的数据风险；
- 最小复现条件；
- 已完成的安全边界测试；
- 建议的修复或缓解方向。

在维护者确认修复与披露时间前，请勿公开 PoC、利用步骤或未修复细节。

## 不要提交

- API Key、访问令牌、Updater 私钥或其他凭据；
- 用户数据库、项目归档或未发布作品；
- 包含用户名、个人路径、代理凭据或模型请求正文的完整日志；
- 可直接用于攻击第三方模型服务或其他用户的材料。
