# Project: zwdroid-skills

Open-source Claude Code skills authored by zwdroid. All skills namespaced with the `zwdroid-` prefix.

## Layout

- **Real skill source**: `.claude/skills/<name>/` — auto-loaded by Claude Code as project-scoped skills when this repo is the cwd.
- **Browse symlink**: `skills/` → `.claude/skills/`. Same files, different path. Both work for editing and tooling.
- **Per-skill dev material**: `devdoc/<name>/` — specs, todos, eval data, test fixtures. Gitignored.

## When working in this repo

- Edit skills directly in `.claude/skills/<name>/` (or the symlinked `skills/<name>/` — same files). Changes take effect on the next skill invocation; no install step.
- For new skills, use `/skill-creator` and target `.claude/skills/zwdroid-<name>/` as the output path.
- Record decisions, plans, and open questions in `devdoc/<name>/todo.md` with date-stamped entries — three months later you'll need them.
- For non-trivial implementation work, write a plan first; copy it into `devdoc/<name>/plan-<topic>.md` after.

## Conventions (summary — see AUTHORING.md for full guide)

- All skills use `zwdroid-` prefix (e.g., `zwdroid-android-jadx`).
- **Python scripts**: stdlib only; target Python 3.9+ with `from __future__ import annotations`. Run with `--help` for usage.
- **Shell scripts**: POSIX bash. Don't assume GNU coreutils flags.
- **Script invocation in SKILL.md**: relative paths from skill root (`scripts/foo.py`), never `${SKILL_DIR}/...` or absolute paths. The agent harness resolves them.
- **Stdout/stderr split**: real data → stdout (pipe-friendly); diagnostics/progress → stderr.
- **SKILL.md ≤ 500 lines**; offload deep content to `references/<topic>.md`.
- **Explain why** in instructions, not just what. LLMs generalize from reasons.
- **No defensive pre-flight checks** for tools that fail-fast naturally — let native errors speak.

## Anti-patterns

- ❌ Copying skills between locations. Single source of truth is `.claude/skills/<name>/`.
- ❌ Bundling test fixtures or eval data inside the skill. They belong in `devdoc/`.
- ❌ Running synthetic eval cycles for tool-type skills. Iterate from real usage instead.
- ❌ Multi-page dependency-check scripts duplicating native error messages.

## Pointers

- Detailed authoring guide: `AUTHORING.md`
- Public face / install instructions: `README.md`
- skill-creator framework: `/skill-creator` slash command
- agentskills.io scripts conventions: https://agentskills.io/skill-creation/using-scripts
