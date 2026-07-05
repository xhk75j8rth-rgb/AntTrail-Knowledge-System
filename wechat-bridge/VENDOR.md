# Vendored CLI-WeChat-Bridge

The GitHub source release does not include copied third-party runtime packages under:

```text
wechat-bridge/vendor/
```

Package metadata:

```text
name: cli-wechat-bridge
version: 1.1.1
repository: https://github.com/UNLINEARITY/CLI-WeChat-Bridge
license: AGPL-3.0-or-later
```

For local WeChat bridge experiments, install or copy a compatible package into the expected vendored location:

```text
wechat-bridge/vendor/cli-wechat-bridge/
```

Some release folders may omit the vendored copy. The Lucas gateway bridge now resolves the runtime in this order:

1. `LUCAS_CLI_WECHAT_BRIDGE_ROOT`
2. `wechat-bridge/vendor/cli-wechat-bridge/`
3. the globally installed `cli-wechat-bridge`

When present, the vendored copy intentionally includes only:

```text
bin/
dist/
README.md
LICENSE.txt
package.json
```

This directory is ignored by git. Do not commit the copied package, `node_modules/`, login sessions, QR codes, cookies, or account data.

At minimum, the current Lucas Gateway bridge expects:

```text
wechat-bridge/vendor/cli-wechat-bridge/dist/wechat/setup.js
wechat-bridge/vendor/cli-wechat-bridge/dist/wechat/wechat-transport.js
```

Use your own external bridge adapter if you do not want to copy a package into `wechat-bridge/vendor/`.

## Lucas Hook

Current UI startup path uses `wechat-bridge/scripts/start-lucas-wechat-gateway-bridge.ps1`, which reuses the resolved WeChat transport but sends all text to Lucas Chat Gateway. The package hook below is kept as historical compatibility context.

The package already has a Lucas-specific link hook in:

```text
vendor/cli-wechat-bridge/dist/bridge/wechat-bridge.js
```

Relevant behavior:

- It detects messages matching `http(s)://` or Douyin short-link patterns.
- It replies to WeChat that link ingestion has started.
- It runs:

```text
python %LUCAS_LINK_PROJECT_ROOT%\tools\wechat_link_entry.py --timeout-sec %LUCAS_LINK_ENTRY_RUNNER_TIMEOUT_SEC%
```

- It sends the original WeChat message to that process via stdin.
- It sends stdout back to WeChat as the final reply.

For a source clone, `LUCAS_LINK_PROJECT_ROOT` should point to the current repository's intake project, for example:

```text
C:\path\to\AntTrail-Knowledge-System\intake-control
```

Do not point it at the merge-test root because `tools/wechat_link_entry.py` lives inside `intake-control/`.
