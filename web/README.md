# Funnel Tracking Web Dashboard

The `web/` app is the dashboard and CRM surface for the Facebook ingestion pipeline. It reads from the shared FrankenSQLite database populated by the CLI tools, shared `fb_pipeline` package, and ADK inbox automation.

## How this app fits into the system

The repo now separates Facebook ingestion logic into a shared package and keeps the UI focused on visualization and operations.

```text
Facebook / Business Suite
        ↓
  tools/*.py wrappers
        ↓
    fb_pipeline/
  session / inbox / comments / persistence
        ↓
FrankenSQLite (memory/agent_memory/frankensqlite.db)
        ↓
     web/ dashboard
        ↓
 operators and CRM users
```

## Relevant package boundaries

### Shared pipeline package

The main ingestion and normalization logic lives outside `web/` in `fb_pipeline/`:

- `fb_pipeline/session/bootstrap.py`
  - CDP attach and Facebook authorization checks
- `fb_pipeline/inbox/pipeline.py`
  - inbox thread normalization, enrichment, persistence, MAS handoff construction
- `fb_pipeline/comments/pipeline.py`
  - comment/post normalization, enrichment, persistence
- `fb_pipeline/persistence/sqlite_store.py`
  - shared SQLite schema setup and DB connection helpers
- `fb_pipeline/contracts/*.py`
  - typed records shared across ingestion and orchestration layers

### Tool wrappers

Operator-facing commands remain in `tools/` and should stay thin:

- `tools/fetch_fb_messages.py`
- `tools/fetch_comments.py`
- `tools/inbox_mas_runner.py`
- `tools/dedup_users.py`

These wrappers are the supported operational entry points. Reusable Facebook logic should go into `fb_pipeline/`, not the UI app.

## End-to-end flow

### Inbox path

1. `tools/fetch_fb_messages.py` or `tools/inbox_mas_runner.py` starts a run
2. `fb_pipeline.session.bootstrap` attaches to an authorized CDP browser session
3. `fb_pipeline.inbox.pipeline` scrapes inbox threads, enriches messages, and persists them
4. inbox enrichment produces a MAS handoff payload for agent-side automation
5. `adk_agents/` uses the persisted data to classify and draft/send responses
6. `web/` reads the resulting CRM and activity data from SQLite

### Comments path

1. `tools/fetch_comments.py` starts a comments run
2. shared session bootstrap opens the Facebook context
3. `fb_pipeline.comments.pipeline` builds/enriches/persists post and comment records
4. `web/` reads comment-derived CRM data from SQLite

Comments now have extraction/persistence parity with the inbox path, but MAS automation remains driven by inbox threads.

## MAS boundary

The UI does not invoke raw scraping logic directly. MAS orchestration sits behind the shared inbox pipeline and runner:

- `fb_pipeline.contracts.inbox.MasHandoff` defines the normalized handoff shape
- `tools/inbox_mas_runner.py` is the operator entry point for automated cycles
- `adk_agents/` consumes persisted inbox state and shared helpers

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

## Developer notes

- Prefer extending `fb_pipeline/` when changing Facebook ingestion behavior.
- Keep `tools/` focused on CLI parsing and operator workflows.
- Keep `web/` focused on presentation, reporting, and CRM interactions over persisted data.
- If you need current schema details, inspect `fb_pipeline/persistence/sqlite_store.py` and the comment-table setup used by `tools/fetch_comments.py`.

## Local development

Run the development server from `web/`:

```bash
npm run dev
```

Then open `http://localhost:3000` in your browser.
