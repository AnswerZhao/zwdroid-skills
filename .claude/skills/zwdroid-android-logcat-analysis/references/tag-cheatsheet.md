# tag 速查与 AAOS 特殊说明

> 这是按需 Read 的参考文件。在 `query_by_time --tags <tag>` 选取过滤词时查阅，或遇到 user switch / 多用户场景需要确认是否为预期行为时查阅。**不必默认载入**。

## system_server 重点 tag 速查

用于 `--tags` 参数过滤，按场景选取。

### Activity / 任务管理

| Tag | 适用场景 |
|---|---|
| `ActivityManager` | ANR、进程管理、Activity 强制结束（Force finishing）|
| `ActivityTaskManager` | Android 10+ Task/Activity 调度（start/stop/pause/resume）|
| `WindowManager` | 窗口冻结超时、窗口添加/移除 |

### ANR 根因

| Tag | 适用场景 |
|---|---|
| `InputDispatcher` | Input dispatch 超时（最常见 ANR 根因）、触摸/按键无响应 |
| `InputReader` | 输入事件读取异常、设备节点问题 |
| `Binder` | Binder 线程耗尽、跨进程调用超时 |
| `SQLiteDatabase` | 数据库锁竞争（主线程等待 DB 锁也会触发 ANR）|
| `ContentResolver` | ContentProvider 调用死锁、跨进程 query 超时 |

### 进程 / 内存

| Tag | 适用场景 |
|---|---|
| `ActivityManager` | am_proc_died、am_kill（OOM 杀进程）|
| `lowmemorykiller` | LMK 内核杀进程记录（需 kernel 日志，通常不在 logcat）|
| `Zygote` | 进程 fork（`Start proc`）、进程退出 |

### 系统安全 / 权限

| Tag | 适用场景 |
|---|---|
| `auditd` | SELinux avc 拒绝记录（`selinux_denial` signal 来源）|
| `SELinux` | SELinux 策略加载、权限检查日志 |

> `selinux_denial` signal 已自动检测 `avc: denied`，但 captures.msg 中的"scontext/tcontext/tclass"需人工解读，确认是真正的权限缺失还是预期的沙箱限制。

### HAL / 驱动

| Tag | 适用场景 |
|---|---|
| `hwservicemanager` | HAL 服务注册/查询失败（`hal_died` signal 来源）|
| `HwBinder` | HAL Binder 事务超时、线程耗尽 |
| `android.hardware.*` | 具体 HAL 实现层日志（如 `android.hardware.audio@6.0`）|

### 崩溃 / Watchdog

| Tag | 适用场景 |
|---|---|
| `AndroidRuntime` | JVM FATAL EXCEPTION（Java 崩溃堆栈）|
| `DEBUG` | Native tombstone |
| `art` / `dalvikvm` | GC pause、内存分配失败（OOM 前兆）|
| `Watchdog` | 系统 watchdog 超时（仅限此精确 tag，非 ClusterWatchdogPolicy）|
| `CarWatchdogService` | AAOS 应用级 watchdog——监控注册 app 的心跳，无响应时主动杀进程 |

> `Watchdog`（系统）vs `CarWatchdogService`（应用）：前者监控 system_server 内部线程，后者监控 AAOS 注册的 app 进程，触发场景和处置方式不同。

### 渲染 / 性能

| Tag | 适用场景 |
|---|---|
| `Choreographer` | 掉帧（Skipped N frames）|
| `SurfaceFlinger` | 合成超时、layer 变化 |
| `RenderThread` | GPU 渲染耗时 |
| `ActivityThread` | `Displayed` 启动首帧时间（如 `Displayed com.x/.MainActivity: +1s234ms`）|
| `ThermalService` | CPU/GPU 热降频（掉帧根因之一）|
| `DisplayManagerService` | 多屏管理（仪表盘+中控）、屏幕添加/切换 |
| `DisplayContent` | WindowManager 多屏内容布局异常 |

### 音频（AAOS 高频）

| Tag | 适用场景 |
|---|---|
| `AudioFlinger` | 音频线程异常、underrun、设备打开失败 |
| `AudioPolicyManager` | 音频路由决策、焦点仲裁 |
| `CarAudioService` | 车机音频分区、音量组、焦点请求 |
| `CarMediaService` | 媒体源切换（radio/BT/USB，不同于 MediaSessionService）|
| `MediaSessionService` | MediaSession 活跃状态变化（触发媒体控制 UI）|

### AAOS 电源 / 整车

| Tag | 适用场景 |
|---|---|
| `CarPowerManagementService` | ACC on/off、进入 suspend、唤醒流程 |
| `VehicleHAL` | HAL 通信异常、车辆属性读写失败 |
| `PowerManagerService` | 系统侧电源状态决策、屏幕亮灭 |
| `PowerManager` | app 进程 wakelock 获取/释放（追 wakelock 持有者时用）|

