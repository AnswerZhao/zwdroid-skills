# Authoring skills in zwdroid-skills

This document covers naming, layout, style, lifecycle, and non-obvious rules learned from prior skills. Read it before creating a new skill or making structural changes to an existing one.

## Naming

All skills use the `zwdroid-` prefix to namespace them clearly within Claude Code's skill registry. A second-level component identifies the domain or workflow.

| Pattern | Example |
|---|---|
| `zwdroid-<platform>-<tool>` | `zwdroid-android-jadx`, `zwdroid-ios-frida` |
| `zwdroid-<verb>` | `zwdroid-deploy`, `zwdroid-review-pr` |
| `zwdroid-<workflow>` | `zwdroid-bug-triage` |

Avoid vague names like `zwdroid-utils` — they accumulate disparate functionality and serve no specific question.

## Per-skill directory layout

```
.claude/skills/zwdroid-<name>/
├── SKILL.md                ← required; agent-facing
├── references/             ← optional; loaded on demand by the agent
│   ├── topic_a.md
│   └── topic_b.md
└── scripts/                ← optional; bundled executables
    ├── _shared.py          ← internal helpers (underscore prefix)
    └── <verb>.py
```

`SKILL.md` is the only required file. Add `references/` and `scripts/` as warranted.

## SKILL.md style guide

### Frontmatter

```yaml
---
name: zwdroid-<name>          # must match the directory name
description: <when-to-trigger; multi-language phrases ok; "use proactively" language welcome; clear boundaries on what the skill is NOT for>
---
```

The description is the only mechanism Claude Code uses to decide whether to trigger the skill. Make it specific:

- Include concrete trigger phrases the user might say. Mix English and Chinese as appropriate to your audience.
- State boundaries explicitly: "Do not use for X, Y, Z."
- "Use proactively whenever..." phrasing helps combat undertriggering — Claude tends to defer too readily.

Length: 600–1200 chars is typical. Longer is OK if it covers real semantic ground; fluff is not.

### Body

- **Keep total ≤ 500 lines.** Approaching the cap is a signal to offload to `references/`.
- **Lead with a Mental Model.** 3–5 principles framing the work. The model is load-bearing — it shapes how the agent generalizes when context shifts.
- **Phase the workflow** (Phase 1 → 2 → ...) for multi-step skills. Predictability lets agents resume mid-flow without re-reading the whole file.
- **Explain the why** behind every rule. Today's LLMs have good theory of mind; if you say "do X" without why, they'll deviate under unusual context. With the why, they generalize correctly.
- **Avoid all-caps `ALWAYS` / `NEVER`.** Reframe as "X because Y" — same constraint, more durable.

### References

For topics detailed enough to merit their own file:

- 50–250 lines per reference is the sweet spot.
- 300+ lines: include a TOC at the top.
- From SKILL.md, link with one-line summaries: `references/foo.md — handles X (read when Y)`. Don't make the agent guess what each reference contains.

## Scripts conventions

These mirror https://agentskills.io/skill-creation/using-scripts. Read it.

### Invocation

Use `${CLAUDE_SKILL_DIR}` to reference bundled scripts, playbooks, and references. Claude Code executes bash commands with the project root as cwd, so relative paths like `scripts/foo.py` would resolve to `<project>/scripts/foo.py` rather than the skill directory. `${CLAUDE_SKILL_DIR}` is the only portable way to locate skill-bundled files regardless of where the skill is installed (project, personal, or plugin).

```
✅ python3 ${CLAUDE_SKILL_DIR}/scripts/foo.py
✅ bash ${CLAUDE_SKILL_DIR}/scripts/foo.sh
❌ python3 scripts/foo.py                   ← resolves to project root, not skill dir
❌ /absolute/path/scripts/foo.py            ← not portable
```

### Language choice

- **Python** for non-trivial logic. **Stdlib only** — no PyPI dependencies. Target Python 3.9+, opening with `from __future__ import annotations` so modern type-hint syntax (`str | None`, `dict[str, int]`) parses without runtime evaluation.
- **Bash** for thin shell glue. POSIX bash; avoid GNU-only flags.
- **Other languages** only when the skill's domain demands it.

### Interface design

`--help` documents usage:
- Python: argparse gives this for free.
- Bash: write a `--help` / `-h` case that prints USAGE / OPTIONS / OUTPUT / EXIT CODE.

Stream separation:
- **stdout**: real, machine-parseable data (JSON, structured records).
- **stderr**: progress, warnings, diagnostics. Lets agents pipe stdout into `jq` or `cut` cleanly.

