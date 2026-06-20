# s08_hook钩子系统

Hook功能是Agent系统中比较重要的一个功能。这一章节我们将花比较长的篇幅来详细地介绍一下Hook系统的完整实现。

## 1. 什么是hook（钩子）系统
设想一个场景，比如说你每次跟Agent对完话以后，你希望Agent帮你记一下本次对话的时间，那么你应该怎么实现呢？你可能会说，我可以在主函数的末尾增加一个代码来主动记录时间，这种方式当然可以，但是这种非常定制化的小需求，你加到了主程序里面，多少显得有点不合适。这个时候如果说有一个支路来实现这样的一个功能，同时又不干扰主函数的话，那么就非常合适了，那这也就是hook系统。

简单来说：
* Hook = 钩子 = 在程序运行的某个固定节点，插入你自己的代码，改变 / 扩展 / 监听行为，而不用改主流程。

## 2. agent中常见的 Hook 节点
Agent 主循环一般长这样：
```
┌─────────────┐
│    开始     │
└───────┬─────┘
        │
┌───────▼─────┐
│  调用模型   │
└───────┬─────┘
        │
┌───────▼─────┐
│  工具调用   │
└───────┬─────┘
        │
┌───────▼─────┐
│  执行工具   │
└───────┬─────┘
        │
┌───────▼─────┐
│    结束     │
└─────────────┘
```
那么上述的每个环节前后都是可以加钩子的，比如我们加了下面几个钩子，那么流程就可能像下面这样：
```
┌─────────────────────────┐
│         开始            │
├─────────────────────────┤
            │
            │ ←── on_agent_start        智能体启动钩子
┌───────────▼─────────────┐
│       调用模型          │
├─────────────────────────┤
            │
            │ ←── before_model_call      模型调用前钩子
┌───────────▼─────────────┐
│       工具调用          │
├─────────────────────────┤
            │
┌───────────▼─────────────┐
│       执行工具          │
├─────────────────────────┤
            │ ←── before_tool_execution  工具执行前/权限校验
            │ ←── after_tool_execution   工具执行后/日志后置
┌───────────▼─────────────┐
│         结束            │
├─────────────────────────┤
            │
            │ ←── on_agent_finish        智能体结束钩子
```
上图就显示，当程序运行到某个环节或者程序需要调用某个工具的时候，就会触发相关的钩子分支。同时运行钩子的相关命令。

## 3. Hook模块的实现
基于上图流程，我们可以将Hook系统划分为两个组成部分：
* 环节标识：即是属于哪个环节的hook支路；
* 具体的动作：即这个环节中具体的某个hook指令，必须是python或者其他环境可执行的命令（因为我们是调用系统的子函数执行命令）；

上面说的两个组成部分，就可以形成一个Hook的配置文件，那么我们想再增加不同的支路的功能，就直接在配置文件中添加就可以了。

这里我直接给了一个我们后面会用到的hook的配置，我们预埋了4个hook，功能分别如下：
* 对话开始时的hook：打印一下欢迎语和当前时间；
* 工具调用前的hook：只匹配`read_file`的工具调用，执行的命令就是简单的输出一下，调用成功。
* 工具调用后的hook：这里使用通配符，也就是说所有的工具调用完以后，都执行一下。命令就是简单的输出调用成功。
* 整轮对话结束以后的hook：一轮对话结束以后，我们记录一下本次对话的时间，然后存在一个本地文件里面。

以上是我们整个hook配置文件所实现的功能，每一个环节所能执行的命令也不止一个，也可以继续添加。那么想要实现这个功能，我们的配置文件如下，注意这个文件需要保存为项目目录下的`".hooks.json"`。

