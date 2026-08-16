# 架构

**语言 / Language: [English](../en/architecture.md) | [中文](architecture.md)**

Intelligence Analysis 平台的后端架构：FastAPI + Pydantic v2 之上运行异步 PostgreSQL，采用 JWT
认证，并严格遵循三层分离。

这里汇总的约定由 `.claude/rules/*` 按文件路径逐条约束，其中的硬性边界写在 `CLAUDE.md` 里。本文说明
这些约定**为什么**成立，以及一个请求如何穿过它们。

---

## 1. 一条规则,其余皆由此推出

```
HTTP  ─▶  Routes  ─▶  Services  ─▶  Repositories  ─▶  PostgreSQL
          (形态)      (规则)        (持久化)
```

每一层只能与紧邻的下一层对话。

| 层 | 包 | 负责 | 绝不 |
| --- | --- | --- | --- |
| Routes | `app/api/routes/v1/` | HTTP 形态：路径、状态码、`response_model`、认证依赖 | 导入 repository、构造查询、承载业务规则 |
| Services | `app/services/` | 业务规则、编排、领域异常 | 接触 `Request`/`Response`、抛 `HTTPException`、写裸 SQL |
| Repositories | `app/repository/` | 数据访问，一个查询一个函数 | 提交事务、施加规则、返回 dict 或裸 ID |

收益是可测试性,以及规则的单一归属:"修改密码即吊销全部 refresh token"只写在
`UserService.change_password` 一处,不会被另一个恰好碰到同一张表的路由绕过。

**路由绝不导入 repository。** 即便只需要一次查询,也要经由一个 service 方法。多写一个方法的代价,
远低于日后出现一条重复规则。

---

## 2. 目录结构

```
backend/app/
├── main.py                  # 只做装配：中间件、异常处理器、路由、生命周期
├── api/
│   ├── deps.py              # 依赖注入：session、services、当前用户、管理员、调用方信息
│   ├── router.py            # 聚合带版本的路由
│   ├── middleware.py        # RequestIDMiddleware、RateLimitMiddleware（纯 ASGI）
│   ├── exception_handlers.py# AppException / 未捕获异常 → JSON 错误体
│   └── routes/v1/           # 一个领域一个模块：user.py、utils.py
├── core/
│   ├── config.py            # pydantic-settings；全项目唯一读取环境变量的地方
│   ├── exceptions.py        # AppException 体系 + HTTP 状态码映射
│   ├── security.py          # 密码哈希、JWT 签发/解码、refresh 摘要
│   ├── logging.py           # PII 脱敏 + request-id 日志过滤器
│   └── context.py           # request-id 的 ContextVar
├── db/
│   ├── base.py              # DeclarativeBase、命名约定、TimestampMixin
│   ├── session.py           # engine，以及请求/上下文/worker 三种会话作用域
│   ├── models/              # SQLAlchemy 模型：user.py、session.py
│   └── todo_pool.py         # 可选的 asyncpg 连接池（deep-research 规划器）
├── repository/              # 数据访问函数：user.py
├── schemas/                 # Pydantic v2：base.py、user.py
├── services/                # 业务逻辑：user.py、email/
├── utils.py                 # 邮件渲染 + SMTP 发送
└── agents/、clawer/         # 预留，目前为空
```

`agents/` 与 `clawer/` 目录存在但为空,尚无任何代码引用;把它们当作预留名称,而不是已存在的层。

顶层 `app/` 只放框架关注点(`api/`、`core/`、`db/`、`repository/`、`schemas/`、`services/`)。
新的业务领域应成为 `services/` 下的一个模块或子包 —— 绝不新建顶层包。

---

## 3. 装配:`main.py`

`main.py` 不含任何逻辑。`create_app()` 是工厂函数而非模块级语句,这样测试可以基于打过补丁的配置
构建应用,而导入该模块除了定义函数之外什么都不做。

`create_app()` 内部的顺序是有意义的:

