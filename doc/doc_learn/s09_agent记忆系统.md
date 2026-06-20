# s09_agent记忆系统

记忆系统可以说是Agent中非常重要的功能，好的记忆系统直接可以拉高Agent的一个能力。本节我们详细拆解一下该项目中的记忆系统。

## 1. 为什么需要记忆系统
在与大模型的对话过程中，总会有一些外部的知识不适合写在系统prompt里面但是又希望Agent每次对话能够记住，比如说用户的习惯、历史上多次出现的错误、个人项目的一些约定等等情况，如果一个 agent 每次新会话都完全从零开始，它就会不断重复忘记这些事情，那么如果我们希望Agent能够记住历史上的一些用户习惯或者是约定，最好的方式就是给大模型增加记忆系统。

## 2. 记忆系统说明
我们直接给出该教程中存在的4类记忆数据，如下：

| 类型 | 标签 | 含义 | 典型场景 |
| ---- | ---- | ---- | ---- |
| user | user | 用户偏好 | "我喜欢 tabs"、"始终用 pytest" |
| feedback | feedback | 用户纠正 | "别这样做"、"那样做是因为..." |
| project | project | 非显而易见的项目事实 | 合规原因存在的规则、遗留模块不能碰的业务原因 |
| reference | reference | 外部资源指针 | 工单板 URL、监控面板、文档链接 |

这是一些适合使用记忆系统来存储的数据。那还有一些数据其实不适合用记忆系统，包括如下：
| 不要存的东西 | 为什么 |
|---|---|
| 文件结构、函数签名、目录布局 | 这些可以重新读代码得到 |
| 当前任务进度 | 这属于 task / plan，不属于 memory |
| 临时分支名、当前 PR 号 | 很快会过时 |
| 修 bug 的具体代码细节 | 代码和提交记录才是准确信息 |
| 密钥、密码、凭证 | 安全风险 |

## 3. 代码实现方案
我们说记忆系统里面的数据并不是用户主动写的，而是系统在运行的过程中自动检测到了哪些很重要，然后系统自己写入的数据。那么这里就有两个问题需要明确：
* 1. 系统是如何检测到哪些内容很重要的，并且需要存储为记忆的？
* 2. 系统存储的记忆又放在哪个地方？

先说第一个问题，Agent如何检测到了重要内容，并且要存储为记忆的。这里教程使用的方式又是**使用一个工具函数来实现的**。也就是说我们给它提供的入口，由大模型自己来判断。如果它判断为需要保存为记忆，那么它就调用这个存储为记忆的工具。

对于第二个问题，存储的记忆自然是存在了本地电脑的磁盘里面，只有这样才能实现永久化的存储记忆，也才能够实现下一次我们重新打开一个对话窗口的时候，agent能够记住这些记忆。而存储的位置则是我们通过工具函数里面定义好来实现的。

运行的模式可以由下图所示：
```text
conversation
   |
   | 用户提到一个长期重要信息
   v
save_memory
   |
   v
.memory/
  ├── MEMORY.md        # 索引
  ├── prefer_tabs.md
  ├── feedback_tests.md
  └── incident_board.md
   |
   v
下次新会话开始时重新加载
```

### 3.1 记忆模块的工具函数-给agent看的

首先是为记忆模块制作一个工具函数。关于工具的制作，想必大家非常熟悉了，之前的章节已经做过了很多次的工具。这里直接给出代码如下：

```
TOOLS = [
  {
    "name": "save_memory",
    "description": "Save a persistent memory that survives across sessions.",
    "input_schema": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "Short identifier (e.g. prefer_tabs, db_schema)"
        },
        "description": {
          "type": "string",
          "description": "One-line summary of what this memory captures"
        },
        "type": {
          "type": "string",
          "enum": [
            "user",
            "feedback",
            "project",
            "reference"
          ],
          "description": "user=preferences, feedback=corrections, project=non-obvious project conventions or decision reasons, reference=external resource pointers"
        },
        "content": {
          "type": "string",
          "description": "Full memory content (multi-line OK)"
        }
      },
      "required": [
        "name",
        "description",
        "type",
        "content"
      ]
    }
  }
]
```

