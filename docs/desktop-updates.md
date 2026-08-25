# 万象谱桌面端自动更新与 GitHub Release 发布规范

> 本文是桌面端自动更新的长期维护依据。任何版本号、签名密钥、GitHub 仓库或打包流程变更，都应同步更新本文。
>
> 发布前必须备份真实用户数据，执行安全预检、安装器与签名验证，并从上一公开版本完成一次真实升级。源码、Release 资产和日志都不得包含用户数据库、作品、API Key、Updater 私钥或个人绝对路径。

## 1. 当前方案

万象谱桌面端使用 Tauri 2 Updater，并通过 GitHub Releases 托管更新清单和安装产物。

更新仓库不再写死在源码里。正式构建时必须显式传入最终公开仓库：

```text
<owner>/<repository>
```

客户端检查地址：

```text
https://github.com/<owner>/<repository>/releases/latest/download/latest.json
```

相关实现位置：

- `src-tauri/tauri.conf.json`：版本号、更新公钥和 Windows 安装模式；正式更新端点由 `build-desktop.ps1` 的 Release overlay 注入，不常驻基础配置。
- `src-tauri/src/lib.rs`：注册 Tauri Updater 插件。
- `src-tauri/installer/installer-hooks.nsh` 与 `installer-preflight.ps1`：在 NSIS 覆盖旧文件前关闭且核验安装目录内的 bundled Python；无法确认解锁时中止安装。
- `frontend/src/utils/updater.ts`：前端更新开关和检查逻辑。
- `frontend/src/components/DesktopUpdateNotice.tsx`：启动后的被动更新提示。
- `frontend/src/pages/sections/DesktopUpdateSettingsSection.tsx`：设置页中的更新操作。
- `build-desktop.ps1`：准备独立 Python 运行时并构建桌面安装包。
- `prepare-github-release.ps1`：生成 GitHub Release 使用的 `latest.json`。

## 2. 首发版本的特殊要求

`v0.3.0` 是第一个公开版本，不需要从更早版本更新过来，但仍应同时发布安装器、`.sig` 和当前版本的 `latest.json`。同版本客户端不会重复安装，而更新端点可从首日起被验证为真实可用。

首发安装包一旦分发，就确定了后续版本的更新信任链：

1. `v0.3.0` 内嵌更新公钥。
2. 后续更新产物必须由对应私钥签名。
3. 私钥遗失或更换后，已安装的 `v0.3.0` 将无法验证新更新。

因此，首发前必须确认：

- 仓库地址最终确定且可访问。
- GitHub 仓库为公开仓库；当前客户端没有访问私有 Release 的 GitHub 凭证。
- 更新公钥确实来自本项目保存的私钥。
- 私钥至少有一份离线备份，且不会提交到 Git。
- 构建时设置 `VITE_DESKTOP_UPDATES_ENABLED=1`。

### 当前发布状态

`v0.3.0` 已作为首个公开 Beta 发布。发布后实测发现：从 Windows 资源管理器启动的应用无法自动继承 Internet Options 手动代理，依赖本地代理访问 GitHub 时会导致更新检查失败。

`v0.3.1` 增加桌面系统代理解析：优先读取当前进程的 `HTTPS_PROXY/ALL_PROXY/HTTP_PROXY`，没有可用值时读取当前用户 Internet Options 的手动代理，并显式传给 Tauri Updater。代理只用于本次检查和下载，不持久化、不写日志。首次从 `v0.3.0` 过渡时，如所在网络必须使用代理，需要从已设置代理环境变量的 PowerShell 启动旧版；升级到 `v0.3.1` 后恢复正常启动即可。

`v0.3.2` 修复 Windows 覆盖安装前的进程生命周期：更新包先下载，随后由桌面宿主明确终止自己管理的内置 Python 后台，并等待其监听端口释放；只有准备成功后才调用安装器。若后台未能安全退出，安装会被阻止，当前版本自动重启恢复，不允许用户在 DLL/PYD 被占用时继续形成不完整安装。

`v0.3.3` 修复慢速或抖动代理下的更新可靠性：更新清单与大包下载分别对连接、TLS、响应流和 5xx 等暂时性错误做有限串行重试，大包单次请求总时限提升到 30 分钟；签名、公钥、404 和确定性 4xx 永不重试。两个更新入口共用 single-flight，下载完全结束并通过 Updater 签名校验后才会停止本地后台。路由切换、组件卸载和重复检查统一通过延迟且幂等的资源释放协议处理，活动安装结束前不会关闭 Tauri Update resource。阶段和脱敏错误写入有界 `updater.log`，并保证安装失败证据先落盘再请求重启，便于区分检查、下载、验签、后台退出和安装器启动故障。

`v0.3.4` 修复大纲架构师引导构建的步骤页高度计算：底部导航固定在右侧工作区内，不再被内容顶出视口；同时确保用户刚选择的回答会立即传入下一题请求，并将架构师工作区背景调整为与主文档一致的低对比灰绿纸面。

## 3. 签名密钥

