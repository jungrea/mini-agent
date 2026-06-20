[toc]

# s04_子agent
> 前言：本系列尽可能详细解读mini agent项目的各个原始基础功能部分，旨在对大模型的认知更近一步，项目github参考（喜欢请给该项目一个🌟以支持）：
https://github.com/jungrea/mini-agent

## 1. 什么时候需要子agent
子agent的内容比较简单，理解了它面临的问题后就很容易理解为什么需要子agent。

我们知道在一个agent的会话中，随着对话次数的增加，上下文就越来越长，再对话的时候，模型总是携带着之前信息，这个时候你让它处理一个和之前对话相关的任务的时候，那么之前的对话内容反而成了干扰信息，那么一个办法是，新开一个全新对话，让它解决刚才的问题不就好了？ 这样确实可以，但是我们有时候又不新开对话怎么办？ 这就是在一个对话中让某个任务以子agent的形式在同一个对话内完成。

其中子agent和新开一个对话的agent完全一样，有完全相同的初始化promot，没有历史上下文，区别仅在于一个是在已有对话框开，一个是新开。

那么如何在一个已有的对话框中让其再运行一个agent呢？方法和上节的多工具系统一样，也注册一个工具，可以叫做task，那么当大模型调用了这个工具的时候，封装一个子函数，在该函数中重新调用一遍具有初始promot的大模型请求即可。

## 2. 子agent的框架
一个子agent框架如：
```text
Parent agent
  |
  | 1. 决定把一个局部任务外包出去
  v
Subagent
  |
  | 2. 在自己的上下文里读文件 / 搜索 / 执行工具
  v
Summary
  |
  | 3. 只把最终摘要或结果带回父智能体
  v
Parent agent continues
```
子智能体的核心：有一个全新的上下文干净的大模型做一次性的任务处理。

前面说子agent的实现方式是以工具实现的，具体是如何做的呢？

### 2.1 给大模型看到的工具TOOLS
专门为子agent定义一个工具，并配好工具的描述，告诉大模型什么时候要使用这个工具即可，简单的模式如下：
```python
{
    "name": "task",
    "description": "Run a subtask in a clean context and return a summary.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"}
        },
        "required": ["prompt"]
    }
}
```
可以看到这里定义了工具名称`task`和描述以及输入参数，那么当agent判断要调用这个工具的时候就会使用。

### 2.2 具体执行的脚本
有了给大模型看的工具，当大模型返回了一个工具调用请求：“task”后，需要代码块去具体执行相关函数的，那么负责具体执行函数的部分，可以定义一个：`run_subagent(prompt: str)`,即输入一个用户问题promot，返回一个结果，内部实现如下：
```
def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]  # fresh context
    for _ in range(30):  # safety limit
        response = client.messages.create(
            model=MODEL, system=SUBAGENT_SYSTEM, messages=sub_messages,
            tools=CHILD_TOOLS, max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)[:50000]})
        sub_messages.append({"role": "user", "content": results})
    # Only the final text returns to the parent -- child context is discarded
    return "".join(b.text for b in response.content if hasattr(b, "text")) or "(no summary)"
```
看代码可以发现，这一段其实相当于新起了一个对话窗口`client.messages.create`,里面新的初始化 SUBAGENT_SYSTEM，且有新的`sub_messages`,这是一个仅有当前对话而没有历史上下文的prompt。之后最多循环30次进行子agent的运行（超过30次只能强行取最后结果了）。
```
SYSTEM = f"You are a coding agent at {WORKDIR}. Use the task tool to delegate exploration or subtasks."
SUBAGENT_SYSTEM = f"You are a coding subagent at {WORKDIR}. Complete the given task, then summarize your findings."
```
有个问题，子agent还能再包子agent吗？理论上可以，不过这里没有。因为`TOOL_HANDLERS.get(block.name)`这里内部没有包含子agent的工具。原因如下。

### 2.3 子agent在大循环中如何被调起
前面说子agent是以工具的形式被调用的。因此如果要被调用，大模型在一次回答中势必会包括：
* block.type == "tool_use"
* block.name == "task"

同时满足这两个才是说要调子agent。那么一个完整的agent_loop一次大循环中的代码如下：
```
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=PARENT_TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"》当前调用的工具为: {block.name}")
                if block.name == "task":
                    desc = block.input.get("description", "subtask")
                    prompt = block.input.get("prompt", "")
                    print(f"> task ({desc}): {prompt[:80]}")
                    output = run_subagent(prompt)
                else:
                    handler = TOOL_HANDLERS.get(block.name)
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                print(f"  {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
        messages.append({"role": "user", "content": results})
```
从这里可以看到，在主agent_loop中，对于命中了`tool_use`，是分开处理`子agent`和`普通工具`，而在上面的`run_subagent`中则只有普通工具，没有再继续嵌套`子agent`。

这里我把中间结果显示出来，可以看看一个例子中，大模型究竟如何运作的。

## 3. 一个具体例子
我们指定agent使用task随便执行一个任务。如：
```
s04 >> 用 task 工具派一个 subagent 去当前目录下新建一个test3.py,随便写内容    
    
》当前调用的工具为: task
> task (新建 test3.py 并写入内容): 请在 /work/learn-claude-code-main 目录下新建一个 test3.py 文件，并在其中写入
  文件已成功创建并执行，确认如下：

- **文件路径**: `/work/learn-claude-code-main/test3.py`
- **文件内容**: `print("hello from test3")`
- **执行结果**: 输出 `hello from test3`，运行正常 ✅
subagent 已成功完成任务！具体如下：

### ✅ 新建 `test3.py` 完成

| 项目 | 内容 |
|------|------|
| **文件路径** | `/work/learn-claude-code-main/test3.py` |
| **文件内容** | `print("hello from test3")` |
| **运行测试** | ✅ 成功输出 `hello from test3`，运行正常 |

文件已创建并验证通过！

```
可以看到的是确实走的是` block.name == "task" `支路，并正常执行了。

那么关于子agent的完整路径成功运行。