1. `setup_logging()` —— 放在最前,确保 PII 脱敏就位之前不会有任何日志被打出。
2. `_init_sentry()` —— 仅在部署环境启用。
3. `FastAPI(...)`,配 `custom_generate_unique_id`,使生成的客户端方法名为 `<tag>-<function>`。
4. `_register_middleware(app)`。
5. `register_exception_handlers(app)`。
6. `include_router(api_router, prefix=settings.API_V1_STR)` —— `/api/v1` 前缀只在这里加一次,
   不在各路由里重复。

### 中间件顺序

`add_middleware` 是前插,因此**最后**添加的位于最外层。代码按由内向外的顺序添加,得到:

```
RequestID ─▶ CORS ─▶ RateLimit ─▶ Session ─▶ 异常处理器 ─▶ 路由
```

* **RequestID 在最外层** —— 即使请求被拒或崩溃也可追踪,且每个响应都带上该头。
* **CORS 在限流之外** —— 浏览器才能真正读到 429,且预检请求不会计入调用方的额度。
* 两个中间件都是纯 ASGI 而非 `BaseHTTPMiddleware`:它们都不需要读取或改写 body,保持纯 ASGI
  可以完全不干扰流式响应、后台任务和 WebSocket。

异常处理器位于中间件栈**内部**。这正是 `RateLimitMiddleware` 自己写出 429 响应体、而不是抛
`RateLimitError` 的原因 —— 在中间件层抛出的异常永远到不了处理器。

### 生命周期

`lifespan` 持有进程级资源:服务开始前获取,结束后按相反顺序释放。目前它调用
`assert_event_loop_supported()`(见 §5)并打开可选的 asyncpg TODO 连接池。预留槽位及其填充顺序
写在 `lifespan` 的 docstring 里:Redis、RAG(嵌入模型 + 向量库)、然后是机器人轮询。

---

## 4. 依赖注入:`api/deps.py`

路由通过 `Annotated` 别名声明所需依赖。路由签名里绝不出现裸 `Depends()`。

```python
SessionDep      = Annotated[AsyncSession, Depends(get_db_session)]
TokenDep        = Annotated[str, Depends(reusable_oauth2)]
UserServiceDep  = Annotated[UserService, Depends(get_user_service)]
CurrentUser     = Annotated[User, Depends(get_current_user)]
AdminUser       = Annotated[User, Depends(get_current_admin)]
ClientInfoDep   = Annotated[ClientInfo, Depends(get_client_info)]
```

Service 工厂接收会话、返回 service —— 一行,无状态:

```python
def get_user_service(db: SessionDep) -> UserService:
    return UserService(db)
```

认证把 bearer token 解析成 `User`:`decode_token(token, expected_type=TokenType.ACCESS)`
→ 取 `sub` → `service.get(user_id)` → 拒绝已停用账号。指向**已删除**账号的 token 抛
`AuthenticationError` 而非 `NotFoundError` —— 失效凭证是 401,不该是一个会泄露"该账号是否曾经
存在"的 404。

授权是函数而不是类:`is_admin(user)` 即 `user.is_app_admin or user.has_role(UserRole.ADMIN)`,
`get_current_admin` 在其之上包一层。两种要求管理员的写法都在用:

```python
# 整个端点加门禁，处理函数本身不需要管理员对象
@router.patch("/{user_id}", dependencies=[Depends(get_current_admin)])

# 注入进来，处理函数需要与调用者本人比对
async def delete_user(user_id: UUID, current_user: AdminUser, ...):
    if user_id == current_user.id:
        raise AuthorizationError("Admins are not allowed to delete themselves")
```

`ClientInfo` 是一个 frozen + slots 的 dataclass,承载调用方 IP 与 user-agent;它被记录到登录
会话行上,而不是在 service 内部临时读取。

---

## 5. 数据层

### 模型 —— `db/models/`

每个模型继承 `Base`,除非自行管理时间戳,否则也继承 `TimestampMixin`(`created_at` 用服务端默认
值,`updated_at` 在更新时写入)。列一律 `Mapped[...]` + `mapped_column()`;每个模型都定义
`__repr__`。

