# ANR 问题分析 playbook

## 适用场景

`signals_summary.md` 中以下信号命中：
- `anr_in_process`（high）— ActivityManager 报告 ANR
- `input_anr`（high）— InputDispatcher 因输入超时触发 ANR
- `watchdog`（high）— 系统级 Watchdog 超时（system_server 内部线程卡住）

如果只命中 `watchdog` 而无 `anr_in_process`，先看常见误判 1。

---

## 标准事件链

完整 ANR 流程依次产生以下日志：

```
1. 触发：InputDispatcher 检测输入超时 / Service onCreate 超 20 秒 / Broadcast 超 10 秒 等
2. ANR 标记：ActivityManager E "ANR in <process_name>"
3. 进程 dump：am_anr 事件（events.jsonl）
4. traces 写入：/data/anr/traces.txt（不在 logcat 中，需单独获取）
```

logcat 能看到 1–3，但**完整 stack 在 traces.txt**——本 SKILL 不解析。

---

## 分析步骤

### 1. 从 signals_summary.md 锚定 ANR

```bash
cat .logcat-analysis/signals_summary.md
```

提取每条 ANR 信号的：
- `ts`：ANR 时间点
- `captures.process`：报告进程（`anr_in_process`）；`input_anr` 没有 process 字段，从 source line 反查
- `source_file:line_no`：原始日志位置

把这批信息 Edit 到 `analysis_log.md` 的"已确认的事实"段。

### 2. 看 ANR 前 30 秒的关键 tag

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py \
    --start "<ANR_ts - 30s>" --end "<ANR_ts>" \
    --tags "InputDispatcher,Binder,SQLiteDatabase,ContentResolver" \
    --level "E,W" --max-lines 200
```

按出现优先级解读：
- `InputDispatcher` 报"Application is not responding" → input timeout 路径
- `Binder` 报"binder thread pool ... blocked" → binder 线程耗尽
- `SQLiteDatabase` 报"slow query" / "lock held N ms" → DB 锁竞争
- `ContentResolver` 调用超时 → cross-process 死锁

### 3. 看进程 ANR 前的最后活动

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_pid.py \
    --pid <进程PID> --max-lines 200
```

找进程在 ANR 前 30 秒做了什么：
- 主线程在等什么（IO / DB / Binder / Lock）
- 是否有 GC pause（`art` tag 报 `Pause N ms`）
- 是否有大量 Choreographer "Skipped frames"

> ⚠️ 主线程 stack 不在 logcat 中——只能从行为模式（哪类 tag 高频出现）反推。

### 4. 判断 ANR 类型并定方向

| 类型 | logcat 特征 | 排查方向 |
|---|---|---|
| input timeout | `InputDispatcher: ANR in ...` + `Reason: Waited NN ms ...` | 主线程被阻塞，看步骤 3 的等待对象 |
| service ANR | `ANR in ...` + `Reason: executing service` | 该 Service 的 onStart/onCreate 卡住 |
| broadcast ANR | `ANR in ...` + `Reason: Broadcast of Intent ...` | 广播接收器主线程任务过重 |
| contentprovider ANR | `ANR in ...` + `Reason: ContentProvider ...` | 跨进程 query 死锁，查 caller 端 binder |
| 系统 Watchdog | `Watchdog: !@Sync ...` 或 `Watchdog killing system_server` | system_server 内部线程卡住，查 dump 行附近的 native crash 或 binder 异常 |

### 5. 升级到 traces.txt 的判定

如果 logcat 里看不到主线程在等什么、且步骤 2/3 都无明显信号，应该升级：

- 在 `report.md` 的"排查建议"段写明：**需补充 `/data/anr/traces.txt` 才能确定主线程 stack**
- 不要在 logcat 里继续无方向的 query 循环

### 6. 输出报告

报告模板见 `${CLAUDE_SKILL_DIR}/playbooks/overview.md` 的"报告模板"段。结论段必须明确写出 ANR 类型（步骤 4 表格中选一个）+ 置信度。

> 通用流程（扩展文件集 / 主动扫描未知区域）见 overview.md 第 5/6 节。

---

## 常见误判

### 误判 1：watchdog 命中 ≠ 一定是 ANR

`watchdog` 信号是 system_server 内部线程超时，不是应用 ANR。常见触发：
- 启动早期（开机 30 秒内）的"启动期抖动"——多数情况下不是问题
- HAL 卡死间接拖慢 system_server 线程

**正确判断**：watchdog 命中后，看是否同时有 `anr_in_process` 或 `hal_died`。只有 watchdog 单独出现且不在开机段时，才需要深查。

### 误判 2：stop user 触发的"伪 ANR"

AAOS user switch 期间，被切换掉的用户进程可能因 binder 调用未完成而被报告为 ANR。但这通常是切换流程的副作用，不是真正的应用问题。

**正确判断**：检查 ANR 进程的 PID 是否在 `signals.json` 中 `proc_died_foreground` 的 captures 内、且对应 am_kill reason 含 `stop user`。若是，该 ANR 可降级为切换噪音。

### 误判 3：input_anr 命中但用户实际无操作

`input_anr` 可能由系统侧合成的 input event 触发（如 wake key、某些自动化测试注入）。如果用户报告"我什么都没点"，先确认 InputDispatcher 日志里 input event 来源。
