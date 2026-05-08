---
name: zwdroid-android-logcat-analysis
description: 分析 Android(含 AAOS)logcat 文本日志，用于 framework 层问题诊断。只要用户提供了 logcat 文件（threadtime 格式），或描述了黑屏、卡顿、ANR、崩溃、异常重启、窗口切换异常、掉帧、用户切换后弹窗等 Android/AAOS 问题，就必须使用此 skill。提供结构化 event 解析、文本日志索引、Activity/进程时间线、异常信号检测（18 条规则），以及通用 framework 问题分析。不处理日志下载/解压/分包，不做 ANR trace 深入解析或 native tombstone 解析。
---

# logcat 分析 SKILL

## 何时使用

**触发场景**：
- 用户提供了 logcat 文本文件，描述了一个 framework 层问题（黑屏、卡顿、崩溃、ANR、异常重启、窗口切换异常等）
- 用户想了解某个时间段内系统/应用发生了什么

**不做的事**：
- 不处理日志下载、解压、多层分包合并（由上游步骤处理）
- 不做 ANR traces 文件深入分析
- 不做 native tombstone 解析
- 不支持 threadtime 以外的格式
- 不处理 kernel 日志

## ⚠️ 硬性约束（违反会导致上下文崩溃或分析丢失）

### 文件读取红线

- ❌ **绝对禁止 Read `.logcat-analysis/events.jsonl` 与 `.logcat-analysis/index.jsonl`**。这两个文件单个就有数百万 token 级，一次 Read 直接撑爆上下文。需要查内容时**必须**用 `query_by_pid.py` / `query_by_time.py` / `grep`。
- ❌ **禁止 Read 大于 5 MB 的 `activity_timeline.json` / `process_timeline.json`**。先 `ls -lh .logcat-analysis/` 看大小，超标就改用 `grep '"event": "..."'` 过滤。
- ✅ 始终可读：`sources.json`、`signals_high.json`、`signals_summary.md`、`analysis_log.md`。
- ✅ `signals_summary.md` = 步骤 3 stdout 的持久摘要（per-rule 命中数 + 头 10 条信号），上下文被压缩后回头 `cat` 即可恢复，不必再读 `signals.json` 整体。

### 进度持久化（核心）

`.logcat-analysis/analysis_log.md` 是分析进度的**唯一可信来源**，让任何中断（上下文压缩、会话切断、人工暂停）都能续接。

- ✅ **每轮分析开始前**：`cat .logcat-analysis/analysis_log.md` 读取已确认事实/已排除方向/待查问题，再决定下一步。
- ✅ **以下节点必须立即 Edit `analysis_log.md`**：
  1. 步骤 1（含 1.1 / 1.2 子节）完成后 → 写"分析上下文"段
  2. 步骤 4 进入 playbook、形成初步假设后 → 写"当前假设"+"待查问题"
  3. 步骤 4 中每次 query 拿到关键证据后 → 追加"已确认的事实"（query 脚本 stderr 也会打印 REMINDER 提示）
  4. 排除某方向时 → 追加"已排除的方向"
  5. 切换假设方向前 → 把当前未结论的内容归位到对应段
- ✅ **每次更新"已确认的事实"段后，回头扫一遍"待查问题"段，已答的删除**。否则 待查问题 段会和事实段重复，造成下一次续接时混乱（以为还有事要查，其实已经答了）。
- ❌ **不要把假设链/证据/排查方向只留在对话里**。压缩或中断后无法恢复。

### query 节制

- 默认 `--max-lines 200`，每次 query 只带**一个具体问题**。
- 同一假设的多次扩展 query 之间，必须先把上一轮结论 Edit 进 `analysis_log.md` 再发下一轮，避免证据只活在临时上下文里。

## 输入

**必需**：
- `files`：一个或多个 logcat 文本文件路径（threadtime 格式，支持多文件按顺序合并处理）

**可选**：
- `year`：日志的年份（默认当前年；从文件名或日志内容推断，例如文件名含 `20260416` 则 year=2026）
- `output_dir`：中间产物目录，默认 `.logcat-analysis/`
- `event_time`：**强烈推荐提供**。问题发生的中心时间点（如 `14:08:30`），是窗口选取和后续查询的锚点；若用户不知道时间，进入全量模式，完成信号检测后再从 high signal 推断锚点
- `target_pid` / `target_package`：已知的问题进程（package name、PID 或自然语言描述均可，Step 1.5 会统一解析为 package name）

## 工作流

> **路径说明**：以下命令使用 `${CLAUDE_SKILL_DIR}` 环境变量，指向本 skill 的安装目录。Claude Code 在执行 bash 命令时 cwd 是 project root，而非 skill 目录，因此必须使用 `${CLAUDE_SKILL_DIR}` 才能正确定位 bundled 脚本和 playbook。

### 步骤 0：确认 event_time 并选取日志文件

**窗口模式下 event_time 是整个分析的前提**，在执行窗口选取前必须先确认：

