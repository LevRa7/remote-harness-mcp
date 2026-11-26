# MCP service with administrator rights (Windows)

The default Windows install is deliberately per-user: a Startup entry and two
per-user scheduled tasks, no elevation, no UAC prompt. That is the right default
for a desktop - the node then lives in the signed-in user's session, where the
desktop tools exist - and it is the wrong shape for a machine that has to expose
administrator-level tools to Gemini with nobody signed in.

`install.ps1 -AdminService` (and the "MCP service with administrator rights"
switch of the visual installer) installs the same node as a **machine service**:

| | per-user install (default) | administrator service (`-AdminService`) |
| :--- | :--- | :--- |
| Autostart | `%APPDATA%\...\Startup\rhm-agent.vbs` | scheduled task, trigger **At startup** |
| Account | the logged-on user | **SYSTEM** |
| Token | user's own | SYSTEM's, run level **Highest** |
| Elevation | none | one UAC prompt during installation |
| Configuration | `%USERPROFILE%\.config\remote-harness-mcp` | `%ProgramData%\remote-harness-mcp` |
| Works without a logged-on user | no | **yes** |
| Watchdog / updater tasks | yes (per-user) | no — the task restarts itself |
| Tray icon | yes | no (there is no user session to show it in) |
| `screen_capture`, clipboard | yes | **no** - session 0 has no desktop |

The last row is the one that surprises people: a Windows service runs in
**session 0**, the non-interactive session, and GDI cannot read a desktop that no
one is attached to. `screen_capture` answers `available: false` with
`"reason": "the node runs as a Windows service in session 0 (SESSIONNAME=Services)..."`
and a `fix` that names this document. If a person sits at the machine, the
per-user install is the right one.

## Installing

Console (the domain must be resolvable):

```powershell
.\install.ps1 -AdminService -Gateway mesh.example.test -User my-node
```

The node part runs unelevated. Elevation is requested **at the service step**: the
installer prints that the node is ready and then starts an elevated child through
`Start-Process -Verb RunAs` with `-ServiceOnly`, which registers and starts the
SYSTEM task and nothing else. So starting the installer without administrator
rights does not block this variant - it simply prompts later, when the privileged
operation actually happens. Refusing the prompt leaves the node installed and the
service absent, and says so.

Visual installer: pick the gateway variant, then tick **Install as a Windows service with administrator rights**
page, fill in the node name and the shared domain, press Install. The wizard runs
the same command through one pipeline step (`install.ps1 ... -AdminService`), so
registration, the URL contract and the error reporting stay in one place.

Already-elevated shells register the task in-process, without a second process or a prompt.

Both paths are opt-in: the console needs `-AdminService` and the wizard's checkbox
starts cleared, because the service trades the desktop tools for boot-time
autostart.

## Funnel variant

The same switch works with **Public URL via Tailscale Funnel**: the task then
hosts `core.server` (the standalone MCP server on loopback) instead of
`core.agent`, keeps its configuration in `C:\ProgramData\remote-harness-mcp`
and starts at boot. Funnel itself is configured machine-wide by `tailscaled`, so
the published `https://<node>.<tailnet>.ts.net` URL keeps working without a
logged-on user. `service.env` carries the choice:

```ini
MESH_SERVICE_MODE=server
MESH_SERVICE_PORT=8096
```


## What gets registered

```
Task:      RemoteHarnessMCPService
Action:    powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden
           -NonInteractive -File <payload>\ops\windows\rhm-service.ps1
Trigger:   At startup
Principal: SYSTEM, run level Highest, logon type ServiceAccount
Settings:  restart every 1 minute, up to 999 times; no execution time limit
```

`ops/windows/rhm-service.ps1` is the service body: it pins the interpreter the
installer already proved usable (never a PATH lookup — SYSTEM's PATH is not the
operator's), exports `MESH_CONFIG_DIR`, runs `python -u -m core.agent` and
restarts it after 5 seconds if it exits. Task Scheduler is the host because a
Python process is not a service binary; `sc.exe create` would need a wrapper.

## Configuration

Everything lives in `%ProgramData%\remote-harness-mcp`:

| file | purpose |
| --- | --- |
| `agent.env` | `MESH_GATEWAY`, `MESH_USER`, `MESH_TOKEN` |
| `domain.env` | the gateway the node publishes links on |
| `service.env` | the pinned interpreter (`MESH_PYTHON`) and `MESH_CONFIG_DIR` |
| `agent.log` | the agent's own output, one marker line per restart |

`core/paths.py` resolves `MESH_CONFIG_DIR` before `~/.config`, which is what makes
the SYSTEM task read the same `agent.env` the installer wrote.

## Controlling it

```powershell
Get-ScheduledTask   -TaskName RemoteHarnessMCPService
Start-ScheduledTask -TaskName RemoteHarnessMCPService
Stop-ScheduledTask  -TaskName RemoteHarnessMCPService
Get-Content "$env:ProgramData\remote-harness-mcp\agent.log" -Tail 50
```

## Security notes

* The service token is a machine-wide secret: SYSTEM can read every file on the
  host, so this variant is for a machine that is dedicated to being a node, not
  for a shared workstation.
* Tools run with SYSTEM's token. The read-only/approval policies in
  `core/policies.py` still apply, but "administrator rights" here really means
  administrator rights.
* Removing the variant: `Unregister-ScheduledTask -TaskName RemoteHarnessMCPService`,
  then delete `%ProgramData%\remote-harness-mcp`. Re-run the plain installer to go
  back to the per-user shape.

## Upgrading from the per-user variant

`-AdminService` retires `rhm-agent.vbs`, the tray `Run` value and the
`RemoteHarnessMCPWatchdog`/`RemoteHarnessMCPUpdater` tasks before registering the
service. Two agents against one node would otherwise register the same name twice
and fight over the tunnel.
