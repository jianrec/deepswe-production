# 已迁移任务基线

本仓库首次导入 11 条已经完成的 Harbor task。运行包来自原 `deepswe/tasks/` 正式导出，而不是旧归档中的工作区或 Docker 镜像。

| Task | 语言 | 仓库 | 状态 | 源码文件 | Changed lines | NOP | Oracle | Mutant |
|---|---|---|---|---:|---:|---|---|---|
| task-0001 | TypeScript | freeCodeCamp/freeCodeCamp | finalized | — | — | 历史 QA | 历史 QA | 历史 QA |
| task-0002 | Go | ollama/ollama | finalized | 13 | 682 | 通过 | 通过 | 通过 |
| task-0003 | Python | NousResearch/hermes-agent | finalized | 11 | 658 | 通过 | 通过 | 通过 |
| task-0004 | JavaScript | react/react | finalized | 7 | 580 | 通过 | 通过 | 通过 |
| task-0005 | Rust | openai/codex | finalized | 13 | 951 | 通过 | 通过 | 通过 |
| task-0006 | TypeScript | pmndrs/zustand | finalized | 9 | 512 | 通过 | 通过 | 通过 |
| task-0007 | Go | gin-gonic/gin | finalized | 7 | 527 | 通过 | 通过 | 通过 |
| task-0008 | Python | TheAlgorithms/Python | finalized | 9 | 514 | 通过 | 通过 | 通过 |
| task-0009 | JavaScript | trekhleb/javascript-algorithms | finalized | 9 | 701 | 通过 | 通过 | 通过 |
| task-0010 | Rust | sharkdp/fd | finalized | 8 | 830 | 通过 | 通过 | 通过 |
| task-0011 | TypeScript | pmndrs/zustand | finalized | 10 | 827 | 通过 | 通过 | 通过 |
| task-0012 | Go | gin-gonic/gin | finalized | 8 | 586 | 通过 | 通过 | 通过 |

说明：早期 `task-0001` 的正式 Harbor 包没有随包携带新版 authoring QA 元数据，但其历史生产记录已通过完整 QA。其他十条在原生产 manifest 中均为 `finalized/finalized`，并记录 `nop_ok=true`、`oracle_ok=true`、`mutant_ok=true`。

本次迁移额外执行：

- 12/12 通过 `pipeline/validate_output.py` 静态 Harbor 校验；
- 未发现 `.DS_Store`、`__pycache__` 等 transient 文件进入 `output/`；
- 未发现常见 API key/token 模式；
- 没有复制 Docker image、container、layer cache、仓库 cache、worktree 或模型响应日志。