```
TOOL_HANDLERS = {
    "save_memory":  lambda **kw: run_save_memory(kw["name"], kw["description"], kw["type"], kw["content"]),
}
```
### 3.2 记忆模块工具函数的具体实现
对于记忆模块也有一个专门的类来统一处理，这个类里面至少包含以下元素：
```
class MemoryManager:
    """
    记忆管理器：跨会话加载、构建、保存持久化记忆。
    记忆显式化：每条记忆一个 Markdown 文件，外加一个紧凑的索引文件。
    """

    def __init__(self, memory_dir: Path = None):
        self.memory_dir = memory_dir or MEMORY_DIR
        self.memories = {}  # name -> {description, type, content}

    def load_all(self):
        """加载 MEMORY.md 索引和所有单独的记忆文件。"""

    def load_memory_prompt(self) -> str:
        """构建记忆段落，用于注入到 system prompt 中。"""

    def save_memory(self, name: str, description: str, mem_type: str, content: str) -> str:
        """
        保存一条记忆到磁盘，并更新索引。
        """

    def _rebuild_index(self):
        """从当前内存状态重建 MEMORY.md，上限 200 行。"""

    def _parse_frontmatter(self, text: str) -> Optional[dict]:
        """解析 --- 界定的 frontmatter + 正文内容。"""
```
从这个类的定义函数可以看到，两个函数是最重要的：第一个就是Save Memory这个函数，也就是我们的工具调用所要触发的一个函数。另外一个就是load memory的这个，也就是当我们有新的内容增加到Memory里面以后，那么在下一次对话时，就是通过load的这个函数动态的把它注入到system prompt里面，实现记忆快速生效。

下面重点看这两个函数的一个具体实现。

- save_memory 函数

```
def save_memory(self, name: str, description: str, mem_type: str, content: str) -> str:
  """
  保存一条记忆到磁盘，并更新索引。
  返回状态消息。
  """
  if mem_type not in MEMORY_TYPES:
      return f"Error: type must be one of {MEMORY_TYPES}"
  
  # 清理名称，使其可用作文件名
  safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())
  if not safe_name:
      return "Error: invalid memory name"
  
  self.memory_dir.mkdir(parents=True, exist_ok=True)
  
  # 写入带 frontmatter 的独立记忆文件
  frontmatter = (
      f"---\n"
      f"name: {name}\n"
      f"description: {description}\n"
      f"type: {mem_type}\n"
      f"---\n"
      f"{content}\n"
  )
  file_name = f"{safe_name}.md"
  file_path = self.memory_dir / file_name
  file_path.write_text(frontmatter)
  
  # 更新内存中的存储
  self.memories[name] = {
      "description": description,
      "type": mem_type,
      "content": content,
      "file": file_name,
  }
  
  # 重建 MEMORY.md 索引
  self._rebuild_index()
  
  return f"Saved memory '{name}' [{mem_type}] to {file_path.relative_to(WORKDIR)}"
```
从函数里面可以看到，这个函数包括以下几个过程，首先为一条记忆取一个名字，其次写入格式化的头文件，之后更新到内存里面，再到最后重建一下Memory.md(这个主要是优化合并当前记忆使用).

比如说我给Agent取了一个名字，并让它记住这个名字,它就会生成一个agent_name_jar.md文件，内容如下：

```
---
name: agent_name_jar克斯
description: 用户给我取名叫贾克斯
type: user
---
用户给我取了一个中文名字叫"贾克斯"，对应的拼音是 Jia Ke Si。以后用户叫我贾克斯时，代表是在称呼我。

```

- load_memory_prompt 函数

load memory的功能主要是把.memory目录下面生成的所有记忆md文件读出来，加载到prompt里面。

