# Repository guidance

## Sources of truth

- Follow [CONTRIBUTING.md](CONTRIBUTING.md) for development, style, migrations,
  verification, and Git conventions.
- Use [README.md](README.md) for Docker and native local startup instructions.
- Preserve the task brief and supplied SQL in `docs/requirements/`.
- Keep changes focused on the requested task. Preserve unrelated working-tree
  and staged changes, and report any checks that fail or cannot be run.

## Stack and layout

Use Python 3.14 (`>=3.14,<3.15`), uv, Flask with APIFlask, SQLAlchemy, psycopg,
Alembic, and PostgreSQL 18. Versions and tooling configuration live in
`pyproject.toml` and `uv.lock`.

- `src/payment_service/app.py`: application factory and dependency wiring.
- `src/payment_service/api/`: HTTP parsing, schemas, authentication, responses,
  and error handling.
- `src/payment_service/services/`: payment coordination, transaction boundaries,
  database queries in `PaymentStore`, and integration contracts.
- `src/payment_service/models/`: SQLAlchemy models and shared metadata.
- `src/payment_service/integrations/`: demo total and payment-provider adapters.
- `migrations/`: Alembic configuration and schema revisions.
- `scripts/seed_dev_db.py`: development data, separate from schema migrations.
- `tests/unit/`: tests without external services.
- `tests/integration/`: PostgreSQL tests using the `integration` marker.
- `tests/fakes.py`: shared test doubles.

Keep these boundaries. Use APIFlask's existing validation and serialization
facilities, and inject integrations through the existing contracts.

## Development and style

Run commands from the repository root. Install locked dependencies with
`uv sync --locked`; run installed tools with `uv run --no-sync --offline`.
Keep dependency changes justified and update `pyproject.toml` and `uv.lock`
together.

Use type annotations and the configured Ruff rules, including the 88-character
code line limit. Format only files changed for the task. Follow CONTRIBUTING's
Google-style docstrings: concise caller-facing summaries, triple double quotes,
and docstring lines within 80 characters. Document meaningful constraints,
side effects, and exceptions without boilerplate.

Create `.env` from `.env.example` only when it does not already exist. Host
commands that need it must use `uv run --env-file .env`; exported variables
take precedence. Never commit credentials, local environments, or editor settings.
Demo authentication through `X-User-ID` is for local review and must remain
disabled by default in application configuration.

## Payment invariants

- Validate ownership of the cart and explicitly selected saved payment method.
  Missing and inaccessible resources use the same `404` response.
- Obtain totals from the total-provider adapter; do not implement price
  calculation. Use `Decimal` internally and two-decimal strings in responses.
  The demo accepts positive USD totals within the existing amount constraints.
- Commit a pending reservation before calling the payment gateway. Do not hold
  the database transaction or its locks across that call.
- Finalize successful payment and cart checkout atomically. When locking both
  rows, lock the cart before the payment.
- Scope idempotency keys to the user. Replays return stored results without
  calling the total provider or payment gateway again.
- Preserve the database constraints preventing duplicate reservations. Pending,
  unknown, and successful attempts block new attempts for the cart. A confirmed
  decline permits a new attempt with a new key.
- Treat timeouts as uncertain outcomes. Do not silently retry charges or delete
  blocking attempts to enable retries. Unexpected provider failures leave the
  reserved attempt pending.
- Derive failure codes from payment status; do not add redundant stored state.
- Keep provider tokens, SQL parameter values, request bodies, and raw provider
  exceptions out of logs and public responses. Preserve `hide_parameters=True`.

## Database changes

Use the models-first workflow in CONTRIBUTING. Bring a disposable database to
the existing head, edit models, then generate a candidate with
`alembic revision --autogenerate`. Inspect both upgrade and downgrade operations
before applying them; autogeneration does not replace constraint review.

Use Alembic's generated revision IDs for future changes. Preserve migration
`0001` and the supplied schema. Keep revisions self-contained without importing
application models, and commit migrations with their model and test changes.
Run schema tests and `alembic check` against the disposable database. Normal
application setup applies existing migrations; it does not generate new ones.

The development Compose database uses tmpfs. Stopping or recreating it discards
its data, and the documented `--force-recreate` startup resets it. Do not use
that command merely to inspect an already-running service. Rerunning the seed
preserves existing rows; it does not reset completed payments. Native PostgreSQL
data persists independently of the application process.

## Verification

Add or update tests for behavior changes. Use the relevant checks below, and
run the full suite before merging:

```bash
uv run --no-sync --offline ruff check .
uv run --no-sync --offline ruff format --check .
uv run --no-sync --offline ty check
uv run --no-sync --offline pytest tests/unit -q
```

Integration fixtures apply migrations and truncate application tables. Use only
a disposable PostgreSQL database whose name ends in `_test`, never the seeded
development database or data that must be retained:

```bash
docker compose -f compose.test.yaml up -d --wait
export TEST_DATABASE_URL='postgresql+psycopg://payment_service_test:local_test_password@127.0.0.1:55433/payment_service_test'
uv run --no-sync --offline pytest tests/integration -q
uv run --no-sync --offline pytest -q
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic check
```

Missing `TEST_DATABASE_URL` fails integration tests rather than skipping them.
Use real PostgreSQL for database and concurrency guarantees. The uv `--offline`
option prevents downloads, not test network access.

For packaging or Docker changes, also run `uv build` and
`docker build -t solid-test-task:dev .`. For startup changes, verify the documented
startup and payment/replay examples. Run pre-commit checks with
`uv run --no-sync --offline pre-commit run --all-files`, or `--files <paths>` for
a focused change. Review any hook edits before staging. Documentation-only
changes need document review and applicable hooks, not new behavior tests.

Before handing off, review `git diff --check`, the working diff, and the staged
diff. Report actual verification results and limitations, not assumed success.

## Git and local planning documents

- Use short-lived branches from an up-to-date `main`; do not commit directly to
  `main`. Preserve existing work when switching branches. Report remote access
  failures rather than claiming the base is current.
- Branch names use `<type>/<short-description>` with lowercase, hyphen-separated
  words. Types are `feature`, `fix`, `docs`, `test`, `refactor`, `chore`, and `ci`.
- Use Conventional Commits with an imperative summary and no trailing period.
  Feature branches use `feature/`, while feature commits use `feat:`.
- Keep commits focused, target PRs at `main`, and prefer squash merging after
  review and applicable checks. PRs describe behavior, verification, assumptions,
  and any failed or unrun checks.
- Respect the requested Git scope: a request to stage changes does not authorize
  committing or pushing them. Stage individual implementation files by name.
- Honor local-only notices in planning documents. Never stage, commit, push,
  or add such documents to an ignore file.
