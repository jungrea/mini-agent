"""tools —— 无状态外部交互函数。

persisted_output: 大结果落盘 + 回传预览标记（s06）
fs:              safe_path / run_read / run_write / run_edit
bash:            run_bash（含基础危险命令黑名单）
subagent:        s04 run_subagent（嵌套一个小型 LLM 工具循环）
"""
