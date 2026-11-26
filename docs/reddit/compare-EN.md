# Remote Harness MCP v0.7.1 vs Easy MCP 2.3.1

One machine, one folder (323 files / 8.5 MB), loopback HTTP, median of 5–7 runs. Windows 11, Python 3.12, Node 22.

| Metric | Remote Harness MCP v0.7.1 | Easy MCP 2.3.1 |
| :--- | :--- | :--- |
| Language / size | Python: 63 modules, 23.6k LOC + 89 test files (17.6k LOC) | JavaScript: 1 file, 2,055 LOC, no tests |
| Start | `python -m core.server --folder . --no-shell`, no admin; full mode = installer + Tailscale Funnel | `npx -y <tar.gz>` — one command |
| Tools in `tools/list` | 34 / 17.5 KB (`core` profile: 22 / 10.6 KB) | 17 / 4.9 KB |
| Read whole file (532 lines) | 6 ms / 22.5 KB | 2 ms / 22.3 KB |
| **Read a line range** | ✅ 15 ms / 4.6 KB | ❌ whole file only, 22.3 KB |
| **Find files by name (glob)** | ✅ 69 ms, `**/*.py` | ❌ none, only one-level `list_directory` |
| **Grep file contents** | ✅ regex + `glob` + context: 6 ms (JSON 30.6 KB / compact 17.0 KB) | substring only: 75 ms (13.5 KB) |
| Full folder scan | 73 ms | 77 ms |
| **8 reads at once** | 1 call, 10 ms, 17.8 KB | 8 calls, 12 ms, 178 KB |
| Write / edit | atomic + sha256 + `replace_all`: 6 ms | plain: 2 ms |
| Binary files | refused, hints `read_media` | refused |
| **Share files with people** | ✅ public HTTPS links | ❌ MCP endpoint only |
| Folder jail | writes only (reads outside allowed) | reads and writes |
| Shell | on, disable with `--no-shell` | off by default |
| Multi-machine / phone | ✅ mesh nodes, Android/Termux | ❌ one folder, one machine |
| git / fetch_url | ✅ / ✅ (SSRF guarded; Python TLS failed on this host) | ✅ / ✅ |
| Empty-call overhead | 2.9 ms | 1.4 ms |
| Idle RAM | 47 MB | 34 MB |

**Bottom line.** Easy MCP wins on onboarding and schema weight; Remote Harness MCP wins on line ranges, glob, batched calls, sharing and multi-node.
