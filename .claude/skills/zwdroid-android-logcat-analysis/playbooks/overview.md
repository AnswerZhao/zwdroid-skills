# 通用分析 playbook

## 适用场景

无明确问题类型时的通用入口。适用于：
- 用户描述了一个现象但不确定根因（"界面卡了一下"、"App 闪退"、"车机重启"）
- 用户想知道某个时间段内系统发生了什么
- 信号检测没有命中已知模式，需要人工排查

如果 signals.json 已明确指向 ANR / crash / 内存压力，直接从步骤 3 开始。

---

## 分析步骤

### 1. 了解日志全貌（读 sources.json）

关注：
- `time_range`：日志覆盖的时间段，确认问题时间点在范围内
- `stats.parse_errors`：若超过 1%，日志质量存疑，需告知用户
- `stats.unknown_event_tags`：列出了字典未覆盖的 event tag，可能含关键 AAOS 私有事件

> 如果问题时间点不在 time_range 内，立即告知用户，不要继续分析。

### 2. 看异常信号

- **优先使用 detect_signals.py 的 stdout 摘要**（per-rule 命中数表），这是初步 triage 的全貌，无需读 signals.json 做汇总
- 若有 high severity 命中，直接读 `.logcat-analysis/signals_high.json`（Step 3 已生成，通常 <10 条）
- 关注**时间集中段**：多个 high 信号在 30 秒内密集出现，通常是某次故障的连锁反应
- 记录每个 high signal 的 `ts` 和 `source`，后续下钻的锚点

**信号解读参考**：
| rule_id | 含义 | 下一步 |
|---|---|---|
| `fatal_exception` | App JVM 崩溃 | 用 query_by_pid 查该 PID；找到 `FATAL EXCEPTION` 行后 Read 原始文件获取完整堆栈（多行 stack trace 不合并）|
| `anr_in_process` | ANR，捕获了进程名 | 用 query_by_time 查 ANR 前 30 秒；补查 `InputDispatcher` tag 确认是否为 input timeout |
| `tombstone_written` | Native crash | 查同时间段 `DEBUG` tag；需补充 `/data/tombstones/` 文件才能看完整 signal/backtrace |
| `hal_died` | HAL 通信失败（hwservicemanager E 级）| 查同时间段所有 E 级日志；确认哪个 `android.hardware.*` 服务异常，再查依赖该 HAL 的进程 |
| `selinux_denial` | SELinux 拒绝操作 | 读 captures.msg 确认被拒绝的操作和目标；大量集中出现时是根因，零星出现时可能是背景噪音 |
| `proc_died_foreground` | 前台进程异常死亡（oom_adj 0–199）| 查该 PID 的死亡前日志 |
| `lowmem_kill` | LMK 内存回收 | 看 `low_memory_event` 密度趋势：间隔越来越短说明内存持续恶化 |
| `watchdog` | 系统 watchdog 超时 | 查同时间段的所有 E 级日志 |
| `skipped_frames` | UI 掉帧 ≥ 30 帧 | 查 Choreographer/SurfaceFlinger；掉帧有但根因不明时升级到 systrace/perfetto |
| `user_switch`（info） | 用户切换生命周期事件（AAOS 专属） | **不是异常，是上下文**。先确认时间线上的切换节点（from_user/to_user），再解读同时段的 high/medium 信号 |

> **AAOS 提示**：`user_switch` 信号是理解其他信号的背景——看到大量 `proc_died_background` 或 `lowmem_kill` 时，先检查前后是否有 `user_switch`，若有则这批死亡大概率是切换触发的正常清理，而非内存问题。

### 3. 看时序（读 activity_timeline + process_timeline）

在**问题时间点前后 2 分钟**内，关注：

**activity_timeline**：
- 是否有非预期的 `destroy` / `pause`（App 没退但 Activity 被销毁）
- `launch_time` 是否异常长（通常 < 1000ms，超过 3000ms 需调查）
- 是否有同一 component 的反复 create/destroy（异常重建）
- 补查 `ActivityThread` 的 `Displayed` 日志获取精确的首帧时间（`Displayed com.example/.MainActivity: +1s234ms`）——这是用户可感知的实际启动耗时，比 launch_time 更准确

