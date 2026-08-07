<p align="center">
  <img src="assets/brand/loreweft-icon.png" width="112" alt="万象谱 Loreweft 图标" />
</p>

<h1 align="center">万象谱 Loreweft</h1>

<p align="center">
  面向长篇小说工程的 AI 原生创作工作台<br />
  <em>AI-native workspace for long-form fiction</em>
</p>

<p align="center">
  <code>Windows 10 / 11</code> · <code>Local-first</code> · <code>Public Beta</code> · <code>Closed Source</code>
</p>

<p align="center">
  <a href="https://github.com/EXwhitewood/wanxiangpu-loreweft/releases/latest">下载最新版</a>
  ·
  <a href="CHANGELOG.md">版本记录</a>
  ·
  <a href="https://github.com/EXwhitewood/wanxiangpu-loreweft/issues">问题反馈</a>
</p>

![万象谱项目大厅](assets/screenshots/project-hall.png)

<p align="center"><sub>“潮汐档案馆”为界面展示使用的虚构示例项目。</sub></p>

## 万象谱是什么

万象谱是一款围绕长篇小说全生命周期设计的本地创作工作台。它把项目定位、世界观、人物与地点、结构大纲、章节蓝图、正文、伏笔、叙事状态和质量诊断放在同一个工程中，让 AI 在明确约束下参与创作，同时保留作者对正文与设定的最终决定权。

主要能力包括：

- Story Plan、章节脊柱、章节蓝图和场景序列；
- 世界规则、人物、地点、伏笔与连续性状态管理；
- AI 分阶段生成、校验、受控修订和提交门禁；
- 作者手写正文、异步状态结算与质量诊断；
- Markdown、Word 导出，以及可校验的项目归档与恢复；
- 本地项目数据与用户自行配置的模型服务。

部分 AI 功能需要用户自行配置兼容的模型 API。没有配置模型时，项目管理、正文编辑、世界观维护和导入导出等本地能力仍可使用。

## 界面预览

| 项目仪表盘 | 沉浸创作 |
| --- | --- |
| ![项目仪表盘](assets/screenshots/project-dashboard.png) | ![沉浸创作](assets/screenshots/editor-workspace.png) |

| 世界观工作台 | 智能体中心 |
| --- | --- |
| ![世界观工作台](assets/screenshots/worldbuilding-overview.png) | ![智能体中心](assets/screenshots/agent-center.png) |

## 下载、安装与自动更新

请只从本仓库的 [Releases](https://github.com/EXwhitewood/wanxiangpu-loreweft/releases) 下载官方 Windows 安装包。安装包已经包含运行所需组件，不需要另行安装 Python、Node.js 或 Rust。

应用通过本仓库的 GitHub Releases 检查更新。每个更新包都必须通过应用内置公钥的签名验证；发布清单、安装包和签名文件会作为同一 Release 的资产提供。

当前版本仍是 Public Beta，Windows 安装包尚未使用 Authenticode 代码签名，安装时可能显示“未知发布者”或触发 SmartScreen。升级或迁移前，请先使用应用内项目归档功能备份重要作品。

## 数据与联网说明

- 项目、章节、设定、诊断和设置默认保存在本地应用数据目录；
- 当前公开版本不会向万象谱维护者发送产品分析遥测；
- 只有在用户主动调用模型功能时，必要的提示词、上下文和作品片段才可能发送给用户选择的模型服务商；
- 更新检查会访问 GitHub Releases，并可能使用用户当前的系统代理；
- 请勿在公开 Issue 中提交 API Key、数据库、项目归档或未公开作品全文。

详情见 [隐私说明](PRIVACY.md) 与 [安全政策](SECURITY.md)。

## 用户作品权利

用户保留其输入内容和原创作品的权利，可以出版、签约、销售、授权、翻译、改编或以其他方式商业使用作品。万象谱不会仅因软件参与创作、编辑、诊断或保存而主张作品所有权、版税或收入分成。

详情见 [用户作品权利说明](OUTPUT_RIGHTS.md)。

## 仓库范围与版权

这是万象谱的公开产品、支持与发行仓库，用于发布产品说明、版本记录、Issue 和经过签名的安装包。仓库不提供核心源码、内部构建系统或源码构建支持，也不接收代码 Pull Request。

本仓库不是开源源码仓库。除明确标注适用独立许可证的第三方材料外，仓库内容与万象谱官方程序 Copyright © 2026 EXwhitewood，保留所有权利。第三方组件归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 反馈

错误报告和功能建议请提交到 [GitHub Issues](https://github.com/EXwhitewood/wanxiangpu-loreweft/issues)。提交前请阅读 [贡献与反馈说明](CONTRIBUTING.md)；安全漏洞请按照 [SECURITY.md](SECURITY.md) 使用私密渠道报告。