### AAOS 用户 / 多用户

| Tag | 适用场景 |
|---|---|
| `CarUserService` | 用户切换服务端日志 |
| `car_helper_user_switching` | 用户切换触发（index.jsonl 文本 tag）|
| `SystemUI` | 媒体控制条、通知栏、状态栏逻辑 |
| `CarService` | 车载服务启动/异常（通用入口）|

### App 调试（debug 包专属）

| Tag | 适用场景 |
|---|---|
| `StrictMode` | 主线程 I/O、网络操作、泄漏检测（仅 debug/userdebug 包输出）|
| `SQLiteDatabase` | 数据库锁竞争（ANR 路径之一）、慢查询 |
| `ContentResolver` | ContentProvider 调用失败、跨进程数据访问异常 |
| `JobScheduler` | 后台 Job 调度、WorkManager 底层触发 |

### 通知 / 后台调度

| Tag | 适用场景 |
|---|---|
| `NotificationManager` | 通知发送/取消，AAOS HUN（抬头通知）触发 |
| `AlarmManager` | 定时任务触发（追查 app 被定时唤醒的来源）|

### 启动链追溯（L3/L4/L5 扩大范围时）

| Tag | 适用场景 |
|---|---|
| `SystemServer` | 系统服务启动顺序 |
| `SystemServiceManager` | 各服务 onStart/onBootPhase 记录，定位服务启动失败 |
| `ServiceManager` | 服务注册失败、binder context 初始化 |
| `PackageManagerService` | 系统侧包扫描、权限授予 |
| `PackageManager` | app 进程包查询、权限检查（客户端侧）|
| `am_proc_start` | 进程启动事件（events.jsonl，含 caller）|
| `wm_create_activity` | Activity 创建事件（events.jsonl，含 callingPackage）|

---

## AAOS 特殊说明

以下行为在标准 Android 中不常见，但在 AAOS 多用户/车机场景中是已验证的正常路径。**遇到时不要误判为异常。**

### 1. Task restore 无 START 日志

用户切换（user switch）完成后，WindowManager 会静默恢复切换前该用户的前台 task（task moved to front），此时：
- **没有** `ActivityTaskManager: START` 日志
- **没有** caller uid/pid
- 只有 `wm_on_create_called` / `wm_task_to_front` 等 WM 生命周期事件

**误判陷阱**：agent 会误认为"没有 START 日志 = 没有触发者 = 来源不明"。  
**正确判断方法**：`task_auto_restored` 信号已标注此场景；`trace_starter.py` 会输出 `kind=task_restore` 行而非 `kind=new_start`。

### 2. 用户进程共存

AAOS 支持多个用户同时存在（User 0 系统用户 + User 10 驾驶员 + User 11 乘客等）。日志中会同时出现属于不同用户的进程。注意：
- `am_proc_died` 中 `oom_adj < 0` 通常是 User 0 系统持久化进程（正常，不告警）
- `stop user X due to finish user` 触发的 `am_kill` 是用户切换的正常清理，不是内存压力
- `proc_died_foreground` 信号中的 PID 若来自被"finish"的用户，属于预期行为

**过滤方法**：检查 `signals.json` 中 `proc_died_foreground` 的 captures，若其 PID 对应 `am_kill` 中 reason 含 `stop user`，该信号可降级为背景噪音（detect_signals 已自动过滤，`stop_user_pids` 机制）。

### 3. 切换后的批量进程死亡是正常清理

user switch 后 10–30 秒内通常会出现：
- 大量 `proc_died_background`（被切换掉的用户进程）
- 若干 `lowmem_kill`（系统为新用户腾空间）

**不要把这批信号当作内存问题**，除非 `low_memory_event` 在切换完成后还持续出现（说明新用户进程启动后内存仍然紧张）。

### 4. AAOS user switch 的标准事件链

完整的切换流程依次触发以下 tag（index.jsonl 文本 tag + events.jsonl 事件 tag）：

```
car_user_mgr_switch_user_req    ← 用户层请求切换
car_user_svc_switch_user_req    ← 服务层接收请求
car_helper_user_switching       ← CarServiceHelper 开始切换
uc_dispatch_user_switch         ← UserController 下发切换（events.jsonl）
car_helper_user_unlocking       ← 新用户 unlocking 阶段
car_helper_user_unlocked        ← 新用户完全解锁
ssm_user_unlocked               ← SystemServiceManager 通知 unlocked（events.jsonl）
```

若某个节点缺失，说明切换在该阶段卡住或失败，可从该 tag 附近的 E 级日志定位根因。
