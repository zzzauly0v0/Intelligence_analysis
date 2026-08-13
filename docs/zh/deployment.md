# FastAPI 项目 - 部署

**语言 / Language: [English](../en/deployment.md) | [中文](deployment.md)**

你可以使用 Docker Compose 将项目部署到远程服务器。

本项目要求你有一个 Traefik 代理来处理与外部世界的通信以及 HTTPS 证书。

你可以使用 CI/CD（持续集成与持续部署）系统来自动部署，项目中已经配置好了基于 GitHub Actions 的相关流程。

但你需要先配置好几件事。🤓

## 准备工作

* 准备并可用的远程服务器。
* 配置你域名的 DNS 记录，指向你刚创建的服务器的 IP。
* 为你的域名配置一个通配符子域名，以便你可以为不同的服务使用多个子域名，例如 `*.fastapi-project.example.com`。这在访问不同组件时会很有用，例如 `dashboard.fastapi-project.example.com`、`api.fastapi-project.example.com`、`traefik.fastapi-project.example.com`、`adminer.fastapi-project.example.com` 等。同样也适用于 `staging` 环境，例如 `dashboard.staging.fastapi-project.example.com`、`adminer.staging.fastapi-project.example.com` 等。
* 在远程服务器上安装并配置 [Docker](https://docs.docker.com/engine/install/)（Docker Engine，而非 Docker Desktop）。

## 公共 Traefik

我们需要一个 Traefik 代理来处理入站连接和 HTTPS 证书。

以下步骤只需要执行一次。

### Traefik 的 Docker Compose

* 创建一个远程目录来存放你的 Traefik Docker Compose 文件：

```bash
mkdir -p /root/code/traefik-public/
```

将 Traefik 的 Docker Compose 文件复制到你的服务器上。你可以在本地终端中运行 `rsync` 命令来完成：

```bash
rsync -a compose.traefik.yml root@your-server.example.com:/root/code/traefik-public/
```

### Traefik 公共网络

这个 Traefik 会期望有一个名为 `traefik-public` 的 Docker "公共网络"，用来与你的技术栈通信。

这样一来，就会有一个单独的公共 Traefik 代理来处理与外部世界的通信（HTTP 和 HTTPS），在它后面，即使是在同一台服务器上，你也可以有一个或多个使用不同域名的技术栈。

要创建名为 `traefik-public` 的 Docker "公共网络"，在你的远程服务器上运行以下命令：

```bash
docker network create traefik-public
```

### Traefik 环境变量

Traefik 的 Docker Compose 文件在启动前，需要你在终端中设置一些环境变量。你可以在远程服务器上运行以下命令来完成。

* 创建 HTTP 基本认证的用户名，例如：

```bash
export USERNAME=admin
```

* 创建一个包含 HTTP 基本认证密码的环境变量，例如：

```bash
export PASSWORD=changethis
```

* 使用 openssl 生成 HTTP 基本认证密码的"哈希"版本，并存储到环境变量中：

```bash
export HASHED_PASSWORD=$(openssl passwd -apr1 $PASSWORD)
```

要验证哈希后的密码是否正确，你可以打印出来查看：

```bash
echo $HASHED_PASSWORD
```

* 创建一个包含你服务器域名的环境变量，例如：

```bash
export DOMAIN=fastapi-project.example.com
```

* 创建一个包含 Let's Encrypt 所需邮箱的环境变量，例如：

```bash
export EMAIL=admin@example.com
```

**注意**：你需要设置一个不同的邮箱，`@example.com` 结尾的邮箱是无法使用的。

### 启动 Traefik 的 Docker Compose

进入你在远程服务器上存放 Traefik Docker Compose 文件的目录：

```bash
cd /root/code/traefik-public/
```

现在环境变量已经设置好，`compose.traefik.yml` 也已经就位，你可以运行以下命令启动 Traefik 的 Docker Compose：

```bash
docker compose -f compose.traefik.yml up -d
```

## 部署 FastAPI 项目

现在 Traefik 已经就位，你可以使用 Docker Compose 部署你的 FastAPI 项目了。

**注意**：你可能想直接跳到关于使用 GitHub Actions 进行持续部署的章节。

## 复制代码

```bash
rsync -av --filter=":- .gitignore" ./ root@your-server.example.com:/root/code/app/
```

注意：`--filter=":- .gitignore"` 告诉 `rsync` 使用与 git 相同的规则，忽略那些被 git 忽略的文件，例如 Python 虚拟环境。

## 环境变量

你需要先设置一些环境变量。

### 生成密钥

`.env` 文件中的一些环境变量默认值为 `changethis`。

你需要将它们改为密钥，可以运行以下命令来生成密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

复制生成的内容作为密码 / 密钥。再运行一次即可生成另一个安全密钥。

### 必需的环境变量

设置 `ENVIRONMENT`，默认值为 `local`（用于开发环境），但在部署到服务器时应设置为 `staging` 或 `production` 之类的值：

```bash
export ENVIRONMENT=production
```

设置 `DOMAIN`，默认值为 `localhost`（用于开发环境），但在部署时应使用你自己的域名，例如：

```bash
export DOMAIN=fastapi-project.example.com
```

将 `POSTGRES_PASSWORD` 设置为与 `changethis` 不同的值：

```bash
export POSTGRES_PASSWORD="changethis"
```

设置用于签名令牌的 `SECRET_KEY`：

```bash
export SECRET_KEY="changethis"
```

注意：你可以使用上面的 Python 命令来生成一个安全的密钥。

将 `FIRST_SUPER_USER_PASSWORD` 设置为与 `changethis` 不同的值：

```bash
export FIRST_SUPERUSER_PASSWORD="changethis"
```

设置 `BACKEND_CORS_ORIGINS` 以包含你的域名：

```bash
export BACKEND_CORS_ORIGINS="https://dashboard.${DOMAIN?Variable not set},https://api.${DOMAIN?Variable not set}"
```

你还可以设置其他几个环境变量：

* `PROJECT_NAME`：项目名称，用于 API 文档和邮件中。
* `STACK_NAME`：用于 Docker Compose 标签和项目名称的栈名，`staging`、`production` 等环境应使用不同的值。你可以使用同一个域名并将点替换为短横线，例如 `fastapi-project-example-com` 和 `staging-fastapi-project-example-com`。
* `BACKEND_CORS_ORIGINS`：以逗号分隔的允许的 CORS 来源列表。
* `FIRST_SUPERUSER`：第一个超级用户的邮箱，该超级用户可以创建新用户。
* `SMTP_HOST`：用于发送邮件的 SMTP 服务器主机，这通常来自你的邮件服务提供商（例如 Mailgun、Sparkpost、Sendgrid 等）。
* `SMTP_USER`：用于发送邮件的 SMTP 服务器用户名。
* `SMTP_PASSWORD`：用于发送邮件的 SMTP 服务器密码。
* `EMAILS_FROM_EMAIL`：用于发送邮件的邮箱账号。
* `POSTGRES_SERVER`：PostgreSQL 服务器的主机名。你可以保留默认值 `db`，它由同一个 Docker Compose 提供。通常只有在使用第三方数据库服务时才需要修改它。
* `POSTGRES_PORT`：PostgreSQL 服务器的端口。你可以保留默认值。通常只有在使用第三方数据库服务时才需要修改它。
* `POSTGRES_USER`：Postgres 用户名，你可以保留默认值。
* `POSTGRES_DB`：本应用要使用的数据库名称。你可以保留默认值 `app`。
* `SENTRY_DSN`：如果你使用 Sentry，这里是它的 DSN。

## GitHub Actions 环境变量

还有一些仅供 GitHub Actions 使用的环境变量，你可以进行配置：

* `LATEST_CHANGES`：由 GitHub Action [latest-changes](https://github.com/tiangolo/latest-changes) 使用，根据已合并的 PR 自动添加发布说明。它是一个个人访问令牌，具体细节请阅读相关文档。
* `SMOKESHOW_AUTH_KEY`：用于通过 [Smokeshow](https://github.com/samuelcolvin/smokeshow) 处理和发布代码覆盖率，请按照它们的说明创建一个（免费的）Smokeshow 密钥。

### 使用 Docker Compose 部署

环境变量设置好之后，你可以使用 Docker Compose 进行部署：

```bash
cd /root/code/app/
docker compose -f compose.yml build
docker compose -f compose.yml up -d
```

在生产环境中，你不会希望使用 `compose.override.yml` 里的覆盖配置，这就是为什么我们显式指定使用 `compose.yml` 这个文件。

## 持续部署（CD）

你可以使用 GitHub Actions 来自动部署你的项目。😎

你可以配置多个环境的部署。

项目中已经配置好了两个环境：`staging` 和 `production`。🚀

### 安装 GitHub Actions Runner

* 在你的远程服务器上，为 GitHub Actions 创建一个用户：

```bash
sudo adduser github
```

* 为 `github` 用户添加 Docker 权限：

```bash
sudo usermod -aG docker github
```

* 临时切换到 `github` 用户：

```bash
sudo su - github
```

* 进入 `github` 用户的主目录：

```bash
cd
```

* [按照官方指南安装 GitHub Action 自托管 runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners#adding-a-self-hosted-runner-to-a-repository)。

* 当被要求填写标签（label）时，为该环境添加一个标签，例如 `production`。你也可以之后再添加标签。

安装完成后，指南会告诉你运行一个命令来启动 runner。不过，一旦你终止该进程，或者你与服务器的本地连接断开，runner 就会停止。

为了确保它能随系统启动并持续运行，你可以将其安装为一个服务。要这样做，先退出 `github` 用户，返回 `root` 用户：

```bash
exit
```

执行后，你会回到之前的用户，并处于该用户之前所在的目录。

在进入 `github` 用户目录之前，你需要先成为 `root` 用户（你可能已经是了）：

```bash
sudo su
```

* 以 `root` 用户身份，进入 `github` 用户主目录下的 `actions-runner` 目录：

```bash
cd /home/github/actions-runner
```

* 以 `github` 用户身份，将自托管 runner 安装为服务：

```bash
./svc.sh install github
```

* 启动服务：

```bash
./svc.sh start
```

* 检查服务状态：

```bash
./svc.sh status
```

你可以在官方指南中阅读更多相关内容：[将自托管 runner 应用配置为服务](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/configuring-the-self-hosted-runner-application-as-a-service)。

### 配置 GitHub Environments

部署工作流使用 [GitHub Environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) 来管理 `staging` 和 `production`。这样可以实现环境专属的密钥、部署保护规则（例如必需的审阅者、等待计时器）以及部署状态跟踪。

要配置它们，进入你仓库的 **Settings** > **Environments**，创建 `staging` 和 `production` 这两个环境。

### 设置密钥

对于每个 GitHub Environment（`staging` 和 `production`），将所需的密钥配置为[环境密钥](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#creating-secrets-for-an-environment)。相比[仓库密钥](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#creating-secrets-for-a-repository)，环境密钥更受推荐，因为它们的作用范围限定于特定环境，可以减少暴露面，并与你配置的任何保护规则保持一致。

当前的 Github Actions 工作流需要以下密钥：

* `DOMAIN_PRODUCTION`
* `DOMAIN_STAGING`
* `STACK_NAME_PRODUCTION`
* `STACK_NAME_STAGING`
* `EMAILS_FROM_EMAIL`
* `FIRST_SUPERUSER`
* `FIRST_SUPERUSER_PASSWORD`
* `POSTGRES_PASSWORD`
* `SECRET_KEY`
* `LATEST_CHANGES`
* `SMOKESHOW_AUTH_KEY`

## GitHub Action 部署工作流

`.github/workflows` 目录中已经配置好了用于部署到各环境的 GitHub Action 工作流（带有对应标签的 GitHub Actions runner）：

* `staging`：在推送（或合并）到 `master` 分支之后触发。
* `production`：在发布一个 release 之后触发。

这两个工作流都分别关联到各自的 GitHub Environment，因此部署情况会显示在仓库的 **Environments** 部分，并会遵循你配置的任何保护规则。

如果你需要添加额外的环境，可以以此为起点进行扩展。

## 各服务地址（URL）

将 `fastapi-project.example.com` 替换为你自己的域名。

### 主 Traefik 控制面板

Traefik UI：`https://traefik.fastapi-project.example.com`

### 生产环境

前端：`https://dashboard.fastapi-project.example.com`

后端 API 文档：`https://api.fastapi-project.example.com/docs`

后端 API 基础地址：`https://api.fastapi-project.example.com`

Adminer：`https://adminer.fastapi-project.example.com`

### 预发布环境

前端：`https://dashboard.staging.fastapi-project.example.com`

后端 API 文档：`https://api.staging.fastapi-project.example.com/docs`

后端 API 基础地址：`https://api.staging.fastapi-project.example.com`

Adminer：`https://adminer.staging.fastapi-project.example.com`
