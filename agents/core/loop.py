"""
core/loop —— s01 主智能体循环（集成 s07 权限三分支 + s08 hook 扩展 + s10 system prompt 装配器）。

对应源 s_full.py 第 745–802 行，加上参照 s07_permission_system.py 的权限管线、
参照 s08_hook_system.py 的 PreToolUse/PostToolUse 外部扩展点、
以及参照 s10_system_prompt.py 的 SystemPromptBuilder 分段装配。

单轮主循环的任务：
    1) microcompact 轻量压缩（每轮必做）
    2) 若 token 预估超阈值 → auto_compact 重度压缩
    3) 从 BackgroundManager 拉取已完成后台任务通知，塞进上下文
    4) 从 CronScheduler 拉取到点的定时任务通知，塞进上下文
    5) 从 lead 收件箱拉取消息，塞进上下文
    6) 用 SystemPromptBuilder 现场装配 system（静态前缀 + 当前权限模式等动态段）
    7) 调 LLM，拿到 stop_reason
    8) 若 stop_reason == "tool_use"：
       对每个 tool_use：Permission.check → (allow) → HookManager.PreToolUse →
                        handler → HookManager.PostToolUse
    9) 处理 TodoWrite 的 nag 计数器（连续 3 轮未更新 todos → 追加 <system-reminder>）
   10) 若 LLM 本轮要求手动 compress，尾部再做一次 auto_compact

说明 —— 内建扩展点 vs 外部 hook
    本文件里的 BG.drain / CRON.drain_notifications / BUS.read_inbox /
    build_system_reminder 可以理解为"内建的 pre-LLM / post-tool hook"，
    它们与工具调用 **正交** 地注入消息；而外部 .hooks.json 的
    PreToolUse / PostToolUse 则是"每次工具调用前后的可插拔扩展层"——
    两者各司其职，不重叠。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, Iterator, Optional

from ..managers.compression import auto_compact, estimate_tokens, microcompact
from ..permissions.manager import PermissionManager
from .config import MODEL, TOKEN_THRESHOLD, client
from .dispatch import PARALLEL_SAFE, TOOL_HANDLERS, TOOLS
from .hooks import HookManager
from .normalize import normalize_messages
from .prompts import BUILDER
from .reminders import build_system_reminder
from .runtime import BG, BUS, CRON, TODO
from .usage import USAGE


#: 并行桶的线程数上限。web_search/web_fetch 是主要受益方；值取太大会撞 API
#: 速率限制，取太小又浪费。3 是"两三个并行网络请求"的常见峰值。
PARALLEL_MAX_WORKERS: int = 3


#: 可选的进度回调签名：progress(event_type, payload_dict)。
#: 由 webui 等前端注入，用于实时渲染"LLM 思考中 / 执行工具 X / …"等状态。
#: 未注入（None）时主循环行为完全不变。
ProgressCallback = Callable[[str, dict], None]


def _safe_call(cb: Optional[ProgressCallback], event: str, payload: dict) -> None:
    """progress 回调绝不能让主循环崩溃——任何异常吞掉。"""
    if cb is None:
        return
    try:
        cb(event, payload)
    except Exception:
        pass


def _time_now() -> float:
    """单独抽一个小函数，方便测试打桩。"""
    import time
    return time.time()


def _safe_preview(output: Any, limit: int = 160) -> str:
    """裁剪工具输出预览给 progress 事件用，避免推长文本到前端。"""
    try:
        s = str(output)
    except Exception:
        return ""
    first = s.splitlines()[0] if s else ""
    preview = first[:limit]
    if len(first) > limit or "\n" in s:
        preview += "…"
    return preview


def _is_cancelled(cancel_check: Optional[Callable[[], bool]]) -> bool:
    """检查外部是否请求了早停；任何异常都按"未取消"处理。"""
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:
        return False


@contextmanager
def _spinning(label: str) -> Iterator[None]:
    """
    包裹"可能花几秒"的阻塞操作，在 TTY 下显示 spinner。

    优先使用 cli.spinner（如果可用）；否则退化为 no-op。
    延迟 import 避免 core 对 cli 的硬依赖——teammate 子进程、测试环境
    不需要 UI 层也能正常跑。
    """
    try:
        from ..cli.spinner import spinning
    except ImportError:
        with nullcontext():
            yield
        return
    with spinning(label):
        yield


def _flush_admitted_to_results(admitted: list[dict],
                               outputs: dict[str, tuple[str, int, bool]],
                               results: list,
                               hooks: HookManager | None,
                               progress: Optional[ProgressCallback],
                               used_todo_ref: bool | None) -> bool:
    """
    工具执行 Phase D：按 admitted 的原顺序回放结果，产出 tool_result。

    每个 admitted 项的归宿：
        * skip=True            —— Phase A 已经定性失败；直接写 skip_result
        * skip=False 且无 output —— 异常路径（例如 serial 桶被 cancel 中断）；不写，交给上层补 cancel
        * skip=False 且有 output —— 正常跑完：发 tool_end、跑 PostHook、拼 tool_result

    返回：更新后的 used_todo 标志（是否本轮出现过 TodoWrite 的有效调用）。
    """
    used_todo = bool(used_todo_ref)
    for item in admitted:
        block = item["block"]

        if item["skip"]:
            # Phase A 已经产生终态文本
            results.append({"type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(item["skip_result"])})
            continue

        if block.id not in outputs:
            # 还没跑到（例如 serial 桶中途取消）——留给上层补 cancel_result
            continue

        output, duration_ms, error_flag = outputs[block.id]
        tool_input = item["tool_input"]
        pre_notes = item.get("pre_notes", "")

        # progress: tool_end（并行桶也在这里发，保证和 PostHook / tool_result 序一致）
        _safe_call(progress, "tool_end", {
            "id": block.id, "name": block.name,
            "duration_ms": duration_ms,
            "output_preview": _safe_preview(output),
            "output": output,
            "error": error_flag,
        })

        # PostToolUse hook
        post_notes = ""
        if hooks is not None:
            post = hooks.run_hooks("PostToolUse", {
                "tool_name": block.name,
                "tool_input": {k: v for k, v in tool_input.items()
                               if k != "tool_use_id"},
                "tool_output": output,
            })
            notes = list(post["messages"])
            if post["blocked"] and post["block_reason"]:
                notes.append(post["block_reason"])
            if notes:
                post_notes = "".join(f"\n[hook note]: {m}" for m in notes)

        # 终端单行摘要
        full = pre_notes + output + post_notes
        first = full.splitlines()[0] if full else ""
        preview = first[:200]
        if len(first) > 200 or "\n" in full:
            preview += "…"
        print(f"> {block.name}: {preview}")

        results.append({"type": "tool_result",
                        "tool_use_id": block.id,
                        "content": full})

        if block.name == "TodoWrite":
            used_todo = True

    return used_todo


def agent_loop(messages: list,
               perms: PermissionManager | None = None,
               hooks: HookManager | None = None,
               progress: Optional[ProgressCallback] = None,
               cancel_check: Optional[Callable[[], bool]] = None) -> None:
    """
    主循环。就地修改 messages。

    参数：
        messages: 对话历史（user / assistant 交替；tool_result 以 dict 块形式塞在 user.content 列表里）
        perms:    权限管理器；None 表示跳过权限检查（等价于源 s_full.py 行为）
        hooks:    外部 hook 管理器；None 表示不启用外部扩展层（等价于无 .hooks.json）
                  Hook 在权限 allow 之后执行，与 PermissionManager 并列而非替代。
        progress: 可选的细粒度进度回调（webui 等前端用）。None 时完全无影响。
                  事件类型（event name）：
                      * "round_start"   —— 一轮新的 LLM 交互开始
                      * "llm_start"     —— 即将发起 messages.create
                      * "llm_end"       —— LLM 返回；payload 带 stop_reason
                      * "tool_start"    —— 某个工具即将执行；payload: {id, name, input}
                      * "tool_end"      —— 某个工具执行结束；payload: {id, name, duration_ms, output_preview}
                      * "tool_denied"   —— 工具被权限/用户/hook 拒绝；payload: {id, name, reason}
                      * "cancelled"     —— 外部请求早停（cancel_check 返回 True），主循环退出
        cancel_check: 可选的取消检查函数（webui stop 按钮用）。每轮开头 + 每次工具调用前 + 工具结束后
                      都会调用一次；返回 True 即中止主循环。注意：一旦 LLM 调用已发出，
                      无法中途打断 Anthropic SDK 的同步请求——但可以确保不再进入下一轮 / 不再跑下一个工具。
                      None 时完全无影响（CLI / teammate 路径默认如此）。

    返回：
        None —— 以模型发出非 tool_use 响应（即纯文本回答）为终止条件
    """
    # nag 计数器：连续多少轮 LLM 没有调用 TodoWrite
    # 仅当 TODO 列表里还有 open items 时，达到阈值才追加 reminder
    rounds_without_todo = 0

    while True:
        # 每轮开始前先检查外部是否请求早停
        if _is_cancelled(cancel_check):
            _safe_call(progress, "cancelled", {"stage": "round_start"})
            return

        _safe_call(progress, "round_start", {})

        # --- s06: 每轮必做的轻量压缩 -------------------------------------
        microcompact(messages)

        # --- s06: token 超阈值时触发重度压缩 ----------------------------
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            print("[auto-compact triggered]")
            messages[:] = auto_compact(messages)

        # --- s08: 拉取后台任务完成通知 -----------------------------------
        # --- s14: 拉取定时任务到点通知 -----------------------------------
        # --- s10: 拉取 lead 收件箱 --------------------------------------
        #
        # 【重要】这三类注入消息必须合并成一条 user 消息，不能各自插入
        # 独立的 user/assistant 对。原因：
        #   Anthropic API 要求 tool_use 之后必须紧接 tool_result，中间不能
        #   插入其他消息。若上一轮 LLM 调用了工具，messages 末尾是
        #   assistant[tool_use...] + user[tool_result...]，此时若再插入
        #   user/assistant 对，下一次 LLM 调用时历史里会出现
        #   "tool_use 之后不是 tool_result"的非法结构，导致 400 错误。
        #   合并为单条 user 消息后，只在没有 pending tool_result 时（即
        #   messages 为空或末尾是 assistant 纯文本回复）才追加，保持结构合法。
        injections: list[str] = []

        notifs = BG.drain()
        if notifs:
            txt = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
            )
            injections.append(f"<background-results>\n{txt}\n</background-results>")

        cron_notifs = CRON.drain_notifications()
        if cron_notifs:
            txt = "\n".join(cron_notifs)
            guard = (
                "This is a scheduled-task reminder — execute the reminder itself "
                "(e.g., perform the task, respond to the user). "
                "Do NOT call cron_create / cron_delete / cron_list to modify "
                "scheduler state unless the user explicitly asks for it in the "
                "current user turn. Treating this reminder as an authorization "
                "to reconfigure the schedule is a mistake."
            )
            injections.append(
                f"<scheduled-tasks>\n{txt}\n\n<guard>{guard}</guard>\n</scheduled-tasks>"
            )

        inbox = BUS.read_inbox("lead")
        if inbox:
            injections.append(f"<inbox>{json.dumps(inbox, indent=2)}</inbox>")

        if injections:
            # 只在 messages 为空，或末尾消息是 assistant 纯文本（非 tool_use）时注入，
            # 确保不破坏 tool_use / tool_result 的配对约束。
            last_is_safe = (
                not messages
                or (
                    messages[-1]["role"] == "assistant"
                    and isinstance(messages[-1].get("content"), str)
                )
            )
            if last_is_safe:
                combined = "\n\n".join(injections)
                messages.append({"role": "user", "content": combined})
                messages.append({"role": "assistant", "content": "Noted."})

        # --- LLM 调用 ---------------------------------------------------
        # spinner 只在 TTY 下可见；teammate 子进程 / 测试环境会自动降级。
        # system prompt 每轮重建：稳定前缀（core/tools/skills/memory/CLAUDE.md）
        # 不变，但 dynamic 段会反映当前日期、cwd、权限模式。重建成本 <1ms，
        # 不做缓存；这也是 s10 DYNAMIC_BOUNDARY 思想的落地——稳定与动态分离。
        system_text = BUILDER.build(mode=perms.mode if perms else None)
        _safe_call(progress, "llm_start", {})
        with _spinning("思考中…"):
            # normalize_messages 是出门安检：保证 messages 结构合法
            # （tool_use ↔ tool_result 配对、tool_result 在 user 内容前部、
            #  无空 content、剥内部字段）。源头 bug 仍应在 loop 自己里修；
            #  这里只是把"漏网"的小错降级为"打日志 + 静默修复"，避免直接 400。
            response = client.messages.create(
                model=MODEL,
                system=system_text,
                messages=normalize_messages(messages),
                tools=TOOLS,
                max_tokens=8000,
            )
        # 记录本轮 token 用量：repl HUD 会读这里的累计数据
        USAGE.record(getattr(response, "usage", None))
        messages.append({"role": "assistant", "content": response.content})

        _safe_call(progress, "llm_end", {"stop_reason": response.stop_reason})

        # 纯文本响应 → 本轮对话结束
        if response.stop_reason != "tool_use":
            # ========= RoundEnd hook（本项目扩展）=========
            # 每轮 agent_loop 自然结束时触发一次；失败静默（与其他 hook 一致）。
            # 提供三个上下文字段给 hook 脚本（通过 HOOK_* 环境变量）：
            #   * tool_name = ""           —— 对齐通用约定，RoundEnd 无关工具
            #   * stop_reason              —— 通常是 "end_turn"
            #   * last_assistant_text      —— 本轮最终 assistant 文本预览（≤200 字）
            # 刻意不尊重 blocked/updated_input —— round 已经结束，改也无意义。
            if hooks is not None:
                preview = ""
                try:
                    for blk in response.content:
                        txt = getattr(blk, "text", None)
                        if txt:
                            preview = txt[:200]
                            break
                except Exception:
                    pass
                hooks.run_hooks("RoundEnd", context={
                    "tool_name": "",
                    "tool_input": {
                        "stop_reason": response.stop_reason,
                        "last_assistant_text": preview,
                    },
                })
            # ========= RoundEnd hook 结束 =========
            return

        # --- 工具执行（两阶段调度：准入串行 + 执行分桶并行/串行）-----------
        #
        # 设计要点：
        #   * Phase A（串行）：权限检查 + ask_user + PreToolUse hook。
        #     这些步骤含用户交互（权限弹窗）和顺序敏感操作（hook 改 input），
        #     必须按 response.content 原顺序一个一个做。
        #   * Phase B（分桶）：准入通过的任务按 PARALLEL_SAFE 分到并行/串行桶。
        #     PreToolUse 改过 input 的任务**强制回落到串行**——因为 hook 可能
        #     依赖执行顺序（例如前一个 hook 改 env 影响下一个）。
        #   * Phase C（执行）：并行桶用 ThreadPoolExecutor；串行桶保持原逻辑。
        #   * Phase D（收口）：按 response.content 原顺序发 progress / PostHook /
        #     拼 tool_result，保证 UI 与日志时序可读。
        results = []
        used_todo = False
        manual_compress = False
        compact_focus = None

        # 同一轮的所有 tool_use 块（用来"cancel 时补齐未执行的 tool_result"）
        all_tool_uses = [b for b in response.content if b.type == "tool_use"]

        # Phase A 的产物：以 block.id 为键的准入结果
        # 每项是 dict:
        #   {"block": block, "tool_input": dict, "pre_notes": str,
        #    "skip": bool,     # True 表示本 block 已经在 Phase A 里定性失败（deny/ask-deny/hook-blocked）
        #    "skip_result": str,   # skip 时要写回的 tool_result 文本
        #   }
        # 用 list 保序（按 response.content 原序），便于 Phase D 回放。
        admitted: list[dict] = []

        def _append_cancel_results() -> None:
            """cancel 早停时补齐所有尚未产出 tool_result 的 tool_use。"""
            done_ids = {r.get("tool_use_id") for r in results}
            for b in all_tool_uses:
                if b.id not in done_ids:
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": "Cancelled by user before tool execution.",
                    })

        # ---------- Phase A：串行准入（权限 + ask + PreHook） ----------
        for block in all_tool_uses:
            if _is_cancelled(cancel_check):
                _safe_call(progress, "cancelled", {"stage": "pre_tool", "tool": block.name})
                _append_cancel_results()
                messages.append({"role": "user", "content": results})
                return

            # 记录是否本轮有 compress 请求（延后处理）
            if block.name == "compress":
                manual_compress = True
                compact_focus = (block.input or {}).get("focus")

            # ---- 权限检查 ----
            if perms is not None:
                decision = perms.check(block.name, block.input or {})
                if decision["behavior"] == "deny":
                    msg = f"Permission denied: {decision['reason']}"
                    print(f"  [DENIED] {block.name}: {decision['reason']}")
                    _safe_call(progress, "tool_denied", {
                        "id": block.id, "name": block.name,
                        "reason": decision["reason"], "source": "permission",
                    })
                    admitted.append({"block": block, "skip": True, "skip_result": msg})
                    continue
                if decision["behavior"] == "ask":
                    if not perms.ask_user(block.name, block.input or {}):
                        msg = f"Permission denied by user for {block.name}"
                        print(f"  [USER DENIED] {block.name}")
                        _safe_call(progress, "tool_denied", {
                            "id": block.id, "name": block.name,
                            "reason": "denied by user", "source": "user",
                        })
                        admitted.append({"block": block, "skip": True, "skip_result": msg})
                        continue

            # ---- PreToolUse hook（权限通过才跑）----
            tool_input = dict(block.input or {})
            tool_input["tool_use_id"] = block.id
            pre_notes = ""
            input_was_updated = False
            if hooks is not None:
                pre = hooks.run_hooks("PreToolUse", {
                    "tool_name": block.name,
                    "tool_input": {k: v for k, v in tool_input.items()
                                   if k != "tool_use_id"},
                })
                if pre["blocked"]:
                    msg = f"Blocked by hook: {pre['block_reason']}"
                    print(f"  [HOOK BLOCKED] {block.name}: {pre['block_reason']}")
                    _safe_call(progress, "tool_denied", {
                        "id": block.id, "name": block.name,
                        "reason": pre.get("block_reason") or "hook blocked",
                        "source": "hook",
                    })
                    admitted.append({"block": block, "skip": True, "skip_result": msg})
                    continue
                if pre.get("updated_input"):
                    new_input = dict(pre["updated_input"])
                    new_input["tool_use_id"] = block.id
                    tool_input = new_input
                    input_was_updated = True
                if pre["messages"]:
                    pre_notes = "".join(f"[hook]: {m}\n" for m in pre["messages"])

            admitted.append({
                "block": block, "skip": False,
                "tool_input": tool_input, "pre_notes": pre_notes,
                "input_was_updated": input_was_updated,
            })

        # ---------- Phase B：分桶 ----------
        # 并行条件：准入通过 + 在 PARALLEL_SAFE 白名单里 + PreHook 没改过 input
        parallel_items: list[dict] = []
        serial_items: list[dict] = []
        for item in admitted:
            if item["skip"]:
                continue
            b = item["block"]
            if b.name in PARALLEL_SAFE and not item["input_was_updated"]:
                parallel_items.append(item)
            else:
                serial_items.append(item)

        # ---------- Phase C：执行 ----------
        # outputs 以 block.id 为键存 (output_str, duration_ms, error_flag)；
        # 方便 Phase D 按原顺序回放 progress / PostHook / tool_result。
        outputs: dict[str, tuple[str, int, bool]] = {}

        def _invoke_handler(item: dict) -> tuple[str, int, bool]:
            """跑 handler，吞异常；返回 (output, duration_ms, error_flag)。"""
            b = item["block"]
            handler = TOOL_HANDLERS.get(b.name)
            t0 = _time_now()
            error = False
            try:
                output = handler(**item["tool_input"]) if handler \
                    else f"Unknown tool: {b.name}"
            except Exception as e:
                output = f"Error: {e}"
                error = True
            duration_ms = int((_time_now() - t0) * 1000)
            return str(output), duration_ms, error

        # 并行桶：线程池内并发；progress 的 tool_start 放在提交时打，tool_end
        # 放在 Phase D（保证与 PostHook/tool_result 顺序匹配）。
        if parallel_items:
            for item in parallel_items:
                b = item["block"]
                _safe_call(progress, "tool_start", {
                    "id": b.id, "name": b.name,
                    "input": {k: v for k, v in item["tool_input"].items()
                              if k != "tool_use_id"},
                })
            with ThreadPoolExecutor(
                max_workers=min(PARALLEL_MAX_WORKERS, len(parallel_items)),
                thread_name_prefix="tool-par",
            ) as ex:
                future_map = {ex.submit(_invoke_handler, it): it
                              for it in parallel_items}
                # 按完成顺序收集即可——outputs 按 id 存，Phase D 会按原序回放
                for fut in future_map:
                    item = future_map[fut]
                    try:
                        outputs[item["block"].id] = fut.result()
                    except Exception as e:   # 防御式：_invoke_handler 自己已吞异常，这里基本不会进
                        outputs[item["block"].id] = (f"Error: {e}", 0, True)

        # 串行桶：保持原行为——带 spinner、逐个跑
        for item in serial_items:
            if _is_cancelled(cancel_check):
                _safe_call(progress, "cancelled",
                           {"stage": "pre_tool", "tool": item["block"].name})
                # 把本桶剩余 + 并行桶未完成（理论上并行桶此刻都已完成）补成 cancelled
                # 先把已完成的收进 results，再补 cancel
                _flush_admitted_to_results(admitted, outputs, results,
                                           hooks, progress, used_todo_ref=None)
                _append_cancel_results()
                messages.append({"role": "user", "content": results})
                return
            b = item["block"]
            _safe_call(progress, "tool_start", {
                "id": b.id, "name": b.name,
                "input": {k: v for k, v in item["tool_input"].items()
                          if k != "tool_use_id"},
            })
            with _spinning(f"执行 {b.name}…"):
                outputs[b.id] = _invoke_handler(item)

        # ---------- Phase D：按原顺序收口（progress.tool_end + PostHook + tool_result） ----------
        used_todo = _flush_admitted_to_results(
            admitted, outputs, results, hooks, progress, used_todo_ref=used_todo,
        )

        # --- s03: TodoWrite nag 计数器 ---------------------------------
        # 只有"todo 工作流真在进行中"（TODO 里有 open items）且"连续 3 轮没调 TodoWrite"
        # 才追加 reminder。避免在"没有任何 todo 的纯对话场景"里骚扰模型。
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            # 走 <system-reminder>（对齐 s10 / Claude Code 真实做法），
            # 由 reminders 模块统一装配。
            #
            # 【重要】reminder 必须 append 到 results **末尾**，不能 insert(0)。
            # Anthropic API 要求：当上一条 assistant 含 tool_use 时，紧接的 user
            # 消息里 tool_result 的位置决定了 tool_use ↔ tool_result 的配对顺序；
            # 若把 text 塞到前面（曾经的实现），API 会把结构判定为"tool_use 之后
            # 没有紧跟 tool_result"而返回 400（messages.N: `tool_use` ids were
            # found without `tool_result` blocks immediately after）。
            # LLM 对尾部 text 块的可见性与开头一致，不影响提醒效果。
            reminder_block = build_system_reminder(todo_nag=True)
            if reminder_block is not None:
                results.append(reminder_block)

        messages.append({"role": "user", "content": results})

        # --- s06: 手动 compress 要求（由模型本轮决定） -----------------
        # 之所以放在工具结果塞回 messages 之后：compress 的 summary 应当包含
        # 本轮最新的工具结果，否则关键信息会丢
        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages, focus=compact_focus)
