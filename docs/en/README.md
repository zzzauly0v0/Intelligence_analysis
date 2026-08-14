# Intelligence Analysis

**Language / 语言: [English](../en/README.md) | [中文](README.md)**

<a href="https://github.com/fastapi/full-stack-fastapi-template/actions?query=workflow%3A%22Test+Docker+Compose%22" target="_blank"><img src="https://github.com/fastapi/full-stack-fastapi-template/workflows/Test%20Docker%20Compose/badge.svg" alt="Test Docker Compose"></a> <a href="https://github.com/fastapi/full-stack-fastapi-template/actions?query=workflow%3A%22Test+Backend%22" target="_blank"><img src="https://github.com/fastapi/full-stack-fastapi-template/workflows/Test%20Backend/badge.svg" alt="Test Backend"></a> <a href="https://coverage-badge.samuelcolvin.workers.dev/redirect/fastapi/full-stack-fastapi-template" target="_blank"><img src="https://coverage-badge.samuelcolvin.workers.dev/fastapi/full-stack-fastapi-template.svg" alt="Coverage"></a>

## Technology Stack and Features

* ⚡ [**FastAPI**](https://fastapi.tiangolo.com) for the Python backend API.
* 🧰 [SQLModel](https://sqlmodel.tiangolo.com) for interacting with SQL databases using Python (ORM).
* 🔍 [Pydantic](https://docs.pydantic.dev), used by FastAPI for data validation and configuration management.
* 💾 [PostgreSQL](https://www.postgresql.org) as the SQL database.
* 🚀 [React](https://react.dev) for the frontend.
* 💃 Modern frontend technologies including TypeScript, Hooks, [Vite](https://vitejs.dev), and more.
* 🎨 [Tailwind CSS](https://tailwindcss.com) and [shadcn/ui](https://ui.shadcn.com) for frontend components.
* 🤖 Automatically generated frontend client.
* 🧪 [Playwright](https://playwright.dev) for end-to-end (E2E) testing.
* 🦇 Dark mode support.
* 🐋 [Docker Compose](https://www.docker.com) for development and production environments.
* 🔒 Secure password hashing enabled by default.
* 🔑 JWT (JSON Web Token) authentication.
* 📫 Email-based password recovery.
* 📬 [MailCatcher](https://mailcatcher.me) for testing emails locally during development.
* ✅ Tests written with [Pytest](https://pytest.org).
* 📞 [Traefik](https://traefik.io) as a reverse proxy / load balancer.
* 🚢 Deployment instructions based on Docker Compose, including automatic HTTPS certificate management through the frontend Traefik proxy.
* 🏭 CI (Continuous Integration) and CD (Continuous Deployment) powered by GitHub Actions.

### Dashboard Login

![Dashboard login screenshot](../../img/login.png)

### Dashboard - Administrator

![Admin dashboard screenshot](../../img/dashboard.png)

### Dashboard - Items

![Items dashboard screenshot](../../img/dashboard-items.png)

### Dashboard - Dark Mode

![Dark mode dashboard screenshot](../../img/dashboard-dark.png)

### Interactive API Documentation

![API docs](../../img/docs.png)

## TODO

Our project development roadmap is available in [TODO.md](./doc/TODO.md).

## Product Roadmap

The complete project design process and initial project presentation are documented in [roadmap.md](./docs/roadmap.md).

## Architecture

The system architecture design is documented in [structure.md](./docs/structure.md).

## Backend Development

Backend documentation: [backend/README.md](./backend/README.md).

## Frontend Development

Frontend documentation: [frontend/README.md](./frontend/README.md).

## Deployment

Deployment documentation: [deployment.md](./deployment.md).

## Development

General development documentation: [development.md](./development.md).

It includes instructions for using Docker Compose, configuring custom local domains, setting up `.env` files, and more.

### Configuration

You can update the configuration values in the `.env` file to customize your application.

Before deployment, make sure to change at least the following values:

* `SECRET_KEY`
* `FIRST_SUPERUSER_PASSWORD`
* `POSTGRES_PASSWORD`

You can, and should, provide these values as environment variables through a secret management system.

Read the [deployment.md](./deployment.md) documentation for more details.

### Generating Secret Keys

Some environment variables in the `.env` file use `changethis` as their default value.

You should replace these values with secure secrets. You can generate a secure secret using the following command:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the generated value and use it as a password or secret key. Run the command again to generate another secure key.

## How to Use - Alternative Method with Copier

This repository also supports using [Copier](https://copier.readthedocs.io) to generate a new project.

Copier copies all project files, asks you a series of configuration questions, and updates the `.env` file according to your answers.

### Installing Copier

You can install Copier using:

```bash
pip install copier
```

Alternatively, if you already have [**pipx**](https://pipx.pypa.io/), which is recommended, you can install it with:

```bash
pipx install copier
```

**Note:** If you have `pipx`, installing Copier separately is optional. You can run it directly.

### Creating a Project with Copier

First, decide on a name for your new project. In the following example, we will use `my-awesome-project`.

Navigate to the directory that will contain your new project and run the following command with your project name:

```bash
copier copy https://github.com/fastapi/full-stack-fastapi-template my-awesome-project --trust
```

If you have `pipx` but do not have Copier installed, you can run it directly:

```bash
pipx run copier copy https://github.com/fastapi/full-stack-fastapi-template my-awesome-project --trust
```

**Note:** The `--trust` option is required because Copier needs to execute a [post-generation script](https://github.com/fastapi/full-stack-fastapi-template/blob/master/.copier/update_dotenv.py) to update your `.env` file.

### Input Variables

Copier will ask you for several configuration values. You may want to prepare this information before generating your project.

Don't worry if you do not have everything ready, as you can update these values directly in the `.env` file later.

The following are the available input variables and their default values. Some values are generated automatically:

* `project_name`: Default: `"FastAPI Project"`. The project name displayed to API users (stored in `.env`).
* `stack_name`: Default: `"fastapi-project"`. The stack name used for Docker Compose labels and the project name. It should not contain spaces or periods (stored in `.env`).
* `secret_key`: Default: `"changethis"`. The project's secret key used for security. It is stored in `.env` and can be generated using the command described above.
* `first_superuser`: Default: `"admin@example.com"`. The email address of the first superuser (stored in `.env`).
* `first_superuser_password`: Default: `"changethis"`. The password of the first superuser (stored in `.env`).
* `smtp_host`: Default: `""`. The hostname of the SMTP server used for sending emails. You can configure it later in `.env`.
* `smtp_user`: Default: `""`. The username for the SMTP server.
* `smtp_password`: Default: `""`. The password for the SMTP server.
* `emails_from_email`: Default: `"info@example.com"`. The email address used to send emails. You can configure it later in `.env`.
* `postgres_password`: Default: `"changethis"`. The PostgreSQL database password, stored in `.env`. You can generate a secure password using the command described above.
* `sentry_dsn`: Default: `""`. The Sentry DSN, if you use Sentry. You can configure it later.

## 🙏 Acknowledgements

This project was inspired by the following projects:

* [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) by @tiangolo
* [FastAPI Best Practices](https://github.com/zhanymkanov/fastapi-best-practices) by @zhanymkanov

## License

The Full Stack FastAPI template is licensed under the terms of the MIT License.
