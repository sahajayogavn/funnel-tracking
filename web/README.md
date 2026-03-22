# Funnel Tracking Web Dashboard

The `web/` app is the dashboard and CRM surface for the Facebook ingestion pipeline. It reads from the shared FrankenSQLite database populated by the stable CLI commands, canonical `l5_*` wrappers, shared `fb_pipeline` package, and ADK inbox automation.

## How this app fits into the system

The repo separates Facebook ingestion logic into a shared package and keeps the UI focused on visualization and operations.

```text
Facebook / Business Suite
        ↓
legacy tools/*.py shims
        ↓
canonical tools/l5_*.py wrappers
        ↓
fb_pipeline/ L1→L4 shared layers
        ↓
FrankenSQLite (memory/agent_memory/frankensqlite.db)
        ↓
     web/ dashboard
        ↓
 operators and CRM users
```

## L1–L5 hierarchy

The landed code now expresses the hierarchy in canonical prefixed filenames:

```text
L5  tools/l5_*.py + adk_agents/tools/l5_*.py
 ↓
L4  fb_pipeline/persistence/l4_sqlite_store.py
 ↓
L3  fb_pipeline/inbox/l3_pipeline.py
    fb_pipeline/comments/l3_pipeline.py
    fb_pipeline/browser/l3_inbox.py
    fb_pipeline/browser/l3_comments.py
 ↓
L2  fb_pipeline/session/l2_bootstrap.py
    fb_pipeline/browser/l2_actions.py
 ↓
L1  fb_pipeline/contracts/l1_session.py
    fb_pipeline/contracts/l1_inbox.py
    fb_pipeline/contracts/l1_comments.py
    fb_pipeline/comments/l1_helpers.py
```

Prefixed files are canonical. Old unprefixed filenames remain compatibility shims so existing commands and exact-path tests continue to work.

For the web app, that means `web/` reads the persisted outputs of the lower layers and should stay decoupled from raw Facebook DOM or browser-session code.

## Relevant package boundaries

### Shared pipeline package

The main ingestion and normalization logic lives outside `web/` in `fb_pipeline/`:

- `fb_pipeline/session/l2_bootstrap.py`
  - CDP attach and Facebook authorization checks
- `fb_pipeline/browser/l3_inbox.py`
  - inbox DOM scraping and ad-label extraction
- `fb_pipeline/browser/l3_comments.py`
  - comments/post DOM scraping
- `fb_pipeline/inbox/l3_pipeline.py`
  - inbox thread normalization, enrichment, persistence, MAS handoff construction
- `fb_pipeline/comments/l3_pipeline.py`
  - comment/post normalization, enrichment, persistence
- `fb_pipeline/persistence/l4_sqlite_store.py`
  - shared SQLite schema setup and DB connection helpers
- `fb_pipeline/contracts/l1_*.py` and `fb_pipeline/comments/l1_helpers.py`
  - typed records and pure helpers shared across ingestion and orchestration layers

### Tool wrappers

Operator-facing wrapper implementations now live in canonical L5 files:

- `tools/l5_fetch_fb_messages.py`
- `tools/l5_fetch_comments.py`
- `tools/l5_inbox_mas_runner.py`

Stable legacy commands remain:

- `tools/fetch_fb_messages.py`
- `tools/fetch_comments.py`
- `tools/inbox_mas_runner.py`
- `tools/dedup_users.py`

Reusable Facebook logic should go into `fb_pipeline/`, not the UI app and not the legacy wrapper shims.

## End-to-end flow

### Inbox path

1. `tools/fetch_fb_messages.py` or `tools/inbox_mas_runner.py` starts a run
2. `fb_pipeline.session.l2_bootstrap` attaches to an authorized CDP browser session
3. `fb_pipeline.browser.l3_inbox` scrapes inbox threads and message panels
4. `fb_pipeline.inbox.l3_pipeline` normalizes threads, enriches messages, and persists them
5. inbox enrichment produces a MAS handoff payload for agent-side automation
6. `adk_agents/tools/l5_*` uses the persisted data to classify and draft/send responses
7. `web/` reads the resulting CRM and activity data from SQLite

### Comments path

1. `tools/fetch_comments.py` starts a comments run
2. shared L2 bootstrap opens the Facebook context
3. `fb_pipeline.browser.l3_comments` collects visible posts and comment payloads
4. `fb_pipeline.comments.l3_pipeline` builds/enriches/persists post and comment records
5. `web/` reads comment-derived CRM data from SQLite

Comments now have extraction/persistence parity with the inbox path, but MAS automation remains driven by inbox threads.

## MAS boundary

The UI does not invoke raw scraping logic directly. MAS orchestration sits behind the shared inbox pipeline and runner:

- `fb_pipeline.contracts.l1_inbox.MasHandoff` defines the normalized handoff shape
- `tools/l5_inbox_mas_runner.py` is the canonical operator entry point for automated cycles
- `adk_agents/tools/l5_*` consumes persisted inbox state and shared helpers

This keeps the UI decoupled from Facebook DOM details.

## Operator entry points

From the repo root:

```bash
python tools/fetch_fb_messages.py --pageId <asset_id> --credential default
python tools/fetch_comments.py --pageId <asset_id> --credential default
python tools/inbox_mas_runner.py --page-id <asset_id> --once
python tools/inbox_mas_runner.py --page-id <asset_id> --once --live
python tools/dedup_users.py --dry-run
```

The old command surface stays stable even though the canonical wrapper implementations are now in `tools/l5_*.py`.

## Developer notes

- Prefer extending canonical prefixed modules in `fb_pipeline/` when changing Facebook ingestion behavior.
- Keep canonical L5 `tools/` and `adk_agents/` pointed at `fb_pipeline` boundaries instead of importing sideways through wrapper shims.
- Keep legacy unprefixed files as compatibility shims only.
- Keep `web/` focused on presentation, reporting, and CRM interactions over persisted data.
- If you need current schema details, inspect `fb_pipeline/persistence/l4_sqlite_store.py` and the comment-table setup used by `tools/l5_fetch_comments.py`.

## Local development

Run the development server from `web/`:

```bash
npm run dev
```

Then open `http://localhost:3000` in your browser.
