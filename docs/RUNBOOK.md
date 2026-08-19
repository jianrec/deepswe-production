# 运行手册

以下命令均从仓库根目录执行。真实凭据文件必须位于仓库外，例如：

```bash
chmod 600 /path/to/pack-strong.env /path/to/pack-weak.env
```

可从 `configs/providers.local.env.example` 复制本地配置模板；真实 key 只放在仓库外的文件中，不提交 Git。

## 1. 初始化状态

跨电脑推荐使用可提交的脱敏快照：原电脑执行 `python3 scripts/export_state.py`，新电脑执行 `python3 scripts/import_state.py`。只有正式输出中已存在且 finalized 的 task 会被恢复为 finalized；staging/worktree 中间状态会安全地重置到 `repository_discovery`。

首次建立新的 500 槽位 manifest：

```bash
python3 pipeline/bootstrap_dataset.py --root "$PWD" --count 489 --start-index 12
```

`--start-index 12` 用于保留已经发布的 `task-0001` 至 `task-0011`；489 个新槽位刚好延续到 `task-0500`。初始化不会调用模型。

## 2. 发现与审计仓库

```bash
python3 pipeline/discover_repositories.py --root "$PWD" --per-language 12
python3 pipeline/audit_repositories.py --root "$PWD"
```

生产前应将 runtime preflight 结果写回候选清单。

## 3. 生成 task staging

```bash
python3 pipeline/author_tasks.py \
  --root "$PWD" --limit 3 --workers 3 \
  --env-file /path/to/pack-strong.env

python3 pipeline/reference_tasks.py \
  --root "$PWD" --limit 3 --workers 3 \
  --env-file /path/to/pack-strong.env

python3 pipeline/qwen_tests.py \
  --root "$PWD" --limit 3 --workers 3 \
  --env-file /path/to/pack-weak.env
```

这些阶段把候选包写入被 Git 忽略的 `tasks/`。

## 4. QA、发布和清理

一次处理一条：

```bash
python3 pipeline/finalize_task.py \
  --root "$PWD" --slot task-0012 \
  --repeats 3 --mutants 4 --retry-failed

python3 pipeline/publish_task.py \
  --root "$PWD" --slot task-0012 \
  --cleanup-docker --cleanup-workspaces --cleanup-staging
```

`publish_task.py` 只接受 manifest 中 `finalized/finalized` 且 QA `passed=true` 的任务。它先验证，再原子复制到 `output/`。`--cleanup-docker` 只清理该 task 的容器和两个 task-specific image，不进行全局 prune。

发布完成第 15 个完整 task 时，`publish_task.py` 会在仓库根目录原子生成一次
`AGENTS.md`。它记录当前生产契约、模型职责、QA 门槛、Windows/WSL 规则和跨电脑合并要求；
后续发布不会覆盖该文件。若生产契约变更，应在审阅过的提交中手动更新它。

Oracle 必须三次全部通过。若出现 2/3，通过次数不足仍然是 QA 失败，不能删除测试、降低门槛或标记为 finalized。应保留测试并检查失败运行；若确认是临时 API/Docker 故障，只重跑失败的 QA，不能改变 task 资产。

发布后如不再需要详细运行日志，可额外使用 `--cleanup-logs`；该选项只删除指定 task 的详细日志，正式输出中的 QA 摘要仍然保留。

## 5. 验证正式输出

```bash
python3 pipeline/validate_output.py --root "$PWD"
```

使用 Harbor 运行一条 Oracle：

```bash
harbor run \
  -p "$PWD/output/task-0011" \
  --agent oracle \
  --env docker \
  --n-concurrent 1 \
  --n-attempts 1 \
  --jobs-dir "$PWD/harbor-jobs" \
  --job-name oracle-task-0011 \
  --yes
```

## 6. 并发原则

- 不同 slot 可并发。
- 单 slot 必须依次经过 author → reference → hidden tests → QA → publish。
- 初始最多两个 Docker QA 并发。
- 不要并行运行仍使用整表写回 manifest 的旧阶段；完成单 slot merge 改造前，author/reference 阶段需要由一个调度器统一提交状态。
- GitHub 仓库 clone 使用 shallow、HTTP/1.1、每次 180 秒超时和最多三次重试；部分 clone 会被清理。固定
  preflight commit 失败的仓库会写入该 slot 的 `excluded_repositories`，下次重试自动换候选仓库。

Windows 可双击仓库根目录的 `start-production.cmd`。启动器会请求管理员权限、启用 WSL 2 组件、启动 Docker，并在 Engine 就绪后恢复 `task-0013` QA 和生产入口。若 CPU 虚拟化在 BIOS 中关闭，先开启 Intel VT-x 后再运行；启用 Windows 功能后按提示重启，再次双击启动器。

## 7. 跨电脑统一入口

```bash
python3 scripts/doctor.py --root "$PWD" --env-file ../packy.env
python3 scripts/import_state.py --root "$PWD"
python3 scripts/produce.py --root "$PWD" --env-file ../packy.env --batch-size 1 --workers 1
```

`doctor.py` 只检查环境和配置；`import_state.py` 只恢复脱敏 registry；`produce.py` 才会调用模型和 Docker。先用 `--once` 做一轮 canary，再扩大 `--batch-size`/`--workers`。
