"""
managers/scheduler —— s14 cron / scheduled tasks 融合实现。

对应源 learn-claude-code-main/agents/s14_cron_scheduler.py。

核心思想：
    agent 调用 cron_create 登记"将来某时要跑一次这段提示"，调度器用后台
    线程按分钟检查；到点把提示字符串投到通知队列，agent_loop 每轮开头
    drain 一次，注入成 <scheduled-tasks> user 消息，让 LLM 自然"醒来"处理。

本模块与 BackgroundManager（managers/background.py）架构同构但职责正交：
    * BG  —— fire-and-forget 的 shell command，完成时通知
    * CRON —— 基于时间的 prompt 唤起，无命令执行动作
两者各自持有 Queue，由 agent_loop 分别 drain。

与 s14 原版的取舍（mini 版）：
    * 保留 5 字段 cron 解析、两种持久化、两种触发模式、jitter、漏触发检测
    * 保留 CronLock（PID 文件跨进程锁，1B 拍板）
    * 不引入额外 hook 事件——cron 的 "fire" 只投通知队列，透明于 HookManager

线程安全：
    * self.tasks 列表的增删由后台线程和主线程并发访问；用 self._lock
      守护。Queue 自身线程安全，无需额外加锁。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from ..core.config import WORKDIR


# cron 事件 listener 签名：回调接收一个事件 dict（type/id/cron/prompt/... 见 _emit）
# 用于 webui 等外部订阅方；零监听者时等价无改动
CronEventListener = Callable[[dict], None]


# ---------------------------------------------------------------------------
# 异步安全输出
# ---------------------------------------------------------------------------

def _async_print(msg: str) -> None:
    """
    从后台线程向终端打印，不撞乱 REPL 的 input() 提示符。

    采用延迟 import 的方式引用 cli.safe_output，原因有二：
        1) managers/ 层按分层原则不应静态依赖 cli/；延迟 import 保持
           managers 可以脱离 cli 单独使用（比如单元测试）
        2) 导入失败时自然退化为普通 print —— 不影响逻辑正确性，只是
           TUI 上稍微乱一点（和加 safe_output 之前一样）
    """
    try:
        from ..cli.safe_output import safe_print
        safe_print(msg)
    except Exception:
        print(msg)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 持久化任务文件（与 s14 原版路径一致）
SCHEDULED_TASKS_FILE: Path = WORKDIR / ".claude" / "scheduled_tasks.json"

#: 跨进程锁文件
CRON_LOCK_FILE: Path = WORKDIR / ".claude" / "cron.lock"

#: 重复任务的自动过期天数（避免"遗忘的 cron"永远跑下去）
AUTO_EXPIRY_DAYS: int = 7

#: 抖动：cron 目标分钟落在这些"整点"时，给一个 1-4 分钟的确定性偏移
#: （防止整点雪崩；教学语义保留）
JITTER_MINUTES: tuple[int, ...] = (0, 30)
JITTER_OFFSET_MAX: int = 4

#: 漏触发回看最长窗口（小时）
MISSED_LOOKBACK_HOURS: int = 24


# ---------------------------------------------------------------------------
# CronLock —— 跨进程 PID 文件锁
# ---------------------------------------------------------------------------

class CronLock:
    """
    基于 PID 文件的跨进程锁，防止多个 REPL 同时触发同一批 durable cron。

    经典实现思路：
        1. 锁文件存在 → 读 PID → os.kill(pid, 0) 探活
           * 存活 → 锁被占用，返回 False
           * 已死 → 视作陈旧锁，接管
        2. 锁文件不存在或已陈旧 → 写入自己的 PID，返回 True

    `os.kill(pid, 0)` 是经典的"进程存活探针"——信号 0 不会真的发送任何
    信号给目标进程，只是让内核走一遍权限/存在性检查；目标不存在会抛
    ProcessLookupError，无权限访问会抛 PermissionError（说明进程存在
    但不是自己的）。

    mini 版对 s14 的唯一调整：锁文件的 parent.mkdir 时已有 exist_ok=True，
    保证首次使用即使 .claude/ 不存在也能无副作用创建。
    """

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path: Path = lock_path or CRON_LOCK_FILE

    def acquire(self) -> bool:
        """尝试获取锁。True=拿到；False=已被活进程持有。"""
        if self._lock_path.exists():
            try:
                stored_pid = int(self._lock_path.read_text().strip())
                # signal 0：权限+存在性检查；不发送信号
                os.kill(stored_pid, 0)
                # 对方还活着 —— 锁被占用
                return False
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                # 锁已陈旧（进程死了 / PID 文件坏了）—— 接管
                pass

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(str(os.getpid()))
        return True

    def release(self) -> None:
        """释放锁：仅当锁文件里的 PID 确实是自己时才删。"""
        try:
            if self._lock_path.exists():
                stored_pid = int(self._lock_path.read_text().strip())
                if stored_pid == os.getpid():
                    self._lock_path.unlink()
        except (ValueError, OSError):
            # 竞态下文件被别人清掉 / 权限错误 —— 忽略，保证 stop() 幂等
            pass


# ---------------------------------------------------------------------------
# cron_matches —— 5 字段解析器（纯手写、零依赖）
# ---------------------------------------------------------------------------

def cron_matches(expr: str, dt: datetime) -> bool:
    """
    检查 5 字段 cron 表达式是否匹配给定 datetime。

    字段顺序：minute hour day-of-month month day-of-week
    支持：
        *       任意
        N       精确值
        N-M     范围
        N,M     枚举
        */N     步长
        N-M/S   范围带步长

    关键细节：Python weekday() 返回 0=周一，cron 惯例 0=周日，这里做换算。
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        return False

    # Python weekday: 0=Mon...6=Sun → cron dow: 0=Sun...6=Sat
    cron_dow = (dt.weekday() + 1) % 7
    values = [dt.minute, dt.hour, dt.day, dt.month, cron_dow]
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]

    for field, value, (lo, hi) in zip(fields, values, ranges):
        if not _field_matches(field, value, lo, hi):
            return False
    return True


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    """匹配单个 cron 字段；任意非法输入都返回 False（教学版不抛异常）。"""
    if field == "*":
        return True

    try:
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, step_str = part.split("/", 1)
                step = int(step_str)

            if part == "*":
                if (value - lo) % step == 0 and lo <= value <= hi:
                    return True
            elif "-" in part:
                start_s, end_s = part.split("-", 1)
                start, end = int(start_s), int(end_s)
                if start <= value <= end and (value - start) % step == 0:
                    return True
            else:
                if int(part) == value:
                    return True
    except ValueError:
        return False

    return False