```
{
  "hooks": {
    "SessionStart": [
      {
        "command": "echo \"Welcome，Agent session started at $(date)\""
      }
    ],
    "PreToolUse": [
      {
        "matcher": "read_file",
        "command": "echo 'tool start: only run read_file use Tool.'"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "command": "echo 'tool end: each tool done.'"
      }
    ],
    "RoundEnd": [
      {
        "command": "date >> hooks_end_notes.md"
      }
    ]
  }
}
```
下面来说最重要的Hook类实现方法。
### 3.1 Hook类的结构
我们需要有一个Hook类来初始化加载我们的配置文件。同时也能处理不同阶段的命令。那么它的结构可以长下面这个样子：
```
class HookManager:
    """
    钩子管理器：从 .hooks.json 加载钩子定义并执行。

    三项职责：
    1. 加载钩子定义
    2. 按事件类型运行匹配的命令
    3. 聚合阻断 / 消息结果返回给调用方
    """

    def __init__(self, config_path: Path = None, sdk_mode: bool = False):
        # 默认从工作区根目录的 .hooks.json 读取配置

    def _check_workspace_trust(self) -> bool:
        # 检查当前工作区是否受信任。

    def run_hooks(self, event: str, context: dict = None) -> dict:
        """
        执行某个事件的所有钩子。
        参数：
            event: 事件名称（PreToolUse / PostToolUse / SessionStart / RoundEnd）
            context: 上下文字典，包含 tool_name / tool_input / tool_output 等
        返回：{"blocked": bool, "messages": list[str]}
          - blocked: 任一钩子返回退出码 1 时为 True
          - messages: 退出码 2 的钩子通过 stderr 注入的消息列表
        """
```
只需要这么几个功能。首先，初始化读取配置文件。其次，检测当前工作区是否可以使用钩子。另外，就是一个运行钩子的一个主函数。

* 初始化读取配置文件

```
def __init__(self, config_path: Path = None, sdk_mode: bool = False):
    self.hooks = {"PreToolUse": [], "PostToolUse": [], "SessionStart": [], "RoundEnd": []}
    self._sdk_mode = sdk_mode
    # 默认从工作区根目录的 .hooks.json 读取配置
    config_path = config_path or (WORKDIR / ".hooks.json")
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            for event in HOOK_EVENTS:
                self.hooks[event] = config.get("hooks", {}).get(event, [])
            print(f"[Hooks loaded from {config_path}]")
        except Exception as e:
            print(f"[Hook config error: {e}]")
```
这里的关键就是将不同环节的钩子命令提取出来存到对应的hook事件中，实现一个结果匹配不同类型hook功能，`self.hooks`

* 最关键的执行hook：`run_hooks`
原函数的代码有点长，我先分解说下这个函数中都有那些东西感觉更好理解。

（1）首先检验一下目录是否允许钩子系统，并且提取出是哪个环节(使用输入参数event)的钩子，提取其下的钩子脚本。
```
  result = {"blocked": False, "messages": []}
  # 信任门槛：不受信任的工作区不执行钩子
  if not self._check_workspace_trust():
      return result
  hooks = self.hooks.get(event, [])
```

（2）循环这个环节下的所有钩子脚本，提取匹配规则和执行命令
```
for hook_def in hooks:
    # 匹配器过滤：PreToolUse/PostToolUse 可按工具名过滤
    matcher = hook_def.get("matcher")
    if matcher and context:
        tool_name = context.get("tool_name", "")
        # "*" 表示匹配所有工具；否则必须精确匹配
        if matcher != "*" and matcher != tool_name:
            continue
    command = hook_def.get("command", "")
    if not command:
        continue
```
（3）构建一个该钩子的环境变量，记录此时这个钩子的输入啊、输出啊、环节呀等等。
 ```
  # 构建环境变量，把钩子上下文传入子进程
  env = dict(os.environ)
  if context:
      env["HOOK_EVENT"] = event
      env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
      # 工具输入 JSON 序列化，截断到 10000 字符防止环境变量溢出
      env["HOOK_TOOL_INPUT"] = json.dumps(
          context.get("tool_input", {}), ensure_ascii=False)[:10000]
      if "tool_output" in context:
          env["HOOK_TOOL_OUTPUT"] = str(
              context["tool_output"])[:10000]
   ```

