# Approved DM delivery — 19/09/2026

Contract: `code:hitl-delivery-guard-001`. Operator approved WebUI delivery,
draft-only default, explicit auto-send, exact recipient and freshness checks.

Offline unit/integration checks:

- Web approval works when Telegram is unavailable; preview never polls Telegram.
- Draft inserts multiline text without Enter, leaves a dedicated visible tab.
- Auto-send requires opt-in and confirms a new Page bubble before `executed`.
- Wrong Page/PSID, ambiguous composer and existing operator draft block typing.
- New/edited/deleted messages, changed actor/quote/source ID and missing snapshot reject.
- A message arriving during fill clears only our unchanged draft and never sends.
- SQLite claim is single-owner, Page scoped; drafted actions do not block other seekers.
- Out-date is rejected and retains a durable refresh request; failed refresh retries.
- Manual send is recognized from the exact approved body appended to the same context.
- Browser-executed fixture checks the real DOM identity/composer boundaries.

Live acceptance: start `live --draft-only` on a fresh, approved test proposal.
Observe correct recipient and text, then inject a new message before execution
to check Out-date and targeted fetch. A controlled `--auto-send` test requires a
designated test recipient. Offline tests must not send to production Facebook.
