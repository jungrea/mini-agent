# s01_agent_loop.py 脚本详解

## 概述

`s01_agent_loop.py` 是一个最小化的编码代理（coding agent）实现，展示了 agent 与大模型交互的核心循环模式。这个脚本是整个 agent 教学项目的基础，通过简洁的代码结构，帮助初学者理解 agent 系统的工作原理。

## 核心概念

### Agent 循环模式

Agent 循环是指 agent 与大模型之间的交互过程，基本流程如下：

```
用户输入 → 模型回复 → 工具执行 → 结果反馈 → 重复
```

这种循环模式使 agent 能够：
1. 理解用户意图
2. 通过工具与外部环境交互
3. 根据交互结果继续推理
4. 最终完成用户任务

## 程序结构

### 文件结构

```
s01_agent_loop.py
├── 导入模块
├── 配置初始化
├── 系统提示和工具定义
├── 数据结构定义 (LoopState)
├── 工具执行函数 (run_bash)
├── 核心循环函数
│   ├── extract_text
│   ├── execute_tool_calls
│   ├── run_one_turn
│   └── agent_loop
└── 主程序
```

### 简化执行流程图

如果上面的流程图渲染有问题，这里提供一个更简洁美观的基于文本的简化版：

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   用户输入      │────▶│ 创建对话历史    │────▶│ 初始化LoopState │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                        │
                                                        ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   显示结果      │◀────│   结束循环      │◀────│  启动agent_loop │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                        │
                                                        ▼
                                      ┌──────────────────────────────┐
                                      │                              │
                          ┌───────────▼───────────┐                  │
                          │    调用大模型API      │                  │
                          └───────────┬───────────┘                  │
                                      │                              │
                          ┌───────────▼───────────┐                  │
                          │  是否工具调用？       │                  │
                          └──────┬────────┬───────┘                  │
                                 │        │                         │
                       否        │        │ 是                      │
                                 ▼        ▼                         │
                  ┌────────────────┐  ┌────────────────┐            │
                  │   结束循环     │  │  执行工具调用   │            │
                  └────────────────┘  └────────┬────────┘            │
                                              │                     │
                                              ▼                     │
                                      ┌────────────────┐            │
                                      │ 添加工具结果   │            │
                                      └────────┬───────┘            │
                                               │                    │
                                               ▼                    │
                                      ┌────────────────┐            │
                                      │ 更新循环状态   │────────────┘
                                      └────────────────┘
```

### 流程图说明

1. **初始化阶段**：用户输入 → 创建对话历史 → 初始化 LoopState → 启动 agent_loop
2. **循环阶段**：
   - 调用大模型 API
   - 判断是否需要工具调用
   - 如果是，执行工具调用 → 添加工具结果 → 更新循环状态 → 回到调用大模型 API
   - 如果否，结束循环
3. **结束阶段**：显示最终结果

## 核心组件详解

### 1. 配置初始化

```python
load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
```

**功能**：
- 加载环境变量配置
- 创建 Anthropic 客户端（支持 DeepSeek 等兼容 API）
- 设置使用的模型 ID

### 2. 系统提示和工具定义

```python
SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash to inspect and change the workspace. Act first, then report clearly."
)

TOOLS = [{
    "name": "bash",
    "description": "Run a shell command in the current workspace.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]
```

**功能**：
- `SYSTEM`：告诉模型它的角色和任务
- `TOOLS`：定义 agent 可以使用的工具（这里是 bash 命令执行工具）

### 3. LoopState 数据类

```python
@dataclass
class LoopState:
    # The minimal loop state: history, loop count, and why we continue.
    messages: list
    turn_count: int = 1
    transition_reason: Optional[str] = None
```

**功能**：
- 跟踪对话历史（`messages`）
- 记录循环次数（`turn_count`）
- 标记状态转换原因（`transition_reason`）

### 4. 工具执行函数

#### run_bash 函数

```python
def run_bash(command: str) -> str:
    """
    执行bash命令并返回结果
    
    Args:
        command: 要执行的bash命令
    
    Returns:
        命令执行的输出结果，如果命令危险或执行失败则返回错误信息
    """
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

    output = (result.stdout + result.stderr).strip()
    return output[:50000] if output else "(no output)"
```

**功能**：
- 执行 bash 命令
- 安全检查：阻止危险命令
- 错误处理：处理超时和其他异常
- 输出限制：限制输出长度为 50000 字符

注意一个细节，在bash工具中，其实相当于执行的是命令行的命令，如果大模型执行了一个删除类命令，其实是很危险的，一次bash具体负责执行的时候，是排掉了高危命令的。

### 5. 核心循环函数

#### extract_text 函数

```python
def extract_text(content) -> str:
    """
    从模型响应内容中提取文本
    
    Args:
        content: 模型响应的内容列表
    
    Returns:
        提取的文本内容，多个文本块会用换行符连接
    """
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()
```

**功能**：
- 从模型响应中提取文本内容
- 处理模型响应的结构化格式

#### execute_tool_calls 函数

```python
def execute_tool_calls(response_content) -> list[dict]:
    """
    执行模型请求的工具调用
    
    Args:
        response_content: 模型响应的内容列表
    
    Returns:
        工具执行结果的列表，每个结果包含type、tool_use_id和content字段
    """
    results = []
    for block in response_content:
        if block.type != "tool_use":
            continue
        command = block.input["command"]
        print(f"\033[33m$【此处调用了工具将执行bash命令】 {command}\033[0m")
        output = run_bash(command)
        print(f"【工具执行结果】 {output[:200]}")
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
        })
    return results
