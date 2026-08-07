# 第三方组件声明 / Third-Party Software Notice

万象谱包含由各自作者维护的第三方依赖。第三方组件继续适用其各自的许可证和归属要求，不因万象谱采用闭源发行方式而改变。

Loreweft depends on third-party software maintained by its respective authors. Those components remain governed by their own licenses and attribution requirements.

以下清单对应万象谱 `v0.3.3` 官方 Windows 发行版，并由受控构建环境中的锁定依赖生成。该清单不替代依赖包随附的完整许可证文本。

The inventory below corresponds to the official Loreweft `v0.3.3` Windows distribution and was generated from locked dependencies in the controlled build environment. It does not replace the full license texts shipped by dependency authors.

## Vendored Tauri NSIS sources

The customized Windows installer template and its English/Simplified Chinese base language files are derived from the Tauri project:

- repository: `https://github.com/tauri-apps/tauri`
- tag: `tauri-bundler-v2.9.4`
- commit: `8909f221d1515955fc843808032bdc5d62209c96`
- upstream template: `crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi`
- upstream template SHA-256: `20F4ECC730DEFB71F1342EAEAEC4021DF13BE3D843ABBA0EFFE88EA5835FA079`
- selected license: MIT; full text: https://github.com/tauri-apps/tauri/blob/tauri-bundler-v2.9.4/LICENSE_MIT

相关 Tauri 材料继续适用其 MIT 许可证。

## Python

| Package | Version | Declared license |
|---|---:|---|
| `aiosqlite` | `0.22.1` | MIT License |
| `annotated-doc` | `0.0.4` | MIT |
| `annotated-types` | `0.7.0` | MIT License |
| `anthropic` | `0.39.0` | MIT |
| `anyio` | `4.14.2` | MIT |
| `certifi` | `2026.6.17` | MPL-2.0 |
| `click` | `8.4.2` | BSD-3-Clause |
| `colorama` | `0.4.6` | BSD License |
| `distro` | `1.9.0` | Apache License, Version 2.0 |
| `fastapi` | `0.139.2` | MIT |
| `greenlet` | `3.5.3` | MIT AND PSF-2.0 |
| `h11` | `0.16.0` | MIT |
| `httpcore` | `1.0.9` | BSD-3-Clause |
| `httptools` | `0.8.0` | MIT |
| `httpx` | `0.27.0` | BSD-3-Clause |
| `idna` | `3.18` | BSD-3-Clause |
| `jieba` | `0.42.1` | MIT |
| `jiter` | `0.16.0` | MIT |
| `joblib` | `1.5.3` | BSD-3-Clause |
| `lxml` | `6.1.1` | BSD-3-Clause |
| `narwhals` | `2.24.0` | MIT |
| `networkx` | `3.6.1` | BSD-3-Clause |
| `numpy` | `2.5.1` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `openai` | `1.50.0` | Apache-2.0 |
| `pydantic` | `2.9.0` | MIT |
| `pydantic-settings` | `2.5.0` | MIT |
| `pydantic_core` | `2.23.2` | MIT |
| `python-docx` | `1.2.0` | MIT |
| `python-dotenv` | `1.2.2` | BSD-3-Clause |
| `python-multipart` | `0.0.32` | Apache-2.0 |
| `PyYAML` | `6.0.3` | MIT |
| `ruptures` | `1.1.10` | BSD-2-Clause |
| `scikit-learn` | `1.9.0` | BSD-3-Clause |
| `scipy` | `1.18.0` | BSD-3-Clause |
| `sniffio` | `1.3.1` | MIT OR Apache-2.0 |
| `sqlalchemy` | `2.0.35` | MIT |
| `starlette` | `1.3.1` | BSD-3-Clause |
| `threadpoolctl` | `3.6.0` | BSD-3-Clause |
| `tqdm` | `4.69.0` | MPL-2.0 AND MIT |
| `typing-inspection` | `0.4.2` | MIT |
| `typing_extensions` | `4.16.0` | PSF-2.0 |
| `tzdata` | `2026.3` | Apache-2.0 |
| `uvicorn` | `0.30.0` | BSD-3-Clause |
| `watchfiles` | `1.2.0` | MIT |
| `websockets` | `16.1.1` | BSD-3-Clause |

## JavaScript / npm