（4）最后执行解析的命令common，并解析执行结果
```
try:
    # 执行钩子命令，shell 模式，限流超时
    r = subprocess.run(
        command, shell=True, cwd=WORKDIR, env=env,
        capture_output=True, text=True, timeout=HOOK_TIMEOUT,
    )

    if r.returncode == 0:
        # 退出码 0：放行，静默通过
        if r.stdout.strip():
            print(f"  [hook:{event}] {r.stdout.strip()[:100]}")

        # 钩子可通过 stdout 返回 JSON 来修改工具输入或注入额外上下文
        try:
            hook_output = json.loads(r.stdout)
            # updatedInput：动态修改即将传给工具的输入参数
            if "updatedInput" in hook_output and context:
                context["tool_input"] = hook_output["updatedInput"]
            # additionalContext：向工具结果追加额外信息
            if "additionalContext" in hook_output:
                result["messages"].append(
                    hook_output["additionalContext"])
            # permissionDecision：覆盖权限决定（如把 ask 改为 allow）
            if "permissionDecision" in hook_output:
                result["permission_override"] = (
                    hook_output["permissionDecision"])
        except (json.JSONDecodeError, TypeError):
            # stdout 不是 JSON —— 对简单钩子来说是正常情况
            pass

    elif r.returncode == 1:
        # 退出码 1：阻断当前工具执行
        result["blocked"] = True
        reason = r.stderr.strip() or "Blocked by hook"
        result["block_reason"] = reason

    elif r.returncode == 2:
        # 退出码 2：注入消息（stderr 内容将追加到工具结果中）
        msg = r.stderr.strip()
        if msg:
            result["messages"].append(msg)
```
可以看到这里的核心就是调用了Python的一个子进程运行函数：`subprocess.run`，并解析出对应的returncode，同时如果命中了某些关键字则把它加到hook的结果里面。最最终的结果就是将hook的整个结果添加到上下文的message中。

## 4. 主流程中hook的应用
有了Hook模块的实现，下面我们将整个hook模块添加到我们的主流程中。
上述hook的四个模块非常的清晰，因此我们只要找到主流程中对应的位置，然后添加进去即可。

* 第一个对话启动时的hook
```
if __name__ == "__main__":
    hooks = HookManager()
    # 触发 SessionStart 钩子
    hooks.run_hooks("SessionStart", {"tool_name": "", "tool_input": {}})

    history = []
    while True:
        ......
```
设置好hook的类型（SessionStart），那么直接调用整个hook类就可以。

* 工具调用执行前、后，以及一次对话后
直接给出一次对话循环的一个整个代码吧，非常清晰简单：

```
def agent_loop(messages: list, hooks: HookManager):
    """
    带钩子的 Agent 主循环。
    """
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        # 如果不是工具调用，说明模型已给出最终回答，退出循环
        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_input = dict(block.input or {})
            ctx = {"tool_name": block.name, "tool_input": tool_input}

            # ── PreToolUse 钩子：工具执行前 ──
            pre_result = hooks.run_hooks("PreToolUse", ctx)
            print(f">> PreToolUse hooks: {pre_result}")

            if pre_result.get("blocked"):
                # 被钩子阻断：不执行工具，直接返回阻断原因作为 tool_result
                reason = pre_result.get("block_reason", "Blocked by hook")
                output = f"Tool blocked by PreToolUse hook: {reason}"
                results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": output,
                })
                continue

            # 初始化工具执行结果
            output = ""
            # 将 PreToolUse 钩子的注入消息追加到输出头部
            for msg in pre_result.get("messages", []):
                output += f"[Hook message]: {msg}\n"

            # ── 执行工具 ──
            handler = TOOL_HANDLERS.get(block.name)
            try:
                tool_output = handler(**tool_input) if handler else f"Unknown: {block.name}"
                output += str(tool_output)
            except Exception as e:
                error_output = f"Error: {e}"
                output += error_output

            # ── PostToolUse 钩子：工具执行后 ──
            ctx["tool_output"] = output
            post_result = hooks.run_hooks("PostToolUse", ctx)
            print(f">> PostToolUse hooks: {post_result}")

            # 将 PostToolUse 钩子的注入消息追加到输出尾部
            for msg in post_result.get("messages", []):
                output += f"\n[Hook note]: {msg}"

            # 将工具执行结果添加到结果列表
            results.append({
                "type": "tool_result", "tool_use_id": block.id,
                "content": str(output),
            })

        # ── RoundEnd 钩子：本轮所有工具执行完毕后 ──
        round_ctx = {
            "tool_name": ", ".join(
                b.name for b in response.content if b.type == "tool_use"
            ) or "(无工具调用)",
            "tool_input": {},
            "tool_count": sum(1 for b in response.content if b.type == "tool_use"),
        }
        round_result = hooks.run_hooks("RoundEnd", round_ctx)
        print(f">> RoundEnd hooks: {round_result}")

        # RoundEnd 的注入消息：追加到本轮最后的工具结果中
        for msg in round_result.get("messages", []):
            if results:
                results[-1]["content"] += f"\n[RoundEnd note]: {msg}"

        messages.append({"role": "user", "content": results})
```

