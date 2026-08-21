# Tests

The layout mirrors `app/` so a test's location tells you which layer it exercises.
It is a *structural* mirror, not a 1:1 filename copy — shared `conftest.py` /
`helpers.py` files are expected, and one test module may cover several source
modules.

```
tests/
├── conftest.py                 # global: anyio_backend, mock_db, entity doubles
├── helpers.py                  # shared test doubles: FakeUser, FakeArticle, auth_headers_for
├── api/                        # -> app/api/
│   ├── conftest.py             # ASGI client + patched repositories
│   └── routes/v1/              # -> app/api/routes/v1/
│       ├── test_user.py
│       ├── test_articles.py
│       └── test_utils.py
├── core/                       # -> app/core/
│   └── test_security.py
└── services/                   # -> app/services/
    ├── conftest.py             # service instances over the mocked session
    └── test_user.py
```

## Fixture placement

A fixture lives in the `conftest.py` closest to the tests that use it:

| Scope | File | Contents |
|-------|------|----------|
| Global | `tests/conftest.py` | `anyio_backend`, `mock_db`, `fake_user`, `fake_admin`, `fake_article` |
| API layer | `tests/api/conftest.py` | `client`, `mock_user_repo`, `mock_article_repo` |
| Service layer | `tests/services/conftest.py` | `user_service` |

Test doubles themselves (the `Fake*` classes) go in `tests/helpers.py` — every
layer needs the same ones, and keeping them out of `conftest.py` means they can
be imported explicitly rather than injected.

## Layers

- **`tests/api/`** — drives the real ASGI app through `httpx.AsyncClient`, so
  routing, auth, RBAC, status codes and `response_model` serialization are all
  under test. Only the DB session is overridden; repositories are patched.
- **`tests/services/`** — unit tests for business logic. Repositories are
  patched per test, so these assert on the exact repo calls and on the domain
  exceptions raised.
- **`tests/core/`** — pure functions (hashing, JWT). No mocks needed.

No database or Redis is required — `uv run pytest` runs the whole suite offline.

See `.claude/rules/testing.md` for naming, async (`@pytest.mark.anyio`) and
assertion conventions.
