# Architecture

**Language: [English](architecture.md) | [中文](../zh/architecture.md)**

Backend architecture of the Intelligence Analysis platform: FastAPI + Pydantic v2 over
async PostgreSQL, with JWT authentication and a strict three-layer separation.

The conventions summarised here are enforced per-file by `.claude/rules/*` and stated as
hard boundaries in `CLAUDE.md`. This document explains *why* they hold and how a request
travels through them.

---

## 1. The one rule everything else follows

```
HTTP  ─▶  Routes  ─▶  Services  ─▶  Repositories  ─▶  PostgreSQL
          (shape)     (rules)       (persistence)
```

Each layer may only talk to the one directly below it.

| Layer | Package | Owns | Must never |
| --- | --- | --- | --- |
| Routes | `app/api/routes/v1/` | HTTP shape: paths, status codes, `response_model`, auth dependencies | Import a repository, build a query, hold business rules |
| Services | `app/services/` | Business rules, orchestration, domain exceptions | Touch `Request`/`Response`, raise `HTTPException`, write raw SQL |
| Repositories | `app/repository/` | Data access, one function per query | Commit, enforce a rule, return dicts or bare IDs |

The payoff is testability and single-authorship of rules: "changing a password revokes
every refresh token" is written once, in `UserService.change_password`, and cannot be
bypassed by a second route that happens to touch the same table.

**Routes never import repositories.** If a route needs one query, it still goes through a
service method. The extra method is cheaper than the eventual duplicate rule.

---

## 2. Directory layout

```
backend/app/
├── main.py                  # assembly only: middleware, handlers, routers, lifespan
├── api/
│   ├── deps.py              # DI: session, services, current user, admin, client info
│   ├── router.py            # aggregates versioned routers
│   ├── middleware.py        # RequestIDMiddleware, RateLimitMiddleware (pure ASGI)
│   ├── exception_handlers.py# AppException / unhandled → JSON error body
│   └── routes/v1/           # one module per domain: user.py, utils.py
├── core/
│   ├── config.py            # pydantic-settings; the only reader of the environment
│   ├── exceptions.py        # AppException hierarchy + HTTP status mapping
│   ├── security.py          # password hashing, JWT issue/decode, refresh digests
│   ├── logging.py           # PII redaction + request-id log filters
│   └── context.py           # request-id ContextVar
├── db/
│   ├── base.py              # DeclarativeBase, naming convention, TimestampMixin
│   ├── session.py           # engine, request/context/worker session scopes
│   ├── models/              # SQLAlchemy models: user.py, session.py
│   └── todo_pool.py         # optional asyncpg pool (deep-research planner)
├── repository/              # data access functions: user.py
├── schemas/                 # Pydantic v2: base.py, user.py
├── services/                # business logic: user.py, email/
├── utils.py                 # email rendering + SMTP send
└── agents/, clawer/         # reserved, empty today
```

`agents/` and `clawer/` exist but are empty. Nothing imports them yet; treat them as
reserved names rather than as layers.

Top-level `app/` is for framework concerns only (`api/`, `core/`, `db/`, `repository/`,
`schemas/`, `services/`). A new business domain becomes a module or subpackage under
`services/` — never a new top-level package.

---

## 3. Assembly: `main.py`

`main.py` owns no logic. `create_app()` is a factory, not module-level statements, so
tests can build an app against patched settings and importing the module does nothing but
define functions.

Order inside `create_app()` matters:

1. `setup_logging()` — first, so nothing is logged before PII redaction is installed.
2. `_init_sentry()` — deployed environments only.
3. `FastAPI(...)` with `custom_generate_unique_id` so generated clients get
   `<tag>-<function>` method names.
4. `_register_middleware(app)`.
5. `register_exception_handlers(app)`.
6. `include_router(api_router, prefix=settings.API_V1_STR)` — the `/api/v1` prefix is
   applied here, once, not repeated in the routers.

### Middleware order

`add_middleware` prepends, so the **last** one added is outermost. They are added
innermost-first, which yields:

```
RequestID ─▶ CORS ─▶ RateLimit ─▶ Session ─▶ exception handlers ─▶ routes
```

* **RequestID outermost** — even a rejected or crashing request is traceable, and every
  response carries the header.
* **CORS outside the rate limit** — a browser can actually read a 429, and a preflight is
  never charged to the caller's budget.
