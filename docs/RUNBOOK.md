# 运行手册

以下命令均从仓库根目录执行。真实凭据文件必须位于仓库外，例如：

```bash
chmod 600 /path/to/pack-strong.env /path/to/pack-weak.env
```

## 1. 初始化状态

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
