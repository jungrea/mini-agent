"""
core/dispatch —— s02 工具分发表与 TOOLS schema。

对应源 s_full.py 第 668–742 行。

说明：
    * TOOL_HANDLERS：tool_name → 执行函数（接收 **kw，返回 str）
    * TOOLS：提交给 LLM 的工具 schema 列表（name / description / input_schema）
    两者一一对应。保持与源逐字一致以确保行为不变。
"""

import json

from ..managers.compression import auto_compact  # noqa: F401 —— compress 工具占位
from ..team.protocols import handle_plan_review, handle_shutdown_request
from ..tools.bash import run_bash
from ..tools.fs import run_edit, run_read, run_write
from ..tools.search import run_search
from ..tools.subagent import run_subagent
from ..tools.web import run_web_fetch, run_web_search
from .config import VALID_MSG_TYPES
from .runtime import BG, BUS, CRON, SKILLS, TASK_MGR, TEAM, TODO


# === TOOL_HANDLERS：tool_name → handler callable ==========================
#
# 每个 handler 接收 **kw，即 LLM 侧提交的 tool_input 展开；
# 额外的 `tool_use_id` 由 agent_loop 在分派前塞进去，供 persisted_output 使用。
TOOL_HANDLERS: dict = {
    # --- 基础 I/O（s02） ---
    "bash":             lambda **kw: run_bash(kw["command"], kw.get("tool_use_id", "")),
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("tool_use_id", ""), kw.get("limit")),
    "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),

    # --- 代码搜索 ---
    "search_content":   lambda **kw: run_search(
        kw["pattern"],
        kw.get("path", "."),
        kw.get("glob"),
        kw.get("case_sensitive", False),
        kw.get("max_results", 200),
    ),

    # --- 网络 ---
    "web_fetch":        lambda **kw: run_web_fetch(kw["url"], kw.get("max_chars", 20000)),
    "web_search":       lambda **kw: run_web_search(kw["query"], kw.get("max_results", 5)),

    # --- 内存型 Todo（s03） ---
    "TodoWrite":        lambda **kw: TODO.update(kw["items"]),

    # --- 子智能体（s04） ---
    "task":             lambda **kw: run_subagent(kw["prompt"], kw.get("agent_type", "Explore")),

    # --- 技能加载（s05） ---
    "load_skill":       lambda **kw: SKILLS.load(kw["name"]),

    # --- 上下文压缩（s06，手动触发：占位字符串，实际压缩由 agent_loop 循环尾部执行） ---
    "compress":         lambda **kw: "Compressing...",

    # --- 后台任务（s08） ---
    "background_run":   lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),

    # --- 定时任务（s14 融合） ---
    # cron_create 不立即执行任何工具，只在调度器里登记一条"到点发 prompt"
    # 的记录。到点时会由 agent_loop 通过 CRON.drain_notifications 注入
    # <scheduled-tasks> 消息，LLM 届时再决定调什么工具——届时仍走完整
    # PermissionManager 管线，cron 本身不提权。
    "cron_create":      lambda **kw: CRON.create(
        kw["cron"], kw["prompt"],
        kw.get("recurring", True),
        kw.get("durable", False),
        kw.get("auto_run", False),
    ),
    "cron_delete":      lambda **kw: CRON.delete(kw["id"]),
    "cron_list":        lambda **kw: CRON.list_tasks(),

    # --- 文件任务（s07 文件任务版） ---
    "task_create":      lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),
    "task_get":         lambda **kw: TASK_MGR.get(kw["task_id"]),
    "task_update":      lambda **kw: TASK_MGR.update(
        kw["task_id"],
        kw.get("status"),
        kw.get("add_blocked_by"),
        kw.get("add_blocks"),
    ),
    "task_list":        lambda **kw: TASK_MGR.list_all(),

    # --- 团队 & 消息（s09 / s11） ---
    "spawn_teammate":   lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates":   lambda **kw: TEAM.list_all(),
    "send_message":     lambda **kw: BUS.send("lead", kw["to"], kw["content"],
                                              kw.get("msg_type", "message")),
    "read_inbox":       lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast":        lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),

    # --- 协议（s10） ---
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"], BUS),
    "plan_approval":    lambda **kw: handle_plan_review(
        kw["request_id"], kw["approve"], kw.get("feedback", ""), BUS
    ),

    # --- lead 自身不能 idle；这里回一句提示 ---
    "idle":             lambda **kw: "Lead does not idle.",

    # --- lead 也可以主动 claim 任务（把 owner 设为 "lead"） ---
    "claim_task":       lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),
}


# === TOOLS：提交给 LLM 的 schema（与 TOOL_HANDLERS 一一对应） ============

