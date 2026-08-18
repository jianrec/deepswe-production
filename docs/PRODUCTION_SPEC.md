# DeepSWE 产线需求与设计

## 1. 目标与边界

- 目标产量：500 条 Harbor/DeepSWE task。
- 语言：TypeScript、Go、Python、JavaScript、Rust，按五语言循环分配。
- 当前只生产题面、固定仓库环境、参考答案、隐藏测试、grader 和 QA 证据。
- 不生成 RL rollout、模型求解轨迹或评测模型的 `model.patch`。
- 正式成品统一进入 `output/task-NNNN/`；临时包只存在于被忽略的 `tasks/`。
- 已有 11 条 finalized task 作为历史基线迁入 `output/`。后续编号从 `task-0012` 继续，避免覆盖。

## 2. 模型职责

### 强模型

- 模型：`claude-opus-4-8`。
- 接口：Anthropic Messages 兼容接口。
- 负责仓库理解、原创 Issue、明确公共 API 契约、3–5 阶段 PR chain 和参考实现。
- 参考实现必须落成可应用的 `solution/solution.patch`。

### 弱模型

- 模型：`gpt-5.6-sol`。
- 接口：Responses 兼容接口。
- 负责隐藏 F2P 测试和 P2P 回归测试。
- 只能读取固定 base repository、题面、验收标准和公共 API；不得读取参考答案。
- 不得通过 skip、build tag、`|| true`、`exit 0`、网络请求或虚构测试 ID 制造假通过。

## 3. 任务形状门槛

- 参考实现源码文件：7–16 个。
- 源码 changed lines：500–1500。
- 至少 3 个模块或 package。
- PR chain：3–5 个阶段。
- F2P：40–150 个具名测试。
- P2P：100–1500 个实际被命令执行的具名测试。
- 题面长度：1200–6000 字符。
- 固定精确 commit；验证阶段无网络。

这些是筛选门槛，不是鼓励模型机械凑行数。题目必须有真实跨模块行为和回归风险。

## 4. 生产流水线

1. **仓库发现与预检**
   - 活跃、知名、许可证清晰、适合 Linux/Docker。
   - 仓库规模和依赖可控。
   - 至少 100 个可静态枚举并能真实执行的公共测试。
   - 固定 base commit，并完成离线基线预检。
2. **强模型原创出题**
   - 生成 Issue、验收标准、公共 API、影响文件、PR chain 和难度卡。
   - 拒绝重复题、轻微改名题、只改文档或纯重构题。
3. **强模型参考实现**
   - 在干净 worktree 中生成并应用代码操作。
   - 验证 patch、源码文件数、行数、声明文件覆盖和 PR 阶段覆盖。
4. **弱模型隐藏测试**
   - 独立生成 F2P/P2P 测试、test patch、test command、grader 和 mapping。
   - Go 必须隔离 F2P/P2P package，避免 `_test.go` 编译互相污染。
5. **静态 Harbor 验证**
   - 文件完整；patch 可应用；无答案/测试泄漏。
   - `environment_mode=separate`、`network_mode=no-network`。
6. **Base/NOP QA**
   - Base 和三次 NOP：F2P 0 通过；P2P 全通过；binary reward 0。
7. **Oracle QA**
   - 标答三次：F2P/P2P 全通过；binary reward 1。
8. **Mutant QA**
   - 生成 4 个有意义的 runtime mutant。
   - 至少 3 个有效 mutant，且每个 binary reward 为 0。
   - 禁止只回退类型声明、注释、barrel export 等无运行时影响文件。
9. **发布与清理**
   - manifest 置为 `finalized/finalized`。
   - 先复制到 `output/.task-NNNN.tmp`，复核后原子改名为 `output/task-NNNN`。
   - 随后精确删除该 task 的临时 worktree、Docker build context、停止容器和 task-specific image。
   - 保留 task 内两个 Dockerfile、QA 报告、镜像 provenance 和生产 usage。

## 5. QA 失败归因

- Base F2P 有通过：题目可能已存在于 base，或测试没有真正覆盖新功能。
- Base P2P 失败：回归集或环境无效。
- Oracle F2P 失败：先按公共契约判断是标答错误还是弱测试越权，不能自动把测试当真。
- Oracle P2P 失败：标答引入回归。
- Mutant 得分 1：测试敏感度不足或 mutant 无效。
- API timeout、Git cache 损坏、Docker 构建失败属于基础设施故障，不等于 task 内容错误。

## 6. 并发设计

- 任务之间可以并发，单条任务内部阶段保持有序。
- 建议初始并发：出题 3、参考实现 3、隐藏测试 3、Docker QA 2。
- Docker QA 根据 CPU、内存和磁盘水位自动降并发。
- 不同 worker 使用独立 task/worktree/build context。
- 状态更新必须使用文件锁与单 slot merge；禁止多个阶段基于旧快照整表覆盖 manifest。
- 五语言 canary 全部稳定后，按 5 → 25 → 100 → 500 扩批。

## 7. 资源与安全

- Dockerfile 很小且是 Harbor task 的必要组成；可删除的是镜像、容器、layer cache 和临时 context。
- 禁止运行针对全局 Docker 的无界清理命令；只清理由该 task 标签和 image ID 精确识别的资源。
- 仓库缓存使用完整、已物化的 clone 或 pinned snapshot；生产期间关闭自动 Git maintenance。
- 凭据只允许从仓库外 env 文件读取，任何日志和模型响应都不得记录 key。

## 8. 完成定义

只有同时满足以下条件才计入产量：

- `status=finalized` 且 `stage=finalized`；
- 静态验证通过；
- Base/NOP、Oracle 和 mutant QA 全部通过；
- task 位于 `output/`；
- QA 报告和 production usage 完整；
- 不含凭据、缓存、模型临时响应或未清理的大型二进制。