Tauri Updater 强制验证更新签名，不能关闭。公钥可以随应用公开；私钥只能保存在受控构建环境中。

推荐私钥位置：

```text
%USERPROFILE%\.loreweft-updater\updater.key
```

首次生成密钥：

```powershell
.\frontend\node_modules\.bin\tauri.cmd signer generate `
  -w "$env:USERPROFILE\.loreweft-updater\updater.key"
```

生成后应完成以下操作：

1. 把输出的公钥内容写入 `src-tauri/tauri.conf.json` 的 `plugins.updater.pubkey`。
2. 将私钥备份到离线、安全、可恢复的位置。
3. 确认 `.loreweft-updater/`、`*.key` 和 `*.key.pub` 永远不进入 Git。
4. 如私钥设置了密码，在构建机上安全设置 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。

构建脚本以 Tauri 文档中的 `TAURI_SIGNING_PRIVATE_KEY` 为主契约，它可以是私钥路径或内容：

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.loreweft-updater\updater.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "<仅在私钥有密码时设置>"
```

不要把私钥内容、私钥路径或密码写进 `.env`、源码、构建日志、GitHub Issue 或 Release。

为兼容已有 CI，脚本也接受 `TAURI_SIGNING_PRIVATE_KEY_PATH`，但会在调用 Tauri 前统一转换为 `TAURI_SIGNING_PRIVATE_KEY`。两者都是 Tauri 2.10+ 支持的变量，禁止同时设置不同私钥。

## 4. 版本号规则

每次桌面发布必须同步更新：

- `src-tauri/tauri.conf.json`
- `src-tauri/Cargo.toml`
- `src-tauri/Cargo.lock` 中 `loreweft` 包的版本
- `frontend/package.json`
- `frontend/package-lock.json` 顶层版本

统一使用三段式 SemVer，例如：

```text
0.3.0 -> 0.3.1 -> 0.3.2 -> 0.3.3 -> 0.3.4 -> 0.4.0
```

更新版本必须高于客户端当前版本。不要重复使用已经公开的版本号和 Git 标签。

## 5. 正式构建

准备 Python 3.12 可再分发运行时后执行：

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.loreweft-updater\updater.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "<可选>"

.\build-desktop.ps1 `
  -PythonRuntimePath .\python `
  -Release `
  -GitHubRepository "owner/repository" `
  -PromptForUpdaterKeyPassword `
  -AllowUnsignedInstaller
```

上例中的 `-AllowUnsignedInstaller` 仅用于已经明确接受 SmartScreen/“未知发布者”风险的公开 Beta 或测试构建。获得 Windows 代码签名证书后应移除此参数；正式稳定版默认仍要求 Authenticode 为 `Valid`。