TOOLS: list[dict] = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "limit": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "old_text": {"type": "string"},
                                     "new_text": {"type": "string"}},
                      "required": ["path", "old_text", "new_text"]}},
    {"name": "search_content",
     "description": ("Search code by regex. Fast (ripgrep if available, Python fallback otherwise). "
                     "Returns lines in 'path:line:content' format. Prefer this over bash grep/find "
                     "for locating code."),
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string",
                                                 "description": "Regex pattern."},
                                     "path": {"type": "string",
                                              "description": "Root directory (relative to workspace). Default '.'"},
                                     "glob": {"type": "string",
                                              "description": "File name glob, e.g. '*.py'. Optional."},
                                     "case_sensitive": {"type": "boolean",
                                                        "description": "Default false."},
                                     "max_results": {"type": "integer",
                                                     "description": "Cap on matching lines. Default 200."}},
                      "required": ["pattern"]}},
    {"name": "web_fetch",
     "description": ("Fetch an http(s) URL and return its visible text (HTML is stripped of tags). "
                     "JSON is pretty-printed. Use this for reading docs, pages, or JSON APIs."),
     "input_schema": {"type": "object",
                      "properties": {"url": {"type": "string"},
                                     "max_chars": {"type": "integer",
                                                   "description": "Truncate output to this many chars. Default 20000."}},
                      "required": ["url"]}},
    {"name": "web_search",
     "description": ("Search the web. Uses Tavily if TAVILY_API_KEY is set; otherwise falls back "
                     "to DuckDuckGo. Returns a numbered list of title/url/snippet."),
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "max_results": {"type": "integer",
                                                     "description": "Default 5."}},
                      "required": ["query"]}},
    {"name": "TodoWrite", "description": "Update task tracking list.",
     "input_schema": {"type": "object",
                      "properties": {"items": {"type": "array",
                                               "items": {"type": "object",
                                                         "properties": {
                                                             "content": {"type": "string"},
                                                             "status": {"type": "string",
                                                                        "enum": ["pending", "in_progress", "completed"]},
                                                             "activeForm": {"type": "string"}},
                                                         "required": ["content", "status", "activeForm"]}}},
                      "required": ["items"]}},
    {"name": "task", "description": "Spawn a subagent for isolated exploration or work.",
     "input_schema": {"type": "object",
                      "properties": {"prompt": {"type": "string"},
                                     "agent_type": {"type": "string",
                                                    "enum": ["Explore", "general-purpose"]}},
                      "required": ["prompt"]}},
    {"name": "load_skill", "description": "Load specialized knowledge by name.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "compress", "description": "Manually compress conversation context.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "background_run", "description": "Run command in background thread.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"},
                                     "timeout": {"type": "integer"}},
                      "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}}}},
    {"name": "cron_create",
     "description": ("Schedule a prompt to fire at cron-matched times. "
                     "When the task fires, the `prompt` string is injected as a user "
                     "message in the next agent loop iteration — the agent then decides "
                     "what tools to call (still subject to the permission manager). "
                     "Cron: 5-field 'min hour dom month dow', e.g. '*/5 * * * *' every 5 min, "
                     "'0 9 * * 1' Monday 9am, '30 14 * * *' daily 2:30pm. "
                     "durable=true persists to .claude/scheduled_tasks.json across sessions; "
                     "recurring=false fires once then auto-deletes. "
                     "auto_run=true executes the prompt as a general-purpose subagent directly "
                     "in a background thread without requiring user input — use this for "
                     "fully unattended tasks like writing files or fetching data."),
     "input_schema": {"type": "object",
                      "properties": {"cron": {"type": "string",
                                              "description": "5-field cron expression."},
                                     "prompt": {"type": "string",
                                                "description": "Prompt injected when the task fires."},
                                     "recurring": {"type": "boolean",
                                                   "description": "Default true."},
                                     "durable": {"type": "boolean",
                                                 "description": "Default false (session-only)."},
                                     "auto_run": {"type": "boolean",
                                                  "description": "Default false. If true, run as background subagent automatically without waiting for user input."}},
                      "required": ["cron", "prompt"]}},
    {"name": "cron_delete", "description": "Delete a scheduled task by its short ID.",
     "input_schema": {"type": "object",
                      "properties": {"id": {"type": "string"}},
                      "required": ["id"]}},
    {"name": "cron_list", "description": "List all scheduled tasks (session + durable).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "task_create", "description": "Create a persistent file task.",
     "input_schema": {"type": "object",
                      "properties": {"subject": {"type": "string"},
                                     "description": {"type": "string"}},
                      "required": ["subject"]}},
    {"name": "task_get", "description": "Get task details by ID.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "integer"}},
                      "required": ["task_id"]}},
    {"name": "task_update", "description": "Update task status or dependencies.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "integer"},
                                     "status": {"type": "string",
                                                "enum": ["pending", "in_progress", "completed", "deleted"]},
                                     "add_blocked_by": {"type": "array", "items": {"type": "integer"}},
                                     "add_blocks": {"type": "array", "items": {"type": "integer"}}},
                      "required": ["task_id"]}},
    {"name": "task_list", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spawn_teammate", "description": "Spawn a persistent autonomous teammate.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "role": {"type": "string"},
                                     "prompt": {"type": "string"}},
                      "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate.",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "content": {"type": "string"},
                                     "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}},
                      "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send message to all teammates.",
     "input_schema": {"type": "object",
                      "properties": {"content": {"type": "string"}},
                      "required": ["content"]}},
    {"name": "shutdown_request", "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"}},
                      "required": ["teammate"]}},
    {"name": "plan_approval", "description": "Approve or reject a teammate's plan.",
     "input_schema": {"type": "object",
                      "properties": {"request_id": {"type": "string"},
                                     "approve": {"type": "boolean"},
                                     "feedback": {"type": "string"}},
                      "required": ["request_id", "approve"]}},
    {"name": "idle", "description": "Enter idle state.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "claim_task", "description": "Claim a task from the board.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "integer"}},
                      "required": ["task_id"]}},
]
