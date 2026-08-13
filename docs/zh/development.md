# FastAPI 项目 - 开发指南

**语言 / Language: [English](../en/development.md) | [中文](development.md)**

## Docker Compose

* 使用 Docker Compose 启动本地技术栈：

```bash
docker compose watch
```

* 现在你可以打开浏览器，访问以下地址：

前端，使用 Docker 构建，根据路径处理路由：<http://localhost:5173>

后端，基于 OpenAPI 的 JSON Web API：<http://localhost:8000>

基于 Swagger UI 的自动交互式文档（来自 OpenAPI 后端）：<http://localhost:8000/docs>

Adminer，数据库网页管理工具：<http://localhost:8080>

Traefik UI，用于查看代理如何处理路由：<http://localhost:8090>

**注意**：第一次启动技术栈时，可能需要一分钟左右才能就绪，因为后端要等待数据库准备好并完成所有配置。你可以查看日志来监控这个过程。

要查看日志，请（在另一个终端中）运行：

```bash
docker compose logs
```

要查看某个特定服务的日志，添加服务名即可，例如：

```bash
docker compose logs backend
```

## Mailcatcher

Mailcatcher 是一个简单的 SMTP 服务器，用于在本地开发期间捕获后端发送的所有邮件。邮件不会真正发出，而是被捕获并显示在网页界面中。

这在以下场景中很有用：

* 在开发过程中测试邮件功能
* 校验邮件内容和格式
* 在不发送真实邮件的情况下调试与邮件相关的功能

当在本地使用 Docker Compose 运行时，后端会自动配置为使用 Mailcatcher（SMTP 端口为 1025）。所有被捕获的邮件都可以在 <http://localhost:1080> 查看。

## 本地开发

Docker Compose 文件的配置使每个服务在 `localhost` 上使用不同的端口。

对于后端和前端，它们使用的端口与本地开发服务器所使用的端口相同，因此后端地址为 `http://localhost:8000`，前端地址为 `http://localhost:5173`。

这样一来，你可以关闭某个 Docker Compose 服务，改为启动它对应的本地开发服务，其余部分依然可以正常工作，因为使用的端口是一致的。

例如，你可以在 Docker Compose 中停止 `frontend` 服务，在另一个终端运行：

```bash
docker compose stop frontend
```

然后启动本地前端开发服务器：

```bash
bun run dev
```

或者你可以停止 `backend` 这个 Docker Compose 服务：

```bash
docker compose stop backend
```

然后运行后端的本地开发服务器：

```bash
cd backend
fastapi dev app/main.py
```

## 在 `localhost.tiangolo.com` 下使用 Docker Compose

启动 Docker Compose 技术栈时，默认使用 `localhost`，每个服务（后端、前端、adminer 等）使用不同的端口。

当你将其部署到生产环境（或预发布环境）时，每个服务会被部署到不同的子域名下，例如后端使用 `api.example.com`，前端使用 `dashboard.example.com`。

在[部署](deployment.md)指南中，你可以了解到关于 Traefik（已配置好的代理）的内容。Traefik 就是负责根据子域名将流量转发到各个服务的组件。

如果你想在本地测试这一整套流程是否正常工作，可以编辑本地的 `.env` 文件，修改：

```dotenv
DOMAIN=localhost.tiangolo.com
```

Docker Compose 文件会用这个值来配置各服务的基础域名。

Traefik 会据此将 `api.localhost.tiangolo.com` 的流量转发给后端，将 `dashboard.localhost.tiangolo.com` 的流量转发给前端。

`localhost.tiangolo.com` 是一个特殊域名，它（连同其所有子域名）被配置指向 `127.0.0.1`。你可以用它来进行本地开发。

修改之后，重新运行：

```bash
docker compose watch
```

在部署到生产环境时，主 Traefik 是在 Docker Compose 文件之外单独配置的。而在本地开发中，`compose.override.yml` 里包含了一个 Traefik 实例，仅用于让你测试域名是否按预期工作，例如 `api.localhost.tiangolo.com` 和 `dashboard.localhost.tiangolo.com`。