`Base.metadata` 带有命名约定,使约束名在多次迁移之间保持稳定:`{table}_{col}_key`、
`{table}_{col}_fkey`、`{table}_pkey`、`{col_label}_idx`、`{table}_{constraint}_check`。

`db/models/__init__.py` 重新导出所有模型。导入该包会注册全部 mapper —— 这既让 `Base.metadata`
对 Alembic autogenerate 完整可见,也让字符串形式的关系(`User.sessions`)能够解析。

目前有两张表:

* **`users`** —— 凭证、`role`(以字符串存储,通过 `user_role` 属性读取)、用于引导管理员的
  `is_app_admin`,以及可选的 OAuth 列。
* **`sessions`** —— 每次登录一行:当前 refresh token 的**摘要**、`expires_at`、`is_active`,
  以及设备/IP/user-agent 指纹。

### 会话作用域 —— `db/session.py`

同一套生命周期,三个入口:

| 辅助函数 | 使用者 | 说明 |
| --- | --- | --- |
| `get_db_session()` | FastAPI `Depends` | 请求作用域;成功则提交,出错则回滚 |
| `get_db_context()` | 手动 `async with`(如 WebSocket) | 同样的生命周期,不走依赖注入 |
| `get_worker_db_context()` | 后台 worker | 每次调用新建 `NullPool` 引擎,退出即销毁 —— 避免跨 fork、跨事件循环复用连接 |

**这就是"不许 commit"规则的由来。** 作用域在处理函数返回之后统一提交一次。因此 repository 只
`flush()`(需要服务端默认值时再 `refresh()`)让调用方看到生成值,绝不 `commit()`。一个提交了的
repository 会破坏请求的原子性:此后发生的失败再也无法回滚先前那次写入。

`expire_on_commit=False` 让 ORM 对象在提交之后仍可读取 —— 正是这一点使处理函数可以返回实体,再由
`response_model` 序列化。

`assert_event_loop_supported()` 在启动时运行:psycopg 无法在 Windows 的 `ProactorEventLoop` 上
以异步模式工作,而 uvicorn 在不带 reloader 启动时正好会选它。启动时失败一次,好过每次查询都 500。

### 驱动

`settings.DATABASE_URL` 形如 `postgresql+psycopg://…` —— **psycopg 3** 同时服务异步引擎与
Alembic,因此不需要额外的 asyncpg 驱动。`DATABASE_DSN` 是无驱动前缀的形式,仅供
`db/todo_pool.py` 使用 —— 它的 `pydantic-ai-todo` 存储后端只认原生 asyncpg。该连接池是可选的:
未安装 asyncpg 时只记录日志并关闭该功能,而不会导致启动失败。

### 迁移

Alembic 位于 `backend/alembic/`,其 `env.py` 导入 `app.db.models` 得到 `target_metadata`,
并从配置读取连接串。

```bash
uv run alembic revision --autogenerate -m "Description"
uv run alembic upgrade head
```

每份自动生成的 revision 都要先读过再提交 —— autogenerate 看不到服务端默认值、枚举变更和数据迁移。

---

## 6. Schemas —— `app/schemas/`

Pydantic v2,一个领域一个模块,全部继承 `BaseSchema`(`from_attributes`、
`populate_by_name`、`str_strip_whitespace`,以及带时区的 ISO 时间编码)。`TimestampSchema`
额外提供 `created_at` / `updated_at`。

一个操作一个 schema,使字段约束与接收它的操作严格对应:

| 后缀 | 用途 |
| --- | --- |
| `*Create` | 创建所必需的字段,带 `Field()` 约束 |
| `*Update` | 所有字段可选(`T \| None = None`) |
| `*Read` | 响应形态:`id` + 时间戳 |
| `*List` | `items: list[*Read]` + `total: int` |

`schemas/user.py` 用继承而非复制来组合它们 —— `UserRegister` → `UserCreate`、
`UserUpdateMe` → `UserUpdate` —— 并把认证相关载荷(`Token`、`RefreshToken`、`NewPassword`、
`UpdatePassword`)放在一起。

