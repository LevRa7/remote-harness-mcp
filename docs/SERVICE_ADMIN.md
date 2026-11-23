# MCP service with administrator rights (Windows)

The default Windows install is deliberately per-user: a Startup entry and two
per-user scheduled tasks, no elevation, no UAC prompt. That is the right default
for a desktop, and it is the wrong shape for a machine that has to expose
administrator-level tools to Gemini.

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

## Installing

Console (the domain must be resolvable):

```powershell
.\install.ps1 -AdminService -Gateway mesh.example.test -User my-node
```

The script detects that it is not elevated and re-runs itself through
`Start-Process -Verb RunAs`, so the UAC prompt is the only interaction. Because an
elevated process cannot have its stdout redirected, the child writes a transcript
and the parent prints it — the output you see is the real installer output, and
the exit code is the child's.

Visual installer: pick the gateway variant, then tick **Install as a Windows service with administrator rights**
page, fill in the node name and the shared domain, press Install. The wizard runs
the same command through one pipeline step (`install.ps1 ... -AdminService`), so
registration, the URL contract and the error reporting stay in one place.

Already-elevated shells skip the relaunch entirely.

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