## Docker Compose 文件与环境变量

项目根目录有一个主 `compose.yml` 文件，包含适用于整个技术栈的所有配置，`docker compose` 会自动使用它。

此外还有一个 `compose.override.yml`，包含针对开发环境的覆盖配置，例如将源代码挂载为卷。`docker compose` 会自动使用它，在 `compose.yml` 的基础上应用这些覆盖配置。

这些 Docker Compose 文件使用 `.env` 文件中的配置，将其作为环境变量注入到容器中。

它们还会使用在调用 `docker compose` 命令之前、脚本中设置的一些额外环境变量配置。

修改变量之后，请确保重启技术栈：

```bash
docker compose watch
```

## .env 文件

`.env` 文件包含了你所有的配置、生成的密钥和密码等信息。

根据你的工作流程，你可能想把它从 Git 中排除，例如当你的项目是公开的时候。在这种情况下，你需要确保为 CI 工具设置一种方式，使其在构建或部署项目时能够获取到这个文件。

一种做法是把每个环境变量都添加到你的 CI/CD 系统中，并修改 `compose.yml` 文件，让它读取指定的环境变量，而不是读取 `.env` 文件。

## Pre-commit 与代码检查

我们使用一个叫 [prek](https://prek.j178.dev/) 的工具（[Pre-commit](https://pre-commit.com/) 的现代化替代方案）来进行代码检查和格式化。

安装之后，它会在你执行 git 提交之前自动运行。这样可以确保代码在提交之前就是一致且格式化好的。

项目根目录下有一个 `.pre-commit-config.yaml` 文件包含相关配置。

#### 安装 prek 以自动运行

`prek` 已经是项目依赖的一部分。

安装并配置好 `prek` 工具之后，你需要将它"安装"到本地仓库中，以便它在每次提交前自动运行。

使用 `uv`，你可以这样做（请确保你在 `backend` 目录内）：

```bash
❯ uv run prek install -f
prek installed at `../.git/hooks/pre-commit`
```

`-f` 参数会强制安装，以覆盖之前可能已经安装过的 `pre-commit` 钩子。

现在，每当你尝试提交时，例如：

```bash
git commit
```

……prek 会自动运行，检查并格式化你即将提交的代码，并要求你用 git 重新添加（stage）这些代码后才能提交。

之后你可以再次 `git add` 修改过的文件，然后就可以提交了。

#### 手动运行 prek 钩子

你也可以使用 `uv` 手动对所有文件运行 `prek`：

```bash
❯ uv run prek run --all-files
check for added large files..............................................Passed
check toml...............................................................Passed
check yaml...............................................................Passed
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
biome check..............................................................Passed
```

## 各服务地址（URL）

生产环境或预发布环境的地址会使用相同的路径，只是换成你自己的域名。

### 开发环境地址

用于本地开发的地址。

前端：<http://localhost:5173>

后端：<http://localhost:8000>

自动交互式文档（Swagger UI）：<http://localhost:8000/docs>

自动备用文档（ReDoc）：<http://localhost:8000/redoc>

Adminer：<http://localhost:8080>

Traefik UI：<http://localhost:8090>

MailCatcher：<http://localhost:1080>

### 配置了 `localhost.tiangolo.com` 之后的开发环境地址

用于本地开发的地址。

前端：<http://dashboard.localhost.tiangolo.com>

后端：<http://api.localhost.tiangolo.com>

自动交互式文档（Swagger UI）：<http://api.localhost.tiangolo.com/docs>

自动备用文档（ReDoc）：<http://api.localhost.tiangolo.com/redoc>

Adminer：<http://localhost.tiangolo.com:8080>

Traefik UI：<http://localhost.tiangolo.com:8090>

MailCatcher：<http://localhost.tiangolo.com:1080>