```

**功能**：
- 识别模型的工具调用请求
- 执行相应的工具（这里是 bash 命令）
- 收集工具执行结果
- 格式化结果为模型可理解的格式

#### run_one_turn 函数

```python
def run_one_turn(state: LoopState) -> bool:
    """
    执行一轮agent循环
    
    Args:
        state: 循环状态对象，包含对话历史等信息
    
    Returns:
        如果需要继续循环则返回True，否则返回False
    """
    print(f"本轮输入内容：{state.messages[-1]['content'][:200]}...")
    response = client.messages.create(
        model=MODEL,
        system=SYSTEM,
        messages=state.messages,
        tools=TOOLS,
        max_tokens=8000,
    )
    state.messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason != "tool_use":
        print(f"【模型回复】 {extract_text(response.content)} !!! 工具状态：{response.stop_reason}，已经结束工具调用")
        state.transition_reason = None
        return False
    
    print(f"【模型回复】 {extract_text(response.content)} 工具状态：{response.stop_reason}")
    results = execute_tool_calls(response.content)
    if not results:
        state.transition_reason = None
        return False

    state.messages.append({"role": "user", "content": results})
    state.turn_count += 1
    state.transition_reason = "tool_result"
    return True
```

**功能**：
- 调用大模型 API 获取响应
- 分析模型响应，判断是否需要执行工具
- 执行工具调用并获取结果
- 更新对话历史和循环状态
- 决定是否继续循环

#### agent_loop 函数

```python
def agent_loop(state: LoopState) -> None:
    """
    持续执行agent循环
    
    Args:
        state: 循环状态对象，包含对话历史等信息
    """
    print(f"\n >>> 当前第 {state.turn_count} 轮循环，状态： {state.transition_reason}")
    while run_one_turn(state):
        print(f"\n >>> 当前第 {state.turn_count} 轮循环，状态： {state.transition_reason}")
        pass
```

**功能**：
- 持续执行 agent 循环，直到模型不再请求工具调用
- 显示循环状态信息

### 6. 主程序

```python
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})
        state = LoopState(messages=history)
        agent_loop(state)

        print(f"\n >>> 本轮对话循环结束，下面是模型最终回复内容：")
        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
```

**功能**：
- 处理用户输入
- 创建对话历史
- 初始化 LoopState 对象
- 启动 agent 循环
- 显示模型的最终回复

## 执行流程分析

### 1. 初始化阶段

1. 加载环境变量配置
2. 创建 Anthropic 客户端
3. 定义系统提示和工具

### 2. 主循环阶段

1. **用户输入**：用户在终端中输入命令或问题
2. **创建对话历史**：将用户输入添加到历史记录
3. **初始化 LoopState**：创建包含对话历史的状态对象
4. **启动 agent 循环**：调用 `agent_loop` 函数

### 3. Agent 循环阶段

1. **调用大模型**：向模型发送对话历史和工具定义
2. **分析响应**：检查模型是否请求执行工具
3. **执行工具**：如果是工具调用，执行相应的命令
4. **处理结果**：将工具执行结果添加到对话历史
5. **继续循环**：重复上述过程，直到模型完成回答

### 4. 结束阶段

1. **显示结果**：显示模型的最终回复
2. **等待新输入**：回到主循环，等待用户的下一个输入

## 输入输出示例

### 示例 1：列出目录内容

#### 输入
```
s01 >> 列出当前目录的文件
```

#### 输出
```
 >>> 当前第 1 轮循环，状态： None