* Both middlewares are plain ASGI, not `BaseHTTPMiddleware`: neither reads or rewrites a
  body, and staying pure ASGI leaves streaming responses, background tasks and WebSockets
  untouched.

Exception handlers sit *inside* the middleware stack. That is why `RateLimitMiddleware`
writes its own 429 body instead of raising `RateLimitError` — an exception raised at
middleware level would never reach a handler.

### Lifespan

`lifespan` owns process-wide resources: acquire before serving, release in reverse.
Today it calls `assert_event_loop_supported()` (see §5) and opens the optional asyncpg
TODO pool. Reserved slots, in the order they should be filled, are documented in the
`lifespan` docstring: Redis, RAG (embedding model + vector store), then bot polling.

---

## 4. Dependency injection: `api/deps.py`

Routes declare what they need through `Annotated` aliases. A raw `Depends()` never appears
in a route signature.

```python
SessionDep      = Annotated[AsyncSession, Depends(get_db_session)]
TokenDep        = Annotated[str, Depends(reusable_oauth2)]
UserServiceDep  = Annotated[UserService, Depends(get_user_service)]
CurrentUser     = Annotated[User, Depends(get_current_user)]
AdminUser       = Annotated[User, Depends(get_current_admin)]
ClientInfoDep   = Annotated[ClientInfo, Depends(get_client_info)]
```

A service factory takes the session and returns the service — one line, no state:

```python
def get_user_service(db: SessionDep) -> UserService:
    return UserService(db)
```

Authentication resolves the bearer token to a `User`:
`decode_token(token, expected_type=TokenType.ACCESS)` → `sub` → `service.get(user_id)` →
reject inactive accounts. A token for a *deleted* account raises `AuthenticationError`,
not `NotFoundError` — a stale credential is a 401, never a 404 that leaks whether the
account ever existed.

Authorization is a function, not a class: `is_admin(user)` is
`user.is_app_admin or user.has_role(UserRole.ADMIN)`, and `get_current_admin` wraps it.
Two ways to require it, both in use:

```python
# whole-endpoint gate, when the handler doesn't need the admin object
@router.patch("/{user_id}", dependencies=[Depends(get_current_admin)])

# inject it, when the handler compares against the caller
async def delete_user(user_id: UUID, current_user: AdminUser, ...):
    if user_id == current_user.id:
        raise AuthorizationError("Admins are not allowed to delete themselves")
```

`ClientInfo` is a frozen slotted dataclass carrying the caller's IP and user-agent; it is
recorded on the login session row rather than read ad hoc inside the service.

---

## 5. Data layer

### Models — `db/models/`

Every model inherits `Base` and, unless it manages its own timestamps, `TimestampMixin`
(`created_at` server default, `updated_at` on update). Columns are `Mapped[...]` +
`mapped_column()`; each model defines `__repr__`.

`Base.metadata` carries a naming convention so constraint names are stable across
migrations: `{table}_{col}_key`, `{table}_{col}_fkey`, `{table}_pkey`,
`{col_label}_idx`, `{table}_{constraint}_check`.

`db/models/__init__.py` re-exports every model. Importing the package registers all
mappers, which is what makes `Base.metadata` complete for Alembic autogenerate and lets
string-based relationships (`User.sessions`) resolve.

Two tables exist today:

* **`users`** — credentials, `role` (stored as a string, read through the `user_role`
  property), `is_app_admin` for the bootstrap admin, optional OAuth columns.
* **`sessions`** — one row per login: the *digest* of the current refresh token,
  `expires_at`, `is_active`, plus device/IP/user-agent fingerprint.

### Session scopes — `db/session.py`

One shared lifecycle, three entry points:

| Helper | Used by | Notes |
| --- | --- | --- |
| `get_db_session()` | FastAPI `Depends` | Request-scoped; commit on success, rollback on error |
| `get_db_context()` | manual `async with` (e.g. WebSockets) | Same lifecycle, no DI |
| `get_worker_db_context()` | background workers | Fresh engine with `NullPool` per call, disposed on exit — no cross-fork or cross-event-loop connection reuse |

**This is the origin of the no-commit rule.** The scope commits once, after the handler
returns. Repositories therefore `flush()` (and `refresh()` when a server default is
needed) so callers see generated values, and never `commit()`. A repository that commits
breaks request atomicity: a later failure can no longer roll the earlier write back.

