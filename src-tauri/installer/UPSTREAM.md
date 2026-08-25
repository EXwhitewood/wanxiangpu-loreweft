# Tauri NSIS template provenance

The installer template and the two base language files in this directory are
vendored from the Tauri project and used under the MIT license included at
`third-party/TAURI-LICENSE-MIT`.

- Repository: <https://github.com/tauri-apps/tauri>
- Tag: `tauri-bundler-v2.9.4`
- Commit: `8909f221d1515955fc843808032bdc5d62209c96`
- Template source: `crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi`
- Upstream template SHA-256: `20F4ECC730DEFB71F1342EAEAEC4021DF13BE3D843ABBA0EFFE88EA5835FA079`

Loreweft changes only the interactive upgrade contract:

1. an upgrade defaults to in-place replacement instead of removing the old
   installation first;
2. focus follows the selected upgrade option;
3. an explicit clean reinstall explains the separate uninstaller window before
   it opens and identifies the returning installer afterward;
4. English and Simplified Chinese messages describe the same behavior and the
   fail-closed preinstall recovery state accurately.

`installer-template-contract.json` pins the toolchain and hashes. The installer
contract test reconstructs the unmodified upstream inputs locally and checks
their pinned SHA-256 values, so an unreviewed template drift fails the build.