本轮输入内容：列出当前目录的文件...
【模型回复】 我需要列出当前目录的文件，让我使用bash工具来执行这个操作。 工具状态：tool_use
$【此处调用了工具将执行bash命令】 ls -la
【工具执行结果】 total 80
drwxr-xr-x  19 user  staff   608 Apr 10 10:00 .
drwxr-xr-x   3 user  staff    96 Apr  9 15:00 ..
-rw-r--r--   1 user  staff   544 Apr 10 09:50 .env
-rw-r--r--   1 user  staff   116 Apr  9 15:00 .env.example
-rw-r--r--   1 user  staff   194 Apr  9 15:00 .gitignore
drwxr-xr-x  20 user  staff   640 Apr  9 15:00 agents
drwxr-xr-x   3 user  staff    96 Apr  9 15:00 docs
drwxr-xr-x   5 user  staff   160 Apr  9 15:00 skills
drwxr-xr-x   3 user  staff    96 Apr  9 15:00 tests
drwxr-xr-x   4 user  staff   128 Apr  9 15:00 web
-rw-r--r--   1 user  staff  1073 Apr  9 15:00 LICENSE
-rw-r--r--   1 user  staff  1000 Apr  9 15:00 README-ja.md
-rw-r--r--   1 user  staff   980 Apr  9 15:00 README-zh.md
-rw-r--r--   1 user  staff  1010 Apr  9 15:00 README.md
-rw-r--r--   1 user  staff    45 Apr  9 15:00 requirements.txt

 >>> 当前第 2 轮循环，状态： tool_result
本轮输入内容：[{'type': 'tool_result', 'tool_use_id': 'tool_use_01H2X7Y8Z9A1B2C3D4E5F6G7H8', 'content': 'total 80\ndrwxr-xr-x  19 user  staff   608 Apr 10 10:00 .\ndrwxr-xr-x   3 user  staff    96 Apr  9 15:00 ..\n-rw-r--r--   1 user  staff   544 Apr 10 09:50 .env\n-rw-r--r--   1 user  staff   116 Apr  9 15:00 .env.example\n-rw-r--r--   1 user  staff   194 Apr  9 15:00 .gitignore\ndrwxr-xr-x  20 user  staff   640 Apr  9 15:00 agents\ndrwxr-xr-x   3 user  staff    96 Apr  9 15:00 docs\ndrwxr-xr-x   5 user  staff   160 Apr  9 15:00 skills\ndrwxr-xr-x   3 user  staff    96 Apr  9 15:00 tests\ndrwxr-xr-x   4 user  staff   128 Apr  9 15:00 web\n-rw-r--r--   1 user  staff  1073 Apr  9 15:00 LICENSE\n-rw-r--r--   1 user  staff  1000 Apr  9 15:00 README-ja.md\n-rw-r--r--   1 user  staff   980 Apr  9 15:00 README-zh.md\n-rw-r--r--   1 user  staff  1010 Apr  9 15:00 README.md\n-rw-r--r--   1 user  staff    45 Apr  9 15:00 requirements.txt'}]...
【模型回复】 我已经执行了 `ls -la` 命令，列出了当前目录的文件和目录。你可以看到主要目录包括：agents、docs、skills、tests 和 web。还有一些配置文件，如 .env 和 requirements.txt。 !!! 工具状态：end_turn，已经结束工具调用

 >>> 本轮对话循环结束，下面是模型最终回复内容：
我已经执行了 `ls -la` 命令，列出了当前目录的文件和目录。你可以看到主要目录包括：agents、docs、skills、tests 和 web。还有一些配置文件，如 .env 和 requirements.txt。
```

### 示例 2：查看文件内容

#### 输入
```
s01 >> 查看 README.md 文件的内容
```

#### 输出
```
 >>> 当前第 1 轮循环，状态： None
本轮输入内容：查看 README.md 文件的内容...
【模型回复】 我需要查看 README.md 文件的内容，让我使用bash工具来执行这个操作。 工具状态：tool_use
$【此处调用了工具将执行bash命令】 cat README.md
【工具执行结果】 # Learn Claude Code

This repository teaches how to build coding agents with Claude.

## Overview

- **s01**: The Agent Loop
- **s02**: Tool Use
- **s03**: TodoWrite
- **s04**: Subagent
- **s05**: Skill Loading
- **s06**: Context Compact
- **s07**: Permission System
- **s08**: Hook System
- **s09**: Memory System
- **s10**: System Prompt
- **s11**: Error Recovery
- **s12**: Task System
- **s13**: Background Tasks
- **s14**: Cron Scheduler
- **s15**: Agent Teams
- **s16**: Team Protocols
- **s17**: Autonomous Agents
- **s18**: Worktree Task Isolation
- **s19**: MCP Plugin

