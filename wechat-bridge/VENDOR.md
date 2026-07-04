# Vendored CLI-WeChat-Bridge

Local package found:

```text
C:\nvm4w\nodejs\node_modules\cli-wechat-bridge
```

Package metadata:

```text
name: cli-wechat-bridge
version: 1.1.1
repository: https://github.com/UNLINEARITY/CLI-WeChat-Bridge
license: AGPL-3.0-or-later
```

Copied into this repository:

```text
wechat-bridge/vendor/cli-wechat-bridge/
```

The vendored copy intentionally includes only:

```text
bin/
dist/
README.md
LICENSE.txt
package.json
```

It does not include `node_modules/`. Use the globally installed command for normal local runs, or install dependencies inside the vendored copy if a fully self-contained copy is required later.

## Lucas Hook

Current UI startup path uses `wechat-bridge/scripts/start-lucas-wechat-gateway-bridge.ps1`, which reuses the vendored WeChat transport but sends all text to Lucas Chat Gateway. The package hook below is kept as historical compatibility context.

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

For this merge-test repo, `LUCAS_LINK_PROJECT_ROOT` must be:

```text
C:\Users\pppppqr\Desktop\Lucas-Knowledge-System-MergeTest\intake-control
```

Do not point it at the merge-test root because `tools/wechat_link_entry.py` lives inside `intake-control/`.