如果私钥受密码保护，交互式本机构建推荐使用 `-PromptForUpdaterKeyPassword`。脚本通过 `Read-Host -AsSecureString` 获取密码，仅在本次构建进程中设置 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`，并在构建结束或失败后恢复原环境；密码不会写入命令历史、文件或构建日志。CI 环境仍应使用受保护的 Secret，而不是交互提示。

传入 `-GitHubRepository` 后，构建必须同时满足：

- 前端以 `VITE_DESKTOP_UPDATES_ENABLED=1` 编译。
- Tauri 使用 GitHub 的 `latest.json` 地址。
- 生成普通首装安装器。
- 生成可供 Updater 使用的签名文件。

构建成功后至少检查：

```powershell
Get-ChildItem src-tauri\target\release-candidate\release\bundle\nsis
Get-FileHash src-tauri\target\release-candidate\release\bundle\nsis\*-setup.exe -Algorithm SHA256
```

GitHub Release 的实际下载资产必须使用 ASCII 文件名。`prepare-github-release.ps1` 会保留本地中文产品名安装器，同时在 `bundle\nsis\github-release` 生成 `Loreweft_<version>_x64-setup.exe`、对应 `.sig` 和指向该 ASCII 文件名的 `latest.json`。这是为了避免 GitHub 清洗中文资产名后造成 Updater URL 与实际文件名不一致。

构建脚本在发布 Tauri resources 前会删除 `site-packages\bin`、所有 `__pycache__` 以及 `*.pyc`/`*.pyo`。随后候选扫描会拒绝数据库、日志、私钥文件、私钥内容和个人绝对路径（例如用户目录或本地源码目录）。`prepare-github-release.ps1` 还会拒绝工作区之外或经过 junction/symlink 的 `-BundleDirectory`，不能用外部目录绕过候选门禁。

旧客户端可能尚未包含新版“下载后、安装前”的后台退出协议，因此 0.3.2 起安装器本身也具有 fail-closed 的 NSIS `PREINSTALL` 兜底：只终止当前安装目录 `python` 下的进程，并在连续稳定确认旧 `.exe`、`.dll`、`.pyd` 均可独占打开后才允许复制。路径逃逸、重解析点、无关 Python 或仍被占用的运行时文件都不会被静默忽略。

## 6. GitHub Release 结构

从首发 `v0.3.0` 开始，每个用于自动更新的 Release 都必须是非草稿、非 GitHub prerelease，并至少包含：

```text
Loreweft_<version>_x64-setup.exe
Loreweft_<version>_x64-setup.exe.sig
latest.json
```

`latest.json` 示例：

```json
{
  "version": "0.4.0",
  "notes": "本次更新说明",
  "pub_date": "2026-08-01T12:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<.sig 文件的完整文本内容>",
      "url": "https://github.com/<owner>/<repository>/releases/download/v0.4.0/Loreweft_0.4.0_x64-setup.exe"
    }
  }
}
```

注意：`signature` 是 `.sig` 文件的文本内容，不是文件名或下载地址。

Release 必须满足：

- 标签与清单版本一致，例如 `v0.4.0` 与 `"version": "0.4.0"`。
- 不是 Draft，也不勾选 GitHub 的 “Set as a pre-release”；`/releases/latest/` 不会选择 prerelease。
- “Public Beta” 是产品成熟度和发布说明标签，不等于 GitHub prerelease。为保持 `releases/latest/download/latest.json` 可用，应在标题和 notes 中标明 Beta，同时让 Release 保持非 prerelease。
- `latest.json` 中的下载地址与实际上传文件名完全一致。
- 仓库公开可访问。
- 安装包与签名来自同一次构建，禁止混用。

## 7. 发布后的验证

上传后先验证三个 URL：

```text
https://github.com/<owner>/<repository>/releases/latest/download/latest.json
https://github.com/<owner>/<repository>/releases/download/v<version>/<installer-name>
https://github.com/<owner>/<repository>/releases/download/v<version>/<signature-name>
```

然后在上一正式版本上执行真实升级测试：

1. 启动上一版本客户端。
2. 确认能够发现新版本。
3. 检查版本号和更新说明。
4. 下载并安装更新。
5. 确认应用重启后版本正确。
6. 确认用户 SQLite 数据库、API 配置、创作数据和缓存位置未被删除。

禁止只在新版本上测试更新；新版本通常会因为版本相同而报告“无更新”。

## 8. 发布门禁

### 8.1 Updater 签名变量

脚本现已以 `TAURI_SIGNING_PRIVATE_KEY` 为主、兼容 `TAURI_SIGNING_PRIVATE_KEY_PATH`。正式发布仍必须用真实私钥生成与安装器同批的 `.sig`，并验证它与客户端内嵌公钥配对。

当前 Tauri CLI 的 `signer` 只有 `generate` 和 `sign`，没有 `verify` 子命令。因此两个发行脚本使用与 Tauri Updater 运行时相同的 Minisign 校验语义进行等价门禁：解码配置中的公钥和 `.sig`，核对 key id，验证安装器签名，并验证 trusted comment 的全局签名。签名为空、错钥、混入另一批安装器或安装器字节被改动时都会在发布前失败。

### 8.2 更新产物格式不一致

构建覆盖配置使用：

```json
"createUpdaterArtifacts": true
```

这是 Tauri 2 原生产物模式，Windows NSIS 生成 `*-setup.exe` 及对应 `.sig`。`prepare-github-release.ps1` 已统一读取这一格式，并按配置版本号要求孤立目录内只有一组成对产物。

本项目从 `v0.3.0` 首发，不使用 `"v1Compatible"` 的旧版 zip 格式。

### 8.3 公开更新源

代码不再携带一个已知 404 的默认更新端点。正式构建必须显式传入最终且可匿名访问的公开资产源；上传后须验证 `latest.json`、安装器和 `.sig` 三个 URL 都是 2xx。

### 8.4 Windows Authenticode

Tauri Updater 的 `.sig` 只用于更新包完整性与信任链验证，不等同于 Windows 代码签名。公开候选包默认要求 `Get-AuthenticodeSignature` 为 `Valid`。仅当发行负责人明确接受 SmartScreen/“未知发布者”风险，并在 Release 标题与说明中标记 Public Beta、披露风险时，才允许显式传入 `-AllowUnsignedInstaller`。为保证 `/releases/latest/` 工作，此类 Beta 更新仍保持 GitHub 非 prerelease；正式稳定版仍应补齐 Windows 代码签名。

## 9. 故障排查

### 客户端没有更新入口

检查构建时是否设置了：

```text
VITE_DESKTOP_UPDATES_ENABLED=1
```

普通安装包存在 Updater 插件并不代表前端更新功能已经启用。

### 返回 404

依次检查仓库是否存在、是否公开、Release 是否正式发布，以及 `latest.json` 是否作为资产上传到最新 Release。

### 签名验证失败

检查公私钥是否配对、安装包与签名是否来自同一次构建，以及 `latest.json.signature` 是否包含 `.sig` 的完整内容。

### 客户端始终认为没有更新

检查 `latest.json.version` 是否严格高于客户端版本，并确认使用的是上一正式版本测试。

## 10. 官方参考

- Tauri 2 Updater：https://v2.tauri.app/plugin/updater/
- GitHub Release 资产：https://docs.github.com/en/rest/releases/assets
- GitHub 最新 Release 固定链接：https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases
