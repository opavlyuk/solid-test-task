# Contributing

## Development setup

Use Git, uv, Python 3.14, and Docker. The Python version is declared in
`.python-version`; dependencies are locked in `uv.lock`.

The HTTP service uses [APIFlask](https://apiflask.com/), which is built on Flask,
with SQLAlchemy and Alembic for PostgreSQL access and migrations.

```bash
git clone git@github.com:opavlyuk/solid-test-task.git
cd solid-test-task
cp .env.example .env
uv sync --locked
uv run --no-sync --offline pre-commit install
```

The task brief and supplied database schema are in
[docs/requirements/](docs/requirements/payment-endpoint.md).

### Local environment settings

Edit `.env` for your local database name, user, password, and published port.
`.env.example` contains local development defaults; `.env` and `.env.*` files
other than that example are ignored by Git and excluded from Docker builds.
Copy the example only when creating your local configuration.

The `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` variables are
standard PostgreSQL client settings. `DATABASE_URL=postgresql+psycopg://` selects
the SQLAlchemy driver and lets psycopg read those settings without putting
credentials in a URL.

Compose reads `.env` automatically and supplies the database settings to its
services. Inside the app and initialization containers, `PGHOST=db` and
`PGPORT=5432` override the host connection settings.
Host commands load `.env` explicitly using `uv run --env-file .env`.
Exported shell variables take precedence over values in `.env`.

`ALLOW_DEMO_AUTH=true` in the example enables the unverified `X-User-ID` header
for local host runs. The application defaults to disabled when it is absent.
The local Compose file explicitly enables it only for `app`; it is not an
authentication mechanism for deployment.

## Local deployment with Docker Compose

The root `compose.dev.yaml` defines the local development environment. Run the
following commands from the repository root.

Start the development container and PostgreSQL 18:

```bash
docker compose -f compose.dev.yaml up --build --force-recreate --wait
docker compose -f compose.dev.yaml ps
```

Compose waits for PostgreSQL's health check, then runs the one-shot `db-init`
service to apply Alembic migrations and load development data. `app` starts only
when initialization succeeds. The Flask development server serves the API at
`http://127.0.0.1:8000`, with interactive documentation at `/docs` and its schema
at `/openapi.json`. The HTTP health check requests that schema and does not test
database or provider connectivity. This server is for local review only.
Source files are copied into the image; rerun
`docker compose -f compose.dev.yaml up --build --force-recreate --wait` after changing code,
dependencies, or migrations. This also resets the local database.

Run commands in the development container:

```bash
docker compose -f compose.dev.yaml exec app alembic upgrade head
docker compose -f compose.dev.yaml exec app alembic current
docker compose -f compose.dev.yaml exec app ruff check .
docker compose -f compose.dev.yaml exec app ty check
docker compose -f compose.dev.yaml exec app pytest tests/unit -q
docker compose -f compose.dev.yaml exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Run integration tests on the host against the separate test database described
under Verification. Generate migration revisions on the host using the commands
below, then rebuild the image to include them.

With the example settings, the database is available to host tools at
`127.0.0.1:5433`, using database and user `payment_service` and password
`local_dev_password`. These defaults are for local development.
The app uses `db:5432` on the Compose network.
To run Alembic from the host:

```bash
uv run --env-file .env --no-sync --offline alembic current
```

To change the host port, set `PGPORT` in `.env` (for example, `PGPORT=55432`) and
rerun Compose. Host commands loading `.env` use the same port automatically.

PostgreSQL stores its data in temporary memory (`tmpfs`). Stopping or recreating
the database container discards all database data. Each fresh Compose startup
recreates the schema and sample data. Stop the stack with:

```bash
docker compose -f compose.dev.yaml down
```

The startup command above uses `--force-recreate` so it resets the database even
if the stack is already running. Without `--force-recreate`,
`docker compose -f compose.dev.yaml up` can reuse a running container and does not
itself reset the database.

Use the full startup command after stopping or restarting individual containers
so the `db-init` job runs again.
`docker compose -f compose.dev.yaml restart db` alone clears the database without
rerunning initialization.

### Development seed data

`scripts/seed_dev_db.py` is independent of the application package. It reads
`DATABASE_URL` and inserts the supplied sample data through SQLAlchemy Core in a
single transaction:

- Alice and Bob.
- A kettle, blanket, and mug.
- Alice's active cart containing one kettle and two mugs, totaling USD 70.00.
- Alice's mock payment-provider token.

Rows have stable UUIDs. Repeated runs skip existing primary keys, preserving
local edits; unrelated constraint violations fail and roll back the entire seed.
To restore the original sample data, recreate the Compose stack.

To rerun only the seed against the existing development database:

```bash
docker compose -f compose.dev.yaml exec app python scripts/seed_dev_db.py
```

Or from the host, using `.env`:

```bash
uv run --env-file .env --no-sync --offline alembic upgrade head
uv run --env-file .env --no-sync --offline python scripts/seed_dev_db.py
```

Schema changes belong in Alembic revisions; development data belongs in this
script. The original `docs/requirements/quick-start-schema.sql` stays unchanged
as a reference. Revision `0002` introduces the payment table and its constraints.

## GitHub flow

Use short-lived branches and pull requests targeting `main`. Keep `main`
ready to run as the service is implemented; do not commit work directly to it.

1. Start from an up-to-date `main` with a clean working tree:

   ```bash
   git switch main
   git pull --ff-only origin main
   git switch -c feature/cart-payment
   ```

2. Make focused changes and commits. Add or update tests for behavior changes
   and documentation where needed.
3. Run the relevant checks below and review your diff.
4. Push the branch and set its upstream:

   ```bash
   git push -u origin feature/cart-payment
   ```

5. Open a pull request against `main`. Describe the problem, the resulting
   behavior, and how you verified it. State assumptions and any failed or
   unrun checks. A draft PR is useful for work in progress.
6. Address review feedback and resolve conflicts. Merge after review and
   successful applicable checks. Prefer squash merging, with the PR title
   following the commit message convention below.
7. Delete the merged branch, then update local `main` before starting new work.


## Branch names

Use `<type>/<short-description>`. Write the description in lowercase, separate
words with hyphens, and choose a name that describes the whole change.

| Type | Purpose | Example |
| --- | --- | --- |
| `feature` | New behavior | `feature/cart-payment` |
| `fix` | Bug fix | `fix/duplicate-payment` |
| `docs` | Documentation only | `docs/payment-assumptions` |
| `test` | Tests only | `test/provider-failure` |
| `refactor` | Restructuring without changing behavior | `refactor/payment-service` |
| `chore` | Tooling, dependencies, or project maintenance | `chore/project-scaffold` |
| `ci` | Continuous integration configuration | `ci/python-checks` |

For changes spanning several categories, use the type that reflects the main
purpose. Branch types and commit types differ for features: use `feature/`
for the branch and `feat:` for the commit.

## Commit messages

Follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).

Use `<type>: <summary>`, optionally adding a scope as
`<type>(<scope>): <summary>`. Use an imperative, concise summary without a
trailing period. Add a body when the reason or tradeoff needs explanation.

Use `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, or `ci`, with the same
purposes as the branch types above.

```text
chore: scaffold the payment service project
docs: add contribution guidelines
feat(payments): add the cart payment endpoint
fix(payments): prevent duplicate charges
test: cover payment provider failures
```

Keep each commit focused on one logical change. Include `pyproject.toml` and
`uv.lock` together when changing dependencies. Never commit local environments,
credentials, or editor settings.

## Docstrings

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
and [Napoleon's Google-style examples](https://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html).

- Describe purpose and caller-facing behavior, not implementation details.
- Keep wording concise. Avoid semicolons, redundant words, and restating names
  or type annotations.
- Use triple double quotes and a one-line summary ending in a period.
  Keep docstring lines within 80 characters.
- Prefer a one-line docstring when it suffices. Otherwise, leave a blank line
  after the summary and use `Args:`, `Returns:`, `Yields:`, or `Raises:` as needed,
  with four-space section indentation.
- Document meaningful constraints, side effects, and exceptions. Omit boilerplate
  for obvious helpers.

## Database migrations

Use [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html) for database
schema changes. Configuration lives in `alembic.ini`, and revision files belong
in `migrations/versions/`. New revisions are linted and formatted by Ruff using
Alembic's post-write hooks, so generate them in the development environment.
Both hooks explicitly use the root `pyproject.toml`. Migration files also pass
through the same Ruff pre-commit hooks as every other Python file.

Use `.env` for local connection settings. A full `DATABASE_URL` with the psycopg
driver is also supported when connecting to a separately configured database.

Use a models-first workflow. Start the disposable test database and export
`TEST_DATABASE_URL` as shown under Verification, then bring it to the existing
head before changing models:

```bash
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic upgrade head
# Edit the SQLAlchemy models in src/payment_service/models/ first.
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic revision --autogenerate -m "describe schema change"
```

Inspect the generated candidate's `upgrade()` and `downgrade()` before applying
it. Confirm types, defaults, constraints, indexes, data preservation, and the
scope of any drops. Autogeneration needs review, especially for check constraints
and renames; add any required operations it misses. Keep revisions self-contained
without importing application models. Use Alembic's generated revision IDs for
future changes; the explicit `0002` ID was specific to the payment migration.

Apply the reviewed candidate, update schema tests for the intended change, then
check both behavior and metadata alignment:

```bash
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic current
uv run --no-sync --offline pytest tests/integration/test_payment_schema.py -q
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic check
```

Review and test upgrades and downgrades on this disposable database before
merging. `alembic check` detects supported metadata drift but does not replace
constraint and migration tests. Commit revisions with their model and test
changes, then rebuild the development image to include them.

Revision `0001` creates the supplied shop schema without sample data. Revisions
are self-contained and do not import application models. Downgrading `0001`
removes its tables but retains `pgcrypto`, which may be used by other schemas.
Revision `0002` adds payment attempts; its downgrade drops only the payment
table, including its indexes. `migrations/env.py` already uses `Base.metadata`
from the application's model package for autogeneration.

## Pre-commit hooks

The setup command installs the Git hook in each checkout. It runs automatically
on `git commit`. Run it manually against all tracked files with:

```bash
uv run --no-sync --offline pre-commit run --all-files
```

The hook configuration is in `.pre-commit-config.yaml`. All checks use local
tools from the development environment, with versions controlled by `uv.lock`.
Run `uv sync --locked` after pulling dependency changes; hook commands run
offline and will fail if required packages are unavailable locally.

| Check | Scope |
| --- | --- |
| YAML and TOML validation | Changed configuration files |
| Merge conflict markers and private keys | Changed text files |
| Trailing whitespace and final newline fixes | Changed text files; the supplied SQL is excluded from newline fixes |
| Lockfile consistency | Changes to `pyproject.toml` or `uv.lock` |
| Ruff lint fixes and formatting | Changed Python files |
| ty type checking | All configured source, test, migration, and development script files when Python files or dependencies change |

If a hook fixes files, review and stage the changes, then commit again. Empty
files remain empty. Private-key detection is a basic check, not a general
credential scanner.

Run pytest separately before merging. Docker builds and
database operations also stay outside the commit hook.

## Verification

After `uv sync --locked`, run the installed tools:

```bash
uv run --no-sync --offline ruff check .
uv run --no-sync --offline ruff format --check .
uv run --no-sync --offline ty check
uv run --no-sync --offline pytest tests/unit -q
```

Ruff handles linting and formatting; ty handles type checking. Format only the
files you intend to change with `uv run --no-sync --offline ruff format <paths>`.

Unit tests belong in `tests/unit/`; tests requiring PostgreSQL or other external
services belong in `tests/integration/` and should use the `integration` marker.
The integration fixtures apply migrations and truncate application tables before
and after each test. They require `TEST_DATABASE_URL` to name a PostgreSQL
database ending in `_test`, and fail if it is absent. Use the dedicated temporary
database, not the seeded development database or any data you need to retain:

```bash
docker compose -f compose.test.yaml up -d --wait
export TEST_DATABASE_URL='postgresql+psycopg://payment_service_test:local_test_password@127.0.0.1:55433/payment_service_test'
uv run --no-sync --offline pytest tests/integration -q
uv run --no-sync --offline pytest -q
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic check
```

The test stack has its own Compose project and publishes PostgreSQL on loopback
port `55433`, separate from the development database's default `5433`. Its data
is also temporary. Run `docker compose -f compose.test.yaml down` when finished.
The uv `--offline` flag prevents uv downloads; it does not prevent tests from
using the network. See the README for manual success, replay, decline, and
timeout examples.

For packaging or Docker changes, also run:

```bash
uv build
docker build -t solid-test-task:dev .
```