**process_timeline**：
- 问题进程是否在事件时间点附近有 `died` 或 `killed`
- `killed` 的 reason 是 `lmk`（内存）还是其他原因
- 是否有多个进程在短时间内连续死亡（级联崩溃或内存雪崩）
- **如果问题是"某进程意外启动"**：在 process_timeline 里找该进程的首条 `start` 记录，确认是冷启动（新 pid）还是重启（pid 变化），并记录启动时间作为下钻的锚点

> 如果两个 timeline 都为空，说明日志来自 app 进程而非 system_server，需要告知用户补充 system_server 日志。

### 4. 形成初步假设

基于信号和时序，归纳 1-2 个可能的根因方向。例如：
- "内存压力导致 com.example.app 在前台被杀"
- "Activity B 在创建后 200ms 内被销毁，可能是权限或配置问题"
- "watchdog 集中在 11:07，可能是系统启动期的正常抖动"

**尚无信号或时序为空时**：直接用 query_by_time 扫问题时间点前后 30 秒的 E/W 级日志，再形成假设。

### 5. 按假设下钻

根据假设选择工具：

**假设涉及特定进程**：
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_pid.py --pid <PID> --level E --max-lines 100
```

**假设涉及特定时间段**：
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py --start "HH:MM:SS" --end "HH:MM:SS" --tags "ActivityManager"
```

下钻原则：
- 每次下钻带着明确问题（"这个 PID 在死亡前 1 秒打了什么 E 级日志？"）
- 找到证据链：信号 → 时间点 → 具体日志行 → 原始文件 line_no
- 没看到预期日志时，扩大时间窗口，或换 tag 重查

**找不到根因时：沿因果链往前扩展**

分析的核心驱动逻辑是：**找到了 WHAT（现象），但找不到 WHY（触发者）→ 往前看更早的日志**。

bug 的直接现象可能只是一个长链条的末端。最初的 ±10 分钟窗口只是起点，当证据链断掉时，需要系统性地往前扩，直到找到根因或确认"日志中信息缺失、无法推断"为止。

#### 扩展流程

每当出现以下情况，就往前扩一层：
- 查询返回空，但逻辑上这里应该有日志
- 找到了"某事件发生"，但找不到"是谁触发了它"
- timeline 显示进程启动，但看不到启动原因
- signal 指向某个方向，但那个时间点没有支撑证据

**第一步：确认是查询范围不够，还是文件不够**

```bash
# 看当前已解析的日志覆盖了多早
cat .logcat-analysis/sources.json  # 看 time_range.start
```

- 目标时间在 `time_range` 内 → 直接扩大 query 范围（用下方查询模板）
- 目标时间在 `time_range` 之外 → 必须先扩展文件集（见下方"扩展文件集"）

**第二步：查询范围扩大（已解析数据内）**

```bash
# 扩大时间窗口查 E/W 级日志
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py --start "HH:MM:SS" --end "HH:MM:SS" --level E,W

# 从进程启动时间起查完整日志（冷启动场景）
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_pid.py --pid <PID> --max-lines 200

# 从已知触发事件（如 user_switch）时间起查关键 tag
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py --start "<trigger_ts>" --end "<event_ts>" --tags "ActivityManager,WindowManager"
```

**第三步：扩展文件集（目标时间超出当前 time_range 时）**

```bash
# 扩大 --window-before，重新选文件（每次扩大 10-30 分钟，不要一次跳到全量）
python3 ${CLAUDE_SKILL_DIR}/scripts/select_logs.py \
    --dir <log_dir> --time "<event_time>" --window-before 1800

# 重新 parse + 重建全部产物（三步，work dir 被覆盖）
python3 ${CLAUDE_SKILL_DIR}/scripts/parse_logcat.py --files <新文件列表> --output-dir .logcat-analysis/ --year <YYYY>
python3 ${CLAUDE_SKILL_DIR}/scripts/build_timeline.py --work-dir .logcat-analysis/
python3 ${CLAUDE_SKILL_DIR}/scripts/detect_signals.py --work-dir .logcat-analysis/
```

> 扩展后 work dir 产物全部刷新，但上下文中已有的分析结论和 source_file+line_no 引用仍然有效（它们指向原始日志文件，不受影响）。扩展完成后从第三步"检测信号"重新读取新产物继续分析。

**第四步：全量兜底（以上扩展均无定论）**

```bash
# 用 grep 直接扫原始文件，不经过 parse，避免 token 爆炸
grep -n "关键词" <log_dir>/log_logcat*.log

# 锁定行号后，用 Read 读取前后 20-30 行确认上下文
```

