# FastAPI 项目 - 后端

**语言 / Language: [English](../../en/backend/README.md) | [中文](README.md)**

## 环境要求

* [Docker](https://www.docker.com/)。
* [uv](https://docs.astral.sh/uv/)，用于 Python 包和环境管理。

## Docker Compose

按照 [../development.md](../development.md) 中的指南，使用 Docker Compose 启动本地开发环境。

## 一般工作流程

默认情况下，依赖项通过 [uv](https://docs.astral.sh/uv/) 管理，请前往该页面安装它。

在 `./backend/` 目录下，你可以用以下命令安装所有依赖：

```console
$ uv sync
```

然后你可以用以下命令激活虚拟环境：

```console
$ source .venv/bin/activate
```

请确保你的编辑器使用的是正确的 Python 虚拟环境，其解释器路径为 `backend/.venv/bin/python`。

在 `./backend/app/models.py` 中修改或添加用于数据和 SQL 表的 SQLModel 模型，在 `./backend/app/api/` 中添加 API 端点，在 `./backend/app/crud.py` 中添加 CRUD（创建、读取、更新、删除）相关的工具函数。

## VS Code

项目中已经配置好了通过 VS Code 调试器运行后端的相关设置，因此你可以使用断点、暂停执行并查看变量等。

同时也已经配置好，你可以通过 VS Code 的 Python 测试标签页运行测试。

## Docker Compose 覆盖配置

在开发过程中，你可以在 `compose.override.yml` 文件中修改只会影响本地开发环境的 Docker Compose 配置。

对该文件的修改只会影响本地开发环境，不会影响生产环境。因此，你可以添加一些有助于开发流程的"临时性"更改。

例如，后端代码所在的目录会被同步到 Docker 容器中，你所做的代码更改会实时复制到容器内的目录中。这样你可以立即测试你的更改，而不需要重新构建 Docker 镜像。这种做法只应在开发期间使用，生产环境中应使用包含最新后端代码的 Docker 镜像进行构建。但在开发期间，它能让你的迭代速度非常快。

此外还有一个命令覆盖配置，运行的是 `fastapi run --reload` 而不是默认的 `fastapi run`。它会启动一个单独的服务器进程（而不是像生产环境那样启动多个），并在代码发生变化时重新加载该进程。请注意，如果你的 Python 文件存在语法错误并保存了它，进程会因此崩溃并退出，容器也会随之停止。之后，你可以在修复错误后再次运行以重启容器：

```console
$ docker compose watch
```

此外还有一个被注释掉的 `command` 覆盖配置，你可以取消注释它，并注释掉默认的那一行。它会让后端容器运行一个"什么都不做"的进程，但会保持容器处于存活状态。这样你就可以进入正在运行的容器内部执行命令，例如运行 Python 解释器来测试已安装的依赖，或者启动能在检测到变化时自动重新加载的开发服务器。

要以 `bash` 会话进入容器，你可以先启动整个技术栈：

```console
$ docker compose watch
```

然后在另一个终端中，`exec` 进入正在运行的容器：

```console
$ docker compose exec backend bash
```

你应该会看到类似这样的输出：

```console
root@7f2607af31c3:/app#
```

这表示你已经以 `root` 用户身份进入了容器内的 `bash` 会话，当前位于 `/app` 目录下，该目录下还有一个名为 "app" 的子目录，这就是你的代码在容器内所在的位置：`/app/app`。

在这里你可以使用 `fastapi run --reload` 命令来运行支持实时重载的调试服务器。

```console
$ fastapi run --reload app/main.py
```

……它看起来会是这样：

```console
root@7f2607af31c3:/app# fastapi run --reload app/main.py
```

然后按下回车。这样就会运行一个能在检测到代码变化时自动重新加载的服务器。

不过，如果它没有检测到变化，而是遇到了语法错误，进程就会直接报错停止。但由于容器仍然存活，且你处于一个 Bash 会话中，你可以在修复错误后快速重启它，只需再次运行相同的命令（按"上箭头"再按"回车"）。

……正是这个细节，使得让容器保持存活、什么都不做，然后在 Bash 会话中运行实时重载服务器这种做法变得很有用。

## 后端测试

要测试后端，运行：

```console
$ bash ./scripts/test.sh
```

测试使用 Pytest 运行，请在 `./backend/tests/` 中修改和添加测试。

如果你使用 GitHub Actions，测试会自动运行。

### 在已运行的技术栈上运行测试

如果你的技术栈已经启动，只是想运行测试，可以使用：

```bash
docker compose exec backend bash scripts/tests-start.sh
```

`/app/scripts/tests-start.sh` 这个脚本只是在确保技术栈的其余部分已经在运行之后，调用 `pytest`。如果你需要向 `pytest` 传递额外的参数，可以直接传给这个命令，它们会被转发过去。

例如，遇到第一个错误就停止：

```bash
docker compose exec backend bash scripts/tests-start.sh -x
```

### 测试覆盖率

测试运行后，会生成一个 `htmlcov/index.html` 文件，你可以在浏览器中打开它查看测试覆盖率。

## 数据库迁移

由于在本地开发期间，你的 app 目录是作为卷挂载到容器内的，你也可以在容器内使用 `alembic` 命令运行迁移，迁移代码会保存在你的 app 目录中（而不是只存在于容器内部）。因此你可以将它添加到 git 仓库中。

请确保每次修改模型后，都为其创建一个"版本（revision）"，并使用该版本"升级（upgrade）"你的数据库。因为这才是真正更新数据库表结构的操作。否则，你的应用会出现错误。

* 在后端容器中启动一个交互式会话：

```console
$ docker compose exec backend bash
```

* Alembic 已经配置好，会从 `./backend/app/models.py` 中导入你的 SQLModel 模型。

* 修改模型之后（例如添加一个列），在容器内创建一个版本，例如：

```console
$ alembic revision --autogenerate -m "Add column last_name to User model"
```

* 将 alembic 目录中生成的文件提交到 git 仓库。

* 创建完版本之后，对数据库运行迁移（这才是真正改变数据库的操作）：

```console
$ alembic upgrade head
```

如果你完全不想使用迁移功能，可以取消注释 `./backend/app/core/db.py` 文件中以下面这行结尾的代码：

```python
SQLModel.metadata.create_all(engine)
```

并注释掉 `scripts/prestart.sh` 文件中包含以下内容的那一行：

```console
$ alembic upgrade head
```

如果你不想使用默认模型，并希望从一开始就删除/修改它们，且没有任何历史版本，你可以删除 `./backend/app/alembic/versions/` 目录下的版本文件（`.py` Python 文件）。然后按照上述方法创建第一个迁移。

## 邮件模板

邮件模板位于 `./backend/app/email-templates/` 中。这里有两个目录：`build` 和 `src`。`src` 目录包含用于构建最终邮件模板的源文件。`build` 目录包含应用实际使用的最终邮件模板。

在继续之前，请确保你的 VS Code 中已安装 [MJML 扩展](https://github.com/mjmlio/vscode-mjml)。

安装好 MJML 扩展后，你可以在 `src` 目录中创建一个新的邮件模板。创建好新邮件模板并在编辑器中打开该 `.mjml` 文件后，使用 `Ctrl+Shift+P` 打开命令面板，搜索 `MJML: Export to HTML`。这会将该 `.mjml` 文件转换为 `.html` 文件，现在你可以将它保存到 build 目录中。
