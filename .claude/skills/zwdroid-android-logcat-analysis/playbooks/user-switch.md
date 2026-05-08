# AAOS 用户切换问题分析 playbook

## 适用场景

- 用户切换（user switch）后出现"莫名弹窗"、"音乐/导航小窗自动启动"等现象
- 用户切换后某 App 异常出现在前台
- 用户切换后系统出现 ANR / crash / 进程大量死亡
- 想了解"切换过程中系统到底做了什么"

如果不确定问题是否与用户切换相关，先用 `overview.md` 通用流程扫一遍信号，确认有 `user_switch` 信号后再回到本 playbook。

---

## 分析步骤

### 步骤 1：确认 user switch 事件链

确认切换是否完整，以及在哪个节点结束或卡住。

```bash
# 查 user switch 信号（info 级别，了解切换时间点）
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py \
    --start "<event_time前5分钟>" --end "<event_time后5分钟>" \
    --tags "car_user_mgr_switch_user_req,car_user_svc_switch_user_req,car_helper_user_switching,car_helper_user_unlocked,CarUserService"

# 从 events.jsonl 确认 uc_dispatch_user_switch 和 ssm_user_unlocked
grep '"tag": "uc_dispatch_user_switch"\|"tag": "ssm_user_unlocked"' .logcat-analysis/events.jsonl
```

**标准完整链**（按顺序）：
1. `car_user_mgr_switch_user_req` — 用户层请求
2. `car_user_svc_switch_user_req` — 服务层接收
3. `car_helper_user_switching` — CarServiceHelper 开始
4. `uc_dispatch_user_switch` — UserController 下发（events.jsonl，含 oldUserId/newUserId）
5. `car_helper_user_unlocking` — 新用户 unlocking
6. `car_helper_user_unlocked` — 新用户解锁完成
7. `ssm_user_unlocked` — SystemServiceManager 通知（events.jsonl）

若链条中断，在中断点附近查 E/W 级日志定位卡点。

---

### 步骤 2：检查 task_auto_restored 信号

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/detect_signals.py --work-dir .logcat-analysis/
# 关注 stdout 中 task_auto_restored 的命中数
```

若 `task_auto_restored > 0`，读 `signals.json` 中该规则的 captures：
```bash
python3 -c "
import json
sigs = json.load(open('.logcat-analysis/signals.json'))
for s in sigs:
    if s['rule_id'] == 'task_auto_restored':
        print(json.dumps(s, ensure_ascii=False, indent=2))
"
```

`captures.component` 即为被 WM 恢复的 Activity。这是**预期行为**——WindowManager 在用户解锁后自动把上次该用户离开时的前台 task 移回前台。不需要找"谁启动了它"，因为没有任何进程主动 start。

---

### 步骤 3：用 trace_starter 确认各进程启动来源

对于现象中提到的"某 App 出现在前台"，先区分是 task restore 还是真正的 startActivity：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/trace_starter.py \
    --package <目标package> \
    --time "<现象发生时间>" \
    --window 120 \
    --work-dir .logcat-analysis/
```

**解读输出**（统一 KV 格式：每行 `kind=... ts=... caller_pkg=... caller_uid=... caller_pid=...`）：

| 输出 | 含义 | 结论 |
|---|---|---|
| `kind=new_start ... caller_pkg=system_server/...` | system_server 主动 startActivity | 找 `caller_pid` 对应的服务（通常是 CarLauncher 或 CarMediaService）|
| `kind=new_start ... caller_uid=1000 caller_pkg=unknown` | 某系统服务触发，但 pkg 不明 | 用 `query_by_pid --pid <caller_pid>` 确认是哪个服务 |
| `kind=task_restore ... caller_pkg=N/A` | WM 静默恢复，无 caller | 属于 user switch 后的预期行为，参见步骤 2 |
| 无输出 | 时间窗口内无相关 START 和 task restore | 扩大 `--window` 到 300，或确认 package 名是否准确 |

---

### 步骤 4：区分"task restore"vs"startActivity"

这是 AAOS user switch 分析中最容易误判的地方：

**task restore（WM 恢复）**：
- `trace_starter` 输出 `kind=task_restore`，`caller_pkg=N/A`
- signals 中有 `task_auto_restored`，captures.after_user_switch_ts 与 user switch 时间吻合
- events.jsonl 中有 `wm_on_create_called`，但无 `wm_create_activity`（无新建 task）
- **结论**：这是 Android 系统设计行为，不是 bug；如果用户"不想要这个 App 自动出现"，需要在应用或 CarLauncher 层处理，而非框架层

**startActivity（主动启动）**：
- `trace_starter` 输出 `kind=new_start`，有明确 `caller_uid`/`caller_pid`
- events.jsonl 中有 `wm_create_activity`（新 task 或新 Activity）
- **结论**：找 caller 的业务逻辑，确认是否有 user switch 监听器触发了该启动

