# Portable state snapshot

These files are a reviewed, secret-free snapshot exported by
`scripts/export_state.py`.  They are intentionally committed so a fresh clone
can restore the task registry before production.  They do not contain
repository clones, Docker images, worktrees, model responses, logs, or API
keys.  In-progress staging is reset to `repository_discovery`; only a task
whose finalized package already exists in `output/` is restored as finalized.

After production advances the local manifest, regenerate the snapshot before
switching machines:

```bash
python3 scripts/export_state.py --root "$PWD"
```
