# 隐私说明 / Privacy Notice

## 本地数据

万象谱桌面版以本地应用方式运行。项目、章节、世界观、诊断、设置和创作状态默认写入用户选择或应用默认的本地创作数据目录。源码仓库和 GitHub Release 不包含用户数据库或作品。

Loreweft Desktop runs locally. Projects, chapters, worldbuilding data, diagnostics, settings, and writing state are stored in the local creative-data directory selected by the user or provided by the application. The source repository and GitHub releases do not include user databases or manuscripts.

## 外部请求

在用户主动配置并使用模型服务时，为完成相应功能，提示词、上下文和作品片段可能发送给用户选择的模型/API 服务商。相关处理受该服务商的隐私政策、数据保留和服务条款约束。

软件更新检查会请求 GitHub Releases。应用可能使用用户的系统代理完成该请求。代理地址仅在运行时使用，不写入公开仓库或更新清单。

When the user configures and invokes a model provider, prompts, context, and manuscript excerpts may be sent to that provider. The provider's privacy, retention, and service terms apply. Update checks contact GitHub Releases and may use the user's system proxy; proxy addresses are runtime-only and are not written to the public repository or update manifest.

## 遥测

当前公开版本没有向万象谱维护者发送产品分析遥测的功能。第三方模型服务、GitHub 和用户自行配置的网络基础设施可能记录各自请求。

The current public version does not send product-analytics telemetry to the Loreweft maintainer. Model providers, GitHub, and user-configured network infrastructure may independently log their requests.

## 用户责任

- 不要在 GitHub Issue 中上传 API Key、数据库或未公开作品全文；
- 在向模型服务发送敏感作品前，检查服务商的数据政策；
- 升级、迁移目录或修改源码前，使用项目归档功能备份重要作品；
- Fork 维护者若新增遥测、云同步或托管服务，必须自行更新隐私说明。