**两者同时出现**：
- `trace_starter` 同时输出 `kind=new_start` 和 `kind=task_restore`
- 可能是同一 App 被 WM 恢复后又被某服务 restart，两次出现
- 分别处理，先确认 task restore 是否正常，再追 startActivity 来源

---

### 步骤 5：进程死亡批量扫描（切换后异常多时）

user switch 后若出现大量 proc_died_foreground / lowmem_kill：

```bash
# 确认是切换清理还是真内存问题
python3 -c "
import json
sigs = json.load(open('.logcat-analysis/signals.json'))
switch_ts = [s['ts'] for s in sigs if s['rule_id']=='user_switch']
died = [s for s in sigs if s['rule_id'] in ('proc_died_foreground','lowmem_kill')]
print(f'user_switch at: {switch_ts}')
print(f'died/killed signals: {len(died)}')
for d in died[:10]:
    print(json.dumps(d, ensure_ascii=False))
"
```

**判断准则**：
- `proc_died_foreground` 的 pid 在 `stop_user_pids` 中（am_kill reason 含 `stop user`）→ 正常清理，已由 detect_signals 自动过滤
- `lowmem_kill` 集中在切换后 30 秒内，之后恢复正常 → 正常
- `low_memory_event` 在切换完成后持续出现（>5 分钟）→ 新用户内存真的紧张，需深查

---

### 步骤 6：输出报告

报告结构与 overview.md 相同，但在"时间线"中必须明确标注 user switch 节点：

```
### 时间线
- HH:MM:SS  user_switch  oldUserId=0 → newUserId=10  (car_user_mgr_switch_user_req)
- HH:MM:SS  car_helper_user_unlocked
- HH:MM:SS  ssm_user_unlocked
- HH:MM:SS  task_auto_restored  component=com.example/.MainActivity  (WM 恢复，正常)
- HH:MM:SS  kind=new_start target_pkg=com.example.music caller_pkg=system_server/CarMediaService  ← 关注点
```

---

## 常见误判

### 误判 1："没有 START 日志就是来源不明"

`trace_starter` 找不到 `kind=new_start` 时，不要直接写"来源不明"。先检查是否有 `kind=task_restore`——WM 恢复不产生 START 日志，这是已知行为。

### 误判 2："切换后大量进程死亡 = 内存问题"

见步骤 5。切换触发的进程清理是预期行为，am_kill reason 含 `stop user` 的一批死亡是正常切换流程的一部分。

### 误判 3："`proc_died_foreground` = 一定是前台进程异常崩溃"

切换前某用户的前台进程（oom_adj=0）在切换过程中被 stop，会触发 `proc_died_foreground`。detect_signals 的 `stop_user_pids` 机制已过滤掉这批（am_kill 先于 am_proc_died 出现），但如果 am_kill 日志丢失，仍可能误报。检查方法：查该 pid 在 events.jsonl 中是否有对应的 am_kill 且 reason 含 `stop user`。

### 误判 4："wm_on_create_called = App 被重新启动"

`wm_on_create_called` 是 Activity.onCreate() 回调，task restore 时同样会触发（新 Activity 实例被创建以恢复 task 状态）。不能仅凭此断定是"主动启动"——要结合 `wm_create_activity`（有则新建，无则恢复）。

---

## 快速参考：user switch 相关 tag

| Tag | 位置 | 含义 |
|---|---|---|
| `car_user_mgr_switch_user_req` | index.jsonl | 用户层切换请求 |
| `car_user_svc_switch_user_req` | index.jsonl | 服务层切换请求 |
| `car_helper_user_switching` | index.jsonl | CarServiceHelper 切换开始 |
| `car_helper_user_unlocking` | index.jsonl | 新用户 unlocking |
| `car_helper_user_unlocked` | index.jsonl | 新用户切换完成 |
| `uc_dispatch_user_switch` | events.jsonl | UserController 下发切换（含 oldUserId/newUserId）|
| `ssm_user_unlocked` | events.jsonl | SSM 通知 unlocked（含 userId）|
| `wm_on_create_called` | events.jsonl | Activity.onCreate()（task restore 也会触发）|
| `wm_create_activity` | events.jsonl | 新 Activity 被系统创建（主动 start 才有）|
| `wm_task_to_front` | events.jsonl | Task 被移到前台（task restore 路径）|

> AAOS 整车 / 多用户场景的"误判陷阱"详解（如 task restore 无 START、proc_died_foreground 误报、批量死亡是正常清理）见 `${CLAUDE_SKILL_DIR}/references/tag-cheatsheet.md` 的"AAOS 特殊说明"章节。
