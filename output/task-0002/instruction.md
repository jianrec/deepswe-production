`ollama launch <integration>` configures a third-party coding tool (Claude Code, Codex, Copilot CLI, OpenCode, and the other registered integrations) to talk to the local Ollama server and then executes it. Today this happens as a single opaque side-effecting step: the command resolves a model, sets environment variables, may create or patch on-disk config files for the target tool, and then execs the tool's binary. Users and scripts have no way to preview exactly what `launch` will change before it runs, no way to diff config mutations, and no machine-readable description of the resolved binary/args/env for automation or debugging.

Introduce a first-class, side-effect-free Launch Plan. Separate "decide what to do" from "do it": every registered integration must be able to compute a `Plan` describing the resolved model, the target binary and its arguments, the exact environment variables that would be set (including the OLLAMA host the tool is pointed at), and every config file that would be created, overwritten, merged, or left unchanged — with the file's final contents and mode. Add `--dry-run` and `--json` flags to `ollama launch` that build and print this plan WITHOUT writing any file, creating any directory, or executing any process. Add `--model` so a plan (and a real launch) can target an explicit model instead of the default, and add an `OLLAMA_LAUNCH_MODEL` environment fallback for the default model so plans are deterministic offline.

Compatibility requirements: the existing `ollama launch <name>` behavior (with no new flags) must be unchanged — same files written, same env, same exec. The real launch path must be re-expressed in terms of the same `Plan` it computes, so that applying a freshly built plan reproduces today's on-disk and environment results exactly (no behavioral drift). Plan output must be deterministic: environment map keys and file entries are emitted in a stable sorted order, and `Plan.JSON()` must round-trip. `--dry-run` must never touch the filesystem or spawn a process even when target config files already exist; it must instead report the `Action` (create/m...

Public API contract:
- package launch
- type FileAction string
- const FileCreate FileAction = "create"
- const FileMerge FileAction = "merge"
- const FileOverwrite FileAction = "overwrite"
- const FileUnchanged FileAction = "unchanged"
- type PlannedFile struct { Path string `json:"path"`; Contents string `json:"contents"`; Mode fs.FileMode `json:"mode"`; Action FileAction `json:"action"` }
- type Plan struct { Integration string `json:"integration"`; Binary string `json:"binary"`; Args []string `json:"args"`; Env map[string]string `json:"env"`; Model string `json:"model"`; Files []PlannedFile `json:"files"`; Notes []string `json:"notes,omitempty"` }
- type PlanOptions struct { Model string; Home string; Host string }
- type Planner interface { Plan(ctx context.Context, opts PlanOptions) (Plan, error) }
- func BuildPlan(ctx context.Context, name string, opts PlanOptions) (Plan, error)
- func RegisteredIntegrations() []string
- func (p Plan) Render() string
- func (p Plan) JSON() ([]byte, error)
- func (p Plan) Apply(ctx context.Context) error
- package envconfig
- func LaunchModel() string
- CLI flag: ollama launch --dry-run (bool, default false)
- CLI flag: ollama launch --json (bool, default false; only meaningful with --dry-run)
- CLI flag: ollama launch --model <string> (default empty)
- environment variable: OLLAMA_LAUNCH_MODEL (default model for launch/plan when --model is omitted)

Acceptance criteria:
- `launch.BuildPlan(ctx, name, opts)` returns a fully populated Plan for every registered integration and an error for unknown names, without performing any filesystem write or process exec.
- `ollama launch <name> --dry-run` prints a human-readable plan and exits 0 without configuring or executing the target tool; adding `--json` prints the same plan as JSON.
- `ollama launch <name> --model <m>` uses `<m>` as the plan/launch model; when omitted the model falls back to `OLLAMA_LAUNCH_MODEL` and then the existing default resolution.
- The real (non-dry-run) launch path builds a Plan and applies it, producing byte-identical config files, identical environment, and the identical binary+args exec as the pre-change behavior for the same inputs.
- Plan.Env keys and Plan.Files entries are emitted in stable sorted order, and Plan.JSON() output is deterministic across repeated calls with equal inputs.
- Each PlannedFile reports Action=create when the target does not exist, overwrite/merge when it exists, and unchanged when applying would not modify the current contents.
- `--dry-run` produces correct create/overwrite/merge/unchanged actions even when the target files already exist on disk, still without mutating them.
- `launch.RegisteredIntegrations()` returns the sorted set of integration names, and every returned name yields a non-empty Plan from BuildPlan.
- Existing `./cmd/launch/...` tests continue to pass unchanged.