```
def load_memory_prompt(self) -> str:
    """构建记忆段落，用于注入到 system prompt 中。"""
    if not self.memories:
        return ""

    sections = []
    sections.append("# Memories (persistent across sessions)")
    sections.append("")

    # 按类型分组，提升可读性
    for mem_type in MEMORY_TYPES:
        typed = {k: v for k, v in self.memories.items() if v["type"] == mem_type}
        if not typed:
            continue
        sections.append(f"## [{mem_type}]")
        for name, mem in typed.items():
            sections.append(f"### {name}: {mem['description']}")
            if mem["content"].strip():
                sections.append(mem["content"].strip())
            sections.append("")

    return "\n".join(sections)
```
那么load_memory_prompt函数在哪使用呢？很简单，在系统prompt里面使用。
```
def build_system_prompt() -> str:
    """组装 system prompt，注入记忆内容。"""
    parts = [f"You are a coding agent at {WORKDIR}. Use tools to solve tasks."]

    # 注入记忆内容（如果有的话）
    memory_section = memory_mgr.load_memory_prompt()
    if memory_section:
        parts.append(memory_section)

    parts.append(MEMORY_GUIDANCE)
    return "\n\n".join(parts)

之后有一块：

system = build_system_prompt()
response = client.messages.create(
    model=MODEL, system=system, messages=messages,
    tools=TOOLS, max_tokens=8000,
)
```

## 4. 串一下主函数以及样例演示
那么记忆的核心内容我们就说完了，现在我们看一下在主循环里面怎么使用。
```
def agent_loop(messages: list):
    """
    带记忆感知的 Agent 主循环。
    每次调用都重建 system prompt，确保新保存的记忆在同一会话的下一轮 LLM 调用中可见。
    """
    while True:
        system = build_system_prompt()
        response = client.messages.create(
            model=MODEL, system=system, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**(block.input or {})) if handler else f"Unknown: {block.name}"
            except Exception as e:
                output = f"Error: {e}"
            print(f"> {block.name}: {str(output)[:200]}")
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output),
            })

        messages.append({"role": "user", "content": results})
```
一个就是动态加载记忆模块，另外一个就是融合到了工具函数里面，当大模型匹配到要存记忆的时候，就会调这个记忆存储工具。

下面是刚才的演示：
```
s09 >> 你叫什么名字？
我叫贾克斯（Jia Ke Si）！这是你给我取的中文名字，我很喜欢。有什么我可以帮你的吗？

s09 >> 编码的时候，我喜欢使用Tab键，请记住。
> save_memory: Saved memory 'prefer_tabs' [user] to .memory/prefer_tabs.md
已记住！以后编码时我会使用 Tab 键进行缩进。

s09 >> 你现在有名字了，现在呢，我需要给你设置一个性格，我希望你幽默一点。
好的，收到！以后我会尽量幽默一点，争取让你在 coding 的时候不仅能解决问题，还能笑一笑 😄

不过我得提醒你，我的幽默感可能有点像程序员笑话——有时候会冷到需要穿件外套 🧥🤣

有什么任务要开始吗？

s09 >> 请把你的这个设定增加到你的记忆 文件里面
> save_memory: Saved memory 'agent_personality' [user] to .memory/agent_personality.md
搞定！我已经把你的名字设定「贾克斯」和性格设定「幽默风趣」都存好了。以后我就是那个叫贾克斯、说话带点幽默感的 AI 助手了——不过请放心，代码质量不会打折，只是可能会多几句俏皮话 🎤😎
```

看看我们记忆系统的目录：

```
.memory/
├── agent_name_jar.md
├── agent_personality.md
├── indentation_style.md
├── MEMORY.md
├── prefer_tabs.md
└── use_pytest.md
```
可以看到，每次你希望Agent增加记忆的时候，它就会写一条记忆MD文件。

## 5. 记忆一直增加的问题
可能你会发现如果我们一直增加记忆的时候，记忆的MD文件就会越来越多，因此，最好的处理方式是，有一些办法把相似的记忆汇总起来，或者是对于无效的记忆做去除。

这里可以使用一个新的类函数，实现的功能就是：定期复盘、整理、精简记忆。

核心处理流程包括：
* 定时化的启动运行；
* 读取所有的记忆MD文件；
* 通过调用大模型的方式，对记忆文件做合并处理；
* 对最终的结果再写回到Memory的目录下面。

经过以上的流程之后，记忆模块就可以保持在一个可控的量级上。









