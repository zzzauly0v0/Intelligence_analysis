# 贡献指南

**语言 / Language: [English](../en/CONTRIBUTING.md) | [中文](CONTRIBUTING.md)**

感谢你对 Full Stack FastAPI 模板项目感兴趣并愿意贡献！🙇

## 先讨论

对于**较大的更改**（新功能、架构变更、重大重构），请先发起一个 [GitHub Discussion](https://github.com/fastapi/full-stack-fastapi-template/discussions)。这样社区和维护者可以在你投入大量实现时间之前，先对方案提供反馈。

对于小而直接的更改，你可以直接提交 Pull Request，而不必先发起讨论。这类更改包括：

- 拼写和语法错误修正
- 小而可复现的 bug 修复
- 修复 lint 警告或类型错误
- 较小的代码改进（例如删除未使用的代码）

请注意，非团队成员提交的 PR 不允许修改 `pyproject.toml` 或 `uv.lock`，以防止供应链风险。
如果你想添加新的依赖，请创建一个新的 [Discussion](https://github.com/fastapi/full-stack-fastapi-template/discussions) 来说明原因。

## 开发

关于如何搭建开发环境、运行整个技术栈、代码检查、pre-commit 钩子等详细说明，请参阅[开发指南](development.md)。

## Pull Request

提交 Pull Request 时：

1. 确保提交前所有测试都能通过。
2. 保持每个 PR 只专注于一个改动点。
3. 如果更改了功能，请同步更新测试。
4. 在 PR 描述中引用相关的 issue。

## 自动化代码与 AI

我们鼓励你使用任何你想用的工具来高效完成工作和贡献，这包括 AI（大语言模型）工具等。不过，贡献仍然需要有实质性的人工介入、判断和上下文考量。

如果你在一个 PR 上投入的**人力成本**（例如编写 LLM 提示词）**低于**我们**审查它所需付出的成本**，请**不要**提交这个 PR。

可以这样理解：我们自己也可以编写 LLM 提示词或运行自动化工具，那样比审查外部 PR 更快。

### 关闭自动化和 AI 生成的 PR

如果我们发现看起来是 AI 生成或以类似方式自动化产出的 PR，我们会标记并关闭它们。

同样的规则也适用于评论和描述，请不要直接复制粘贴 LLM 生成的内容。

### 人力资源的拒绝服务攻击

使用自动化工具和 AI 提交需要我们仔细审查和处理的 PR 或评论，相当于对我们的人力资源发起一次[拒绝服务攻击](https://en.wikipedia.org/wiki/Denial-of-service_attack)。

提交者只需花费极少的精力（一个 LLM 提示词），却会在我们这边产生大量的工作量（仔细审查代码）。

请不要这样做。

我们将不得不封禁那些反复用自动化方式向我们刷 PR 或评论的账号。

### 明智地使用工具

正如蜘蛛侠里的本叔叔所说：

> 能力越大，责任越大，工具亦是如此。

请避免无意中造成损害。

你手中掌握着强大的工具，请明智地使用它们来高效地帮助我们。

## 有疑问？

如果你对贡献流程有任何疑问，欢迎发起一个 [GitHub Discussion](https://github.com/fastapi/full-stack-fastapi-template/discussions)。
