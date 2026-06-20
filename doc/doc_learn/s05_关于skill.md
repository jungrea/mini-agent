# s05_关于skill

> 前言：本系列尽可能详细解读mini agent项目的各个原始基础功能部分，旨在对大模型的认知更近一步，项目github参考（喜欢请给该项目一个🌟以支持）：
https://github.com/jungrea/mini-agent

本节详细拆解skill在agent中的使用方法。skill能力就目前来看是agent超强能力中非常重要的部分，通过skill可以引入很多外部各种各样的能力，直接让大模型能处理的任务上了一个台阶。了解skill如何一步步在agent调用中发挥作用对于如何好的使用skill至关重要。

## 1. 什么是skill

skill其实就是把某些知识、流程、SOP、工具调用和约束封装成可复用、可触发、可组合的 “能力模块（Skill）”，让大模型可以基于skill的内容进行规范化的回答。

为什么需要这个skill？

试想一下，你有一个比较大的文件，你不可能直接把这个文件当成上下文以对话方式给大模型，那样太占上下文。但是你又想在问某些特定问题、或者执行某些特定操作的时候让大模型知道有这个东西，那么skill就能完美解决这个问题。

所以一个skill通常会告诉 agent：
- 什么时候该用它
- 做这类任务时具体要怎么做，有哪些步骤，有哪些注意事项等；

## 2. skill如何实现让大模型知道且不全部加载的

我们知道，一个完整skill的内容其实通常也是很长的，如果你要在后续使用，那么在加载的时候势必要加载进去，但是如果全部内容加载到大模型，上下文占用又非常长。那么如何解决？这就引出了skill的第一个核心问题：** 初始化只加载非常有限的概要说明。**

虽然完整的skill内容很长，但是agent初始化就加载一小段主要说明，这个说明主要完成的任务是：让大模型知道什么时候该用它；通常这个说明在`SKILL.md`的最开头的一段位置。

比如说一个`code review`的skill，它的skill.md如下：

```
---
name: code-review
description: Perform thorough code reviews with security, performance, and maintainability analysis. Use when user asks to review code, check for bugs, or audit a codebase.
---

# Code Review Skill

You now have expertise in conducting comprehensive code reviews. Follow this structured approach:

## Review Checklist

### 1. Security (Critical)

。。。省略剩下几百行
```
那么这里最开头的部分才会加载到模型中。加载的形式则是将开头的部分以追加到`system promot`的方式加入。这样大模型基于这样的一个简介描述来决定某个任务是否可以使用某个skill来完成。

所以有了skill功能后，一个对话系统在初始化系统提示词的时候，就需要将所有skill的简要内容都追加进去，也就是`system=SYSTEM`这里，将会多出更多内容，其中就包括所有skill的简要。

## 3. 如何使用skill
那么大模型如何使用呢？
一般来说当用户某个提问命中了某个skill的说明，或者用户就点名到姓要某个skill来处理提问的时候，大模型就会加载完整skill内容。这里就涉及到了一个辅助工具函数：`load_skill`。

没错又是工具，我们需要在工具TOOLS中再注册一个工具：`load_skill`，当大模型说要调用load_skill工具的时候，这个函数就执行查阅某个skill全部内容并返回给大模型看。大模型此时就是基于skill的全部内容进行做下一步打算了。

load_skill内部实现的感恩就是基于某个skill名称/路径，完整返回该skill的全部内容。

好了有了以上内容，我们再来捋一下skill在agent中如何使用的：
* 首先system prompt初始化的时候，在原来的基础上，追加所有skill的说明书内容。如：
```text
system prompt
  |
  +-- Skills available:
      - code-review: review checklist
      - git-workflow: branch and commit guidance
      - mcp-builder: build an MCP server
```
* 其次使用的时候，当用户的对话命中了某skill的说明，必须得使用某skill来处理用户问题的时候，模型就执行加载该skill的工具函数，实现把全部内容给大模型让大模型继续判断如何进行下一步的动作。
```text
load_skill("code-review")
   |
   v
tool_result
   |
   v
<skill name="code-review">
完整审查说明
</skill>
```