- 若用户已提供时间点（如 `14:08:52`）：直接进入文件选取
- 若用户提示词中未提供：**先向用户询问**，不要自行推断
- 若用户明确不知道时间：告知将对所有文件全量解析，跳过文件选取，手动指定 `--files` 为目录下全部 logcat 文件；完成步骤 3 后再从 high signal 推断 event_time 锚点

确认 event_time 后，自动选取相关文件：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/select_logs.py \
    --dir <log_dir> \
    --time "14:08:52" \
    --year <YYYY> \
    [--window-before 600] \
    [--window-after 120]
```

- stdout：按时间排序的文件路径列表，直接用于步骤 1 的 `--files`
- stderr：覆盖时间范围摘要（确认窗口是否合理）
- 若返回 ERROR（无文件覆盖该时间点）：检查 event_time 是否正确，或日志是否缺失

**默认窗口**：事发前 10 分钟、事发后 2 分钟，覆盖大多数触发链。如问题涉及开机初始化或长时间积累，用 `--window-before 1800` 扩大。

---

### 步骤 1：解析日志 + 确认分析上下文

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_logcat.py \
    --files <步骤0输出的文件列表> \
    --output-dir .logcat-analysis/ \
    --year <YYYY>
```

产出：`events.jsonl`（结构化 event 记录）、`index.jsonl`（文本日志索引）、`sources.json`（元信息）

**注意**：
- `--year` 务必与日志实际年份一致，否则时间戳会错位
- 多个文件按顺序处理，统一写入同一 work dir

完成 parse 后，必须做两个 post-check 才能进入步骤 2，目的是先锁定"什么时间 + 什么进程"作为后续 query 的过滤锚点。

#### 1.1 验证 event_time 在 time_range 内

- 用户已提供时间点 → 核对 `sources.json` 的 `time_range`，若未覆盖立即告知用户停止分析
- 用户不知道时间（全量模式）→ 跳过此检查，等步骤 3 后从 `signals_high.json` 推断最早 high 信号时间作为锚点

#### 1.2 解析目标进程身份

| 输入形式 | 处理方式 |
|---|---|
| 明确 package name（如 `com.flyme.auto.mediacontrol`）| 直接使用 |
| 自然语言描述（如"音乐小窗"、"导航应用"）| 在 process_timeline.json 中搜索候选进程名，列出后请用户确认 |
| PID（如 `pid=7579`）| `grep '"pid": 7579' .logcat-analysis/events.jsonl \| head -5` 反查 process_name |

#### 1.3 输出确认摘要并写入 analysis_log.md

```
分析上下文：event_time=<HH:MM:SS>（±<N>分钟窗口），target=<package_or_process_name>
```

**立即 Edit `.logcat-analysis/analysis_log.md` 的"分析上下文"段**，填入 event_time、target、时间窗口。这是中断恢复的锚点，不要跳过。

### 步骤 2：构建时间线

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/build_timeline.py --work-dir .logcat-analysis/
```

产出：`activity_timeline.json`（Activity 生命周期）、`process_timeline.json`（进程启动/死亡/被杀）

**注意**：
- 这两个时间线需要来自 **system_server** 的 logcat。如果日志只包含 app 进程日志，时间线将为空——这是正常的，继续后续步骤
- Android 12 起 Activity 生命周期 tag 已从 `am_*` 改为 `wm_*` 前缀

### 步骤 3：检测信号

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/detect_signals.py --work-dir .logcat-analysis/
```

产出：`signals.json`（完整信号列表）、`signals_high.json`（仅 high severity，通常 <10 条）、`signals_summary.md`（持久摘要）

> **stdout = 实时反馈，`signals_summary.md` = 持久摘要**：脚本 stdout 打印 per-rule 命中数 + 头 10 条信号 JSON，可直接用于步骤 4 的 playbook 路由；同样的内容也写到 `signals_summary.md`，上下文被压缩或会话中断后回头 `cat` 即可恢复，**无需再读取 signals.json 整体**。

**当前覆盖的规则**（18 条）：
- `fatal_exception`、`anr_in_process`、`tombstone_written`（high）
- `window_freeze`、`force_finishing`、`watchdog`、`hal_died`（high）
- `proc_died_foreground`（前台进程异常死亡，high）
- `system_server_restart`（system_server 进程死亡，high）
- `input_anr`（InputDispatcher 报告 ANR，high）
- `lowmem_kill`、`low_memory_event`、`proc_died_background`、`selinux_denial`（medium）
- `binder_died`（binder/HwBinder 通信失败，medium）
- `skipped_frames`（≥30 帧，medium）
- `user_switch`（用户切换生命周期，info）
- `task_auto_restored`（user switch 后 WM 静默恢复 task，info）

### 步骤 4：按 playbook 执行

主流程到此结束。完整的"读产物 → 形成假设 → 下钻 → 输出报告"由 playbook 驱动。

#### 路由表

按 `signals_summary.md`（步骤 3 stdout / 文件）的命中信号选 playbook：

