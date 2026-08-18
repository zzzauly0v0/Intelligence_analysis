# 开发协议（Git Flow + 强制 PR）

本文档定义本仓库的统一开发流程与合并规则。除紧急授权场景外，所有成员必须遵循。

## 1. 目标

- 保持 main 始终可发布。
- 保持 develop 作为日常集成分支。
- 所有进入 develop 和 main 的变更必须经过 PR 审查。

## 2. 分支模型

- main：生产稳定分支，仅接收发布级变更。
- develop：开发集成分支，功能联调入口。
- feature/*：功能分支，必须从 develop 创建，并合并回 develop。

## 3. 强制规则

### 3.1 禁止直接提交

- 禁止直接 push 到 develop。
- 禁止直接 push 到 main。
- develop 和 main 的所有变更必须通过 PR 合并。

### 3.2 Feature 流程

1. 从 develop 拉取最新代码并创建分支。
2. 在 feature/* 开发并提交。
3. 发起 PR：feature/* -> develop。
4. 通过审核和 CI 后再合并。

示例命令：

```bash
git checkout develop
git pull origin develop
git checkout -b feature/your-feature-name
```

### 3.3 发布流程

1. develop 达到发布条件后，发起 PR：develop -> main。
2. 必须通过完整 CI 与人工审核。
3. 合并后打版本 Tag（如 v1.4.0）。

### 3.4 Hotfix 流程

1. 从 main 创建 hotfix/*。
2. 修复后发起 PR：hotfix/* -> main。
3. main 合并后，必须再发起 PR：hotfix/* 或 main -> develop，确保修复回流。

## 4. PR 审查标准

### 4.1 必需门禁

- 至少 1 名 Reviewer 批准（建议核心模块为 2 名）。
- 所有必需状态检查通过（CI、测试、lint、类型检查）。
- 所有评审对话已解决。
- 新提交后自动失效旧批准（Dismiss stale approvals）。

### 4.2 变更要求

- PR 标题清晰描述目的。
- PR 描述必须包含：背景、改动点、影响范围、验证方式。
- 涉及接口/数据库变更时，必须说明兼容性与回滚方案。
- 涉及前端可视改动时，附关键截图或录屏。

## 5. 分支保护（GitHub）

对 develop 和 main 配置 Branch Protection 或 Ruleset：

- Require a pull request before merging。
- Require approvals。
- Dismiss stale pull request approvals when new commits are pushed。
- Require status checks to pass before merging。
- Require conversation resolution before merging。
- Restrict who can push to matching branches。
- Do not allow force pushes。
- Do not allow deletions。

## 6. 合并策略

- 默认使用 Squash Merge，保持历史整洁。
- PR 合并后自动删除源分支（feature/*、hotfix/*）。
- 非必要不使用 Rebase and Merge；如需保留完整分支历史可按仓库管理员决策启用。

## 7. 提交与同步规范

- 小步提交，避免超大 PR。
- 开发过程中定期同步 develop，优先使用 rebase 保持线性历史。
- 提交信息建议使用 Conventional Commits（如 feat:、fix:、refactor:、docs:、test:）。

## 8. 紧急例外

- 仅仓库管理员可在生产事故中执行例外操作。
- 例外操作后 24 小时内必须补齐：PR、审查记录、事故说明与回溯。

## 9. 生效范围

- 本协议适用于仓库内全部目录（backend、frontend、scripts、docs 等）。
- 若与临时口头约定冲突，以本协议为准；若需变更，必须通过文档 PR 修改本文件。
