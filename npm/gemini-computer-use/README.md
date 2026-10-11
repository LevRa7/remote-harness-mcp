# gemini-computer-use

**This package is an alias.** The project was renamed to
[**remote-harness-mcp**](https://www.npmjs.com/package/remote-harness-mcp), and
this name is kept so existing installs, scripts and documentation keep working.

```bash
# both commands do the same thing:
npx gemini-computer-use
npx remote-harness-mcp

# both install the same node:
npm install -g gemini-computer-use
npm install -g remote-harness-mcp
```

`gemini-computer-use` contains no code of its own: it depends on
`remote-harness-mcp` and forwards every argument to that package's CLI. An
existing `gemini-computer-use` install therefore keeps receiving the current
version instead of a frozen copy.

* Repository: <https://github.com/LevRa7/remote-harness-mcp>
* What the CLI can do (`doctor`, `restart`, `update`, installer bootstrap):
  see the [main README](https://github.com/LevRa7/remote-harness-mcp#readme).
* Why the rename happened, and what stays compatible:
  [docs/UPDATES.md § 0](https://github.com/LevRa7/remote-harness-mcp/blob/main/docs/UPDATES.md).
