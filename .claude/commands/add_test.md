---
description: Scaffold unit tests for an existing module, in the right layer with the right fixtures
---

Write tests for: $ARGUMENTS

Stack: pytest + **anyio** (never pytest-asyncio) + `unittest.mock` + `httpx.AsyncClient`.
No database or Redis — the whole suite runs offline against patched repositories.
Full tree and fixture table: `backend/tests/README.md`. Conventions: `.claude/rules/testing.md`.

## 1. Pick the layer — the source path decides the test path

Tests mirror `app/`. Find the module under test, then write to the mirrored path:

| Source | Test file | What to assert |
|---|---|---|
| `app/api/routes/v1/<x>.py` | `tests/api/routes/v1/test_<x>.py` | status codes, auth/RBAC, response body, query-param validation |
| `app/services/<x>.py` | `tests/services/test_<x>.py` | domain exceptions, exact repo calls, business rules |
| `app/core/<x>.py` | `tests/core/test_<x>.py` | pure functions — no mocks needed |
| `app/services/crawler/<pkg>/<x>.py` | `tests/services/crawler/<pkg>/test_<x>.py` | pure functions (dates, urls, titles, extract) — no mocks |
| `app/repository/<x>.py` | **stop** | needs a real Postgres; no fixture exists yet. Cover the behaviour through its service instead and say so in your summary. |

New directories need `__init__.py` (every test package has one).

## 2. Reuse the fixtures — do not redefine them

Injected via `conftest.py`, nearest scope wins:

- `tests/conftest.py` — `anyio_backend`, `mock_db`, `fake_user`, `fake_admin`, `fake_article`
- `tests/api/conftest.py` — `client`, `mock_user_repo`, `mock_article_repo`
- `tests/services/conftest.py` — `user_service`

Imported explicitly from `tests/helpers.py` — `FakeUser`, `FakeArticle`, `auth_headers_for(user)`, `by_id(*users)`.

Rules:
- Never redefine `anyio_backend` or `mock_db` in a test file — that breaks the single-backend pin.
- Need a new domain double? Add a `Fake<Entity>` to `tests/helpers.py` (kwargs-only, typed, mirroring the `*Read` schema fields) and a `fake_<entity>` fixture to `tests/conftest.py`.
- Need a new repo patch? Add `mock_<entity>_repo` to `tests/api/conftest.py`:
  ```python
  @pytest.fixture
  def mock_<entity>_repo() -> Iterator[MagicMock]:
      with patch("app.services.<entity>.<entity>_repo") as repo:
          yield repo
  ```
  The patch target is the **service module's alias**, because services do
  `from app.repository import <entity> as <entity>_repo` — replacing the whole module
  has to go through that name. (`patch("app.repository.<entity>.get_by_id")` also works
  for a single function, but then you lose the one handle that stubs every repo call.)
- A fixture used by exactly one file stays in that file; promote it to `conftest.py` only on the second user.

## 3. Naming

`test_<action>_<scenario>_<expected_result>`, grouped in behaviour classes (not one class per module):

```python
class TestRegister:
    async def test_register_first_user_becomes_admin(...)
    async def test_register_with_duplicate_email_raises_already_exists(...)
```

## 4. Route test template (`tests/api/routes/v1/`)

```python
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock

from tests.helpers import FakeUser, auth_headers_for


class TestReadEntity:
    @pytest.mark.anyio
    async def test_read_entity_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)   # authenticates the caller

        response = await client.get("/api/v1/entities/1", headers=auth_headers_for(fake_user))

        assert response.status_code == 200
        assert response.json()["id"] == 1
```

- Auth: any endpoint behind `CurrentUser` needs `mock_user_repo.get_by_id` to resolve the token's
  subject. Use `by_id(caller, target)` when the handler looks up a *second* user.
- Cover per endpoint: happy path, `401` no token, `403` wrong role / not-owner, `404` missing,
  `422` schema or `Query()` bound violation (e.g. `limit=201`).
- **Domain** exceptions render as `{"detail": ..., "code": ..., "details": ...}` — assert on
  `["code"]` (`NOT_FOUND`, `ALREADY_EXISTS`, `AUTHENTICATION_ERROR`, `AUTHORIZATION_ERROR`,
  `BAD_REQUEST`, `VALIDATION_ERROR`, …), not on prose.
- A `422` from Pydantic/`Query()` validation is **not** a domain exception —
  `RequestValidationError` is unhandled, so the body is FastAPI's default `{"detail": [...]}`
  with **no `code` key**. Assert the status only.
- `401` responses must carry `response.headers["www-authenticate"] == "Bearer"`.

## 5. Service test template (`tests/services/`)

```python
class TestGetEntity:
    @pytest.mark.anyio
    async def test_get_by_id_not_found_raises_not_found(self, entity_service: EntityService):
        with patch("app.services.entity.entity_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await entity_service.get_by_id(uuid4())
```

- Patch inline with `with patch(...)` here (per-test control), not via a fixture.
- Assert the repo contract: `mock_repo.create.assert_awaited_once_with(service.db, email=..., ...)`.
- Every `raise` in the service needs its own test.

## 6. Gotchas that will bite you

- `@pytest.mark.anyio`, never `@pytest.mark.asyncio`. `--strict-markers` is on, so a typo'd
  marker is an error rather than a silent skip.
- `assert_not_awaited()` on a `MagicMock` child attribute raises
  `AttributeError: 'assert_not_awaited' is not a valid assertion`. Assign it first:
  ```python
  mock_article_repo.list_today = AsyncMock()      # now assert_not_awaited() works
  ```
- `ty` cannot narrow `await_args` (`_Call | None`). Always bind and assert:
  ```python
  await_args = mock_repo.create.await_args
  assert await_args is not None
  kwargs = await_args.kwargs
  ```
- A session-scoped async fixture would `ScopeMismatch` against the function-scoped
  `anyio_backend`. Don't add one without raising that fixture's scope too.
- `client` is only available under `tests/api/` — service and core tests must not request it.

## 7. Verify

```bash
cd backend
uv run pytest tests/<path>/test_<x>.py -v        # the new file
uv run ruff check tests/ --fix && uv run ruff format tests/
uv run ty check tests/
uv run pytest -q                                 # whole suite still green
```

Report the test count added and anything you could not cover (repository SQL, external I/O)
rather than mocking it into a tautology.