# ---------------------------------------------------------------------------
# CronScheduler
# ---------------------------------------------------------------------------

class CronScheduler:
    """
    用后台线程 + 分钟切换去重 + Queue 的极简调度器。

    生命周期：
        start()  —— 载入 durable 任务、启动后台线程，返回 missed 列表
        stop()   —— 优雅停线程、释放锁
        drain_notifications() —— agent_loop 每轮开头调用

    任务字典结构：
        {
            "id":          "a1b2c3d4",          # uuid4[:8]
            "cron":        "*/5 * * * *",
            "prompt":      "...",                # fire 时注入给 LLM 的内容
            "recurring":   True,
            "durable":     False,
            "createdAt":   1234567890.0,
            "last_fired":  Optional[float],      # 最后一次 fire 的时间戳
            "jitter_offset": 0,                  # 整点抖动分钟数
        }
    """

    def __init__(self, workdir: Path | None = None) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.queue: Queue = Queue()
        self._stop_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        #: 同一分钟内只检查一次（避免 1 秒睡眠误差带来的双触发）
        self._last_check_minute: int = -1
        #: 主线程 /cron list + 后台 _check_tasks 可能并发改 self.tasks
        self._lock: threading.Lock = threading.Lock()
        #: 跨进程锁
        self._cron_lock: CronLock = CronLock()
        self._has_lock: bool = False

        #: 事件监听器（webui 等外部订阅方）；用独立锁避免与 tasks 锁嵌套
        self._listeners: list[CronEventListener] = []
        self._listeners_lock: threading.Lock = threading.Lock()

        # 允许注入自定义 workdir，方便单元测试用 tempdir
        if workdir is not None:
            self._tasks_file: Path = workdir / ".claude" / "scheduled_tasks.json"
            self._cron_lock = CronLock(workdir / ".claude" / "cron.lock")
        else:
            self._tasks_file = SCHEDULED_TASKS_FILE

    # ---- 事件监听器 --------------------------------------------------------

    def add_event_listener(self, cb: CronEventListener) -> None:
        """注册事件监听器（webui 等）。未注册时 fire 点零额外开销。"""
        with self._listeners_lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_event_listener(self, cb: CronEventListener) -> None:
        """注销事件监听器。未注册过的 cb 静默忽略。"""
        with self._listeners_lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    def _emit(self, event: dict) -> None:
        """
        向所有监听器 fan-out 事件。任何监听器抛异常都被吞掉，
        避免影响 cron 后台线程的可靠性（监听器的正确性不是 cron 的责任）。
        """
        # 在锁内拷贝出快照再逐个调，避免 listener 回调里再注册/注销造成并发修改
        with self._listeners_lock:
            if not self._listeners:
                return
            snapshot = list(self._listeners)
        for cb in snapshot:
            try:
                cb(event)
            except Exception:
                pass

    # ---- 生命周期 ----------------------------------------------------------

    def start(self) -> list[dict[str, Any]]:
        """
        启动调度：
            1) 尝试拿跨进程锁（拿不到 → 本进程进入"只读模式"，不触发、不持久化）
            2) 载入 durable 任务
            3) 检测漏触发
            4) 启动后台线程

        返回：missed 列表（启动时回看 24h 内漏跑的 durable 任务）。
        调用方可以把它作为 <scheduled-tasks> 注入，让 LLM 决定补不补。
        """
        self._has_lock = self._cron_lock.acquire()
        if not self._has_lock:
            print("[cron] another session already holds cron.lock; "
                  "this REPL will not fire scheduled tasks")
            return []

        self._load_durable()
        missed = self.detect_missed_tasks()

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()

        return missed

    def stop(self) -> None:
        """停止线程、释放锁。多次调用幂等。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._has_lock:
            self._cron_lock.release()
            self._has_lock = False

    # ---- CRUD --------------------------------------------------------------

    def create(self, cron: str, prompt: str,
               recurring: bool = True, durable: bool = False,
               auto_run: bool = False) -> str:
        """
        创建一个新任务。返回人读的成功消息（同时给 LLM 与用户看）。

        auto_run=True：到点时直接在后台线程执行子任务（general-purpose 模式），
                       无需用户输入触发 agent_loop，适合无人值守的定时任务。
        auto_run=False（默认）：到点时把 prompt 投入通知队列，等 agent_loop
                               drain 时注入给 LLM 处理。
        """
        # 语法检查：让 LLM 直接收到"表达式不合法"而不是默默失败
        if not _validate_cron_syntax(cron):
            return f"Error: invalid cron expression {cron!r} (expected 5 fields)"

        task_id = str(uuid.uuid4())[:8]
        task: dict[str, Any] = {
            "id": task_id,
            "cron": cron,
            "prompt": prompt,
            "recurring": bool(recurring),
            "durable": bool(durable),
            "auto_run": bool(auto_run),
            "createdAt": time.time(),
            "last_fired": None,
        }
        if recurring:
            task["jitter_offset"] = self._compute_jitter(cron)

        with self._lock:
            self.tasks.append(task)
            if durable:
                self._save_durable_locked()

        mode = "recurring" if recurring else "one-shot"
        store = "durable" if durable else "session-only"
        run_mode = "auto-run" if auto_run else "queue"
        return f"Created task {task_id} ({mode}, {store}, {run_mode}): cron={cron!r}"

    def delete(self, task_id: str) -> str:
        """按 id 删除任务。找不到 → 返回明确提示。"""
        with self._lock:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if t["id"] != task_id]
            if len(self.tasks) == before:
                return f"Task {task_id} not found"
            self._save_durable_locked()
        return f"Deleted task {task_id}"

    def list_tasks(self) -> str:
        """列出所有任务（人读格式；/cron list 与 cron_list 工具都用它）。"""
        with self._lock:
            snapshot = list(self.tasks)

        if not snapshot:
            return "No scheduled tasks."

        lines: list[str] = []
        now = time.time()
        for t in snapshot:
            mode = "recurring" if t["recurring"] else "one-shot"
            store = "durable" if t.get("durable") else "session"
            age_hours = (now - t["createdAt"]) / 3600
            preview = t["prompt"][:60] + ("…" if len(t["prompt"]) > 60 else "")
            last = "-" if t.get("last_fired") is None else \
                f"{(now - t['last_fired']) / 60:.1f}m ago"
            human = _describe_cron(t["cron"])
            lines.append(
                f"  {t['id']}  {human:<16} [{mode}/{store}] "
                f"(cron={t['cron']!r}, age={age_hours:.1f}h, last={last})\n"
                f"         → {preview}"
            )
        return "\n".join(lines)

    def clear(self, include_durable: bool = False) -> str:
        """
        清空任务。教学演示 / 重来用。
            include_durable=False → 只清 session 任务（durable 保留）
            include_durable=True  → 连 .claude/scheduled_tasks.json 一起清
        """
        with self._lock:
            before = len(self.tasks)
            if include_durable:
                self.tasks = []
            else:
                self.tasks = [t for t in self.tasks if t.get("durable")]
            removed = before - len(self.tasks)
            # 持久化同步（即使只清 session，durable 子集没变，写盘也无副作用）
            self._save_durable_locked()

        return f"Cleared {removed} task(s){' including durable' if include_durable else ''}"

    # ---- 主循环集成 --------------------------------------------------------

    def drain_notifications(self) -> list[str]:
        """
        非阻塞清空通知队列。agent_loop 每轮开头调一次。
        """
        notifs: list[str] = []
        while True:
            try:
                notifs.append(self.queue.get_nowait())
            except Empty:
                break
        return notifs

    # ---- 调试 / 教学 -------------------------------------------------------

    def fire_test(self, prompt: str = "this is a test notification") -> None:
        """手动往队列里丢一条 —— /cron test 用。不经过 cron 匹配。"""
        self.queue.put(f"[Scheduled task test-0000]: {prompt}")

    # ---- 漏触发检测 --------------------------------------------------------

    def detect_missed_tasks(self) -> list[dict[str, Any]]:
        """
        启动时回看：对每个 durable 任务检查 last_fired → now 的分钟序列里
        是否至少有一个 cron 匹配；有则视为"错过"。

        仅回看 MISSED_LOOKBACK_HOURS（24h）以控制计算量；同一任务只报一次
        （只要有一次漏即可，不必枚举全部漏点）。
        """
        now = datetime.now()
        missed: list[dict[str, Any]] = []

        with self._lock:
            snapshot = list(self.tasks)

        for task in snapshot:
            last_fired = task.get("last_fired")
            if last_fired is None:
                continue
            last_dt = datetime.fromtimestamp(last_fired)
            cap = min(now, last_dt + timedelta(hours=MISSED_LOOKBACK_HOURS))
            check = last_dt + timedelta(minutes=1)
            while check <= cap:
                if cron_matches(task["cron"], check):
                    missed.append({
                        "id": task["id"],
                        "cron": task["cron"],
                        "prompt": task["prompt"],
                        "missed_at": check.isoformat(timespec="minutes"),
                    })
                    break
                check += timedelta(minutes=1)
        return missed

    # ---- 内部 --------------------------------------------------------------

    def _compute_jitter(self, cron: str) -> int:
        """cron 的分钟字段落在整点 (:00 / :30) 时，返回 1-4 分钟的确定性偏移。"""
        fields = cron.strip().split()
        if not fields:
            return 0
        try:
            minute_val = int(fields[0])
        except ValueError:
            return 0
        if minute_val in JITTER_MINUTES:
            # hash 取模得到稳定偏移，不同 cron 表达式分散到不同分钟
            return (abs(hash(cron)) % JITTER_OFFSET_MAX) + 1
        return 0

    def _check_loop(self) -> None:
        """
        后台线程主循环：每秒醒一次；只在分钟切换时才检查任务。

        为什么 1s 粒度而不是 60s？
            stop_event.wait(1) 能让 stop() 最迟 1 秒内响应；60s 粒度会让
            REPL 退出时卡顿明显。CPU 代价可忽略（每秒一次轻量时间比较）。
        """
        while not self._stop_event.is_set():
            now = datetime.now()
            current_minute = now.hour * 60 + now.minute
            if current_minute != self._last_check_minute:
                self._last_check_minute = current_minute
                try:
                    self._check_tasks(now)
                except Exception as e:
                    # 不让调度异常杀掉后台线程 —— 任何错误打印即可
                    _async_print(f"[cron] check loop error: {e}")
            self._stop_event.wait(timeout=1)

    def _check_tasks(self, now: datetime) -> None:
        """
        按当前时间检查所有任务：到点就 enqueue；one-shot 与过期自动删除。
        """
        with self._lock:
            snapshot = list(self.tasks)

        expired: list[str] = []
        fired_oneshots: list[str] = []
        fired_any = False

        for task in snapshot:
            # 1) 自动过期：recurring 任务超过 7 天
            age_days = (time.time() - task["createdAt"]) / 86400
            if task.get("recurring") and age_days > AUTO_EXPIRY_DAYS:
                expired.append(task["id"])
                continue

            # 2) jitter 偏移：实际匹配时间 = now - jitter
            check_time = now
            jitter = int(task.get("jitter_offset", 0) or 0)
            if jitter:
                check_time = now - timedelta(minutes=jitter)

            if cron_matches(task["cron"], check_time):
                # 任务级本分钟去重：_last_check_minute 是 scheduler 级保护，
                # 但 jitter 偏移 + "* * * * *"（任意分钟）组合下，同一分钟内
                # 可能仍然触到两次匹配边界；这里用任务自己的 _fired_minute
                # 再保险一次，确保"每任务每分钟最多投一次"。
                fired_minute_key = check_time.hour * 60 + check_time.minute
                if task.get("_fired_minute") == fired_minute_key:
                    continue
                task["_fired_minute"] = fired_minute_key

                task["last_fired"] = time.time()
                fired_any = True

                # 诊断输出：任务 id + 人话频率 + prompt 预览（最多 60 字）。
                human = _describe_cron(task["cron"])
                preview = task["prompt"][:60] + ("…" if len(task["prompt"]) > 60 else "")
                mode = "recurring" if task.get("recurring") else "one-shot"
                _async_print(f"[cron] fired: {task['id']}  ({human}, {mode}) — task: {preview!r}")

                # 事件 fan-out：webui 等监听者消费（零监听者时等价无改动）
                self._emit({
                    "type": "fire",
                    "id": task["id"],
                    "cron": task["cron"],
                    "prompt": task["prompt"],
                    "recurring": bool(task.get("recurring")),
                    "durable": bool(task.get("durable")),
                    "auto_run": bool(task.get("auto_run")),
                    "fired_at": task["last_fired"],
                })

                if task.get("auto_run"):
                    # auto_run 模式：直接在独立线程里执行子任务，无需用户输入触发
                    task_snapshot = dict(task)
                    threading.Thread(
                        target=self._run_auto_task,
                        args=(task_snapshot,),
                        daemon=True,
                    ).start()
                else:
                    # 普通模式：入队，等 agent_loop drain 时注入给 LLM
                    self.queue.put(f"[Scheduled task {task['id']}]: {task['prompt']}")

                if not task.get("recurring"):
                    fired_oneshots.append(task["id"])

        # 3) 清理 expired + one-shot；写盘
        if expired or fired_oneshots or fired_any:
            remove_ids = set(expired) | set(fired_oneshots)
            with self._lock:
                if remove_ids:
                    self.tasks = [t for t in self.tasks if t["id"] not in remove_ids]
                # last_fired 写入 durable 存档，供下次 detect_missed 用
                self._save_durable_locked()
            for tid in expired:
                _async_print(f"[cron] auto-expired: {tid} (older than {AUTO_EXPIRY_DAYS}d)")
            for tid in fired_oneshots:
                _async_print(f"[cron] one-shot done and removed: {tid}")

    def _run_auto_task(self, task: dict[str, Any]) -> None:
        """
        auto_run 模式下，在独立后台线程里直接执行子任务。

        通过延迟 import run_subagent，避免 managers 层对 tools 层的静态依赖。
        执行完成后把结果摘要通过 _async_print 输出到终端，让用户可见。
        """
        _async_print(f"[cron] auto_run start: {task['id']}")
        self._emit({"type": "auto_run_start", "id": task["id"], "prompt": task.get("prompt", "")})
        try:
            from ..tools.subagent import run_subagent
            result = run_subagent(task["prompt"], agent_type="general-purpose")
            preview = result[:200] + ("…" if len(result) > 200 else "")
            _async_print(f"[cron] auto_run done: {task['id']} — {preview}")
            self._emit({
                "type": "auto_run_done",
                "id": task["id"],
                "result": result,
                "preview": preview,
            })
        except Exception as e:
            _async_print(f"[cron] auto_run error: {task['id']} — {e}")
            self._emit({"type": "auto_run_error", "id": task["id"], "error": str(e)})

    # ---- 持久化 ------------------------------------------------------------

    def _load_durable(self) -> None:
        """从磁盘加载 durable 任务。文件不存在 / 解析失败 → 空列表，不抛。"""
        if not self._tasks_file.exists():
            return
        try:
            raw = json.loads(self._tasks_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[cron] failed to load {self._tasks_file}: {e}")
            return
        if not isinstance(raw, list):
            print(f"[cron] {self._tasks_file} must be a JSON list; ignored")
            return

        with self._lock:
            # 只载入 durable 记录；脏字段安全容错（最少要有 id/cron/prompt）
            self.tasks = [
                t for t in raw
                if isinstance(t, dict)
                and t.get("durable")
                and "id" in t and "cron" in t and "prompt" in t
            ]
        if self.tasks:
            print(f"[cron] loaded {len(self.tasks)} durable task(s) from {self._tasks_file.name}")

    def _save_durable_locked(self) -> None:
        """
        保存 durable 任务。**必须在持有 self._lock 的上下文中调用。**

        调用方已经拿了锁，这里不再获取，避免重入。
        """
        durable = [t for t in self.tasks if t.get("durable")]
        try:
            self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
            self._tasks_file.write_text(
                json.dumps(durable, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as e:
            # _save_durable_locked 既可能由主线程（create/delete/clear）调用，
            # 也可能由后台线程（_check_tasks 清理过期/one-shot）调用 —— 用
            # _async_print 两种路径都能安全输出。
            _async_print(f"[cron] failed to save {self._tasks_file}: {e}")


# ---------------------------------------------------------------------------
# 模块级工具：cron 表达式语法合法性预检（比 cron_matches 更严格）
# ---------------------------------------------------------------------------

def _validate_cron_syntax(expr: str) -> bool:
    """
    最小语法校验：非空、5 个字段、每个字段非空；具体值域交给 cron_matches。
    """
    if not expr or not expr.strip():
        return False
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    return all(f.strip() for f in fields)


def _describe_cron(expr: str) -> str:
    """
    把 cron 表达式翻译成简短中文描述，仅覆盖最常见的几种模式；
    遇到不识别的组合则回退到 "cron: <expr>"。

    覆盖的模式（按优先级）：
        1. "* * * * *"               → 每分钟
        2. "*/N * * * *"             → 每 N 分钟
        3. "M * * * *"（M 为数字）   → 每小时的第 M 分钟
        4. "M H * * *"               → 每天 HH:MM
        5. "M H * * D"（D 为数字）   → 每周{周X} HH:MM
        6. "0 */N * * *"             → 每 N 小时整点
        其它                         → "cron: <原表达式>"

    设计取舍：
        * 不引入 croniter 等外部库，保持零依赖教学风格
        * 只做"一眼就能说清"的表达式；复杂表达式故意不翻译，避免给学员
          错觉"这里能处理所有 cron"——学员有动力的话可以自己扩展
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        return f"cron: {expr}"

    m, h, dom, mon, dow = fields
    dow_names = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

    # 1) 每分钟
    if (m, h, dom, mon, dow) == ("*", "*", "*", "*", "*"):
        return "每分钟一次"

    # 2) 每 N 分钟
    if m.startswith("*/") and h == dom == mon == dow == "*":
        try:
            n = int(m[2:])
            return f"每 {n} 分钟一次"
        except ValueError:
            pass

    # 6) 每 N 小时整点
    if m == "0" and h.startswith("*/") and dom == mon == dow == "*":
        try:
            n = int(h[2:])
            return f"每 {n} 小时整点一次"
        except ValueError:
            pass

    # 尝试把 m / h 转成整数，供 3/4/5 用
    try:
        mi = int(m)
    except ValueError:
        mi = None
    try:
        hi = int(h)
    except ValueError:
        hi = None

    # 3) 每小时第 M 分钟
    if mi is not None and h == dom == mon == dow == "*":
        return f"每小时第 {mi} 分钟"

    # 4) 每天 HH:MM
    if mi is not None and hi is not None and dom == mon == dow == "*":
        return f"每天 {hi:02d}:{mi:02d}"

    # 5) 每周 X HH:MM
    if mi is not None and hi is not None and dom == mon == "*":
        try:
            di = int(dow)
            if 0 <= di <= 6:
                return f"每{dow_names[di]} {hi:02d}:{mi:02d}"
        except ValueError:
            pass

    return f"cron: {expr}"