## Quick Start

1. Install dependencies: `pip install -r requirements.txt`
2. Copy .env.example to .env and fill in your Anthropic API key
3. Run an agent: `python agents/s01_agent_loop.py`

## License

MIT

 >>> 当前第 2 轮循环，状态： tool_result
本轮输入内容：[{'type': 'tool_result', 'tool_use_id': 'tool_use_01H2X7Y8Z9A1B2C3D4E5F6G7H9', 'content': '# Learn Claude Code\n\nThis repository teaches how to build coding agents with Claude.\n\n## Overview\n\n- **s01**: The Agent Loop\n- **s02**: Tool Use\n- **s03**: TodoWrite\n- **s04**: Subagent\n- **s05**: Skill Loading\n- **s06**: Context Compact\n- **s07**: Permission System\n- **s08**: Hook System\n- **s09**: Memory System\n- **s10**: System Prompt\n- **s11**: Error Recovery\n- **s12**: Task System\n- **s13**: Background Tasks\n- **s14**: Cron Scheduler\n- **s15**: Agent Teams\n- **s16**: Team Protocols\n- **s17**: Autonomous Agents\n- **s18**: Worktree Task Isolation\n- **s19**: MCP Plugin\n\n## Quick Start\n\n1. Install dependencies: `pip install -r requirements.txt`\n2. Copy .env.example to .env and fill in your Anthropic API key\n3. Run an agent: `python agents/s01_agent_loop.py`\n\n## License\n\nMIT'}]...
【模型回复】 我已经查看了 README.md 文件的内容。这是一个关于如何使用 Claude 构建编码代理的教学仓库。文件中包含了项目的概述、快速开始指南和许可证信息。概述部分列出了从 s01 到 s19 的各个章节，每个章节介绍了不同的功能，如代理循环、工具使用、任务管理等。 !!! 工具状态：end_turn，已经结束工具调用

 >>> 本轮对话循环结束，下面是模型最终回复内容：
我已经查看了 README.md 文件的内容。这是一个关于如何使用 Claude 构建编码代理的教学仓库。文件中包含了项目的概述、快速开始指南和许可证信息。概述部分列出了从 s01 到 s19 的各个章节，每个章节介绍了不同的功能，如代理循环、工具使用、任务管理等。
```

## 学习要点

### 1. Agent 循环的核心概念

- **状态管理**：使用 `LoopState` 跟踪对话状态
- **工具调用**：模型通过结构化输出请求执行工具
- **结果反馈**：工具执行结果被添加到对话历史，供模型继续推理
- **循环控制**：根据模型的 `stop_reason` 决定是否继续循环

### 2. 安全考虑

- **危险命令检查**：`run_bash` 函数会阻止危险命令的执行
- **超时处理**：命令执行有 120 秒的超时限制
- **错误处理**：优雅处理各种异常情况

### 3. 扩展方向

- **添加更多工具**：除了 bash 命令，还可以添加文件操作、网络请求等工具
- **改进状态管理**：添加更复杂的状态跟踪和管理机制
- **增强错误处理**：添加更健壮的错误恢复机制
- **优化提示工程**：改进系统提示，提高模型的性能

### 4. 技术要点

- **API 调用**：使用 Anthropic 客户端调用大模型 API
- **工具集成**：通过工具定义和执行机制，使模型能够与外部系统交互
- **对话管理**：维护对话历史，支持多轮交互
- **流程控制**：通过循环和条件判断，实现灵活的 agent 行为

## 总结

`s01_agent_loop.py` 实现了一个简洁但功能完整的 agent 循环，展示了 agent 系统的核心工作原理。通过理解这个基础实现，初学者可以掌握：

1. **Agent 循环模式**：用户输入 → 模型回复 → 工具执行 → 结果反馈 → 重复
2. **工具调用机制**：模型通过结构化输出请求执行工具
3. **状态管理**：通过 `LoopState` 跟踪对话状态
4. **安全执行**：对工具调用进行安全检查
5. **错误处理**：优雅处理各种异常情况

这个脚本是整个 agent 教学项目的基础，后续的章节会在此基础上添加更多功能，如任务管理、团队协作、技能加载等。通过学习这个基础实现，初学者可以更好地理解 agent 系统的工作原理，为构建更复杂的 agent 系统打下基础。