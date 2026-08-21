---
description: Testing standards, fixtures, async test patterns
globs: ["backend/tests/**/*.py", "tests/**/*.py", "**/test_*.py", "**/conftest.py"]
---

# Testing

## Running Tests

```bash
cd backend
uv run pytest                                    # all tests (testpaths = ["tests"])
uv run pytest tests/services/test_user.py -v     # single file
uv run pytest -k "register" -v                   # by name
uv run coverage run -m pytest && uv run coverage report   # with coverage
uv run bash scripts/test.sh                      # same, plus htmlcov/
```

`pytest-cov` is **not** installed — `--cov=app` fails. Coverage runs through the
`coverage` CLI (configured in `pyproject.toml`, `source = ["app"]`).

`addopts` in `pyproject.toml` sets `--strict-markers --strict-config`, so a
misspelled marker (`@pytest.mark.asyncio`) is an error, not a silent skip.

No database, Redis or network is needed — the whole suite runs offline.

## Structure

Mirror the source layout — `app/services/user.py` → `tests/services/test_user.py`,
`app/api/routes/v1/user.py` → `tests/api/routes/v1/test_user.py`. It's a
structural mirror, not a 1:1 filename copy: shared helper files are expected.

- Fixtures go in the `conftest.py` closest to the tests using them:
  `tests/conftest.py` (global — `anyio_backend`, `mock_db`, entity doubles),
  `tests/api/conftest.py` (`client`, patched repositories),
  `tests/services/conftest.py` (service instances).
- Test doubles (`FakeUser`, `FakeArticle`, `auth_headers_for`) live in
  `tests/helpers.py` and are imported, not injected.
- See `backend/tests/README.md` for the full tree.

## Naming

```python
# test_<action>_<scenario>_<expected_result>
def test_register_with_duplicate_email_raises_already_exists
def test_authenticate_inactive_user_raises_authentication_error
def test_read_other_users_profile_as_non_admin_returns_403
```

Group them in behaviour classes, not one class per module — `TestRegister`,
`TestAuthenticate`, `TestUpdateAndDelete`, `TestGetToday`.

## Fixtures

There is no real session — `mock_db` is an `AsyncMock`, and each test stubs the
repository functions it needs. Services are built over it:

```python
# tests/services/conftest.py
@pytest.fixture
def user_service(mock_db: AsyncMock) -> UserService:
    return UserService(mock_db)
```

Entity data comes from the doubles in `tests/helpers.py`, exposed as fixtures in
`tests/conftest.py` (`fake_user`, `fake_admin`, `fake_article`) — never persisted:

```python
@pytest.fixture
def fake_admin() -> FakeUser:
    return FakeUser(email="admin@example.com", role=UserRole.ADMIN)
```

Repository patches target the **service module's alias**, because services do
`from app.repository import user as user_repo`:

```python
with patch("app.services.user.user_repo") as mock_repo:      # service tests: inline
    mock_repo.get_by_id = AsyncMock(return_value=fake_user)
```

```python
# tests/api/conftest.py — route tests: fixture, since auth needs it on every request
@pytest.fixture
def mock_user_repo() -> Iterator[MagicMock]:
    with patch("app.services.user.user_repo") as repo:
        yield repo
```

`app/repository/*.py` has no tests of its own — SQL needs a real Postgres and no
such fixture exists yet. Cover repository behaviour through its service and note
the gap rather than mocking it into a tautology.

## Async Tests

This project uses **anyio**, not pytest-asyncio. Mark async tests with
`@pytest.mark.anyio` (never `@pytest.mark.asyncio`). The backend is pinned via
the `anyio_backend` fixture in `tests/conftest.py`, which returns `"asyncio"` —
the same loop uvicorn uses. Without it every async test would run twice
(asyncio + trio), so never redefine it in a test file.

```python
import pytest

@pytest.mark.anyio
async def test_get_by_id_success(user_service: UserService, fake_user: FakeUser):
    with patch("app.services.user.user_repo") as mock_repo:
        mock_repo.get_by_id = AsyncMock(return_value=fake_user)

        result = await user_service.get_by_id(fake_user.id)

        assert result == fake_user
```

`anyio_backend` is function-scoped: a session-scoped async fixture would raise
`ScopeMismatch` against it.

## API Tests

Use `httpx.AsyncClient`, not `TestClient`. The `client` fixture in
`tests/api/conftest.py` builds a fresh app via `create_app()` and overrides only
`get_db_session` — routing, auth, RBAC and `response_model` serialization all
run for real. It is unavailable outside `tests/api/`.

Auth headers come from `auth_headers_for(user)` in `tests/helpers.py`, and the
caller is resolved through `mock_user_repo.get_by_id` — so any endpoint behind
`CurrentUser` needs that stub, even when the test is about something else:

```python
@pytest.mark.anyio
async def test_create_user_as_admin_success(
    client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
):
    mock_user_repo.get_by_id = AsyncMock(return_value=fake_admin)   # authenticates the caller
    mock_user_repo.get_by_email = AsyncMock(return_value=None)
    mock_user_repo.create = AsyncMock(return_value=FakeUser(email="new@example.com"))

    response = await client.post(
        "/api/v1/users/",
        json={"email": "new@example.com", "password": "securepass123"},
        headers=auth_headers_for(fake_admin),
    )

    assert response.status_code == 201
    assert response.json()["email"] == "new@example.com"
```

Use `by_id(caller, target)` when the handler looks up a second user.

Cover per endpoint: happy path, `401` (no token, and assert
`headers["www-authenticate"] == "Bearer"`), `403` (wrong role / not owner),
`404`, and `422` for schema or `Query()` bound violations.

## Exception Testing

Services raise domain exceptions; every `raise` deserves a test:

```python
@pytest.mark.anyio
async def test_get_by_id_not_found_raises_not_found(user_service: UserService):
    with patch("app.services.user.user_repo") as mock_repo:
        mock_repo.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await user_service.get_by_id(uuid4())
```

Over HTTP, domain exceptions render as `{"detail", "code", "details"}` — assert on
`["code"]` (`NOT_FOUND`, `ALREADY_EXISTS`, `AUTHORIZATION_ERROR`, …), not on prose.
A `422` from Pydantic/`Query()` is **not** a domain exception:
`RequestValidationError` is unhandled, so the body is FastAPI's default
`{"detail": [...]}` with no `code` key — assert the status only.

## Rules

- Each test is independent — no shared mutable state
- Use plain `assert` (pytest rewrites for detailed output)
- One logical assertion per test (multiple asserts are fine if testing one behavior)
- Use the doubles in `tests/helpers.py` for test data, not raw dicts
- Async tests use `@pytest.mark.anyio` — this project runs on anyio, not pytest-asyncio
- `assert_not_awaited()` on a `MagicMock` child attribute raises `AttributeError`;
  assign `mock_repo.method = AsyncMock()` first
- `ty` cannot narrow `await_args` (`_Call | None`) — bind it, then
  `assert await_args is not None` before reading `.kwargs`

Scaffolding a new test file? Use `/add_test <module>`.