`expire_on_commit=False` keeps ORM objects readable after the commit, which is what lets a
handler return an entity that `response_model` then serialises.

`assert_event_loop_supported()` runs at startup: psycopg cannot run in async mode on
Windows' `ProactorEventLoop`, which uvicorn picks when started without a reloader. Failing
once at boot beats a 500 on every query.

### Drivers

`settings.DATABASE_URL` is `postgresql+psycopg://…` — **psycopg 3** serves both the async
engine and Alembic, so no separate asyncpg driver is required. `DATABASE_DSN` is the
driverless form, used only by `db/todo_pool.py`, whose `pydantic-ai-todo` storage backend
speaks raw asyncpg. That pool is optional: a missing asyncpg install logs and disables the
feature instead of failing boot.

### Migrations

Alembic lives in `backend/alembic/`, with `env.py` importing `app.db.models` for
`target_metadata` and reading the URL from settings.

```bash
uv run alembic revision --autogenerate -m "Description"
uv run alembic upgrade head
```

Review every autogenerated revision before committing it — autogenerate does not see
server-side defaults, enum changes or data moves.

---

## 6. Schemas — `app/schemas/`

Pydantic v2, one module per domain, all inheriting `BaseSchema`
(`from_attributes`, `populate_by_name`, `str_strip_whitespace`, ISO-with-timezone datetime
encoding). `TimestampSchema` adds `created_at` / `updated_at`.

One schema per operation, so a field's rules match the operation that accepts it:

| Suffix | Purpose |
| --- | --- |
| `*Create` | Required fields with `Field()` constraints |
| `*Update` | Every field optional (`T \| None = None`) |
| `*Read` | Response shape: `id` + timestamps |
| `*List` | `items: list[*Read]` + `total: int` |

`schemas/user.py` composes them by inheritance rather than repetition — `UserRegister` →
`UserCreate`, `UserUpdateMe` → `UserUpdate` — and keeps auth payloads (`Token`,
`RefreshToken`, `NewPassword`, `UpdatePassword`) alongside.

Route handlers return `-> Any` and declare `response_model`. Returning the ORM object and
letting `response_model` serialise it avoids a second full Pydantic validation pass;
annotating the handler with the schema instead would pay for validation twice.

Shared response types live in `schemas/base.py`: `Message`, `ErrorResponse`,
`HealthResponse`.

---

## 7. Errors

Services raise domain exceptions; nothing below the API layer knows about HTTP.

`core/exceptions.py` defines `AppException` with class-level `message`, `code` and
`status_code`, and one subclass per outcome:

| Exception | Status | Code |
| --- | --- | --- |
| `BadRequestError` | 400 | `BAD_REQUEST` |
| `AuthenticationError` | 401 | `AUTHENTICATION_ERROR` |
| `PaymentRequiredError` | 402 | `PAYMENT_REQUIRED` |
| `AuthorizationError` | 403 | `AUTHORIZATION_ERROR` |
| `NotFoundError` | 404 | `NOT_FOUND` |
| `AlreadyExistsError` | 409 | `ALREADY_EXISTS` |
| `ValidationError` | 422 | `VALIDATION_ERROR` |
| `RateLimitError` | 429 | `RATE_LIMIT_EXCEEDED` |
| `DatabaseError` / `InternalError` | 500 | `DATABASE_ERROR` / `INTERNAL_ERROR` |
| `ExternalServiceError` | 503 | `EXTERNAL_SERVICE_ERROR` |

Pass a `message` and, where a client can act on it, `details`:

```python
raise NotFoundError("User not found", details={"user_id": str(user_id)})
raise AlreadyExistsError("User with this email already exists", details={"email": email})
```

`api/exception_handlers.py` turns them into responses:

```json
{ "detail": "User not found", "code": "NOT_FOUND", "details": { "user_id": "..." } }
```

`detail` holds the human-readable message so the body matches FastAPI's own
`HTTPException` and validation errors — one field for a client to read — while `code` and
`details` carry the machine-readable part.

Handler behaviour worth knowing:

* 5xx logs at `error`, 4xx at `warning`, both with `path`, `method`, `error_code` and
  `details`.
* **`details` are dropped on 5xx.** They describe our internals; they are logged, never
  shipped.
* 401 responses get `WWW-Authenticate: Bearer`.
* A WebSocket scope raising `AppException` before `accept()` is logged and returns `None`
  — Starlette closes the socket; there is no HTTP body to write.
