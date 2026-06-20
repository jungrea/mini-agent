# s16团队协议

上节我们讲了Agent的团队协作模式，本节是在团队协作模式的基础上补充团队协议，让团队在协作交流的过程中有更加规范、有记录可查、断点可恢复等。

## 1. 为什么需要团队协议

本节需要配合上节内容进行理解，假设你已经了解了上节的团队协作模式相关的数据结构和工作方法。

回顾一下上节中，团队之间的沟通方式是什么样的呢？
* `send_message(sender: str, to: str, content: str)`, 这是一个核心沟通函数，就是成员A给成员B发送任务消息（或者通知完成结果）
* 不同成员通过读取自己的消息库来决定是否有要执行的任务，如果自己的消息库有消息，就把消息提取出来，同时把该消息内容给删除掉。

整个过程中，我们是看不到消息的具体内容以及谁给谁发消息了的。那么补充团队交流见的协议框架就可以解决这样的一个问题，对于每一项任务：
* 提交任务的时候有唯一的记录几下；
* 员工执行该任务的时候，需要leader的审批；
* 员工只有拿到审批才能够执行。

如果把一个小任务的生命周期用一个RequestID来看，那么这个协议就针对这个RequestID的结构化的工作流对象。

|          | 普通消息        | 协议请求                         |
|----------|----------------------|-----------------------------------------|
| 形式     | 自由文本             | 结构化 JSON，包含 `request_id`          |
| 状态     | 无状态追踪           | 持久化到 `.team/requests/`              |
| 生命周期 | 发送即忘             | `pending -> approved / rejected`        |
| 回复方式 | 随意回复             | 必须通过 `request_id` 关联回复         |
| 可恢复性 | 不可恢复             | 重启后可查询历史请求状态                |

一个请求/响应的形状可以支持多种团队工作流。本节演示两种：shutdown 和 plan_approval，但这个模式可以扩展到更多协议类型，它会创建可追踪的请求记录。
* 有 request_id
* 有状态：pending / approved / rejected
* 适合需要闭环的流程：审批计划、优雅关闭等。

## 2. 一个请求协议的格式

每一个小任务当以request_id落盘的时间，那么落盘的数据结构定义如下：
`requests/{request_id}.json` 示例:
```
{
  "request_id": "f778f041",
  "kind": "plan_approval",
  "from": "alice",
  "to": "lead",
  "status": "pending",
  "plan": "先创建 HTML 结构，再实现 Snake 类...",
  "created_at": 1710000000.0,
  "updated_at": 1710000000.0
}
```
这里的kind就是支持的任务类型,比如可以包括:
```
VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval",
    "plan_approval_response",
    "result",
}
```
然后是哪个成员给哪个成员的消息，以及具体的计划内容和记录时间，另外就是一个状态。

那么这个Request的内容在哪个地方被产生，以及被使用呢？

前面我们提到了，`send_message`函数是不同成员之间相互进行交流的唯一渠道，有了RequestID以后，那么它的函数输入格式就需要发生变化，比如：
```
send_message(
  sender, 
  "lead", 
  message, 
  "plan_approval", # 增加一个消息类型。
  {"request_id": req_id, "plan": message} # 增加一个具体的消息内容
)
```
可以看到我们以前send函数是没有最后2个消息类型的。那么在增加了这种协议以后，是需要传入到对应消息类型的，同时我们这里也需要把request_id带进来，从而可以让它去关联到对应的磁盘内的内容，而一个request_id的内容如果在send函数之前是会被默认创建的。

对比上一节中的团队协作所产生的离线磁盘内容结构，本节中所产生的磁盘内容结构如下：
```
.team/
├── config.json                 # 团队成员持久状态
├── inbox/
│   ├── lead.jsonl              # Leader 的收件箱
│   ├── alice.jsonl             # Alice 的收件箱
│   └── bob.jsonl               # Bob 的收件箱
└── requests/                   # ---本节新增的协议结构---
    ├── f778f041.json           # 一个计划审批或关闭请求
    └── 4adafd17.json
```
* 可以看到成员的定义还是在Config里面，这里面包括了这个成员它的功能，它处于什么样的工作状态。
* Inbox则是不同成员的消息收件箱，某个成员给另外一个成员发消息之后，对应成员的收件箱就会有内容，而对应的成员在检测到有内容的时候，就会把内容读出来进行执行，同时把这条内容给删除掉，可以说Inbox就是一个消息中转站的角色。
* Requests目录下则是存储的一个小任务，具体的流转内容，这对于观测任务的流转状态、任务的恢复至关重要。

## 3. 本节涉及到的类结构

Agents团队协议模式到目前为止已经比较复杂了，涉及到的类已经不再是一两个可以完成的，这里我们统一列一下具有哪些类，以及类具有哪些功能从而了解整个框架。

结合上节内容，汇总下本节所用到的类包括：

