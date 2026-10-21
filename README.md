# Cart payment service

A local payment endpoint using Python 3.14, Flask with APIFlask, SQLAlchemy,
Alembic, and PostgreSQL 18. It reserves a payment attempt before contacting a
mock provider, then records the outcome and checks out the cart together on
success. No real payments are made.
The [task brief](docs/requirements/payment-endpoint.md) and
[supplied schema](docs/requirements/quick-start-schema.sql) are included.

## Start the seeded application with Docker

Install Docker with Compose. For host development and tests, also install uv and
Python 3.14. From the repository root:

```bash
# Create local settings only if .env does not already exist.
test -e .env || cp .env.example .env
docker compose -f compose.dev.yaml up --build --force-recreate --wait
docker compose -f compose.dev.yaml ps
```

The API is at <http://127.0.0.1:8000>, interactive documentation at
<http://127.0.0.1:8000/docs>, and the OpenAPI schema at
<http://127.0.0.1:8000/openapi.json>.

The Flask development server and demo authentication are for local review only.
Compose explicitly enables `ALLOW_DEMO_AUTH=true` for `app`, which trusts the
`X-User-ID` header without authenticating its sender. Both published ports bind
to loopback. Outside this local configuration, demo authentication defaults to
off when the setting is absent; a real identity integration is not implemented.

PostgreSQL uses temporary memory. Stopping or recreating its container discards
the data, and the startup command above intentionally resets it even when the
stack is already running. Rebuild with that command after code changes, and use
it after stopping or restarting individual containers so initialization runs
again. Restarting only `db` loses its data without rerunning migrations or seed.
Stop the stack with:

```bash
docker compose -f compose.dev.yaml down
```

The seed contains Alice and Bob, three products, Alice's saved payment method,
and her active cart with one kettle and two mugs, totaling USD 70.00.

## Start locally without Docker

Install uv, Python 3.14, and PostgreSQL 18 with its command-line tools and
`pgcrypto` extension files. Start PostgreSQL using your operating system's
service manager.

Create a database user and a database it owns. These commands assume PostgreSQL
is listening on `127.0.0.1:5432` and the administrator is `postgres`; adjust them
to match your installation. Enter a password for the new user when prompted:

```bash
createuser --host=127.0.0.1 --port=5432 --username=postgres --pwprompt payment_service
createdb --host=127.0.0.1 --port=5432 --username=postgres --owner=payment_service payment_service
```

From the repository root, install dependencies and create local settings if
needed:

```bash
uv sync --locked
test -e .env || cp .env.example .env
```

Edit `.env` to match your local database. In particular, change the example's
`PGPORT=5433` to your PostgreSQL port (usually `5432`) and set `PGPASSWORD` to
the password you chose:

```dotenv
DATABASE_URL=postgresql+psycopg://
PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=payment_service
PGUSER=payment_service
PGPASSWORD=your_local_password
ALLOW_DEMO_AUTH=true
```

If the Docker app is already running, stop it with
`docker compose -f compose.dev.yaml stop app` to free port `8000`.
Apply the existing migrations, load the sample data, and start Flask:

```bash
uv run --env-file .env --no-sync --offline alembic upgrade head
uv run --env-file .env --no-sync --offline python scripts/seed_dev_db.py
uv run --env-file .env --no-sync --offline flask --app payment_service:create_app run --host=127.0.0.1 --port=8000
```

No new migration needs to be generated for setup. The commands explicitly load
`.env`; existing exported environment variables take precedence over its values.
Demo authentication is enabled here only for local review.

Open <http://127.0.0.1:8000/docs> or <http://127.0.0.1:8000/openapi.json>, then
use the payment and replay example below. Press Ctrl+C to stop the local Flask
server.

Your local PostgreSQL database persists across application restarts. Rerunning
the seed preserves existing rows, including completed payments; it does not
reset the cart to its initial state.

## Pay and replay

Run this command against the freshly seeded application:

```bash
curl -i http://127.0.0.1:8000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: 11111111-1111-1111-1111-111111111111' \
  -H 'Idempotency-Key: 33333333-3333-4333-8333-333333333333' \
  -d '{"payment_method_id":"11111111-2222-3333-4444-555555555555"}'
```

The first response is `201 Created` with this shape (the payment ID varies):

```json
{
  "id": "<payment UUID>",
  "cart_id": "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1",
  "payment_method_id": "11111111-2222-3333-4444-555555555555",
  "amount": "70.00",
  "currency": "USD",
  "status": "succeeded",
  "failure_code": null
}
```

Run the **same curl command again**, including its idempotency key. Expect `200 OK`
and the same payment ID and body, with no second provider call. A key is scoped
to a user and identifies the cart and payment method; changing either with the
same key returns `409`. A new key cannot pay an already checked-out cart.
Always reuse the original key after an interrupted request.

Inspect the stored attempt and cart when using Docker:

```bash
docker compose -f compose.dev.yaml exec db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, cart_id, amount, currency, status FROM payments; SELECT id, status FROM carts;"'
```

For a native PostgreSQL setup, load the connection settings from `.env`:

```bash
uv run --env-file .env --no-sync --offline psql -v ON_ERROR_STOP=1 \
  -c 'SELECT id, cart_id, amount, currency, status FROM payments; SELECT id, status FROM carts;'
```

The body accepts only `payment_method_id` (a UUID). Identity and idempotency key
are required UUID headers. Clients cannot supply amounts, provider tokens, or
provider outcome switches. The user must exist and own both cart and saved
method; a fresh attempt requires a nonempty, active cart. Missing and foreign
resources both produce `404`.

| HTTP status | Meaning |
| --- | --- |
| `201` | New successful payment; cart checked out |
| `200` | Replay of a successful payment |
| `202` | Stored `pending` or `unknown` attempt; outcome unresolved |
| `400` | Malformed JSON |
| `401` | Demo auth disabled, identity missing/invalid, or user unknown |
| `402` | Declined attempt, including replay: `failed`, `card_declined` |
| `404` | Cart or payment method missing or owned by someone else |
| `405` | Unsupported HTTP method |
| `409` | Inactive/empty cart, blocking payment, or conflicting key reuse |
| `415` | Body is not sent as JSON |
| `422` | Invalid UUID, missing required input, or unexpected body field |
| `500` | Unexpected application error, including database constraint/data errors |
| `503` | Total unavailable or database unavailable |

Payment responses (`200`, `201`, `202`, `402`) use the shape above. Other errors
use `{"error":{"code":"validation_error","message":"Request validation failed."}}`
with the appropriate code and message. `failure_code` is derived from status:
`card_declined` for `failed`, `provider_unavailable` for `unknown`, and `null`
for `pending` or `succeeded`. Provider tokens and references are not returned.

## Run tests and checks

Integration tests apply migrations and truncate application tables before and
after each test. Use the separate disposable database below, never the seeded
development database or a database containing data you need. The fixture requires
`TEST_DATABASE_URL` naming a PostgreSQL database whose name ends in `_test`.
Missing settings fail the integration suite rather than skipping it.

```bash
uv sync --locked
docker compose -f compose.test.yaml up -d --wait
export TEST_DATABASE_URL='postgresql+psycopg://payment_service_test:local_test_password@127.0.0.1:55433/payment_service_test'

uv run --no-sync --offline ruff check .
uv run --no-sync --offline ruff format --check .
uv run --no-sync --offline ty check
uv run --no-sync --offline pytest tests/unit -q
uv run --no-sync --offline pytest tests/integration -q
uv run --no-sync --offline pytest -q
DATABASE_URL="$TEST_DATABASE_URL" uv run --no-sync --offline alembic check
uv build
docker build -t solid-test-task:dev .
```

The test database uses port `55433` and its own Compose project, so it can run
alongside the development database (port `5433` by default). Tests cover request
validation, ownership, replay, concurrent requests, provider failures, schema
constraints, migration round trips, and database failure windows. Unit tests do
not need PostgreSQL. Stop the test database when finished:

```bash
docker compose -f compose.test.yaml down
```

### Declines and timeouts

With the test database running and `TEST_DATABASE_URL` exported, exercise both
mock outcomes and their replays through the endpoint fixtures:

```bash
uv run --no-sync --offline pytest tests/integration/test_payment_endpoint.py -k provider_outcome -q
```

For a manual curl demonstration, recreate the development stack first, then edit
Alice's saved token before paying. This command selects a decline:

```bash
docker compose -f compose.dev.yaml up --build --force-recreate --wait
docker compose -f compose.dev.yaml exec -T db sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
UPDATE user_payment_methods
SET provider_token = 'tok_test_declined'
WHERE id = '11111111-2222-3333-4444-555555555555';
SQL
```

Run the payment curl twice: both responses are `402`, with the same failed
payment. For a timeout, recreate the development stack again and run the same
SQL using `tok_test_timeout`, then repeat the curl. Both responses are `202`,
with `status: unknown` and `failure_code: provider_unavailable`. The mock timeout
raises immediately; it does not wait on a network request.

A declined payment permits a deliberate new attempt with a **new** key. Restoring
the token to `tok_test_alice_visa` and using a new key will succeed while the
cart is still active. An unknown or pending attempt blocks new attempts; changing
its token or key does not resolve it. Recreate the disposable stack to reset a
manual scenario. These outcome controls belong to fixtures or saved local data,
never to the public request.

## Schema and payment flow

Migration `0001` retains the supplied shop tables: `users`, `products`, `carts`,
`cart_items`, and `user_payment_methods`. Migration `0002` adds `payments` with
UUID identifiers and foreign keys, a per-user idempotency key, `NUMERIC(12,2)`
amount, currency, status, nullable provider reference, and timestamps.

Constraints enforce a positive amount up to `9999999999.99`, USD currency,
and the allowed status/reference combinations: only `succeeded` has a non-null
provider reference. A unique constraint covers `(user_id, idempotency_key)`;
a partial unique index allows at most one `pending`, `unknown`, or `succeeded`
attempt per cart. Failed attempts remain available for replay and auditing.
Failure codes are derived at the API boundary, not stored in another column.

Reservation locks the cart, validates eligibility, obtains the total, and commits
a pending attempt. The provider call happens after that transaction closes.
Finalization uses a second transaction to store the result and, on success,
change the cart to `checked_out` atomically. Replays return the stored attempt
without contacting the provider. Money uses Decimal internally and a two-decimal
string in JSON.

## Assumptions and recovery limits

- The demo total provider returns a fixed USD 70.00 only for the seeded cart.
  It does not recalculate item prices; another cart returns `503` when its total
  is requested. A real total service belongs behind the existing adapter.
- Clients explicitly choose an owned saved payment method; there is no implicit
  default-method selection. Inventory, discounts, refunds, and cart editing are
  outside this endpoint. Any future cart editor must respect payment locking and
  unresolved attempts.
- A decline is definitive. A provider timeout is uncertain and is stored as
  `unknown`; the service does not assume that no charge occurred.
- A crash after reservation, unexpected provider exception, or failed outcome
  commit can leave `pending`. A lost database commit acknowledgment can return
  an error even though a write persisted. Repeating the same key reads durable
  state without charging again. An unsuccessful reservation commit may leave no
  attempt; its retry can then reserve normally.
- There is no reconciliation worker, webhook, automatic retry, or recovery
  endpoint. Pending and unknown attempts block new charges until a future
  reconciliation process verifies the provider outcome. They must not be blindly
  marked failed or deleted to retry a real charge. A `202` response does not
  promise background progress.
- The mock gateway returns deterministic results and has no external charge
  ledger. Mock tests do not establish real-provider reliability or exactly-once
  delivery. A production provider needs durable idempotency and reconciliation,
  along with real authentication, persistent storage, and a production server.

See [CONTRIBUTING.md](CONTRIBUTING.md) for local settings, the models-first
migration workflow, and contribution checks.
