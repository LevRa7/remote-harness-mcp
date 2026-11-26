# Remote Harness MCP v0.7.2 vs Easy MCP 2.3.1

One machine, one folder (330 files / 8.6 MB), loopback HTTP, **minimum of 7–9 runs**. Windows 11, Python 3.12, Node 22.

| Metric | Remote Harness MCP v0.7.2 | Easy MCP 2.3.1 |
| :--- | :--- | :--- |
| Language / size | Python: 63 modules, 23.6k LOC + 89 test files (17.6k LOC) | JavaScript: 1 file, 2,055 LOC, no tests |
| Start | `python -m core.server --folder . --no-shell`, no admin; full mode = installer + Tailscale Funnel | `npx -y <tar.gz>` — one command |
| Tools in `tools/list` | 34 / 17.5 KB (`core` profile: 22 / 10.6 KB) | 17 / 4.9 KB |
| Read whole file (532 lines) | 2.6 ms / 22.5 KB | 1.4 ms / 23.3 KB |
| **Read a line range** | ✅ 2.4 ms / 4.6 KB | ❌ whole file only, 23.3 KB |
| **Find files by name (glob)** | ✅ 59 ms, `**/*.py` (330 paths) | ❌ none, only one-level `list_directory` |
| **Grep file contents** | ✅ regex + `glob` + context: 6.0 ms (JSON 30.6 KB / compact 17.0 KB) | substring only: 75 ms (13.5 KB) |
| Full folder scan | 67 ms | 67 ms |
| **8 reads at once** | 1 call, 7.3 ms, 17.8 KB | 8 calls, 10.9 ms, 186.8 KB |
| Write 5 KB | 5.4 ms (atomic + sha256 + fsync) | 1.2 ms |
| Edit a snippet | 10.3 ms (write included, sha256 guard) | 1.6 ms |
| Binary files | refused, hints `read_media` (46 ms) | refused (16 ms) |
| **Share files with people** | ✅ public HTTPS links | ❌ MCP endpoint only |
| Folder jail | writes only (reads outside allowed) | reads and writes |
| Shell | on, disable with `--no-shell` | off by default |
| Multi-machine / phone | ✅ mesh nodes, Android/Termux | ❌ one folder, one machine |
| git / fetch_url | ✅ / ✅ (SSRF; trust: bundle → certifi → system) | ✅ / ✅ |
| Empty-call overhead | 2.6 ms | 0.7 ms |
| Idle RAM | ~47 MB | ~34 MB |

**How to read it.** Easy's smaller milliseconds mean less work per call: flat text instead of JSON metadata, no line numbers, no pagination, no atomic `fsync` write, no policy checks. For a real task what matters is round trips and context size: 1 call instead of 8, 17.8 KB instead of 186.8 KB.
