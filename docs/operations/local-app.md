---
title: "Local application operations"
status: "current"
authoritative_source: "server launcher, settings, managed data service, and browser build"
verified: "2026-08-07"
audience: "local users and contributors"
maintenance: "hand-maintained"
concern: "local-operations"
---

# Local application operations

## Start and stop

From the repository root in PowerShell:

```powershell
.\scripts\bootstrap_windows.ps1
.\.venv\Scripts\quorune-server.exe
```

Quorune requires CPython 3.12.x. Bootstrap creates a local environment,
installs project and browser dependencies, and validates the runtime without
changing global `PATH`, elevating, or removing another Python. The server builds
the browser when needed, prepares card data, serves same-origin HTTP/WebSocket
and static assets, and prints the local URL.

The default never opens or focuses a browser. Open the printed URL yourself or
use `--open` as an explicit opt-in. Stop with `Ctrl+C`. Do not run two servers
against the same data root; on Windows an older process can keep the active
SQLite snapshot locked.

## Useful modes

```powershell
.\.venv\Scripts\quorune-server.exe --open
.\.venv\Scripts\quorune-server.exe --offline
$env:MTG_CARD_DB = "data/test-ci.sqlite3"
.\.venv\Scripts\quorune-server.exe --offline
```

`--offline` requires an existing usable database. `--no-build-browser` skips
the production client build. The legacy `--no-open` flag is a compatibility
alias for the safe default.

## Managed card data

Without `MTG_CARD_DB`, startup checks the Scryfall Oracle and rulings bulk
manifest, downloads the required archives to ignored storage, builds a pending
SQLite database, and activates it before the runtime becomes ready. A later
refresh never replaces the database underneath an active game. Records pin the
snapshot metadata hash; referenced old databases remain in the ignored snapshot
directory until no local record needs them.

Deck review can compare the active database with retained snapshots. For a
reviewed snapshot that the host maintains separately, store the unchanged
SQLite file under `data/card-snapshots/reviewed/<metadata-hash>.sqlite3` or under
`reviewed` in the configured `MTG_CARD_SNAPSHOT_DIR`. Use the snapshot's Game
Record metadata hash for its filename. Keep that file available for games that
use it. Managed refresh leaves this directory in place.

A network failure falls back to a usable existing database with a warning. A
first-run failure remains visible and can be retried. When Windows locks an
activation target, stop other server processes and restart. If no current
database exists, activation fails closed.

The browser receives projected card fields and constrained image routes, never
the bulk export. Archives, databases, retained snapshots, images, records, and
caches are local runtime data and must not be committed.

## Environment settings

| Setting | Purpose |
| --- | --- |
| `MTG_SERVER_DATA` | Control-plane database and Game Record root |
| `MTG_CARD_DB` | Use an explicit existing card database |
| `MTG_DATA_ROOT` | Managed card-data root |
| `MTG_BULK_DIR` | Managed bulk-download directory |
| `MTG_CARD_SNAPSHOT_DIR` | Record-pinned database retention directory |
| `MTG_IMAGE_CACHE` | Local image-cache directory |
| `MTG_CARD_IMAGE_DB` | Optional fallback SQLite catalog for card artwork when using compact card data |
| `MTG_WEB_DIST` | Built browser asset directory |
| `MTG_AUTO_UPDATE_CARDS` | Enable or disable managed refresh |
| `MTG_CARD_UPDATE_SECONDS` | Manifest-check interval |
| `MTG_ALLOWED_ORIGINS` | Additional development/deployment browser origins |
| `MTG_SECURE_COOKIES` | Require secure cookies behind HTTPS |

The server's own HTTP origin is accepted automatically for WebSocket upgrades;
do not repeat it in `MTG_ALLOWED_ORIGINS`. Restrict additional origins exactly.

## Inspect and recover

The setup panel reports readiness, warnings, and pending activation. Room and
game pages expose seat-safe lifecycle inspection. An owner administrative stop
is restart-safe and resumes the same pending decision. A rules, semantic,
fidelity, corruption, abort, or terminal boundary cannot be cleared through the
browser; update the implementation or create a compatible new game.

If an accepted request loses its response, retry the byte-equivalent command
with the same client command ID. If an actor fails during ambiguous persistence,
restart and let durable recovery recreate it. Do not edit checkpoints, SQLite
rows, journals, or manifests by hand.

## Update a Windows source ZIP installation

Stop the Quorune console with `Ctrl+C`. From a directory outside the installed
app, run the release's `scripts/update_playtest_windows.ps1` and pass its full
merged commit. The helper downloads that source archive, keeps the existing
install as a rollback sibling, copies its runtime data, and builds the
environment and browser assets at the original path. Leave any configured
external directories and launch environment settings in place.

```powershell
$quoruneCommit = Read-Host 'Enter the full merged Quorune commit'
& .\update_playtest_windows.ps1 -Commit $quoruneCommit
Set-Location (Join-Path $env:LOCALAPPDATA 'QuorunePlay/app')
.\.venv\Scripts\quorune-server.exe --host 127.0.0.1 --port 8765
```

For a comparison using a retained usable database, start with `--offline`.
Use the same launch environment and Tailscale Serve origin. From another
terminal, check `(Invoke-RestMethod 'http://127.0.0.1:8765/api/v1/health')`
for an `ok` status and the expected build. Open **Game menu → About Quorune**
to compare the browser and server builds. Keep the browser's site data and
start a fresh game for the first playtest. Saved games still require their
compatible Oracle and rulings snapshot.

To roll back, stop the new server with `Ctrl+C`, move the current `app`
directory to an unused sibling to retain any new records, and rename the
reported `app.rollback-*` directory to `app`. Restart it using the original
settings and port.

## Validate browser changes

Use the repository's generated types and headless checks:

```powershell
npm ci --prefix web
npm run generate:types --prefix web
npm run typecheck --prefix web
npm run build --prefix web
npx playwright install chromium --with-deps
npm run e2e --prefix web
```

Automation must keep browsers isolated and headless and must stop processes it
starts. See the [CI guide](../development/ci-pipeline.md) for exact-head
certification and [hosted boundary](hosted.md) before considering deployment.
