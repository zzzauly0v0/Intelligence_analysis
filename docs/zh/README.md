# Intelligence_analysis 

**语言 / Language: [English](../en/README.md) | [中文](README.md)**

<a href="https://github.com/fastapi/full-stack-fastapi-template/actions?query=workflow%3A%22Test+Docker+Compose%22" target="_blank"><img src="https://github.com/fastapi/full-stack-fastapi-template/workflows/Test%20Docker%20Compose/badge.svg" alt="Test Docker Compose"></a>
<a href="https://github.com/fastapi/full-stack-fastapi-template/actions?query=workflow%3A%22Test+Backend%22" target="_blank"><img src="https://github.com/fastapi/full-stack-fastapi-template/workflows/Test%20Backend/badge.svg" alt="Test Backend"></a>
<a href="https://coverage-badge.samuelcolvin.workers.dev/redirect/fastapi/full-stack-fastapi-template" target="_blank"><img src="https://coverage-badge.samuelcolvin.workers.dev/fastapi/full-stack-fastapi-template.svg" alt="Coverage"></a>

## 技术栈与功能特性

- ⚡ [**FastAPI**](https://fastapi.tiangolo.com) 用于 Python 后端 API。
  - 🧰 [SQLModel](https://sqlmodel.tiangolo.com) 用于 Python 与 SQL 数据库的交互（ORM）。
  - 🔍 [Pydantic](https://docs.pydantic.dev)，由 FastAPI 使用，用于数据校验和配置管理。
  - 💾 [PostgreSQL](https://www.postgresql.org) 作为 SQL 数据库。
- 🚀 [React](https://react.dev) 用于前端。
  - 💃 使用 TypeScript、hooks、[Vite](https://vitejs.dev) 等现代前端技术栈的组成部分。
  - 🎨 [Tailwind CSS](https://tailwindcss.com) 和 [shadcn/ui](https://ui.shadcn.com) 用于前端组件。
  - 🤖 自动生成的前端客户端。
  - 🧪 [Playwright](https://playwright.dev) 用于端到端（E2E）测试。
  - 🦇 支持深色模式。
- 🐋 [Docker Compose](https://www.docker.com) 用于开发和生产环境。
- 🔒 默认提供安全的密码哈希处理。
- 🔑 JWT（JSON Web Token）身份认证。
- 📫 基于邮件的密码找回。
- 📬 [Mailcatcher](https://mailcatcher.me) 用于开发环境下的本地邮件测试。
- ✅ 使用 [Pytest](https://pytest.org) 编写测试。
- 📞 [Traefik](https://traefik.io) 作为反向代理 / 负载均衡器。
- 🚢 提供基于 Docker Compose 的部署说明，包括如何配置前端 Traefik 代理以自动处理 HTTPS 证书。
- 🏭 基于 GitHub Actions 的 CI（持续集成）和 CD（持续部署）。


### 控制台登录

![Dashboard login screenshot](../../img/login.png)
### 控制台 - 管理员

![Admin dashboard screenshot](../../img/dashboard.png)
### 控制台 - 条目

![Items dashboard screenshot](../../img/dashboard-items.png)
### 控制台 - 深色模式

![Dark mode dashboard screenshot](../../img/dashboard-dark.png)
### 交互式 API 文档

![API docs](../../img/docs.png)
## TODO

这是我们的项目开发周期路线[TODO.md](./doc/TODO.md)

## 产品路线图

完整的项目设计流程以及项目初展示[roadmap.md](./docs/roadmap.md)

## 架构图

系统架构设计图[structure.md](./docs/structure.md)

## 后端开发

后端文档：[backend/README.md](./backend/README.md)。

## 前端开发

前端文档：[frontend/README.md](./frontend/README.md)。

## 部署

部署文档：[deployment.md](./deployment.md)。

## 开发

通用开发文档：[development.md](./development.md)。

其中包括使用 Docker Compose、自定义本地域名、`.env` 配置等内容。

### 配置

你可以在 `.env` 文件中更新配置项，以自定义你的配置。

在部署之前，请确保至少修改以下这些值：

- `SECRET_KEY`
- `FIRST_SUPERUSER_PASSWORD`
- `POSTGRES_PASSWORD`

你可以（也应该）将这些值以环境变量的形式从密钥管理系统传入。

阅读 [deployment.md](./deployment.md) 文档以了解更多细节。

### 生成密钥

`.env` 文件中的一些环境变量默认值为 `changethis`。

你需要将它们改为密钥，可以运行以下命令来生成密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

复制生成的内容作为密码 / 密钥。再运行一次即可生成另一个安全密钥。

## 如何使用 - 使用 Copier 的替代方式

本仓库还支持使用 [Copier](https://copier.readthedocs.io) 生成一个新项目。

它会复制所有文件，向你询问一些配置问题，并根据你的回答更新 `.env` 文件。

### 安装 Copier

你可以通过以下方式安装 Copier：

```bash
pip install copier
```

或者更好的方式，如果你已经安装了 [`pipx`](https://pipx.pypa.io/)，可以这样运行：

```bash
pipx install copier
```

**注意**：如果你有 `pipx`，安装 copier 是可选的，你可以直接运行它。

### 使用 Copier 生成项目

先决定你新项目目录的名称，下面会用到它。例如 `my-awesome-project`。

进入将作为你项目父目录的目录，运行以下命令并带上你的项目名：

```bash
copier copy https://github.com/fastapi/full-stack-fastapi-template my-awesome-project --trust
```

如果你有 `pipx` 但没有安装 `copier`，你可以直接运行它：

```bash
pipx run copier copy https://github.com/fastapi/full-stack-fastapi-template my-awesome-project --trust
```

**注意**：`--trust` 选项是必须的，因为需要执行一个[创建后脚本](https://github.com/fastapi/full-stack-fastapi-template/blob/master/.copier/update_dotenv.py)来更新你的 `.env` 文件。

### 输入变量

Copier 会向你询问一些数据，你可能希望在生成项目之前就准备好这些信息。

但不用担心，之后你也可以直接在 `.env` 文件中更新这些内容。

以下是输入变量及其默认值（部分为自动生成）：

- `project_name`：（默认值：`"FastAPI Project"`）项目名称，会展示给 API 用户（在 .env 中）。
- `stack_name`：（默认值：`"fastapi-project"`）用于 Docker Compose 标签和项目名称的栈名（不含空格和句点）（在 .env 中）。
- `secret_key`：（默认值：`"changethis"`）项目的密钥，用于安全防护，存储在 .env 中，你可以用上面的方法生成一个。
- `first_superuser`：（默认值：`"admin@example.com"`）第一个超级用户的邮箱（在 .env 中）。
- `first_superuser_password`：（默认值：`"changethis"`）第一个超级用户的密码（在 .env 中）。
- `smtp_host`：（默认值：""）用于发送邮件的 SMTP 服务器主机，你可以稍后在 .env 中设置。
- `smtp_user`：（默认值：""）用于发送邮件的 SMTP 服务器用户名。
- `smtp_password`：（默认值：""）用于发送邮件的 SMTP 服务器密码。
- `emails_from_email`：（默认值：`"info@example.com"`）用于发送邮件的邮箱账号，你可以稍后在 .env 中设置。
- `postgres_password`：（默认值：`"changethis"`）PostgreSQL 数据库的密码，存储在 .env 中，你可以用上面的方法生成一个。
- `sentry_dsn`：（默认值：""）如果你使用 Sentry，这里是它的 DSN，你可以稍后设置。

## 🙏 感谢
该项目深受以下项目灵感启发:

- [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template.git) by @tiangolo
- [FastAPI Best Practices](https://github.com/zhanymkanov/fastapi-best-practices.git) by @zhanymkanov

## 许可证

Full Stack FastAPI 模板依据 MIT 许可证的条款进行授权。
