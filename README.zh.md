# zwdroid-skills

由 zwdroid 编写的开源 Agent Skills ，涵盖 Android、兴趣、代理、生活与效率。

所有技能均使用 `zwdroid-` 前缀命名。

## 技能列表

| 名称 | 用途 |
|---|---|
| [`zwdroid-android-jadx`](skills/zwdroid-android-jadx/) | 使用 jadx 反编译 Android APK / dex / jar / aar；将 logcat 与源码交叉引用以定位 Bug。 |
| [`zwdroid-android-logcat-analysis`](skills/zwdroid-android-logcat-analysis/) | 解析 Android/AAOS logcat（threadtime 格式）；结构化事件索引、时间线、异常信号检测，以及 playbook 驱动的 framework 问题诊断。 |
| [`zwdroid-unstuck`](skills/zwdroid-unstuck/) | 诊断多轮对话的卡顿点，给出下一步具体动作。 |

## 使用方式

本仓库的 `.claude/skills/` 会在你在仓库目录内启动 Claude Code 会话时，作为**项目级技能**自动加载：

```bash
git clone https://github.com/zwdroid/zwdroid-skills
cd zwdroid-skills
# 在此目录打开 Claude Code — 技能立即可用
```

如需**全局**可用（在任何项目都能使用该技能，不仅限于本仓库），可将技能软链接到用户级技能目录：

```bash
ln -s "$PWD/skills/zwdroid-android-jadx" ~/.claude/skills/zwdroid-android-jadx
```

## 仓库结构

- **`.claude/skills/<name>/`** — 技能的真正源码。在本仓库内工作时由 Claude Code 自动加载。
- **`skills/`** — 指向 `.claude/skills/` 的软链接。内容相同；便于浏览器 / IDE 导航。
- **`devdoc/<name>/`** — 每个技能的开发资料（规格、待办、评估数据、测试夹具）。已加入 `.gitignore`。
- **`CLAUDE.md`** — 项目级指令，在本仓库内由 Claude Code 自动加载。
- **`AUTHORING.md`** — 新增技能的样式指南与生命周期说明。

## 贡献

这是一套个人技能集，但欢迎提交 issue 与建议。

如果你想新增技能或提议改动，请先阅读 [`AUTHORING.md`](AUTHORING.md) — 其中记录了约定以及过去迭代中总结的一些非显而易见的经验。

## 许可证

待定 — 考虑 MIT 或 Apache-2.0。
