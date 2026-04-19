"""
team/protocols —— s10 停机 / 计划审批协议。

对应源 s_full.py 第 485–487、651–665 行。

两种请求的共同特点是"请求-应答带 request_id"：
    * lead 发起请求后把 request_id 登记到本模块级字典
    * teammate 收到 shutdown_request 或发送 plan_approval_response 时
      都把同一个 request_id 回写，便于 lead 关联上下文

字典保留为模块级（而非 TeammateManager 的实例属性）是故意的：
这些协议的发起方是 lead（主 REPL）、接收方可能是多个 teammate，
放在模块级方便两侧通过同一个 import 访问。
"""

import uuid


# 请求 ID → 请求快照 dict
# {"target": "<teammate_name>", "status": "pending"|"sent"}
shutdown_requests: dict[str, dict] = {}

# 请求 ID → 请求快照 dict
# {"from": "<teammate_name>", "status": "pending"|"approved"|"rejected"}
plan_requests: dict[str, dict] = {}


def handle_shutdown_request(teammate: str, bus) -> str:
    """
    lead 向某个 teammate 发起"请停机"请求。

    参数：
        teammate: 目标成员名
        bus:      MessageBus 实例（由调用方注入，避免模块层耦合全局单例）

    流程：
        1) 生成 request_id
        2) 记录到 shutdown_requests
        3) 通过 bus 发送一条 type=shutdown_request 的消息
        teammate 的 _loop 在任一阶段读到这种消息即 return（见 team/teammate.py）。
    """
    req_id = str(uuid.uuid4())[:8]
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    bus.send("lead", teammate, "Please shut down.", "shutdown_request",
             {"request_id": req_id})
    return f"Shutdown request {req_id} sent to '{teammate}'"


def handle_plan_review(request_id: str, approve: bool, feedback: str, bus) -> str:
    """
    lead 对某个 teammate 提交的"计划"给出批准/拒绝响应。

    参数：
        request_id: 之前由 teammate 发起 plan_request 时带来的 ID
        approve:    True=批准
        feedback:   文字反馈（即使批准也可以附建议）
        bus:        MessageBus 实例

    若 request_id 未登记则返回错误字符串，让调用方（通常是 LLM 的 tool_result）
    看到失败原因并自行调整。
    """
    req = plan_requests.get(request_id)
    if not req:
        return f"Error: Unknown plan request_id '{request_id}'"

    req["status"] = "approved" if approve else "rejected"
    bus.send("lead", req["from"], feedback, "plan_approval_response",
             {"request_id": request_id, "approve": approve, "feedback": feedback})
    return f"Plan {req['status']} for '{req['from']}'"