路由处理函数返回 `-> Any` 并声明 `response_model`。返回 ORM 对象、交由 `response_model` 序列化,
可以避免第二遍完整的 Pydantic 校验;若把 schema 直接标注为处理函数的返回类型,校验代价会付两次。

共用的响应类型放在 `schemas/base.py`:`Message`、`ErrorResponse`、`HealthResponse`。

---

## 7. 错误

Service 抛领域异常;API 层以下的任何代码都不知道 HTTP 的存在。

`core/exceptions.py` 定义 `AppException`,带类级别的 `message`、`code`、`status_code`,并为每种
结果提供一个子类:

| 异常 | 状态码 | code |
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

务必传 `message`;当客户端能据此采取行动时,再传 `details`:

```python
raise NotFoundError("User not found", details={"user_id": str(user_id)})
raise AlreadyExistsError("User with this email already exists", details={"email": email})
```

`api/exception_handlers.py` 把它们转成响应:

```json
{ "detail": "User not found", "code": "NOT_FOUND", "details": { "user_id": "..." } }
```

人类可读的信息放在 `detail`,使响应体与 FastAPI 自带的 `HTTPException`、校验错误保持一致 ——
客户端只需读一个字段 —— 而 `code` 与 `details` 承载机器可读的部分。

处理器中值得记住的行为:

* 5xx 以 `error` 级别记录,4xx 以 `warning`,两者都带 `path`、`method`、`error_code` 与
  `details`。
* **5xx 会丢弃 `details`。** 它们描述的是我们的内部状态:只写进日志,绝不外发。
* 401 响应会加上 `WWW-Authenticate: Bearer`。
* 若 WebSocket 在 `accept()` 之前抛出 `AppException`,处理器记录日志并返回 `None` ——
  Starlette 会关闭连接,非 HTTP 作用域下没有可写的响应体。
* 兜底的 `Exception` 处理器记录完整 traceback,对外只返回通用的 `INTERNAL_ERROR`。

---

## 8. 可观测性

`RequestIDMiddleware` 接受外部传入的 `X-Request-ID`,前提是它可以安全回显
(`[A-Za-z0-9_-]`、≤64 字符 —— 否则调用方就能借它把 CRLF 注入日志和响应头),否则生成一个 UUID4
hex。该 ID 落在三个位置:`request.state.request_id`、`core/context.py` 里的 `ContextVar`、
以及响应头。浏览器能读到它,是因为 CORS 把它列进了 `expose_headers`。

那个 `ContextVar` 正是 service 与 repository 无需新增 `request_id` 参数、也无需从 `app.api`
导入,就能被关联起来的原因。

`core/logging.py` 把两个过滤器装在**handler** 上而不是 logger 上 —— logger 自身的过滤器只对在
该 logger 上创建的记录生效,装在 root 上会漏掉应用里每一个 `logging.getLogger(__name__)`:

* `RequestIDFilter` —— 写入 `record.request_id`(请求之外为 `-`),使 `%(request_id)s` 可用,
  也给结构化 handler 一个可按请求聚合的字段。
* `PiiRedactionFilter` —— 从消息、`args`、`extra=` 容器(最多下钻 4 层)以及渲染后的 traceback
  中抹掉邮箱、JWT、API key、bearer token 和类似密码的值。其中 traceback 最要紧:它经常把查询
  参数原样带出来。

`install_log_filters()` 也会扫过其他库的 logger,因为 uvicorn 自带 handler 且
`propagate = False`,而它的访问日志携带完整请求路径。该函数是幂等的 —— 任何新装 handler 的动作
之后都可以再调一次。

---

## 9. 安全

### 密码

使用 `pwdlib`,以 `Argon2Hasher` 为首选,`BcryptHasher` 兼容历史哈希。`verify_password()`
返回 `(verified, updated_hash)`;当存储的哈希已过时,service 会把升级后的哈希写回,于是账号在成功
登录时自动迁移。

