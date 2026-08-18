# DeepSWE Production Pipeline

用于生产 500 条五语言 Harbor/DeepSWE 评测任务。当前阶段只生产 task package、参考答案和测试资产，不生产模型 rollout 或求解轨迹。

## 目录

- `pipeline/`：仓库筛选、出题、参考实现、隐藏测试、QA、发布与清理脚本。
- `configs/`：无密钥的生产参数和 provider 示例。
- `docs/`：冻结需求、质量门槛和运行手册。
- `registry/`：可恢复的任务状态与生产事件；运行时生成。
- `tasks/`：QA 前的临时 task staging；不提交 Git。
- `workspaces/`、`logs/`：仓库缓存、worktree、模型响应和 QA 日志；不提交 Git。
- `output/`：唯一正式输出目录，只包含通过完整 QA 的 Harbor task。

## 当前基线

`output/` 已迁入 11 条已完成任务：`task-0001` 至 `task-0011`。它们保留 Harbor 所需的 `environment/Dockerfile` 和 `tests/Dockerfile`；Docker 镜像、容器和构建缓存不会提交。

## 生产原则

1. 强模型负责原创题目、公共 API 契约、PR chain 和参考实现。
2. 弱模型独立生成隐藏测试，不得读取 `solution.patch`。
3. Base/NOP 必须 F2P 全失败、P2P 全通过。
4. Oracle 必须 F2P/P2P 全通过，且重复三次。
5. 至少三个有效 runtime mutant 必须被测试杀死。
6. 只有 `finalized/finalized` task 可以发布到 `output/`。
7. 发布后可以精确删除该 task 的 Docker 容器、镜像和构建上下文；不可删除 task 内的 Dockerfile。

完整方案见 [`docs/PRODUCTION_SPEC.md`](docs/PRODUCTION_SPEC.md)，执行命令见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)，历史 11 条任务清单见 [`docs/BASELINE_TASKS.md`](docs/BASELINE_TASKS.md)。

## 凭据

API key 只能从仓库外部、权限为 `0600` 的 env 文件注入。禁止把 key 写入代码、配置、日志、registry 或 task package。