| 命中信号 | playbook |
|---|---|
| `fatal_exception` / `tombstone_written` | `${CLAUDE_SKILL_DIR}/playbooks/crash.md` |
| `anr_in_process` / `input_anr` / `watchdog`（独立出现）| `${CLAUDE_SKILL_DIR}/playbooks/anr.md` |
| `user_switch` + `task_auto_restored`（或用户描述涉及切换/弹窗）| `${CLAUDE_SKILL_DIR}/playbooks/user-switch.md` |
| 其他 / 多类信号同时出现 / 不确定 | `${CLAUDE_SKILL_DIR}/playbooks/overview.md` |

> 多类信号同时命中时，按 high severity 优先；都是 high 时按时间最早的一类优先。

#### playbook 内部覆盖

每个 playbook 自带完整流程：定位锚点 → 读产物（sources.json / signals_summary.md / timeline）→ 形成假设 → 下钻 query → 输出 `.logcat-analysis/report.md`。SKILL.md 不再重复 query 三件套（`trace_starter` / `query_by_pid` / `query_by_time`）的命令样例，详见 overview.md 第 5 节。

#### 红线提醒（playbook 内部也再次强调）

- ❌ 严禁 `Read events.jsonl / index.jsonl`
- ✅ 读 timeline 前先 `ls -lh .logcat-analysis/`，超 5 MB 改 grep
- ✅ 每次 query 拿到关键证据 → 立即 Edit `analysis_log.md`（query 脚本 stderr 也会打 REMINDER 提示）
- ✅ 报告必须 Write 到 `.logcat-analysis/report.md`，对话里只输出 < 300 字精简版

---

## 何时停止分析

证据链断裂或日志缺失时，**立即停止 query 循环**，在 `report.md` 写明"证据不足，需补充 X"。以下 4 类情况是明确的退出信号：

1. **日志 time_range 不覆盖问题时间点**
   - 第 1 次：用 `select_logs.py --window-before 1800` 扩大窗口重 parse
   - 扩窗 2 次后仍不覆盖 → 停。报告写"日志时间范围不覆盖事发时间，需补充 [事发时间] 前后的 logcat"

2. **关键 tag 全部 0 命中**
   - playbook 提示的关键 tag（如 ANR 场景的 `InputDispatcher`/`Binder`、crash 场景的 `AndroidRuntime`/`DEBUG`）在事发时间窗内全部 0 命中 → 停
   - 报告写"事发时间窗内未捕获 [tag 列表]，需确认 logcat buffer 是否完整 / 该 buffer 是否启用"

3. **证据链断在 system_server 而日志只有 app**
   - `activity_timeline.json` / `process_timeline.json` 为空（步骤 2 已提示）+ 需要追"是谁启动 / 是谁杀进程"时 → 停
   - 报告写"需补充 system_server 进程的 logcat，当前日志只包含 app 进程"

4. **playbook 走完仍无定论**
   - 经过 playbook 全部步骤 + overview.md 第 5 节"扩展文件集" + 第 6 节"扫描未知区域"后仍无清晰因果链 → 停
   - 报告写"证据不足，可能根因方向：[A / B / C]，需补充 [具体数据，如 traces.txt / tombstone / kernel log]"

> 退出条件触发时，**不要继续盲扩 window-before 或盲发 query**。承认日志限制比堆砌无效证据更有价值。

---

## 何时追加 TODO

发现 SKILL 本身的盲区或问题时，追加到 `TODO.md` 的"待处理(MVP 后)"段（参考 CLAUDE.md 既定模板）。两个高频触发点：

1. **步骤 1 后查 sources.json.unknown_event_tags**
   - 列表中出现"看起来与本次问题相关、但字典未覆盖"的 tag → 追加 TODO："event-log-tags 字典缺失 tag:xxx（步骤 X，YYYY-MM-DD 真实 case 中遇到）"
   - **不要为此中断分析**——继续走 playbook，结束后再写

2. **步骤 4 playbook 下钻时的脚本/规则异常**
   - detect_signals 误报 / 漏报某种已知模式
   - 某 query 脚本输出格式不符合 playbook 预期
   - 某 playbook 的判断准则在新 case 中不成立
   - → 追加 TODO，附带 `source_file:line_no` 证据，便于后续修

> TODO.md 中的条目由用户决定何时实施。**MVP 阶段不要直接改算法/规则去"修复"这些发现**——按 CLAUDE.md 的"单向前进，不回头重构"原则。

---

## 已知限制

- 仅支持 threadtime 格式
- 仅基于 Android 12（android-12.0.0_r34）的 event-log-tags schema；其他版本的 tag 字段数不同时可能出现字段串位
- 跨年日志需手动指定 `--year`
- 多行 Java stack trace 不合并，每行独立索引
- 厂商定制 tag（AAOS OEM、MIUI 等）在 unknown_event_tags 中列出，不结构化解析
- `query_by_pid` 默认合并 index.jsonl + events.jsonl；`query_by_time` 默认只搜索 index.jsonl，加 `--include-events` 可包含结构化事件
