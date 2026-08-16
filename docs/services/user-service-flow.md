# UserService 流程图

```mermaid
flowchart TD
    A[客户端 Login] --> B[UserService.login]
    B --> C[authenticate]
    C --> D[根据 Email 查询用户]
    D --> E{用户存在且有密码?}

    E -- 否 --> F[使用 DUMMY_HASH 验证]
    F --> G[AuthenticationError]

    E -- 是 --> H[verify_password]
    H --> I{密码正确?}

    I -- 否 --> G
    I -- 是 --> J{用户是否激活?}

    J -- 否 --> K[AuthenticationError<br/>Inactive user]
    J -- 是 --> L[生成 session_id]

    L --> M[生成 Refresh Token]
    M --> N[保存 Session 到数据库]
    N --> O[生成 Access Token]
    O --> P[返回 Token]
```

## 说明

这是 `UserService` 的核心业务流程，覆盖了：

- 注册、创建、更新、删除
- 登录、刷新 token、退出登录
- 修改密码
- 密码找回与重置
- 会话失效与安全回收

它严格遵循“服务层处理业务规则，repository 负责数据访问”的结构。
```

## 说明

这是 `UserService` 的核心业务流程，覆盖了：

- 注册、创建、更新、删除
- 登录、刷新 token、退出登录
- 修改密码
- 密码找回与重置
- 会话失效与安全回收

它严格遵循“服务层处理业务规则，repository 负责数据访问”的结构。
