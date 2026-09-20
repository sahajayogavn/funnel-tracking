# Funnel Tracking PostgreSQL deployment

The live instance is deployed at `/mnt/s2/Dockers/postgres-funnel-tracking`
on `10.0.1.42`, as container `postgres-funnel-tracking`. This directory is the
versioned, secret-free provisioning reference for that deployment.

1. Copy `.env.example` to `.env` on the server, set distinct long secrets, and
   run `chmod 600 .env`.
2. Create `data`, `backups`, and `init` directories, then run
   `docker compose -f compose.yml up -d`.
3. Run `./backup.sh` once to validate backup access. The live server schedules
   it daily at 02:00 and retains 14 days.

The port is intentionally bound only to `10.0.1.42:5432`. Application clients
must use `DATABASE_URL` and `FUNNEL_REQUIRE_POSTGRES=1`; never add a SQLite
fallback in a production process after cutover.
