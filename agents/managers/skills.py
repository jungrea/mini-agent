"""
managers/skills —— s05 技能加载器。

对应源 s_full.py 第 259–284 行。

技能目录约定（与 Anthropic Skills 对齐的极简版本）：

    skills/
      my-skill/
        SKILL.md   # YAML frontmatter + Markdown 正文

SKILL.md 形如：

    ---
    name: my-skill
    description: 一句话说明
    ---
    # 正文，这里是被 load_skill 加载后塞进 LLM 对话的内容
"""

import re
from pathlib import Path


class SkillLoader:
    """
    扫描 skills/ 目录，解析每个 SKILL.md 的 frontmatter + body。

    对外提供：
        descriptions() —— 启动时给系统提示用的清单（"名字：一句话描述"）
        load(name)     —— 按名字加载技能，返回 <skill name="..."> 包裹的正文
    """

    def __init__(self, skills_dir: Path):
        self.skills: dict[str, dict] = {}
        if not skills_dir.exists():
            return

        # rglob 允许任意嵌套深度；sorted 保证在 descriptions() 输出里有稳定顺序
        for f in sorted(skills_dir.rglob("SKILL.md")):
            text = f.read_text()

            # frontmatter 正则：以 "---\n" 开头，到下一个 "---\n" 结束；
            # DOTALL 让 . 匹配换行，避免 YAML 里的多行 value 被切断
            match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)

            meta: dict = {}
            body = text  # 没有 frontmatter 时整个文件都当 body
            if match:
                # 逐行解析 key: value；value 允许含冒号（所以 split maxsplit=1）
                for line in match.group(1).strip().splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = match.group(2).strip()

            # 没写 name 就退化为父目录名（skills/xxx/SKILL.md → "xxx"）
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body}

    def descriptions(self) -> str:
        """返回 '  - 名字: 描述' 的多行字符串，供 SYSTEM prompt 使用。"""
        if not self.skills:
            return "(no skills)"
        return "\n".join(
            f"  - {n}: {s['meta'].get('description', '-')}"
            for n, s in self.skills.items()
        )

    def load(self, name: str) -> str:
        """按名字加载技能正文；未知名字返回错误提示（不抛异常）。"""
        if not self.skills:
            return (
                "Error: No skills are installed. "
                "Create skills/<name>/SKILL.md (with YAML frontmatter) to register one."
            )
        s = self.skills.get(name)
        if not s:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{s['body']}\n</skill>"