登录对"账号是否存在"是常量时间的:未知邮箱会与 `DUMMY_HASH` 做一次校验,使响应时间不泄露该地址
是否已注册。

### 令牌

`TokenType` —— `ACCESS`、`REFRESH`、`PASSWORD_RESET` —— 写在 `type` claim 里,且
`decode_token` 强制要求期望的类型。因此 access token 无法被当作 refresh token 或重置链接重放。
Claims:`sub`、`exp`、`iat`、`nbf`、`type`、`jti`,以及标识登录会话的 `sid`。

Refresh token 以 **SHA-256 摘要**存入 `sessions.refresh_token_hash`,即使表泄露也无法重放。摘要
故意不加盐 —— 正是这一点使按 token 查找成为可能,而签名后的 JWT 本身已带 200+ 位熵。

Refresh 会轮换:`UserService.refresh` 校验通过后,换入新签发 token 的摘要并更新 `last_used_at`。
被出示的那个 token 就此失效,重放它(或任何更早的 token)都找不到有效会话。

### 会话吊销

Access token 是无状态的 —— 吊销一次登录杀掉的是它的 *refresh* token;已签发的 access token 在
过期前仍然有效。所有"应当让账号在所有设备登出"的动作都调用 `deactivate_all_user_sessions`:
本人改密、管理员重置密码、以及凭恢复令牌重置密码。

密码恢复对未知或已停用账号保持静默,且端点两种情况下的响应文案完全一致,因此无法用它来枚举地址。

---

## 10. 一个请求的完整路径

`PATCH /api/v1/users/me/password`

1. **RequestID** 生成或接受 `X-Request-ID`,并绑定 `ContextVar`。
2. **CORS**,然后 **RateLimit** —— 按 IP 的固定窗口,计数在每个 worker 进程内独立;文档、
   OpenAPI schema 和健康检查探针在豁免列表中。
3. **路由**匹配到 `api/routes/v1/user.py` 里的 `update_password_me`。
4. **依赖**解析:`get_db_session` 开启会话,`get_user_service` 构造 `UserService`,
   `get_current_user` 解码 bearer token 并载入 `User`。
5. **请求体**按 `UpdatePassword` 校验。
6. **路由**转交:`await service.change_password(current_user, body)`。
7. **Service** 施加规则 —— 账号已设密码、当前密码校验通过、新密码不同于旧密码 —— 随后哈希,并
   调用两个 repository 函数:`update`,然后 `deactivate_all_user_sessions`。
8. **Repository** 只 `flush()`,不提交。
9. **返回** `Message(...)`;依赖栈退出时,会话作用域统一提交一次。
10. 若抛出任何 `AppException`,作用域改为回滚,处理器渲染 `{detail, code, details}`,而响应上
    已经带着请求 ID。

---

## 11. 新增一个领域

对于薄领域(自身不含基础设施),自下而上五到八个文件:

1. `app/db/models/<entity>.py`,并在 `db/models/__init__.py` 中重新导出。
2. `uv run alembic revision --autogenerate -m "Add <entity>"`,然后逐行读一遍生成的 revision。
3. `app/schemas/<entity>.py` —— 基于 `BaseSchema` 的 `*Create` / `*Update` / `*Read` /
   `*List`。
4. `app/repository/<entity>.py` —— 查询函数,`db` 之后全部 keyword-only,返回实体。
5. `app/services/<entity>.py` —— 持有 `db` 的类,抛领域异常。
6. `app/api/deps.py` —— 一个工厂函数与一个 `Annotated` 别名。
7. `app/api/routes/v1/<entity>.py` —— 一个 router,在 `routes/v1/__init__.py` 中挂载。
8. `backend/tests/…`,路径与源码镜像对应。

### 薄领域 vs. 厚领域

自带基础设施的领域 —— API 客户端、适配器、流水线、解析器、模板 —— 应改为子包:

```
app/services/rag/
├── __init__.py        # 只重新导出 facade，别的都不导出
├── facade.py          # 路由唯一能看到的类
├── ingestion.py       # 内部子 service
├── vectorstore.py     # 基础设施
└── exceptions.py      # 继承自 core/exceptions.py
```

调用方只从包根导入;子模块属于包内实现。领域专属异常放在子包内,并继承 `core/exceptions.py` 的
基类,这样现有的异常处理器依然能正确映射它们。

`services/email/` 就是为这一形态预留的占位(目前为空;SMTP 发送暂时在 `app/utils.py`,经
`UserService._send_email` 调用 —— 它会吞掉异常,以免一个坏掉的邮箱把它所搭载的那个请求一起弄失败)。

---

## 12. API 约定

* 所有路由位于 `/api/v1/` 之下;URL 用 kebab-case;一个领域实体一个模块,tag 在挂载处指定。
* `POST` → `201`;`DELETE` → `204`,无返回内容时配 `response_model=None`。
* 分页用带边界的 `skip` / `limit` 查询参数,列表端点返回 `items` + `total`,使客户端无需二次
  请求即可翻页:

  ```python
  skip: Annotated[int, Query(ge=0)] = 0
  limit: Annotated[int, Query(ge=1, le=200)] = 100
  ```

* 静态路径必须声明在 `/{id}` **之前**,否则会被 `/{id}` 吞掉 —— 这正是
  `routes/v1/user.py` 中 `/me`、`/signup`、`/login/*` 排在前面的原因。
* 处理函数一律 `-> Any` 配 `response_model`;例外是返回类型**本身就是** schema、且不涉及 ORM
  对象的情况(`-> Token`、`-> Message`)。

---

## 13. 配置

`core/config.py` 是唯一读取环境变量的模块。`Settings`(pydantic-settings)加载仓库根目录的
`.env`,派生值都是 `@computed_field` 属性而非重复的字符串:`DATABASE_URL`、
`DATABASE_URL_SYNC`、`DATABASE_DSN`、`all_cors_origins`、`rate_limit_exempt_paths`、
`emails_enabled`、`session_https_only`。

一个 model validator 会拒绝 `SECRET_KEY`、`POSTGRES_PASSWORD`、`FIRST_SUPERUSER_PASSWORD`
取值为 `"changethis"` —— 本地只是警告,staging 与 production 直接失败。

所有可选功能默认关闭,各由自己的开关控制:`RATE_LIMIT_ENABLED`、`SESSION_ENABLED`(签名 cookie,
其 `itsdangerous` 导入放在分支内部,功能关闭时不付任何代价)、`SENTRY_DSN`、`REDIS_URL`、
`DB_ECHO`。

---

## 14. 测试

`backend/tests/` 与源码结构镜像对应(`app/services/user.py` → `tests/services/test_user.py`);
目前包骨架已就位,测试用例尚待编写。

本项目跑在 **anyio** 上而非 pytest-asyncio:异步测试标 `@pytest.mark.anyio`,后端由
`anyio_backend` fixture 固定。API 测试用 `httpx.AsyncClient` 搭 `ASGITransport`,而不是
`TestClient`,这样跑的是与 uvicorn 相同的事件循环 —— 而上文的中间件顺序与会话生命周期,恰恰就是
在这里才会出问题。

---

## 15. 不变式

评审前值得再读一遍的短清单:

1. Repository 只 `flush()` + `refresh()`,绝不 `commit()`。
2. 路由调 service;绝不调 repository。
3. 处理函数返回 `-> Any`;序列化交给 `response_model`。
4. Service 抛领域异常;绝不返回错误码,也绝不用 `None` 表示"未找到"。
5. `datetime.now(UTC)`,绝不 `datetime.utcnow()`。
6. 任何按值比较的密钥都用常量时间比较(`secrets.compare_digest`)。
7. 路由签名里不出现裸 `Depends()` —— 用 `deps.py` 里的别名。
8. `core/config.py` 是唯一读取环境变量的地方。
9. 新业务领域放到 `services/` 下,不要新建顶层包。
10. 5xx 响应绝不把 `details` 发给客户端。
