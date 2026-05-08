# 崩溃问题分析 playbook

## 适用场景

`signals_summary.md` 中以下信号命中：
- `fatal_exception`（high）— Java/Kotlin 层 JVM 崩溃（`AndroidRuntime: FATAL EXCEPTION`）
- `tombstone_written`（high）— Native 层 crash（`DEBUG` tag 写入 tombstone）

如果两类信号同时命中、且 PID 重合，看常见误判 1。

---

## 分析步骤

### 1. 从 signals_summary.md 锚定 crash

```bash
cat .logcat-analysis/signals_summary.md
```

提取每条 crash 信号的：
- `ts`：crash 时间点
- `source_file:line_no`：原始日志位置
- 通过 `query_by_pid` 反查 PID，或用 grep 在 events.jsonl 找对应进程：
  ```bash
  grep '"ts": "<crash_ts>"' .logcat-analysis/events.jsonl | head -5
  ```

把"crash 进程 / 时间 / source 行号"Edit 到 `analysis_log.md` 的"已确认的事实"段。

---

### 2. fatal_exception 路径（Java 崩溃）

#### 2.1 用 query_by_pid 拿 FATAL EXCEPTION 行号

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_pid.py \
    --pid <PID> --keyword "FATAL EXCEPTION" --max-lines 5
```

输出包含 `[<source_file> L<line_no>]` 前缀，记下 line_no。

#### 2.2 必须 Read 原始日志取多行 stack trace

⚠️ **不能用 query 脚本拿完整 stack**——`parse_logcat.py` 把每行单独索引，多行 Java stack trace 不合并。必须直接 Read 原始文件：

```
Read tool: file_path=<log_dir>/<source_file>, offset=<line_no - 2>, limit=80
```

读取 FATAL EXCEPTION 行往后 60–80 行（根据 stack 深度调整），覆盖：
- 异常类（如 `java.lang.NullPointerException`）
- Caused by 链（如有）
- 完整调用栈

#### 2.3 判断异常类型

| 异常类 | 通常根因 | 优先查 |
|---|---|---|
| `NullPointerException` | 对象未初始化 / 异步回调时已被回收 | 看 stack 顶部的 caller，判断是否生命周期相关 |
| `OutOfMemoryError` | 真内存不足 / 大 bitmap / 内存泄漏 | 同时间段 `low_memory_event`、`am_proc_start` 看进程数 |
| `IllegalStateException` | 状态机违反约定（如 Activity 已 destroyed 后操作）| 查该进程 Activity 生命周期事件 |
| `SecurityException` | 权限不足 / SELinux 拒绝 | 同时间段 `selinux_denial` 信号 |
| `RemoteException` / `DeadObjectException` | binder 远端进程已挂 | 查远端进程是否有 `proc_died_*` 信号 |
| `RuntimeException: Unable to start activity` | Activity onCreate 内部抛异常 | 看 Caused by 链定位真正的原始异常 |

---

### 3. tombstone_written 路径（Native 崩溃）

#### 3.1 查同时间段 DEBUG tag

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/query_by_time.py \
    --start "<crash_ts - 5s>" --end "<crash_ts + 30s>" \
    --tags "DEBUG" --max-lines 100
```

logcat 中的 DEBUG tag 通常包含：
- 信号类型（SIGSEGV / SIGABRT / SIGILL）
- 进程名 / PID / TID
- 简化的 backtrace（前几帧）

#### 3.2 完整 backtrace 在 logcat 之外

⚠️ **本 SKILL 不解析 tombstone**。完整 backtrace + 寄存器 + memory map 在 `/data/tombstones/tombstone_NN`，需要额外 SKILL 或 ndk-stack 工具处理。

在 `report.md` 的"排查建议"段写：**需补充 `/data/tombstones/tombstone_NN` 才能定位 native 崩溃帧**。

#### 3.3 logcat 内能拿到的信息

- 信号类型（SEGV → 内存非法访问，ABRT → assert / fortify_check 主动退出）
- 崩溃前 30 秒该进程的最后活动（`query_by_pid <PID>`）—— 提供间接线索
- 是否有 `hal_died` / `binder_died` 同时段命中（HAL 进程崩溃常伴随 native crash）

---

### 4. 输出报告

报告模板见 `${CLAUDE_SKILL_DIR}/playbooks/overview.md` 的"报告模板"段。

崩溃类报告的"结论"段要求：
- 异常类（fatal_exception）或信号类型（tombstone）
- 抛出点（具体 class.method，从 stack 顶部取）
- 置信度：高/中/低

> 通用流程（扩展文件集 / 主动扫描未知区域）见 overview.md 第 5/6 节。

---

## 常见误判

### 误判 1：同进程多次 fatal_exception 都是同一根因

应用进程崩溃后被系统重启，启动条件没变可能立即再次崩溃。同 process_name 的多次 `fatal_exception`：

- **5 分钟内多次崩溃** + 异常类相同 → 同一根因，分析最早一次即可
- **崩溃间隔 > 5 分钟** 或异常类不同 → 可能是级联崩溃（A 进程崩溃 → B 进程依赖 A 也崩溃），逐个分析

判断方法：用 `signals_summary.md` 看时间间隔 + 异常 message 是否一致。

### 误判 2：tombstone 时间点附近有 fatal_exception

如果 native crash 和 java fatal exception 在同一进程同一时间点附近出现：

- 通常 native 崩溃**先发生**（毫秒级），Java 层捕获到 native 异常后包装成 Java exception 抛出
- 优先分析 native 端（步骤 3），Java 端的 stack 只是"现场转录"

### 误判 3：fatal_exception 信号有但没有 stack trace

`parse_logcat.py` 把多行 stack 每行单独索引，所以 `query_by_pid --keyword "FATAL EXCEPTION"` 只返回那一行。**不要因为"看不到 stack"就以为日志缺失**——按步骤 2.2 直接 Read 原始文件即可。