## 4. 具体实现
有了以上2步，现在看看如何。
首先定义一个skill的类，完成后续各个需求，类中包括函数：
```
class SkillRegistry:
    # 初始化，给定skill路径，加载路径下所有skill
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.documents: dict[str, SkillDocument] = {}
        self._load_all()
    
    # 加载所有skill以及内容
    def _load_all(self) -> None:
        ```
    # 辅助函数：将skill.md的内容分为【简要内容】& 【详细内容】
    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        ```
    # 得到所以skill的【简要内容】集合
    def describe_available(self) -> str:
        ```
    # 得到某个skill的【详细内容】集合
    def load_full_text(self, name: str) -> str:
        ```
```
skill类中包含了上述步骤用到的所有内容，包括入prompt和后续作为工具的调用返回。

### 4.1 初始化入prompt
看具体代码位置：
```
SKILL_REGISTRY = SkillRegistry(SKILLS_DIR)

SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use load_skill when a task needs specialized instructions before you act.

Skills available:
{SKILL_REGISTRY.describe_available()}
"""
```
有了这个prompt，大模型就具备了解skill的大致功能的能力。

### 4.2 加入到工具系统
如同读写功能的TOOLS注册调用一样，这里也有一个load_skill工具的具体执行函数，执行的动作也非常简单，就是根据具体skill的名称，把它的全部内容捞出来给大模型即可。
```
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "load_skill": lambda **kw: SKILL_REGISTRY.load_full_text(kw["name"]),
}
```

### 4.3 给大模型看的TOOLS
当然还需要一个给大模型看的TOOLS。这里只贴和skill相关的，如下，TOOLS作为输送给大模型看的东西，当大模型对【用户的问题 + system_prompt + TOOLS】这三者内容一拼接并理解后，输出一个动作：执行某个工具skill，再完成skill的调用。
```
TOOLS = [
    {
        "name": "load_skill",
        "description": "Load the full body of a named skill into the current context.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]
```

### 4.4 大模型后续的动作
以上执行完，大模型相当于在本轮才正式加载了某个skill内容的全部内容，然后基于理解的内容作出下一轮的动作。

理解了以上4个步骤过程就能发现，为什么说大模型对skill的调用是【不需要占用很大上下文】、【渐进式加载内容】。

## 5. 看看主循环以及一个例子
最后看下一轮`agent_loop`主循环是如何使用和运行的：
```
def agent_loop(messages: list) -> None:
    loopnum = 0
    while True:
        loopnum += 1
        print(f"\n新的一次循环{loopnum}")
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"》当前调用的工具为: {block.name}")
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
            except Exception as exc:
                output = f"Error: {exc}"

            print(f"> {block.name}: {str(output)[:200]}")
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output),
            })

        messages.append({"role": "user", "content": results})
```
其实没什么东西，都已经封装到了工具函数中了。

看一个实例：我们想使用 `Code Review Skill` 对某个代码做审核，我们可以这么问，然后看看具体调用的工具链路：

```
s05 >> 请使用skill技能对当前目录下的 agents/s02_tool_use.py 做一次代码审查

新的一次循环1
》当前调用的工具为: load_skill
> load_skill: <skill name="code-review">
# Code Review Skill

You now have expertise in conducting comprehensive code reviews. Follow this structured approach:

## Review Checklist

### 1. Security (Critical)

Chec
》当前调用的工具为: read_file
> read_file: #!/usr/bin/env python3
# Harness: tool dispatch -- expanding what the model can reach.
"""
s02_tool_use.py - Tool dispatch + message normalization

The agent loop from s01 didn't change. We added tool

新的一次循环2
》当前调用的工具为: bash
> bash: (no output)
》当前调用的工具为: bash
> bash: Name: anthropic
Version: 0.92.0
Summary: The official Python library for the anthropic API
Home-page: 
Author:
》当前调用的工具为: bash
> bash: 198 agents/s02_tool_use.py

新的一次循环3
》当前调用的工具为: bash
> bash: Name: python-dotenv
Version: 1.2.1
Summary: Read key-value pairs from a .env file and set them as environment variables
Home-page: 
Author:

新的一次循环4
以下是完整的代码审查报告：

---

## Code Review: `agents/s02_tool_use.py`

。。。此处省略一堆审计结果。。。

```
我们重点看到，成功加载了skill，同时加载了skill中的完整内容到对话中，后续又有几轮循环，基于skill完整内容做了代码审计。

好了，skill基础篇就到这。skill的内容远不止这些，比如说skill还能渐进式加载知识库，渐进式执行脚本，等等功能。包括当前agent已经进化到了`Hermes Agent`，其中一个核心功能是可以自动化帮忙把内容沉淀为skill的功能，可以说skill已经是agent一个非常核心的功能了。







