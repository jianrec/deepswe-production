# Task 与产线文件的生命周期

生产过程中有三类文件，不能混为一谈。

## 1. 共享的生产代码：必须保留

`pipeline/` 下的 Python 文件是产线本身，不是某一个 task 的代码。它们负责仓库筛选、出题、参考实现、隐藏测试、QA 和发布。某一条 task 遇到问题后对这些文件的修复，可能会影响后续所有语言和 task，所以必须人工审查、测试后提交到版本库，不能让模型在运行中自行改写。

如果修复只针对某种语言，也仍然放在 `pipeline/`，用明确的语言分支和变更记录隔离；不要把修复代码复制到 `tasks/task-NNNN`。

## 2. task 的正式资产：必须保留在 `output/`

发布后的 `output/task-NNNN/` 是 Harbor 的完整评测输入。`instruction.md`、`task.toml`、两个 Dockerfile、固定仓库元数据、`solution/solution.patch`、`tests/test.patch`、grader 和测试配置都不能删除。即使 Oracle 已通过，测试也是评测任务的一部分，删除测试会改变题目契约和分数含义。

## 3. task 的临时工作文件：QA 后可以精确清理

`tasks/task-NNNN/` 是 staging，`workspaces/` 是仓库/worktree/参考实现/构建上下文，`logs/qa/task-NNNN/` 和 `logs/model-responses/task-NNNN/` 是详细运行日志。这些文件不属于 Harbor 成品。

发布并完成证据写入后，可以执行：

```bash
python3 pipeline/publish_task.py \
  --root "$PWD" --slot task-0012 \
  --cleanup-docker --cleanup-workspaces --cleanup-logs --cleanup-staging
```

该命令只删除指定 slot 的容器、镜像、worktree、详细日志和 staging，不做全局 Docker prune，也不会删除 `output/` 或其他 task 的缓存。共享的 `workspaces/repositories/` 仓库缓存默认保留，因为后续 task 可能复用。
