Automated and CI usage of `codex exec` currently has no built-in way to bound how long a non-interactive run may execute or how many shell/tool commands it may run before it is stopped. Long-running or runaway agent turns can consume unbounded wall-clock time and keep invoking commands until the model decides to stop. We want first-class guardrails so that a run can be capped and stopped gracefully, and so that machine consumers of the JSON event stream can detect that a run ended because a limit was hit (rather than because the task completed normally or errored).

Add two run limits to `codex exec`:

1. `--max-commands <N>`: after the Nth shell/tool command execution has started, the run is stopped as soon as that command settles. `N` counts command executions observed on the event stream for the run.
2. `--max-wall-time <DURATION>`: once the elapsed wall-clock time since the run started reaches DURATION, the run is stopped after the in-flight command (if any) settles. DURATION accepts a bare integer (seconds) or a suffixed value using `ms`, `s`, `m`, or `h` (for example `30`, `500ms`, `90s`, `10m`, `2h`).

Both limits are also configurable from `config.toml` under a new `[exec]`...

Public API contract:
- codex-rs/exec/src/limits.rs: `pub struct RunLimits { pub max_commands: Option<u64>, pub max_wall_time: Option<std::time::Duration> }`
- codex-rs/exec/src/limits.rs: `impl RunLimits { pub fn is_unbounded(&self) -> bool }`
- codex-rs/exec/src/limits.rs: `impl RunLimits { pub fn from_exec_limits(limits: &codex_config::ExecLimits) -> RunLimits }`
- codex-rs/exec/src/limits.rs: `pub struct LimitTracker` (opaque; fields private)
- codex-rs/exec/src/limits.rs: `impl LimitTracker { pub fn new(limits: RunLimits, started_at: std::time::Instant) -> Self }`
- codex-rs/exec/src/limits.rs: `impl LimitTracker { pub fn record_command_started(&mut self) }`
- codex-rs/exec/src/limits.rs: `impl LimitTracker { pub fn commands_started(&self) -> u64 }`
- codex-rs/exec/src/limits.rs: `impl LimitTracker { pub fn evaluate(&self, now: std::time::Instant) -> Option<LimitReason> }`
- codex-rs/exec/src/limits.rs: `#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)] #[serde(rename_all = "snake_case")] pub enum LimitReason { MaxCommands, MaxWallTime }`
- codex-rs/exec/src/limits.rs: `impl LimitReason { pub fn as_str(&self) -> &'static str }` returning `"max_commands"` or `"max_wall_time"`
- codex-rs/exec/src/exec_events.rs: `#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)] pub struct RunLimitReachedEvent { pub reason: LimitReason, pub commands_started: u64, pub elapsed_ms: u64, pub max_commands: Option<u64>, pub max_wall_time_ms: Option<u64> }`
- codex-rs/exec/src/exec_events.rs: a new variant on the existing exec JSON event enum tagged `run_limit_reached` carrying `RunLimitReachedEvent` (serialized as `{"type":"run_limit_reached", ...flattened fields...}`)
- codex-rs/exec/src/lib.rs: `pub const EXIT_CODE_RUN_LIMIT_REACHED: i32 = 7;`
- codex-rs/config/src/config_toml.rs: `#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)] pub struct ExecLimitsToml { pub max_commands: Option<u64>, pub max_wall_time: Option<String> }` and a new optional field `pub exec: Option<ExecLimitsToml>` on the top-level `ConfigToml`
- codex-rs/config/src/config_toml.rs: `#[derive(Clone, Debug, Default, PartialEq, Eq)] pub struct ExecLimits { pub max_commands: Option<u64>, pub max_wall_time: Option<std::time::Duration> }`
- codex-rs/config/src/config_toml.rs: `pub fn parse_wall_time(raw: &str) -> Result<std::time::Duration, String>` accepting bare seconds and `ms`/`s`/`m`/`h` suffixes, rejecting zero/empty/unparseable input
- codex-rs/core/src/config/mod.rs: new public field `pub exec_limits: codex_config::ExecLimits` on the resolved `Config`, populated from `ConfigToml.exec`
- CLI flag: `codex exec --max-commands <N>` (u64, optional)
- CLI flag: `codex exec --max-wall-time <DURATION>` (string parsed via `parse_wall_time`, optional)
- config.toml keys: `[exec] max_commands` (integer) and `[exec] max_wall_time` (string)

Acceptance criteria:
- `codex exec --max-commands N "..."` stops the run after at most N command executions have started and exits with the dedicated limit exit code.
- `codex exec --max-wall-time DURATION "..."` stops the run once elapsed wall-clock reaches DURATION and exits with the dedicated limit exit code.
- `max_commands` and `max_wall_time` under `[exec]` in config.toml are honored; a matching CLI flag overrides the config value.
- With no flag and no config key set, run behavior, stdout/stderr, and exit code are unchanged from baseline.
- When a limit is hit, exactly one structured limit event is emitted on the `--json` stream with a stable `type` tag and the fields defined in the public contract.
- When a limit is hit in human output mode, a stable, single-line notice is printed to the human-output path.
- `--max-wall-time` accepts bare seconds and `ms`/`s`/`m`/`h` suffixes; zero, empty, negative, or unparseable values fail before the run starts with a clear error.
- If both command and wall-time limits are simultaneously satisfied, the reported reason is `max_commands`.
- The run is stopped by interrupting the active turn (graceful), not by aborting the process mid-command; any already-started command is allowed to settle before exit.
- The pure limit-accounting type reports the number of commands started and evaluates the active reason deterministically for a given count and elapsed duration.
