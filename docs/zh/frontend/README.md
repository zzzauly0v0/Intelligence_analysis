# FastAPI 项目 - 前端

**语言 / Language: [English](../../en/frontend/README.md) | [中文](README.md)**

前端使用 [Vite](https://vitejs.dev/)、[React](https://reactjs.org/)、[TypeScript](https://www.typescriptlang.org/)、[TanStack Query](https://tanstack.com/query)、[TanStack Router](https://tanstack.com/router) 和 [Tailwind CSS](https://tailwindcss.com/) 构建。

## 环境要求

- [Bun](https://bun.sh/)（推荐）或 [Node.js](https://nodejs.org/)

## 快速开始

```bash
bun install
bun run dev
```

* 然后在浏览器中打开 http://localhost:5173/。

请注意，这个实时开发服务器不是运行在 Docker 内部的，它是用于本地开发的，也是推荐的工作方式。当你对前端的修改感到满意后，可以构建前端的 Docker 镜像并启动它，在类生产环境中进行测试。但如果每次改动都重新构建镜像，效率会远不如运行带有实时重载功能的本地开发服务器。

查看 `package.json` 文件以了解其他可用的选项。

### 移除前端

如果你正在开发一个仅提供 API 的应用，并想移除前端，可以很容易地做到：

* 删除 `./frontend` 目录。

* 在 `compose.yml` 文件中，删除整个 `frontend` 服务 / 配置段。

* 在 `compose.override.yml` 文件中，删除整个 `frontend` 和 `playwright` 服务 / 配置段。

完成，现在你拥有一个没有前端（仅 API）的应用了。🤓

---

如果你想的话，也可以从以下位置移除 `FRONTEND` 相关的环境变量：

* `.env`
* `./scripts/*.sh`

不过这只是为了清理，留着它们其实也不会有什么实际影响。

## 生成客户端

### 自动生成

* 激活后端虚拟环境。
* 在项目顶层目录下，运行脚本：

```bash
bash ./scripts/generate-client.sh
```

* 提交更改。

### 手动生成

* 启动 Docker Compose 技术栈。

* 从 `http://localhost/api/v1/openapi.json` 下载 OpenAPI JSON 文件，并将其复制为 `frontend` 目录根路径下的一个新文件 `openapi.json`。

* 要生成前端客户端，运行：

```bash
bun run generate-client
```

* 提交更改。

请注意，每当后端发生变化（即 OpenAPI schema 发生变化）时，你都应该重复以上步骤来更新前端客户端。

## 使用远程 API

如果你想使用远程 API，可以将环境变量 `VITE_API_URL` 设置为远程 API 的地址。例如，你可以在 `frontend/.env` 文件中这样设置：

```env
VITE_API_URL=https://api.my-domain.example.com
```

这样，当你运行前端时，它会使用这个地址作为 API 的基础 URL。

## 代码结构

前端代码的结构如下：

* `frontend/src` - 主要的前端代码。
* `frontend/src/assets` - 静态资源。
* `frontend/src/client` - 自动生成的 OpenAPI 客户端。
* `frontend/src/components` - 前端的各个组件。
* `frontend/src/hooks` - 自定义 hooks。
* `frontend/src/routes` - 前端的各个路由，其中包含各个页面。

## 使用 Playwright 进行端到端测试

前端已经包含了使用 Playwright 编写的初始端到端测试。要运行这些测试，你需要先让 Docker Compose 技术栈处于运行状态。使用以下命令启动技术栈：

```bash
docker compose up -d --wait backend
```

然后，你可以使用以下命令运行测试：

```bash
bunx playwright test
```

你也可以以 UI 模式运行测试，以便查看浏览器并与其运行过程进行交互：

```bash
bunx playwright test --ui
```

要停止并移除 Docker Compose 技术栈，并清理测试过程中产生的数据，使用以下命令：

```bash
docker compose down -v
```

要更新测试，进入测试目录，修改现有的测试文件或根据需要添加新的测试文件。

有关编写和运行 Playwright 测试的更多信息，请参阅官方 [Playwright 文档](https://playwright.dev/docs/intro)。
