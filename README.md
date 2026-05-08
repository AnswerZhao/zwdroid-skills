# zwdroid-skills

Open-source Agent Skills authored by zwdroid — covering Android, Interest, Agency, Life, and Efficiency.

All skills are namespaced with the `zwdroid-` prefix.

## Skills

| Name | Purpose |
|---|---|
| [`zwdroid-android-jadx`](skills/zwdroid-android-jadx/) | Decompile Android APK / dex / jar / aar with jadx; cross-reference logcat to source for bug investigation. |
| [`zwdroid-android-logcat-analysis`](skills/zwdroid-android-logcat-analysis/) | Parse Android/AAOS logcat (threadtime); structured event indexing, timeline, signal detection, and playbook-driven framework diagnosis. |
| [`zwdroid-unstuck`](skills/zwdroid-unstuck/) | Diagnose where a multi-turn dialogue is stuck and suggest the next concrete action. |

## Use

The repo's `.claude/skills/` is auto-loaded by Claude Code as **project-scoped skills** when you start a session inside the repo:

```bash
git clone https://github.com/zwdroid/zwdroid-skills
cd zwdroid-skills
# Open Claude Code here — skills are immediately available
```

For **global** availability (skill works in any project, not just this one), symlink the skill into your user-level skills dir:

```bash
ln -s "$PWD/skills/zwdroid-android-jadx" ~/.claude/skills/zwdroid-android-jadx
```

## Repo layout

- **`.claude/skills/<name>/`** — real skill source. Auto-loaded by Claude Code when working in this repo.
- **`skills/`** — symlink to `.claude/skills/`. Same files; provided for browser / IDE navigation.
- **`devdoc/<name>/`** — per-skill dev material (specs, todos, eval data, test fixtures). Gitignored.
- **`CLAUDE.md`** — project-level instructions automatically loaded by Claude Code in this repo.
- **`AUTHORING.md`** — style guide and lifecycle for adding new skills.

## Contributing

This is a personal skills suite, but issues / suggestions welcome.

If you're adding a skill or proposing a change, read [`AUTHORING.md`](AUTHORING.md) first — it captures the conventions and a few non-obvious lessons from past iterations.

## License

TBD — pick MIT or Apache-2.0.