| 组件                | 作用                                                                 | 关键方法/函数                                  |
|---------------------|----------------------------------------------------------------------|-----------------------------------------------|
| `MessageBus`        | 基于 JSONL 文件的消息总线，每个成员一个 inbox。                       | `send`、`read_inbox`、`broadcast`             |
| `RequestStore`      | 持久化协议请求，保存每个 `request_id` 的状态。                       | `create`、`get`、`update`                     |
| `TeammateManager`   | 管理队友注册、线程、状态和协议处理。                                 | `spawn`、`_teammate_loop`、`mark_active`      |
| `consume_lead_inbox`| Leader 统一读取 inbox，并先路由协议响应。                           | `_route_lead_protocol_message`                |
| `agent_loop`        | Leader 主循环：读 inbox、调用模型、执行工具、等待队友。              | `TOOL_HANDLERS`                               |

对于一个员工来说，那么他所能具备的状态也有一定的增加：

| 状态               | 含义                                               |
|--------------------|----------------------------------------------------|
| `idle`             | 线程在轮询 inbox，没有正在处理任务。               |
| `working`          | 队友正在执行模型调用或工具调用。                   |
| `waiting_approval` | 队友已提交计划，正在等待 Leader 审批。             |
| `shutdown`         | 队友已优雅关闭，线程停止。                         |

## 4. 一个计划的详细流转过程

我们用一个实际的例子来看一下，让员工以提计划的方式来执行某项小任务的时候，整个流转过程。

| 步骤 | 角色/组件 | 操作说明 |
|------|-----------|----------|
| 1 | 用户 | 输入：让 Alice 开发功能，开始前先提交计划。 |
| 2 | Leader | 调用 `request_plan(teammate="alice", task="...")`。 |
| 3 | MessageBus | 把普通消息写入 `.team/inbox/alice.jsonl` |
| 4 | Alice | 线程读取 inbox，进入 `working`，模型生成计划并调用 `submit_plan`。 |
| 5 | RequestStore | 创建 `.team/requests/{request_id}.json`，状态为 `pending`。 |
| 6 | Alice | 状态变为 `waiting_approval`，暂停模型调用，等待 `plan_approval_response`。 |
| 7 | Leader | 读取 inbox，看到计划请求，调用 `review_plan(request_id, approve=True)` 进行审批通过。 |
| 8 | Alice | 收到审批响应，切回 `working`，继续写代码、测试、发结果。 |

整个过程就分为以下几个阶段，这几个阶段是可以清晰看出来拥有团队协议情况下Agent团队的工作模式的：

| 阶段 | 函数/工具 | 作用 |
| :--- | :--- | :--- |
| 要求提交计划 | `handle_request_plan` | Leader 给队友发“请先提交计划”的消息。 |
| 提交计划 | `submit_plan` | 队友创建 `plan_approval` 请求。 |
| 暂停执行 | `waiting_approval` | 防止队友未获批就继续写代码。 |
| 审批计划 | `handle_review_plan` | Leader 更新 request 状态并发回审批响应。 |
| 继续执行 | `_handle_teammate_inbox_message` | 队友收到 `plan_approval_response` 后恢复工作。 |

## 5. 不同角色的工具明细

由于增加了团队协议，那么也是通过工具函数的方式来赋予不同角色相关的功能：

### Leader 工具

| 工具              | 用途                                         | 是否创建 `request_id`             |
|-------------------|----------------------------------------------|-----------------------------------|
| `spawn_teammate`  | 创建队友线程，只等待 inbox，不立即干活。     | 否                                |
| `send_message`    | 普通派发消息。                               | 否                                |
| `check_inbox`     | 读取 Leader inbox，并先路由协议响应。        | 否                                |
| `request_plan`    | 要求队友先提交计划。                         | 间接：队友 `submit_plan` 时创建   |
| `review_plan`     | 审批或拒绝已有计划。                         | 否，使用已有 `request_id`         |
| `request_shutdown`| 请求队友优雅关闭。                           | 是                                |
| `shutdown_response`| 查询关闭请求状态。                          | 否                                |

### Teammate（员工） 工具

| 工具                                  | 用途                                  | 是否创建 `request_id`          |
|---------------------------------------|---------------------------------------|--------------------------------|
| `send_message`                        | 给 Leader 或其他队友发普通消息。      | 否                             |
| `submit_plan`                         | 提交计划给 Leader 审批。              | 是                             |
| `shutdown_response`                   | 响应关闭请求。                        | 否，使用已有 `request_id`      |
| `read_file等` | 执行实际工作。                        | 否                             |

总结一下，Agent团队协作和团队协议主要服务于更大项目、更稳定、可追溯、可复现的功能实现的，高可靠与稳定是Agent发展的一个重要趋势，包括Claude Code、Codex等主流的Agent软件基本上都在走更可靠的路线。