* The catch-all `Exception` handler logs the traceback and returns a generic
  `INTERNAL_ERROR`.

---

## 8. Observability

`RequestIDMiddleware` accepts an inbound `X-Request-ID` if it is safe to echo
(`[A-Za-z0-9_-]`, ≤64 chars — a rejected value would otherwise let a caller inject CRLF
into logs and headers), otherwise mints a UUID4 hex. The ID lands in three places:
`request.state.request_id`, a `ContextVar` in `core/context.py`, and the response header.
Browsers only see it because CORS lists it in `expose_headers`.

The `ContextVar` is why services and repositories can be correlated without growing a
`request_id` argument or importing from `app.api`.

`core/logging.py` installs two filters on **handlers**, not loggers — a logger's own
filters run only for records created on that logger, so a filter on root would skip every
`logging.getLogger(__name__)` in the app:

* `RequestIDFilter` — stamps `record.request_id` (`-` outside a request), making
  `%(request_id)s` usable and giving structured handlers one field to group a request by.
* `PiiRedactionFilter` — scrubs emails, JWTs, API keys, bearer tokens and password-like
  values from the message, `args`, `extra=` containers (walked up to 4 levels) and the
  rendered traceback. Tracebacks matter most: they routinely echo query parameters.

`install_log_filters()` sweeps other libraries' loggers too, because uvicorn keeps its own
handlers with `propagate = False` and its access log carries full request paths. It is
idempotent — call it again after anything installs a new handler.

---

## 9. Security

### Passwords

`pwdlib` with `Argon2Hasher` first and `BcryptHasher` for legacy hashes.
`verify_password()` returns `(verified, updated_hash)`; when the stored hash is outdated
the service writes the upgraded hash back, so accounts migrate on successful login.

Login is constant-time with respect to account existence: an unknown email is verified
against `DUMMY_HASH` so the response time does not reveal whether the address is
registered.

### Tokens

`TokenType` — `ACCESS`, `REFRESH`, `PASSWORD_RESET` — is carried in the `type` claim, and
`decode_token` requires the expected type. An access token therefore cannot be replayed as
a refresh token or a reset link. Claims: `sub`, `exp`, `iat`, `nbf`, `type`, `jti`, plus
`sid` for the login session.

Refresh tokens are stored as a **SHA-256 digest** in `sessions.refresh_token_hash`, so a
leaked table cannot be replayed. The digest is unsalted on purpose — that is what allows
lookup by token, and a signed JWT already carries 200+ bits of entropy.

Refresh rotates: `UserService.refresh` validates, swaps in the digest of the newly issued
token and updates `last_used_at`. The presented token dies there, so a replay of it (or of
any earlier one) finds no active session.

### Session revocation

Access tokens are stateless — revoking a login kills its *refresh* token; an
already-issued access token stays valid until it expires. Everything that should sign an
account out everywhere calls `deactivate_all_user_sessions`: password change, admin
password reset, and recovery-token reset.

Password recovery is silent for unknown or disabled accounts, and the endpoint's response
text is identical either way, so it cannot be used to enumerate addresses.

---

## 10. A request end to end

`PATCH /api/v1/users/me/password`

1. **RequestID** mints or accepts `X-Request-ID` and binds the `ContextVar`.
2. **CORS**, then **RateLimit** — per-IP fixed window, counted per worker process; docs,
   the OpenAPI schema and the health probe are exempt.
3. **Routing** to `update_password_me` in `api/routes/v1/user.py`.
4. **Dependencies** resolve: `get_db_session` opens a session, `get_user_service` builds
   `UserService`, `get_current_user` decodes the bearer token and loads the `User`.
5. **Body validation** against `UpdatePassword`.
6. **Route** delegates: `await service.change_password(current_user, body)`.
7. **Service** applies the rules — account has a password, current password verifies, new
   password differs — then hashes and calls two repository functions: `update`, then
   `deactivate_all_user_sessions`.
8. **Repositories** `flush()`; they do not commit.
9. **Return** `Message(...)`; the session scope commits once, as the dependency unwinds.
10. On any raised `AppException` the scope rolls back instead, and the handler renders
    `{detail, code, details}` with the ID already on the response.

---

## 11. Adding a domain

For a thin domain — no infrastructure of its own — five files, bottom up:

1. `app/db/models/<entity>.py`, re-exported from `db/models/__init__.py`.
2. `uv run alembic revision --autogenerate -m "Add <entity>"`, then read the revision.
3. `app/schemas/<entity>.py` — `*Create` / `*Update` / `*Read` / `*List` on `BaseSchema`.
4. `app/repository/<entity>.py` — query functions, keyword-only after `db`, returning
   entities.
5. `app/services/<entity>.py` — a class holding `db`, raising domain exceptions.
6. `app/api/deps.py` — a factory and an `Annotated` alias.
7. `app/api/routes/v1/<entity>.py` — a router, included in `routes/v1/__init__.py`.
8. `backend/tests/…` mirroring the source path.

### Thin vs. thick

A domain that owns infrastructure — an API client, an adapter, a pipeline, parsers,
templates — becomes a subpackage instead:

```
app/services/rag/
├── __init__.py        # re-exports the facade, and nothing else
├── facade.py          # the only class routes see
├── ingestion.py       # internal sub-service
├── vectorstore.py     # infra
└── exceptions.py      # inherits from core/exceptions.py
```

Callers import only from the package root; sub-modules are package-internal. Domain
exceptions live in the subpackage and inherit from the `core/exceptions.py` base classes,
so the existing handler still maps them.

`services/email/` is the placeholder for this shape (empty today; SMTP sending currently
lives in `app/utils.py` and is called through `UserService._send_email`, which swallows
failures so a broken mailbox cannot fail the request it rides on).

---

## 12. API conventions

* Every route under `/api/v1/`; URLs kebab-case; one module per domain entity, tagged
  where it is included.
* `POST` → `201`; `DELETE` → `204` with `response_model=None` when it returns nothing.
* Pagination is `skip` / `limit` query parameters with bounds, and the list endpoint
  returns `items` + `total` so a client can page without a second call:

  ```python
  skip: Annotated[int, Query(ge=0)] = 0
  limit: Annotated[int, Query(ge=1, le=200)] = 100
  ```

* Declare static paths **before** `/{id}`, or `/{id}` swallows them — this is why
  `/me`, `/signup` and `/login/*` come first in `routes/v1/user.py`.
* Handlers are `-> Any` with `response_model`, except where the return type *is* the
  schema and no ORM object is involved (`-> Token`, `-> Message`).

---

## 13. Configuration

`core/config.py` is the only module that reads the environment. `Settings`
(pydantic-settings) loads the repo-root `.env`, and derived values are `@computed_field`
properties rather than duplicated strings: `DATABASE_URL`, `DATABASE_URL_SYNC`,
`DATABASE_DSN`, `all_cors_origins`, `rate_limit_exempt_paths`, `emails_enabled`,
`session_https_only`.

A model validator rejects `"changethis"` for `SECRET_KEY`, `POSTGRES_PASSWORD` and
`FIRST_SUPERUSER_PASSWORD` — a warning locally, a hard failure in staging and production.

Everything optional is off by default and gated by its own flag: `RATE_LIMIT_ENABLED`,
`SESSION_ENABLED` (signed cookies, and its `itsdangerous` import stays inside the branch
so nothing pays for it while off), `SENTRY_DSN`, `REDIS_URL`, `DB_ECHO`.

---

## 14. Testing

`backend/tests/` mirrors the source layout (`app/services/user.py` →
`tests/services/test_user.py`); the package skeleton is in place and the suites are still
to be written.

The project runs on **anyio**, not pytest-asyncio: mark async tests `@pytest.mark.anyio`,
with the backend pinned by the `anyio_backend` fixture. API tests use
`httpx.AsyncClient` over `ASGITransport` rather than `TestClient`, so they exercise the
same event loop uvicorn uses — which is exactly where the middleware ordering and session
lifecycle above can go wrong.

---

## 15. Invariants

The short list worth re-reading before a review:

1. Repositories `flush()` + `refresh()`, never `commit()`.
2. Routes call services; never repositories.
3. Handlers return `-> Any`; `response_model` serialises.
4. Services raise domain exceptions; they never return error codes or `None` for
   "not found".
5. `datetime.now(UTC)`, never `datetime.utcnow()`.
6. Constant-time comparison for any secret compared by value
   (`secrets.compare_digest`).
7. No raw `Depends()` in a route signature — use an alias from `deps.py`.
8. `core/config.py` is the only reader of the environment.
9. New business domains go under `services/`, not into a new top-level package.
10. `details` never travel to a client on a 5xx.