Output:
- Prefer structured formats (JSON, TSV) over free-form text. They compose with standard tools.
- Predictable size: large output should write to a file (`--output path`) or paginate (`--offset`). Don't dump 50 MB to stdout — many agent harnesses truncate.

Exit codes:
- `0` success.
- `1` general failure.
- `2` invalid usage / args (argparse's default).
- `3` resource not found / lookup miss.
- Document them in `--help`.

### Error messages

```
❌ Error: invalid input
✅ class_outline: class not found: com.foo.Bar
   Hint: run scripts/build_xref_index.py first.
```

An error message directly shapes the agent's next attempt. Make it actionable: what went wrong, what was expected, what to try.

### Don't do

- **Interactive prompts** — agents can't respond to TTY.
- **Hidden state** — env vars not documented in `--help`.
- **Bundled binaries** — have the user install via their package manager; surface missing deps via fail-fast.
- **Defensive pre-flight checks duplicating native errors.** A 170-line dependency-check script that just re-prints what `command not found` already says is pure overhead.

## Dependency handling

Most missing-dep cases produce native errors clear enough on their own (`jadx: command not found`, `python3: command not found`). Don't write elaborate pre-flight scripts to re-surface them.

The narrow exception: when a transitive dep failure produces a cryptic error (e.g., jadx without Java throws JNI errors). For those rare cases, a 2-line inline check at the top of the relevant phase is enough:

```bash
command -v <tool> >/dev/null 2>&1 && <transitive-check> \
  || { echo "Need <tool> + <transitive>; install: <hint>" >&2; exit 1; }
```

That's the entire pattern. Don't grow it into a multi-platform install-hint orchestrator.

## Dev material → `devdoc/`

Per-skill dev artifacts live in `devdoc/zwdroid-<name>/`, gitignored:

```
devdoc/zwdroid-<name>/
├── spec.md                ← functional requirements / what to build
├── todo.md                ← progress + decision log (date-stamped!)
├── plan-<topic>.md        ← non-trivial implementation plans
├── samples/               ← test fixtures (binaries, sample inputs)
└── test-ws/               ← evaluation workspaces, eval results
```

Why gitignored: these are dev-process artifacts, not shippable. Keep them locally for reference; don't ship them to users.

Decision log entries should be date-stamped:

```markdown
- **2026-05-08**: chose `.claude/skills/` (project-scoped) over `~/.claude/skills/` (user-scoped). Single-source-of-truth + clone-and-go beats global availability for this repo.
```

Three months later you'll thank yourself.

## Lifecycle

```
new:
  /skill-creator drafts → .claude/skills/zwdroid-<name>/
  devdoc/zwdroid-<name>/spec.md captures intent

bake:
  test on real input (not synthetic eval — see anti-patterns)
  fix obvious gaps surfaced by smoke testing
  commit when "good enough for first real use"

iterate:
  real usage exposes friction → edit .claude/skills/<name>/ directly
  update devdoc/<name>/todo.md with date-stamped entry
  commit

retire:
  move to .claude/skills/_archive/<name>/, or just delete
  git history preserves it either way
```

Don't run synthetic eval cycles for tool-type skills. They over-constrain to test prompts and rarely surface real issues.

## Anti-patterns (lessons from prior skills)

- **Don't pre-flight what fails-fast naturally.** A 170-line `check_requirements.sh` adds maintenance burden without catching anything users wouldn't see from `command not found`. Cut to a 2-line inline check for the one case that matters (the cryptic transitive). Lesson from `zwdroid-android-jadx`.

- **Don't synthetic-eval tool skills.** Pass rates from assertions you wrote, while the skill follows the workflow you wrote, are tautological. The +0.15 pass-rate "win" in `zwdroid-android-jadx` iteration-1 came entirely from compliance assertions ("did agent use the workspace path I told it to use?"), not from any quality measurement. Real users surface real bugs; synthetic agents surface design echoes of yourself.

- **Don't over-document reusable scripts in SKILL.md.** SKILL.md should say *when* to use a script and *what to expect from output*; the script's `--help` documents everything else. Phase 1.1 was 37 lines for "run this check"; cut to 7 by trusting `--help`.

- **Don't bundle multiple skills in one.** A `zwdroid-utils` is a code smell. Each skill should answer ~one substantive question. Two questions = two skills.

- **Don't copy skills between locations.** Single source of truth: `.claude/skills/<name>/`. The `skills/` symlink at the repo root is for browsing only; it's the same files. No `cp` or `rsync` between locations should ever be necessary.

## Reference

- skill-creator framework: `/skill-creator` slash command
- agentskills.io conventions: https://agentskills.io/skill-creation/using-scripts
- Existing skills in this repo: read them as patterns