| Package | Version | Declared license |
|---|---:|---|
| `@adobe/css-tools` | `4.5.0` | MIT |
| `@alloc/quick-lru` | `5.2.0` | MIT |
| `@asamuzakjp/css-color` | `3.2.0` | MIT |
| `@babel/code-frame` | `7.29.7` | MIT |
| `@babel/helper-validator-identifier` | `7.29.7` | MIT |
| `@babel/runtime` | `7.29.7` | MIT |
| `@csstools/color-helpers` | `5.1.0` | MIT-0 |
| `@csstools/css-calc` | `2.1.4` | MIT |
| `@csstools/css-color-parser` | `3.1.0` | MIT |
| `@csstools/css-parser-algorithms` | `3.0.5` | MIT |
| `@csstools/css-tokenizer` | `3.0.4` | MIT |
| `@emnapi/core` | `1.11.1` | MIT |
| `@emnapi/runtime` | `1.11.1` | MIT |
| `@emnapi/wasi-threads` | `1.2.2` | MIT |
| `@jridgewell/gen-mapping` | `0.3.13` | MIT |
| `@jridgewell/resolve-uri` | `3.1.2` | MIT |
| `@jridgewell/sourcemap-codec` | `1.5.5` | MIT |
| `@jridgewell/trace-mapping` | `0.3.31` | MIT |
| `@napi-rs/wasm-runtime` | `1.1.6` | MIT |
| `@nodelib/fs.scandir` | `2.1.5` | MIT |
| `@nodelib/fs.stat` | `2.0.5` | MIT |
| `@nodelib/fs.walk` | `1.2.8` | MIT |
| `@oxc-project/types` | `0.139.0` | MIT |
| `@playwright/test` | `1.62.0` | Apache-2.0 |
| `@popperjs/core` | `2.11.8` | MIT |
| `@remirror/core-constants` | `3.0.0` | MIT |
| `@rolldown/binding-android-arm64` | `1.1.5` | MIT |
| `@rolldown/binding-darwin-arm64` | `1.1.5` | MIT |
| `@rolldown/binding-darwin-x64` | `1.1.5` | MIT |
| `@rolldown/binding-freebsd-x64` | `1.1.5` | MIT |
| `@rolldown/binding-linux-arm-gnueabihf` | `1.1.5` | MIT |
| `@rolldown/binding-linux-arm64-gnu` | `1.1.5` | MIT |
| `@rolldown/binding-linux-arm64-musl` | `1.1.5` | MIT |
| `@rolldown/binding-linux-ppc64-gnu` | `1.1.5` | MIT |
| `@rolldown/binding-linux-s390x-gnu` | `1.1.5` | MIT |
| `@rolldown/binding-linux-x64-gnu` | `1.1.5` | MIT |
| `@rolldown/binding-linux-x64-musl` | `1.1.5` | MIT |
| `@rolldown/binding-openharmony-arm64` | `1.1.5` | MIT |
| `@rolldown/binding-wasm32-wasi` | `1.1.5` | MIT |
| `@rolldown/binding-win32-arm64-msvc` | `1.1.5` | MIT |
| `@rolldown/binding-win32-x64-msvc` | `1.1.5` | MIT |
| `@rolldown/pluginutils` | `1.0.1` | MIT |
| `@standard-schema/spec` | `1.1.0` | MIT |
| `@tauri-apps/api` | `2.11.1` | Apache-2.0 OR MIT |
| `@tauri-apps/cli` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-darwin-arm64` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-darwin-x64` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-linux-arm-gnueabihf` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-linux-arm64-gnu` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-linux-arm64-musl` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-linux-riscv64-gnu` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-linux-x64-gnu` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-linux-x64-musl` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-win32-arm64-msvc` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-win32-ia32-msvc` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/cli-win32-x64-msvc` | `2.11.4` | Apache-2.0 OR MIT |
| `@tauri-apps/plugin-updater` | `2.10.1` | MIT OR Apache-2.0 |
| `@testing-library/dom` | `10.4.1` | MIT |
| `@testing-library/jest-dom` | `7.0.0` | MIT |
| `@testing-library/react` | `16.3.2` | MIT |
| `@tiptap/core` | `2.27.2` | MIT |
| `@tiptap/extension-blockquote` | `2.27.2` | MIT |
| `@tiptap/extension-bold` | `2.27.2` | MIT |
| `@tiptap/extension-bubble-menu` | `2.27.2` | MIT |
| `@tiptap/extension-bullet-list` | `2.27.2` | MIT |
| `@tiptap/extension-code` | `2.27.2` | MIT |
| `@tiptap/extension-code-block` | `2.27.2` | MIT |
| `@tiptap/extension-document` | `2.27.2` | MIT |
| `@tiptap/extension-dropcursor` | `2.27.2` | MIT |
| `@tiptap/extension-floating-menu` | `2.27.2` | MIT |
| `@tiptap/extension-gapcursor` | `2.27.2` | MIT |
| `@tiptap/extension-hard-break` | `2.27.2` | MIT |
| `@tiptap/extension-heading` | `2.27.2` | MIT |
| `@tiptap/extension-history` | `2.27.2` | MIT |
| `@tiptap/extension-horizontal-rule` | `2.27.2` | MIT |
| `@tiptap/extension-italic` | `2.27.2` | MIT |
| `@tiptap/extension-list-item` | `2.27.2` | MIT |
| `@tiptap/extension-ordered-list` | `2.27.2` | MIT |
| `@tiptap/extension-paragraph` | `2.27.2` | MIT |
| `@tiptap/extension-placeholder` | `2.27.2` | MIT |
| `@tiptap/extension-strike` | `2.27.2` | MIT |
| `@tiptap/extension-text` | `2.27.2` | MIT |
| `@tiptap/extension-text-style` | `2.27.2` | MIT |
| `@tiptap/pm` | `2.27.2` | MIT |
| `@tiptap/react` | `2.27.2` | MIT |
| `@tiptap/starter-kit` | `2.27.2` | MIT |
| `@tybys/wasm-util` | `0.10.3` | MIT |
| `@types/aria-query` | `5.0.4` | MIT |
| `@types/chai` | `5.2.3` | MIT |
| `@types/d3-color` | `3.1.3` | MIT |
| `@types/d3-drag` | `3.0.7` | MIT |
| `@types/d3-interpolate` | `3.0.4` | MIT |
| `@types/d3-selection` | `3.0.11` | MIT |
| `@types/d3-transition` | `3.0.9` | MIT |
| `@types/d3-zoom` | `3.0.8` | MIT |
| `@types/deep-eql` | `4.0.2` | MIT |
| `@types/estree` | `1.0.9` | MIT |
| `@types/linkify-it` | `5.0.0` | MIT |
| `@types/markdown-it` | `14.1.2` | MIT |
| `@types/mdurl` | `2.0.0` | MIT |
| `@types/prop-types` | `15.7.15` | MIT |
| `@types/react` | `18.3.29` | MIT |
| `@types/react-dom` | `18.3.7` | MIT |
| `@types/use-sync-external-store` | `0.0.6` | MIT |
| `@vitejs/plugin-react` | `6.0.3` | MIT |
| `@vitest/expect` | `4.1.10` | MIT |
| `@vitest/mocker` | `4.1.10` | MIT |
| `@vitest/pretty-format` | `4.1.10` | MIT |
| `@vitest/runner` | `4.1.10` | MIT |
| `@vitest/snapshot` | `4.1.10` | MIT |
| `@vitest/spy` | `4.1.10` | MIT |
| `@vitest/utils` | `4.1.10` | MIT |
| `@xyflow/react` | `12.10.2` | MIT |
| `@xyflow/system` | `0.0.76` | MIT |
| `agent-base` | `7.1.4` | MIT |
| `ansi-regex` | `5.0.1` | MIT |
| `ansi-styles` | `5.2.0` | MIT |
| `any-promise` | `1.3.0` | MIT |
| `anymatch` | `3.1.3` | ISC |
| `arg` | `5.0.2` | MIT |
| `argparse` | `2.0.1` | Python-2.0 |
| `aria-query` | `5.3.0` | Apache-2.0 |
| `assertion-error` | `2.0.1` | MIT |
| `autoprefixer` | `10.5.0` | MIT |
| `baseline-browser-mapping` | `2.10.31` | Apache-2.0 |
| `binary-extensions` | `2.3.0` | MIT |
| `braces` | `3.0.3` | MIT |
| `browserslist` | `4.28.2` | MIT |
| `camelcase-css` | `2.0.1` | MIT |
| `caniuse-lite` | `1.0.30001793` | CC-BY-4.0 |
| `chai` | `6.2.2` | MIT |
| `chokidar` | `3.6.0` | MIT |
| `classcat` | `5.0.5` | MIT |
| `clsx` | `2.1.1` | MIT |
| `commander` | `4.1.1` | MIT |
| `convert-source-map` | `2.0.0` | MIT |
| `cookie` | `1.1.1` | MIT |
| `crelt` | `1.0.6` | MIT |
| `css.escape` | `1.5.1` | MIT |
| `cssesc` | `3.0.0` | MIT |
| `cssstyle` | `4.6.0` | MIT |
| `csstype` | `3.2.3` | MIT |
| `d3-color` | `3.1.0` | ISC |
| `d3-dispatch` | `3.0.1` | ISC |
| `d3-drag` | `3.0.0` | ISC |
| `d3-ease` | `3.0.1` | BSD-3-Clause |
| `d3-interpolate` | `3.0.1` | ISC |
| `d3-selection` | `3.0.0` | ISC |
| `d3-timer` | `3.0.1` | ISC |
| `d3-transition` | `3.0.1` | ISC |
| `d3-zoom` | `3.0.0` | ISC |
| `data-urls` | `5.0.0` | MIT |
| `debug` | `4.4.3` | MIT |
| `decimal.js` | `10.6.0` | MIT |
| `dequal` | `2.0.3` | MIT |
| `detect-libc` | `2.1.2` | Apache-2.0 |
| `didyoumean` | `1.2.2` | Apache-2.0 |
| `dlv` | `1.1.3` | MIT |
| `dom-accessibility-api` | `0.5.16` | MIT |
| `dom-accessibility-api` | `0.6.3` | MIT |
| `echarts` | `6.1.0` | Apache-2.0 |
| `echarts-for-react` | `3.0.6` | MIT |
| `electron-to-chromium` | `1.5.361` | ISC |
| `entities` | `4.5.0` | BSD-2-Clause |
| `entities` | `6.0.1` | BSD-2-Clause |
| `es-errors` | `1.3.0` | MIT |
| `es-module-lexer` | `2.3.1` | MIT |
| `escalade` | `3.2.0` | MIT |
| `escape-string-regexp` | `4.0.0` | MIT |
| `estree-walker` | `3.0.3` | MIT |
| `expect-type` | `1.4.0` | Apache-2.0 |
| `fast-deep-equal` | `3.1.3` | MIT |
| `fast-glob` | `3.3.3` | MIT |
| `fastq` | `1.20.1` | ISC |
| `fdir` | `6.5.0` | MIT |
| `fill-range` | `7.1.1` | MIT |
| `fraction.js` | `5.3.4` | MIT |
| `framer-motion` | `12.40.0` | MIT |
| `fsevents` | `2.3.2` | MIT |
| `fsevents` | `2.3.3` | MIT |
| `function-bind` | `1.1.2` | MIT |
| `glob-parent` | `5.1.2` | ISC |
| `glob-parent` | `6.0.2` | ISC |
| `hasown` | `2.0.3` | MIT |
| `html-encoding-sniffer` | `4.0.0` | MIT |
| `http-proxy-agent` | `7.0.2` | MIT |
| `https-proxy-agent` | `7.0.6` | MIT |
| `iconv-lite` | `0.6.3` | MIT |
| `indent-string` | `4.0.0` | MIT |
| `is-binary-path` | `2.1.0` | MIT |
| `is-core-module` | `2.16.2` | MIT |
| `is-extglob` | `2.1.1` | MIT |
| `is-glob` | `4.0.3` | MIT |
| `is-number` | `7.0.0` | MIT |
| `is-potential-custom-element-name` | `1.0.1` | MIT |
| `jiti` | `1.21.7` | MIT |
| `js-tokens` | `4.0.0` | MIT |
| `jsdom` | `26.1.0` | MIT |
| `lightningcss` | `1.33.0` | MPL-2.0 |
| `lightningcss-android-arm64` | `1.33.0` | MPL-2.0 |
| `lightningcss-darwin-arm64` | `1.33.0` | MPL-2.0 |
| `lightningcss-darwin-x64` | `1.33.0` | MPL-2.0 |
| `lightningcss-freebsd-x64` | `1.33.0` | MPL-2.0 |
| `lightningcss-linux-arm-gnueabihf` | `1.33.0` | MPL-2.0 |
| `lightningcss-linux-arm64-gnu` | `1.33.0` | MPL-2.0 |
| `lightningcss-linux-arm64-musl` | `1.33.0` | MPL-2.0 |
| `lightningcss-linux-x64-gnu` | `1.33.0` | MPL-2.0 |
| `lightningcss-linux-x64-musl` | `1.33.0` | MPL-2.0 |
| `lightningcss-win32-arm64-msvc` | `1.33.0` | MPL-2.0 |
| `lightningcss-win32-x64-msvc` | `1.33.0` | MPL-2.0 |
| `lilconfig` | `3.1.3` | MIT |
| `lines-and-columns` | `1.2.4` | MIT |
| `linkify-it` | `5.0.2` | MIT |
| `loose-envify` | `1.4.0` | MIT |
| `lru-cache` | `10.4.3` | ISC |
| `lucide-react` | `0.441.0` | ISC |
| `lz-string` | `1.5.0` | MIT |
| `magic-string` | `0.30.21` | MIT |
| `markdown-it` | `14.3.0` | MIT |
| `marked` | `18.0.4` | MIT |
| `mdurl` | `2.0.0` | MIT |
| `merge2` | `1.4.1` | MIT |
| `micromatch` | `4.0.8` | MIT |
| `min-indent` | `1.0.1` | MIT |
| `motion-dom` | `12.40.0` | MIT |
| `motion-utils` | `12.39.0` | MIT |
| `ms` | `2.1.3` | MIT |
| `mz` | `2.7.0` | MIT |
| `nanoid` | `3.3.16` | MIT |
| `node-releases` | `2.0.45` | MIT |
| `normalize-path` | `3.0.0` | MIT |
| `nwsapi` | `2.2.24` | MIT |
| `object-assign` | `4.1.1` | MIT |
| `object-hash` | `3.0.0` | MIT |
| `obug` | `2.1.4` | MIT |
| `orderedmap` | `2.1.1` | MIT |
| `parse5` | `7.3.0` | MIT |
| `path-parse` | `1.0.7` | MIT |
| `pathe` | `2.0.3` | MIT |
| `picocolors` | `1.1.1` | ISC |
| `picomatch` | `2.3.2` | MIT |
| `picomatch` | `4.0.4` | MIT |
| `picomatch` | `4.0.5` | MIT |
| `pify` | `2.3.0` | MIT |
| `pirates` | `4.0.7` | MIT |
| `playwright` | `1.62.0` | Apache-2.0 |
| `playwright-core` | `1.62.0` | Apache-2.0 |
| `postcss` | `8.5.20` | MIT |
| `postcss-import` | `15.1.0` | MIT |
| `postcss-js` | `4.1.0` | MIT |
| `postcss-load-config` | `6.0.1` | MIT |
| `postcss-nested` | `6.2.0` | MIT |
| `postcss-selector-parser` | `6.1.2` | MIT |
| `postcss-value-parser` | `4.2.0` | MIT |
| `pretty-format` | `27.5.1` | MIT |
| `prosemirror-changeset` | `2.4.1` | MIT |
| `prosemirror-collab` | `1.3.1` | MIT |
| `prosemirror-commands` | `1.7.1` | MIT |
| `prosemirror-dropcursor` | `1.8.2` | MIT |
| `prosemirror-gapcursor` | `1.4.1` | MIT |
| `prosemirror-history` | `1.5.0` | MIT |
| `prosemirror-inputrules` | `1.5.1` | MIT |
| `prosemirror-keymap` | `1.2.3` | MIT |
| `prosemirror-markdown` | `1.13.4` | MIT |
| `prosemirror-menu` | `1.3.2` | MIT |
| `prosemirror-model` | `1.25.7` | MIT |
| `prosemirror-schema-basic` | `1.2.4` | MIT |
| `prosemirror-schema-list` | `1.5.1` | MIT |
| `prosemirror-state` | `1.4.4` | MIT |
| `prosemirror-tables` | `1.8.5` | MIT |
| `prosemirror-trailing-node` | `3.0.0` | MIT |
| `prosemirror-transform` | `1.12.0` | MIT |
| `prosemirror-view` | `1.41.8` | MIT |
| `punycode` | `2.3.1` | MIT |
| `punycode.js` | `2.3.1` | MIT |
| `queue-microtask` | `1.2.3` | MIT |
| `react` | `18.3.1` | MIT |
| `react-dom` | `18.3.1` | MIT |
| `react-is` | `17.0.2` | MIT |
| `react-router` | `7.18.1` | MIT |
| `react-router-dom` | `7.18.1` | MIT |
| `read-cache` | `1.0.0` | MIT |
| `readdirp` | `3.6.0` | MIT |
| `redent` | `3.0.0` | MIT |
| `resolve` | `1.22.12` | MIT |
| `reusify` | `1.1.0` | MIT |
| `rolldown` | `1.1.5` | MIT |
| `rope-sequence` | `1.3.4` | MIT |
| `rrweb-cssom` | `0.8.0` | MIT |
| `run-parallel` | `1.2.0` | MIT |
| `safer-buffer` | `2.1.2` | MIT |
| `saxes` | `6.0.0` | ISC |
| `scheduler` | `0.23.2` | MIT |
| `set-cookie-parser` | `2.7.2` | MIT |
| `siginfo` | `2.0.0` | ISC |
| `size-sensor` | `1.0.3` | ISC |
| `source-map-js` | `1.2.1` | BSD-3-Clause |
| `stackback` | `0.0.2` | MIT |
| `std-env` | `4.2.0` | MIT |
| `strip-indent` | `3.0.0` | MIT |
| `sucrase` | `3.35.1` | MIT |
| `supports-preserve-symlinks-flag` | `1.0.0` | MIT |
| `symbol-tree` | `3.2.4` | MIT |
| `tailwind-merge` | `3.6.0` | MIT |
| `tailwindcss` | `3.4.19` | MIT |
| `thenify` | `3.3.1` | MIT |
| `thenify-all` | `1.6.0` | MIT |
| `tinybench` | `2.9.0` | MIT |
| `tinyexec` | `1.2.4` | MIT |
| `tinyglobby` | `0.2.17` | MIT |
| `tinyrainbow` | `3.1.0` | MIT |
| `tippy.js` | `6.3.7` | MIT |
| `tldts` | `6.1.86` | MIT |
| `tldts-core` | `6.1.86` | MIT |
| `to-regex-range` | `5.0.1` | MIT |
| `tough-cookie` | `5.1.2` | BSD-3-Clause |
| `tr46` | `5.1.1` | MIT |
| `ts-interface-checker` | `0.1.13` | Apache-2.0 |
| `tslib` | `2.3.0` | 0BSD |
| `tslib` | `2.8.1` | 0BSD |
| `typescript` | `5.9.3` | Apache-2.0 |
| `uc.micro` | `2.1.0` | MIT |
| `update-browserslist-db` | `1.2.3` | MIT |
| `use-sync-external-store` | `1.6.0` | MIT |
| `util-deprecate` | `1.0.2` | MIT |
| `vite` | `8.1.5` | MIT |
| `vitest` | `4.1.10` | MIT |
| `w3c-keyname` | `2.2.8` | MIT |
| `w3c-xmlserializer` | `5.0.0` | MIT |
| `webidl-conversions` | `7.0.0` | BSD-2-Clause |
| `whatwg-encoding` | `3.1.1` | MIT |
| `whatwg-mimetype` | `4.0.0` | MIT |
| `whatwg-url` | `14.2.0` | MIT |
| `why-is-node-running` | `2.3.0` | MIT |
| `ws` | `8.21.1` | MIT |
| `xml-name-validator` | `5.0.0` | Apache-2.0 |
| `xmlchars` | `2.2.0` | MIT |
| `zrender` | `6.1.0` | BSD-3-Clause |
| `zustand` | `4.5.7` | MIT |

## Rust / Cargo

| Package | Version | Declared license |
|---|---:|---|
| `adler2` | `2.0.1` | 0BSD OR MIT OR Apache-2.0 |
| `aho-corasick` | `1.1.4` | Unlicense OR MIT |
| `alloc-no-stdlib` | `2.0.4` | BSD-3-Clause |
| `alloc-stdlib` | `0.2.4` | BSD-3-Clause |
| `android_system_properties` | `0.1.5` | MIT/Apache-2.0 |
| `anyhow` | `1.0.104` | MIT OR Apache-2.0 |
| `arbitrary` | `1.4.2` | MIT OR Apache-2.0 |
| `ashpd` | `0.11.1` | MIT |
| `async-broadcast` | `0.7.2` | MIT OR Apache-2.0 |
| `async-channel` | `2.5.0` | Apache-2.0 OR MIT |
| `async-executor` | `1.14.0` | Apache-2.0 OR MIT |
| `async-fs` | `2.2.0` | Apache-2.0 OR MIT |
| `async-io` | `2.6.0` | Apache-2.0 OR MIT |
| `async-lock` | `3.4.2` | Apache-2.0 OR MIT |
| `async-net` | `2.0.0` | Apache-2.0 OR MIT |
| `async-process` | `2.5.0` | Apache-2.0 OR MIT |
| `async-recursion` | `1.1.1` | MIT OR Apache-2.0 |
| `async-signal` | `0.2.14` | Apache-2.0 OR MIT |
| `async-task` | `4.7.1` | Apache-2.0 OR MIT |
| `async-trait` | `0.1.91` | MIT OR Apache-2.0 |
| `atk` | `0.18.2` | MIT |
| `atk-sys` | `0.18.2` | MIT |
| `atomic-waker` | `1.1.2` | Apache-2.0 OR MIT |
| `autocfg` | `1.5.1` | Apache-2.0 OR MIT |
| `base64` | `0.21.7` | MIT OR Apache-2.0 |
| `base64` | `0.22.1` | MIT OR Apache-2.0 |
| `bit-set` | `0.8.0` | Apache-2.0 OR MIT |
| `bit-vec` | `0.8.0` | Apache-2.0 OR MIT |
| `bitflags` | `1.3.2` | MIT/Apache-2.0 |
| `bitflags` | `2.13.1` | MIT OR Apache-2.0 |
| `block-buffer` | `0.10.4` | MIT OR Apache-2.0 |
| `block2` | `0.6.2` | MIT |
| `blocking` | `1.6.2` | Apache-2.0 OR MIT |
| `brotli` | `8.0.4` | BSD-3-Clause AND MIT |
| `brotli-decompressor` | `5.0.3` | BSD-3-Clause/MIT |
| `bs58` | `0.5.1` | MIT/Apache-2.0 |
| `bumpalo` | `3.20.3` | MIT OR Apache-2.0 |
| `bytemuck` | `1.25.2` | Zlib OR Apache-2.0 OR MIT |
| `byteorder` | `1.5.0` | Unlicense OR MIT |
| `bytes` | `1.12.1` | MIT |
| `cairo-rs` | `0.18.5` | MIT |
| `cairo-sys-rs` | `0.18.2` | MIT |
| `camino` | `1.2.4` | MIT OR Apache-2.0 |
| `cargo-platform` | `0.1.9` | MIT OR Apache-2.0 |
| `cargo_metadata` | `0.19.2` | MIT |
| `cargo_toml` | `0.22.3` | Apache-2.0 OR MIT |
| `cc` | `1.3.0` | MIT OR Apache-2.0 |
| `cesu8` | `1.1.0` | Apache-2.0/MIT |
| `cfb` | `0.7.3` | MIT |
| `cfg-expr` | `0.15.8` | MIT OR Apache-2.0 |
| `cfg-if` | `1.0.4` | MIT OR Apache-2.0 |
| `chrono` | `0.4.45` | MIT OR Apache-2.0 |
| `combine` | `4.6.7` | MIT |
| `concurrent-queue` | `2.5.0` | Apache-2.0 OR MIT |
| `cookie` | `0.18.1` | MIT OR Apache-2.0 |
| `core-foundation` | `0.10.1` | MIT OR Apache-2.0 |
| `core-foundation-sys` | `0.8.7` | MIT OR Apache-2.0 |
| `core-graphics` | `0.25.0` | MIT OR Apache-2.0 |
| `core-graphics-types` | `0.2.0` | MIT OR Apache-2.0 |
| `cpufeatures` | `0.2.17` | MIT OR Apache-2.0 |
| `crc32fast` | `1.5.0` | MIT OR Apache-2.0 |
| `crossbeam-channel` | `0.5.16` | MIT OR Apache-2.0 |
| `crossbeam-utils` | `0.8.22` | MIT OR Apache-2.0 |
| `crypto-common` | `0.1.7` | MIT OR Apache-2.0 |
| `cssparser` | `0.36.0` | MPL-2.0 |
| `cssparser-macros` | `0.6.1` | MPL-2.0 |
| `ctor` | `0.8.0` | Apache-2.0 OR MIT |
| `ctor-proc-macro` | `0.0.7` | Apache-2.0 OR MIT |
| `darling` | `0.23.0` | MIT |
| `darling_core` | `0.23.0` | MIT |
| `darling_macro` | `0.23.0` | MIT |
| `dbus` | `0.9.12` | Apache-2.0/MIT |
| `deranged` | `0.5.8` | MIT OR Apache-2.0 |
| `derive_arbitrary` | `1.4.2` | MIT OR Apache-2.0 |
| `derive_more` | `2.1.1` | MIT |
| `derive_more-impl` | `2.1.1` | MIT |
| `digest` | `0.10.7` | MIT OR Apache-2.0 |
| `dirs` | `6.0.0` | MIT OR Apache-2.0 |
| `dirs-sys` | `0.5.0` | MIT OR Apache-2.0 |
| `dispatch2` | `0.3.1` | Zlib OR Apache-2.0 OR MIT |
| `displaydoc` | `0.2.6` | MIT OR Apache-2.0 |
| `dlib` | `0.5.3` | MIT |
| `dlopen2` | `0.8.2` | MIT |
| `dlopen2_derive` | `0.4.3` | MIT |
| `dom_query` | `0.27.0` | MIT |
| `downcast-rs` | `1.2.1` | MIT/Apache-2.0 |
| `dpi` | `0.1.2` | Apache-2.0 AND MIT |
| `dtoa` | `1.0.11` | MIT OR Apache-2.0 |
| `dtoa-short` | `0.3.5` | MPL-2.0 |
| `dtor` | `0.3.0` | Apache-2.0 OR MIT |
| `dtor-proc-macro` | `0.0.6` | Apache-2.0 OR MIT |
| `dunce` | `1.0.5` | CC0-1.0 OR MIT-0 OR Apache-2.0 |
| `dyn-clone` | `1.0.20` | MIT OR Apache-2.0 |
| `embed-resource` | `3.0.11` | MIT |
| `embed_plist` | `1.2.2` | MIT OR Apache-2.0 |
| `endi` | `1.1.1` | MIT |
| `enumflags2` | `0.7.12` | MIT OR Apache-2.0 |
| `enumflags2_derive` | `0.7.12` | MIT OR Apache-2.0 |
| `equivalent` | `1.0.2` | Apache-2.0 OR MIT |
| `erased-serde` | `0.4.10` | MIT OR Apache-2.0 |
| `errno` | `0.3.14` | MIT OR Apache-2.0 |
| `event-listener` | `5.4.1` | Apache-2.0 OR MIT |
| `event-listener-strategy` | `0.5.4` | Apache-2.0 OR MIT |
| `fastrand` | `2.5.0` | Apache-2.0 OR MIT |
| `fdeflate` | `0.3.7` | MIT OR Apache-2.0 |
| `field-offset` | `0.3.6` | MIT OR Apache-2.0 |
| `filetime` | `0.2.29` | MIT/Apache-2.0 |
| `find-msvc-tools` | `0.1.9` | MIT OR Apache-2.0 |
| `flate2` | `1.1.9` | MIT OR Apache-2.0 |
| `fnv` | `1.0.7` | Apache-2.0 / MIT |
| `foldhash` | `0.2.0` | Zlib |
| `foreign-types` | `0.5.0` | MIT/Apache-2.0 |
| `foreign-types-macros` | `0.2.3` | MIT/Apache-2.0 |
| `foreign-types-shared` | `0.3.1` | MIT/Apache-2.0 |
| `form_urlencoded` | `1.2.2` | MIT OR Apache-2.0 |
| `futures-channel` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-core` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-executor` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-io` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-lite` | `2.6.1` | Apache-2.0 OR MIT |
| `futures-macro` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-sink` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-task` | `0.3.33` | MIT OR Apache-2.0 |
| `futures-util` | `0.3.33` | MIT OR Apache-2.0 |
| `gdk` | `0.18.2` | MIT |
| `gdk-pixbuf` | `0.18.5` | MIT |
| `gdk-pixbuf-sys` | `0.18.0` | MIT |
| `gdk-sys` | `0.18.2` | MIT |
| `gdkwayland-sys` | `0.18.2` | MIT |
| `gdkx11` | `0.18.2` | MIT |
| `gdkx11-sys` | `0.18.2` | MIT |
| `generic-array` | `0.14.7` | MIT |
| `getrandom` | `0.2.17` | MIT OR Apache-2.0 |
| `getrandom` | `0.3.4` | MIT OR Apache-2.0 |
| `getrandom` | `0.4.3` | MIT OR Apache-2.0 |
| `gio` | `0.18.4` | MIT |
| `gio-sys` | `0.18.1` | MIT |
| `glib` | `0.18.5` | MIT |
| `glib-macros` | `0.18.5` | MIT |
| `glib-sys` | `0.18.1` | MIT |
| `glob` | `0.3.3` | MIT OR Apache-2.0 |
| `gobject-sys` | `0.18.0` | MIT |
| `gtk` | `0.18.2` | MIT |
| `gtk-sys` | `0.18.2` | MIT |
| `gtk3-macros` | `0.18.2` | MIT |
| `hashbrown` | `0.12.3` | MIT OR Apache-2.0 |
| `hashbrown` | `0.17.1` | MIT OR Apache-2.0 |
| `heck` | `0.4.1` | MIT OR Apache-2.0 |
| `heck` | `0.5.0` | MIT OR Apache-2.0 |
| `hermit-abi` | `0.5.2` | MIT OR Apache-2.0 |
| `hex` | `0.4.3` | MIT OR Apache-2.0 |
| `html5ever` | `0.38.0` | MIT OR Apache-2.0 |
| `http` | `1.4.2` | MIT OR Apache-2.0 |
| `http-body` | `1.1.0` | MIT |
| `http-body-util` | `0.1.4` | MIT |
| `httparse` | `1.10.1` | MIT OR Apache-2.0 |
| `hyper` | `1.10.1` | MIT |
| `hyper-rustls` | `0.27.9` | Apache-2.0 OR ISC OR MIT |
| `hyper-util` | `0.1.20` | MIT |
| `iana-time-zone` | `0.1.65` | MIT OR Apache-2.0 |
| `iana-time-zone-haiku` | `0.1.2` | MIT OR Apache-2.0 |
| `ico` | `0.5.0` | MIT |
| `icu_collections` | `2.2.0` | Unicode-3.0 |
| `icu_locale_core` | `2.2.0` | Unicode-3.0 |
| `icu_normalizer` | `2.2.0` | Unicode-3.0 |
| `icu_normalizer_data` | `2.2.0` | Unicode-3.0 |
| `icu_properties` | `2.2.0` | Unicode-3.0 |
| `icu_properties_data` | `2.2.0` | Unicode-3.0 |
| `icu_provider` | `2.2.0` | Unicode-3.0 |
| `ident_case` | `1.0.1` | MIT/Apache-2.0 |
| `idna` | `1.1.0` | MIT OR Apache-2.0 |
| `idna_adapter` | `1.2.2` | Apache-2.0 OR MIT |
| `indexmap` | `1.9.3` | Apache-2.0 OR MIT |
| `indexmap` | `2.14.0` | Apache-2.0 OR MIT |
| `infer` | `0.19.0` | MIT |
| `ipnet` | `2.12.0` | MIT OR Apache-2.0 |
| `itoa` | `1.0.18` | MIT OR Apache-2.0 |
| `javascriptcore-rs` | `1.1.2` | MIT |
| `javascriptcore-rs-sys` | `1.1.1` | MIT |
| `jni` | `0.21.1` | MIT/Apache-2.0 |
| `jni` | `0.22.4` | MIT OR Apache-2.0 |
| `jni-macros` | `0.22.4` | MIT OR Apache-2.0 |
| `jni-sys` | `0.3.1` | MIT OR Apache-2.0 |
| `jni-sys` | `0.4.1` | MIT OR Apache-2.0 |
| `jni-sys-macros` | `0.4.1` | MIT OR Apache-2.0 |
| `js-sys` | `0.3.103` | MIT OR Apache-2.0 |
| `json-patch` | `3.0.1` | MIT/Apache-2.0 |
| `jsonptr` | `0.6.3` | MIT OR Apache-2.0 |
| `keyboard-types` | `0.7.0` | MIT OR Apache-2.0 |
| `libappindicator` | `0.9.0` | Apache-2.0 OR MIT |
| `libappindicator-sys` | `0.9.0` | Apache-2.0 OR MIT |
| `libc` | `0.2.186` | MIT OR Apache-2.0 |
| `libdbus-sys` | `0.2.7` | Apache-2.0/MIT |
| `libloading` | `0.7.4` | ISC |
| `libredox` | `0.1.18` | MIT |
| `linux-raw-sys` | `0.12.1` | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `litemap` | `0.8.2` | Unicode-3.0 |
| `lock_api` | `0.4.14` | MIT OR Apache-2.0 |
| `log` | `0.4.33` | MIT OR Apache-2.0 |
| `markup5ever` | `0.38.0` | MIT OR Apache-2.0 |
| `memchr` | `2.8.3` | Unlicense OR MIT |
| `memoffset` | `0.9.1` | MIT |
| `mime` | `0.3.17` | MIT OR Apache-2.0 |
| `minisign-verify` | `0.2.5` | MIT |
| `miniz_oxide` | `0.8.9` | MIT OR Zlib OR Apache-2.0 |
| `mio` | `1.2.2` | MIT |
| `muda` | `0.19.3` | Apache-2.0 OR MIT |
| `ndk` | `0.9.0` | MIT OR Apache-2.0 |
| `ndk-sys` | `0.6.0+11769913` | MIT OR Apache-2.0 |
| `new_debug_unreachable` | `1.0.6` | MIT |
| `num-conv` | `0.2.2` | MIT OR Apache-2.0 |
| `num-traits` | `0.2.19` | MIT OR Apache-2.0 |
| `num_enum` | `0.7.6` | BSD-3-Clause OR MIT OR Apache-2.0 |
| `num_enum_derive` | `0.7.6` | BSD-3-Clause OR MIT OR Apache-2.0 |
| `objc2` | `0.6.4` | MIT |
| `objc2-app-kit` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-cloud-kit` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-core-data` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-core-foundation` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-core-graphics` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-core-image` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-core-location` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-core-text` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-encode` | `4.1.0` | MIT |
| `objc2-exception-helper` | `0.1.1` | Zlib OR Apache-2.0 OR MIT |
| `objc2-foundation` | `0.3.2` | MIT |
| `objc2-io-surface` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-osa-kit` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-quartz-core` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-ui-kit` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-user-notifications` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `objc2-web-kit` | `0.3.2` | Zlib OR Apache-2.0 OR MIT |
| `once_cell` | `1.21.4` | MIT OR Apache-2.0 |
| `openssl-probe` | `0.2.1` | MIT OR Apache-2.0 |
| `option-ext` | `0.2.0` | MPL-2.0 |
| `ordered-stream` | `0.2.0` | MIT OR Apache-2.0 |
| `osakit` | `0.3.1` | MIT OR Apache-2.0 |
| `pango` | `0.18.3` | MIT |
| `pango-sys` | `0.18.0` | MIT |
| `parking` | `2.2.1` | Apache-2.0 OR MIT |
| `parking_lot` | `0.12.5` | MIT OR Apache-2.0 |
| `parking_lot_core` | `0.9.12` | MIT OR Apache-2.0 |
| `percent-encoding` | `2.3.2` | MIT OR Apache-2.0 |
| `phf` | `0.13.1` | MIT |
| `phf_codegen` | `0.13.1` | MIT |
| `phf_generator` | `0.13.1` | MIT |
| `phf_macros` | `0.13.1` | MIT |
| `phf_shared` | `0.13.1` | MIT |
| `pin-project-lite` | `0.2.17` | Apache-2.0 OR MIT |
| `piper` | `0.2.5` | MIT OR Apache-2.0 |
| `pkg-config` | `0.3.33` | MIT OR Apache-2.0 |
| `plist` | `1.10.0` | MIT |
| `png` | `0.17.16` | MIT OR Apache-2.0 |
| `png` | `0.18.1` | MIT OR Apache-2.0 |
| `polling` | `3.11.0` | Apache-2.0 OR MIT |
| `pollster` | `0.4.0` | Apache-2.0/MIT |
| `potential_utf` | `0.1.5` | Unicode-3.0 |
| `powerfmt` | `0.2.0` | MIT OR Apache-2.0 |
| `ppv-lite86` | `0.2.21` | MIT OR Apache-2.0 |
| `precomputed-hash` | `0.1.1` | MIT |
| `proc-macro-crate` | `1.3.1` | MIT OR Apache-2.0 |
| `proc-macro-crate` | `2.0.2` | MIT OR Apache-2.0 |
| `proc-macro-crate` | `3.5.0` | MIT OR Apache-2.0 |
| `proc-macro-error` | `1.0.4` | MIT OR Apache-2.0 |
| `proc-macro-error-attr` | `1.0.4` | MIT OR Apache-2.0 |
| `proc-macro2` | `1.0.107` | MIT OR Apache-2.0 |
| `quick-xml` | `0.39.4` | MIT |
| `quick-xml` | `0.41.0` | MIT |
| `quote` | `1.0.47` | MIT OR Apache-2.0 |
| `r-efi` | `5.3.0` | MIT OR Apache-2.0 OR LGPL-2.1-or-later |
| `r-efi` | `6.0.0` | MIT OR Apache-2.0 OR LGPL-2.1-or-later |
| `rand` | `0.9.5` | MIT OR Apache-2.0 |
| `rand_chacha` | `0.9.0` | MIT OR Apache-2.0 |
| `rand_core` | `0.9.5` | MIT OR Apache-2.0 |
| `raw-window-handle` | `0.6.2` | MIT OR Apache-2.0 OR Zlib |
| `redox_syscall` | `0.5.18` | MIT |
| `redox_users` | `0.5.2` | MIT |
| `ref-cast` | `1.0.26` | MIT OR Apache-2.0 |
| `ref-cast-impl` | `1.0.26` | MIT OR Apache-2.0 |
| `regex` | `1.13.1` | MIT OR Apache-2.0 |
| `regex-automata` | `0.4.16` | MIT OR Apache-2.0 |
| `regex-syntax` | `0.8.11` | MIT OR Apache-2.0 |
| `reqwest` | `0.13.4` | MIT OR Apache-2.0 |
| `rfd` | `0.15.4` | MIT |
| `ring` | `0.17.14` | Apache-2.0 AND ISC |
| `rustc-hash` | `2.1.3` | Apache-2.0 OR MIT |
| `rustc_version` | `0.4.1` | MIT OR Apache-2.0 |
| `rustix` | `1.1.4` | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `rustls` | `0.23.42` | Apache-2.0 OR ISC OR MIT |
| `rustls-native-certs` | `0.8.4` | Apache-2.0 OR ISC OR MIT |
| `rustls-pki-types` | `1.15.0` | MIT OR Apache-2.0 |
| `rustls-platform-verifier` | `0.7.0` | MIT OR Apache-2.0 |
| `rustls-platform-verifier-android` | `0.1.1` | MIT OR Apache-2.0 |
| `rustls-webpki` | `0.103.13` | ISC |
| `rustversion` | `1.0.23` | MIT OR Apache-2.0 |
| `same-file` | `1.0.6` | Unlicense/MIT |
| `schannel` | `0.1.29` | MIT |
| `schemars` | `0.8.22` | MIT |
| `schemars` | `0.9.0` | MIT |
| `schemars` | `1.2.1` | MIT |
| `schemars_derive` | `0.8.22` | MIT |
| `scoped-tls` | `1.0.1` | MIT/Apache-2.0 |
| `scopeguard` | `1.2.0` | MIT OR Apache-2.0 |
| `security-framework` | `3.7.0` | MIT OR Apache-2.0 |
| `security-framework-sys` | `2.17.0` | MIT OR Apache-2.0 |
| `selectors` | `0.36.1` | MPL-2.0 |
| `semver` | `1.0.28` | MIT OR Apache-2.0 |
| `serde` | `1.0.229` | MIT OR Apache-2.0 |
| `serde-untagged` | `0.1.9` | MIT OR Apache-2.0 |
| `serde_core` | `1.0.229` | MIT OR Apache-2.0 |
| `serde_derive` | `1.0.229` | MIT OR Apache-2.0 |
| `serde_derive_internals` | `0.29.1` | MIT OR Apache-2.0 |
| `serde_json` | `1.0.151` | MIT OR Apache-2.0 |
| `serde_repr` | `0.1.21` | MIT OR Apache-2.0 |
| `serde_spanned` | `0.6.9` | MIT OR Apache-2.0 |
| `serde_spanned` | `1.1.1` | MIT OR Apache-2.0 |
| `serde_with` | `3.21.0` | MIT OR Apache-2.0 |
| `serde_with_macros` | `3.21.0` | MIT OR Apache-2.0 |
| `serialize-to-javascript` | `0.1.2` | MIT OR Apache-2.0 |
| `serialize-to-javascript-impl` | `0.1.2` | MIT OR Apache-2.0 |
| `servo_arc` | `0.4.3` | MIT OR Apache-2.0 |
| `sha2` | `0.10.9` | MIT OR Apache-2.0 |
| `shlex` | `2.0.1` | MIT OR Apache-2.0 |
| `signal-hook-registry` | `1.4.8` | MIT OR Apache-2.0 |
| `simd-adler32` | `0.3.10` | MIT |
| `simd_cesu8` | `1.2.0` | Apache-2.0 OR MIT |
| `simdutf8` | `0.1.5` | MIT OR Apache-2.0 |
| `siphasher` | `1.0.3` | MIT/Apache-2.0 |
| `slab` | `0.4.12` | MIT |
| `smallvec` | `1.15.2` | MIT OR Apache-2.0 |
| `socket2` | `0.6.5` | MIT OR Apache-2.0 |
| `softbuffer` | `0.4.8` | MIT OR Apache-2.0 |
| `soup3` | `0.5.0` | MIT |
| `soup3-sys` | `0.5.0` | MIT |
| `stable_deref_trait` | `1.2.1` | MIT OR Apache-2.0 |
| `string_cache` | `0.9.0` | MIT OR Apache-2.0 |
| `string_cache_codegen` | `0.6.1` | MIT OR Apache-2.0 |
| `strsim` | `0.11.1` | MIT |
| `subtle` | `2.6.1` | BSD-3-Clause |
| `swift-rs` | `1.0.7` | MIT OR Apache-2.0 |
| `syn` | `1.0.109` | MIT OR Apache-2.0 |
| `syn` | `2.0.119` | MIT OR Apache-2.0 |
| `syn` | `3.0.2` | MIT OR Apache-2.0 |
| `sync_wrapper` | `1.0.2` | Apache-2.0 |
| `synstructure` | `0.13.2` | MIT |
| `system-deps` | `6.2.2` | MIT OR Apache-2.0 |
| `tao` | `0.35.3` | Apache-2.0 |
| `tao-macros` | `0.1.3` | MIT OR Apache-2.0 |
| `tar` | `0.4.46` | MIT OR Apache-2.0 |
| `target-lexicon` | `0.12.16` | Apache-2.0 WITH LLVM-exception |
| `tauri` | `2.11.5` | Apache-2.0 OR MIT |
| `tauri-build` | `2.6.3` | Apache-2.0 OR MIT |
| `tauri-codegen` | `2.6.3` | Apache-2.0 OR MIT |
| `tauri-macros` | `2.6.3` | Apache-2.0 OR MIT |
| `tauri-plugin` | `2.6.3` | Apache-2.0 OR MIT |
| `tauri-plugin-single-instance` | `2.4.3` | Apache-2.0 OR MIT |
| `tauri-plugin-updater` | `2.10.1` | Apache-2.0 OR MIT |
| `tauri-runtime` | `2.11.3` | Apache-2.0 OR MIT |
| `tauri-runtime-wry` | `2.11.4` | Apache-2.0 OR MIT |
| `tauri-utils` | `2.9.3` | Apache-2.0 OR MIT |
| `tauri-winres` | `0.3.6` | MIT |
| `tempfile` | `3.27.0` | MIT OR Apache-2.0 |
| `tendril` | `0.5.1` | MIT OR Apache-2.0 |
| `thiserror` | `1.0.69` | MIT OR Apache-2.0 |
| `thiserror` | `2.0.19` | MIT OR Apache-2.0 |
| `thiserror-impl` | `1.0.69` | MIT OR Apache-2.0 |
| `thiserror-impl` | `2.0.19` | MIT OR Apache-2.0 |
| `time` | `0.3.54` | MIT OR Apache-2.0 |
| `time-core` | `0.1.9` | MIT OR Apache-2.0 |
| `time-macros` | `0.2.32` | MIT OR Apache-2.0 |
| `tinystr` | `0.8.3` | Unicode-3.0 |
| `tinyvec` | `1.12.0` | Zlib OR Apache-2.0 OR MIT |
| `tinyvec_macros` | `0.1.1` | MIT OR Apache-2.0 OR Zlib |
| `tokio` | `1.53.0` | MIT |
| `tokio-rustls` | `0.26.4` | MIT OR Apache-2.0 |
| `tokio-util` | `0.7.18` | MIT |
| `toml` | `0.8.2` | MIT OR Apache-2.0 |
| `toml` | `0.9.12+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml` | `1.1.3+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml_datetime` | `0.6.3` | MIT OR Apache-2.0 |
| `toml_datetime` | `0.7.5+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml_datetime` | `1.1.1+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml_edit` | `0.19.15` | MIT OR Apache-2.0 |
| `toml_edit` | `0.20.2` | MIT OR Apache-2.0 |
| `toml_edit` | `0.25.13+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml_parser` | `1.1.2+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml_writer` | `1.1.2+spec-1.1.0` | MIT OR Apache-2.0 |
| `tower` | `0.5.3` | MIT |
| `tower-http` | `0.6.11` | MIT |
| `tower-layer` | `0.3.3` | MIT |
| `tower-service` | `0.3.3` | MIT |
| `tracing` | `0.1.44` | MIT |
| `tracing-attributes` | `0.1.31` | MIT |
| `tracing-core` | `0.1.36` | MIT |
| `tray-icon` | `0.24.1` | MIT OR Apache-2.0 |
| `try-lock` | `0.2.5` | MIT |
| `typeid` | `1.0.3` | MIT OR Apache-2.0 |
| `typenum` | `1.20.1` | MIT OR Apache-2.0 |
| `uds_windows` | `1.2.1` | MIT |
| `unic-char-property` | `0.9.0` | MIT/Apache-2.0 |
| `unic-char-range` | `0.9.0` | MIT/Apache-2.0 |
| `unic-common` | `0.9.0` | MIT/Apache-2.0 |
| `unic-ucd-ident` | `0.9.0` | MIT/Apache-2.0 |
| `unic-ucd-version` | `0.9.0` | MIT/Apache-2.0 |
| `unicode-ident` | `1.0.24` | (MIT OR Apache-2.0) AND Unicode-3.0 |
| `unicode-segmentation` | `1.13.3` | MIT OR Apache-2.0 |
| `untrusted` | `0.9.0` | ISC |
| `url` | `2.5.8` | MIT OR Apache-2.0 |
| `urlencoding` | `2.1.3` | MIT |
| `urlpattern` | `0.3.0` | MIT |
| `utf8_iter` | `1.0.4` | Apache-2.0 OR MIT |
| `uuid` | `1.24.0` | Apache-2.0 OR MIT |
| `version-compare` | `0.2.1` | MIT |
| `version_check` | `0.9.5` | MIT/Apache-2.0 |
| `vswhom` | `0.1.0` | MIT |
| `vswhom-sys` | `0.1.3` | MIT |
| `walkdir` | `2.5.0` | Unlicense/MIT |
| `want` | `0.3.1` | MIT |
| `wasi` | `0.11.1+wasi-snapshot-preview1` | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `wasip2` | `1.0.4+wasi-0.2.12` | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `wasm-bindgen` | `0.2.126` | MIT OR Apache-2.0 |
| `wasm-bindgen-futures` | `0.4.76` | MIT OR Apache-2.0 |
| `wasm-bindgen-macro` | `0.2.126` | MIT OR Apache-2.0 |
| `wasm-bindgen-macro-support` | `0.2.126` | MIT OR Apache-2.0 |
| `wasm-bindgen-shared` | `0.2.126` | MIT OR Apache-2.0 |
| `wasm-streams` | `0.5.0` | MIT OR Apache-2.0 |
| `wayland-backend` | `0.3.15` | MIT |
| `wayland-client` | `0.31.14` | MIT |
| `wayland-protocols` | `0.32.13` | MIT |
| `wayland-scanner` | `0.31.10` | MIT |
| `wayland-sys` | `0.31.11` | MIT |
| `web-sys` | `0.3.103` | MIT OR Apache-2.0 |
| `web_atoms` | `0.2.5` | MIT OR Apache-2.0 |
| `webkit2gtk` | `2.0.2` | MIT |
| `webkit2gtk-sys` | `2.0.2` | MIT |
| `webpki-root-certs` | `1.0.9` | CDLA-Permissive-2.0 |
| `webview2-com` | `0.38.2` | MIT |
| `webview2-com-macros` | `0.8.1` | MIT |
| `webview2-com-sys` | `0.38.2` | MIT |
| `winapi` | `0.3.9` | MIT/Apache-2.0 |
| `winapi-i686-pc-windows-gnu` | `0.4.0` | MIT/Apache-2.0 |
| `winapi-util` | `0.1.11` | Unlicense OR MIT |
| `winapi-x86_64-pc-windows-gnu` | `0.4.0` | MIT/Apache-2.0 |
| `window-vibrancy` | `0.6.0` | Apache-2.0 OR MIT |
| `windows` | `0.61.3` | MIT OR Apache-2.0 |
| `windows-collections` | `0.2.0` | MIT OR Apache-2.0 |
| `windows-core` | `0.61.2` | MIT OR Apache-2.0 |
| `windows-core` | `0.62.2` | MIT OR Apache-2.0 |
| `windows-future` | `0.2.1` | MIT OR Apache-2.0 |
| `windows-implement` | `0.60.2` | MIT OR Apache-2.0 |
| `windows-interface` | `0.59.3` | MIT OR Apache-2.0 |
| `windows-link` | `0.1.3` | MIT OR Apache-2.0 |
| `windows-link` | `0.2.1` | MIT OR Apache-2.0 |
| `windows-numerics` | `0.2.0` | MIT OR Apache-2.0 |
| `windows-result` | `0.3.4` | MIT OR Apache-2.0 |
| `windows-result` | `0.4.1` | MIT OR Apache-2.0 |
| `windows-strings` | `0.4.2` | MIT OR Apache-2.0 |
| `windows-strings` | `0.5.1` | MIT OR Apache-2.0 |
| `windows-sys` | `0.45.0` | MIT OR Apache-2.0 |
| `windows-sys` | `0.52.0` | MIT OR Apache-2.0 |
| `windows-sys` | `0.59.0` | MIT OR Apache-2.0 |
| `windows-sys` | `0.60.2` | MIT OR Apache-2.0 |
| `windows-sys` | `0.61.2` | MIT OR Apache-2.0 |
| `windows-targets` | `0.42.2` | MIT OR Apache-2.0 |
| `windows-targets` | `0.52.6` | MIT OR Apache-2.0 |
| `windows-targets` | `0.53.5` | MIT OR Apache-2.0 |
| `windows-threading` | `0.1.0` | MIT OR Apache-2.0 |
| `windows-version` | `0.1.7` | MIT OR Apache-2.0 |
| `windows_aarch64_gnullvm` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_aarch64_gnullvm` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_aarch64_gnullvm` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_aarch64_msvc` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_aarch64_msvc` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_aarch64_msvc` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_i686_gnu` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_i686_gnu` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_i686_gnu` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_i686_gnullvm` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_i686_gnullvm` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_i686_msvc` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_i686_msvc` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_i686_msvc` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_x86_64_gnu` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_x86_64_gnu` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_x86_64_gnu` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_x86_64_gnullvm` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_x86_64_gnullvm` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_x86_64_gnullvm` | `0.53.1` | MIT OR Apache-2.0 |
| `windows_x86_64_msvc` | `0.42.2` | MIT OR Apache-2.0 |
| `windows_x86_64_msvc` | `0.52.6` | MIT OR Apache-2.0 |
| `windows_x86_64_msvc` | `0.53.1` | MIT OR Apache-2.0 |
| `winnow` | `0.5.40` | MIT |
| `winnow` | `0.7.15` | MIT |
| `winnow` | `1.0.4` | MIT |
| `winreg` | `0.55.0` | MIT |
| `wit-bindgen` | `0.57.1` | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `writeable` | `0.6.3` | Unicode-3.0 |
| `wry` | `0.55.1` | Apache-2.0 OR MIT |
| `x11` | `2.21.0` | MIT |
| `x11-dl` | `2.21.0` | MIT |
| `xattr` | `1.6.1` | MIT OR Apache-2.0 |
| `yoke` | `0.8.3` | Unicode-3.0 |
| `yoke-derive` | `0.8.2` | Unicode-3.0 |
| `zbus` | `5.18.0` | MIT |
| `zbus_macros` | `5.18.0` | MIT |
| `zbus_names` | `4.3.4` | MIT |
| `zerocopy` | `0.8.55` | BSD-2-Clause OR Apache-2.0 OR MIT |
| `zerocopy-derive` | `0.8.55` | BSD-2-Clause OR Apache-2.0 OR MIT |
| `zerofrom` | `0.1.8` | Unicode-3.0 |
| `zerofrom-derive` | `0.1.7` | Unicode-3.0 |
| `zeroize` | `1.9.0` | Apache-2.0 OR MIT |
| `zerotrie` | `0.2.4` | Unicode-3.0 |
| `zerovec` | `0.11.6` | Unicode-3.0 |
| `zerovec-derive` | `0.11.3` | Unicode-3.0 |
| `zip` | `4.6.1` | MIT |
| `zmij` | `1.0.23` | MIT |
| `zvariant` | `5.13.1` | MIT |
| `zvariant_derive` | `5.13.1` | MIT |
| `zvariant_utils` | `3.5.0` | MIT |