此时如果仍找不到根因，应在报告中明确写明：**证据链在哪个时间点断掉、缺少什么信息、需要补充什么日志**（如需要 system_server 日志、需要更早的开机段日志等）。

### 6. 主动扫描"未知区域"

在给出结论前，额外做两件事：

**6a. 审阅 unknown_event_tags**

`sources.json` 里的 unknown_event_tags 是日志中出现、但字典未覆盖的 event tag（可能含 AAOS 私有关键事件）。抽取其中 **看起来与问题相关** 的 tag，用 query_by_time 查一下：
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py --start "..." --end "..." --tags "<unknown_tag>"
```

**6b. 抽查非 signal 的 E 级日志**

用 query_by_time 加 `--level E,W` 扫问题时间点前后 1 分钟，看是否有 signals 没覆盖的错误：
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py --start "HH:MM:SS" --end "HH:MM:SS" --level E,W
```
关注与已知模块无关的 E 级日志，可能是规则尚未覆盖的新异常模式。

### 7. 输出报告

---

## 报告模板

> **报告写到 `.logcat-analysis/report.md`**，不要只输出在对话里。这是面向人的最终交付物，需要脱离对话和原始日志独立可读。
> 推理过程、未确认假设、已排除方向留在 `.logcat-analysis/analysis_log.md`，**不进 report.md**。

```
# Logcat 分析报告

## 问题描述
<一句话复述用户报的问题：什么时间、什么现象、什么期待行为>
例：04-16 14:08:52.593 切换用户登录后，com.flyme.auto.mediacontrol（音乐小窗）被自动启动，用户未主动操作。

## 结论
<一句话因果（根因或最可能的方向）+ 置信度：高/中/低>
<如证据不足，明确写"证据不足，需补充 X 才能定论">
例：高置信。这是 AAOS user switch (11→12) 后 WindowManager 静默恢复 task 的标准行为，不是 startActivity 路径，无 caller。

## 触发链
<按时间顺序串起来的关键事件，每条带 source_file:line_no>
<同一连续动作的多个子步骤（如 wm_on_create / wm_on_start / wm_on_resume 三连）合并为一条，只保留第一条标志性的；不要 1ms 一条堆满模板>
例：
1. 14:08:29.931 uc_dispatch_user_switch user 11→12  [log_logcat@20260416_14-09-52.log:80313]
2. 14:08:52.561 mediacontrol pid=23387 fork (user 12)  [log_logcat@20260416_14-09-52.log:84546]
3. 14:08:52.593 wm_on_create_called MainActivity (token=264098726, signal_1702 task_auto_restored) [log_logcat@20260416_14-09-52.log:84606]
   （后续 wm_on_start/on_resume 在同一 token 上 9ms 内完成，已折叠）

## 关键证据
<带 source_file + line_no 的原文引用，含阴性证据>
例：
- [log_logcat@20260416_14-09-52.log:84606] wm_on_create_called: [Token=264098726, ...MainActivity, performCreate]
- trace_starter 输出 note="WindowManager restored existing task — no caller, triggered by user/system switch"
- 阴性证据：14:08:29 ~ 14:08:52 区间无 am_proc_start / 无 START event 针对 mediacontrol

## 排查建议
<下一步建议：需要补充什么日志、重现步骤、或调用哪个专项 SKILL；如已得出结论无需后续，注明"无需后续"或写"修复方向">
例：
- 修复方向：在 user 12 启动配置中 disable mediacontrol task restore；或在 mediacontrol onCreate 检测 user_id 变化主动 finish
- 如需进一步确认 WindowManager 决策路径，需补充 system_server 的 wm_restore_task / Recents 相关日志

## 附：完整推理过程
见 `.logcat-analysis/analysis_log.md`（包含已排除方向、未确认假设、待查问题等过程信息）。
```

---

## 附录：tag 速查与 AAOS 特殊说明

> 见 `${CLAUDE_SKILL_DIR}/references/tag-cheatsheet.md`：包含 14 个分类的 system_server tag 速查表（Activity / ANR / 内存 / HAL / 渲染 / 音频 / AAOS 整车 / 启动链 等）+ 4 节 AAOS 特殊说明（task restore 无 START、用户进程共存、切换后批量死亡是正常清理、user switch 标准事件链）。
>
> **按需 Read，不要默认载入**——只在 `query_by_time --tags <X>` 选取过滤词、或遇到 user switch / 多用户场景需要确认是否预期行为时才查阅。