这就是我们一次对话循环的主内容，各个模块很清晰，和之前的相比流程变得更多了，增加了hook的流程，但是主函数一旦确定下来以后，后续我们想增加内容，就不用再改主流程了，只需要在hook的配置文件中增加和删除想要的hook脚本即可。

## 5. 一个实例
下面用一个实际的例子来看一下整个hook的四个阶段是否正确地被执行了。
```
localhost learn-claude-code-main % python agents/s08_hook_system.py
```
我们通过在命令窗口执行这个教程脚本,让它帮我读一下目录下面的某个文件内容，并且输出：
```
[Hooks loaded from /work/learn-claude-code-main/.hooks.json]
  [hook:SessionStart] Agent session started at 2026年 5月16日 星期六 08时59分03秒 CST

s08 >> 帮我读一下test3.py内容并输出

  [hook:SessionStart] Welcome，Agent session started at 2026年 5月16日 星期六 10时28分23秒 CST
s08 >> 帮我读一下test3.py内容并输出
  [hook:PreToolUse] tool start: only run read_file use Tool.
>> PreToolUse hooks: {'blocked': False, 'messages': []}
  [hook:PostToolUse] tool end: each tool done.
>> PostToolUse hooks: {'blocked': False, 'messages': []}
>> RoundEnd hooks: {'blocked': False, 'messages': []}
文件 `test3.py` 的内容如下：

"hello from test3"


这是一个非常简单的 Python 脚本，只有一行代码，功能是打印输出字符串 `"hello from test3"`。
```
同时我们在本地文件的目录看到对话结束之后写入了日期到了本地文件：`hooks_end_notes.md`中，这是我们在配置文件中写好的功能。

## 6. 总结一下Hook系统
通过我们整个流程的讲解，我们就能发现Hook功能非常的灵活，只在配置文件里面改，并且可以很灵活地增加我们想要的一些功能。

*用一句话终极总结：Hook = 在 Agent 运行的关键节点，插入自定义逻辑，不修改核心代码，实现拦截、扩展、监听、权限控制。*

|Hook 钩子|触发时机|核心用途（极简案例）|
|---|---|---|
|**on\_agent\_start**|智能体启动时|初始化会话状态、重置计数器、开启日志统计|
|**before\_model\_call**|调用大模型前|上下文压缩、敏感词拦截、优化输入文本|
|**before\_tool\_execution**|执行工具前|**权限校验**、参数校验、拦截高危工具调用|
|**after\_tool\_execution**|执行工具后|记录执行日志、结果脱敏、兜底处理异常|
|**on\_agent\_finish**|智能体结束时|汇总会话数据、释放系统资源、清空临时状态|

按照类型在具体点的hook：

| Hook 类型 | 作用场景 | 挂载生效范围 |
|-----------|----------|--------------|
| 权限检查 Hook | 校验用户/角色是否允许调用模型、使用工具 | 全局所有Agent请求入口 |
| 日志监控 Hook | 入参出参日志、链路追踪、行为记录 | 模型调用、工具调用、任务执行全链路 |
| 限流熔断 Hook | QPS限流、并发控制、超时熔断、防风暴 | 所有对外模型请求、内部任务调度 |
| 上下文压缩 Hook | 自动裁剪超长上下文、精简历史对话 | Agent会话、多轮对话请求前置拦截 |
| 安全拦截 Hook | 敏感词过滤、违规Prompt拦截、输出内容风控 | 输入Prompt拦截、模型输出后置审核 |
| 耗时统计 Hook | 模型推理耗时、工具执行耗时打点上报 | 每一次模型/工具调用自动统计 |
| 操作审计 Hook | 记录谁、何时、执行了什么操作、入参出参 | 企业级合规审计、行为追溯 |

关于Hook这一章节的内容，我们就介绍到这里。

