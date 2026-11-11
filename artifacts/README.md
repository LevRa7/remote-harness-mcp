# Evidence artifacts

Each WBS row writes its proof here, by `VERIFICATION_PROTOCOL.md`:

```
artifacts/<WBS-ID>/<TEST-ID>.log     # command, output, exit code
artifacts/<WBS-ID>/red.log           # failing run before implementation
artifacts/<WBS-ID>/green.log         # passing run after
artifacts/<WBS-ID>/manual-<date>.md  # checklist + screenshot path
```

The directory is **not committed** (logs may contain test data). CI uploads it as a build artifact with 30/90-day retention; `evidence.json` records commit, command, exit code, sha256 and reviewer. The plan only references the paths — a missing artifact means the row is not done.
