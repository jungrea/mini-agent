# Cover Generator Skill

根据 Markdown 文章内容自动生成公众号风格封面 PNG 图。

## 触发条件

当用户要求为文章生成封面图、配图、封面 PNG 时触发。

## 使用方式

### 步骤 1：读取文章

读取目标 Markdown 文件，提取以下信息：
- 文章主题和核心关键词
- 主要技术栈/工具
- 核心要点（通常 2~4 个）

### 步骤 2：选择图形类型

根据文章主题选择右侧图形类型：

| 类型 | 适用场景 | graphic 值 |
|------|---------|-----------|
| 数据库圆柱 | 数据库、存储、后端服务 | `database` |
| Mac 终端 | Mac使用、命令行工具、Agent | `mac` |
| 代码编辑器 | 编程语言、代码实战、框架 | `code` |
| 数据图表 | 数据分析、监控、统计 | `chart` |
| 自定义 | 以上都不适合时 | `custom` |

### 步骤 3：生成 JSON 配置

在**文章所在目录**创建配置文件 `cover-config.json`，格式如下：

```json
{
  "series": "mcp",
  "theme": "blue-purple",
  "title_line1": "标题第一行",
  "title_line2": "标题第二行（渐变色）",
  "subtitle": "副标题描述",
  "badge_text": "徽章文字",
  "tags": [
    {"icon": "🛡️", "text": "标签1"},
    {"icon": "⚡", "text": "标签2"}
  ],
  "graphic": "database",
  "graphic_config": {
    "badge_text": "MCP",
    "badge_sub": "SERVER",
    "perf_label": "高性能",
    "rows": [
      {"icon": "✓", "icon_type": "safe", "text": "SELECT * FROM users"},
      {"icon": "⏱", "icon_type": "timeout", "text": "timeout: 30s"}
    ]
  },
  "footer": "底部文字（可省略，series 会自动补）",
  "output_name": "文章名"
}
```

推荐优先使用 `theme` / `series`，不要手写颜色字段。脚本会自动补齐：

| 字段 | 可选值 | 作用 |
|------|--------|------|
| `theme` | `blue-purple` / `cyan-blue` / `amber-purple` | 统一背景、光晕、标题渐变、徽章色 |
| `series` | `mcp` | 统一 MCP 系列默认 footer、默认图形和基础风格 |

高级用法仍可覆盖 `bg_gradient`、`glow_colors`、`title2_gradient`、`badge_color`、`spark_color`。

**各图形类型的 graphic_config 说明：**

#### database 类型
```json
{
  "badge_text": "徽章大字",
  "badge_sub": "徽章小字",
  "perf_label": "角标文字",
  "rows": [
    {"icon": "✓", "icon_type": "safe|timeout|async|schema|info", "text": "行内容"}
  ]
}
```

#### mac 类型
```json
{
  "screen_title": "终端标题",
  "badge_text": "徽章大字",
  "badge_sub": "徽章小字",
  "reborn_label": "角标文字",
  "lines": [
    {"type": "prompt", "text": "输入的命令"},
    {"type": "output", "text": "输出内容"},
    {"type": "success", "text": "成功信息"}
  ]
}
```

#### code 类型（带行号编辑器）
```json
{
  "filename": "文件名.py",
  "editor_glow": "245,158,11",
  "editor_accent": ["#fbbf24", "#f97316"],
  "badge_text": "Py",
  "badge_sub": "CODE",
  "code_label": "角标文字（左下胶囊）",
  "lines": [
    {"segments": [
      {"cls": "kw", "text": "def "},
      {"cls": "fn", "text": "main"},
      {"cls": "op", "text": "():"}
    ]},
    {"segments": [
      {"cls": "kw", "text": "return "},
      {"cls": "str", "text": "'hello'"}
    ]}
  ]
}
```
| 字段 | 说明 | 默认值 |
|------|------|--------|
| `editor_glow` | 编辑器顶部内发光 RGB 颜色，让编辑器从背景"浮起来" | `"96,165,250"` |
| `editor_accent` | 徽章渐变色 `[亮色, 暗色]` | `["#82aaff","#c792ea"]` |
| `lines` | 代码行数组，推荐使用 `segments` 格式；旧格式 `{ "kw": "def " }` 仍兼容 | — |

CSS 类名：`kw`(关键字) `fn`(函数名) `str`(字符串) `cmt`(注释) `num`(数字) `op`(运算符) `var`(变量) `cls`(类名)
> **注意**：推荐使用 `segments`，避免 JSON dict 重复 key 导致片段丢失；文本会自动 HTML escape。

#### chart 类型（带副标题柱状图）
```json
{
  "chart_title": "图表主标题",
  "chart_subtitle": "图表副标题 / 描述行",
  "chart_gradient": ["#60a5fa", "#8b5cf6"],
  "badge_text": "📊 或数字",
  "badge_sub": "DATA",
  "stat_label": "角标文字（左下胶囊）",
  "bars": [
    {"label": "标签", "value": "100%", "height": 120}
  ]
}
```
| 字段 | 说明 |
|------|------|
| `chart_subtitle` | 图表卡片内副标题（新增） |
| `bars[].height` | 柱子高度 px，建议 40~160 |
| `stat_label` | 左下角胶囊标签文字 |

#### custom 类型
```json
{
  "html": "<div>自定义HTML</div>",
  "css": "自定义CSS样式"
}
```

### 步骤 4：运行生成脚本

```bash
python3 pic-generator/generate.py cover-config.json --output-dir .
```

脚本会自动：
1. 从 JSON 生成 HTML
2. 启动临时 HTTP 服务器
3. 调用 playwright-cli 截图
4. 输出 `{output_name}-cover.html` 和 `{output_name}-cover.png`

### 步骤 5：清理

生成完成后删除临时配置文件 `cover-config.json`（如不需要保留）。

## 主题参考

| theme | 适用场景 | 特点 |
|------|---------|------|
| `blue-purple` | 默认技术封面、MCP/数据库/架构 | 最稳，接近 `mcp-arch-design-cover.png` 的观感 |
| `cyan-blue` | Agent、AI 工具、终端体验 | 更轻盈，科技感更强 |
| `amber-purple` | Python、代码实战、工程实践 | 暖色高亮，适合代码类文章 |

除非要做特殊视觉，不建议直接手写颜色字段。

## 注意事项

- 输出图片固定 1200×675 像素（16:9 公众号封面比例）
- `output_name` 建议用文章英文简写，避免中文文件名
- 图形类型的 icon_type 可选值：safe(绿)、timeout(黄)、async(紫)、schema(粉)、info(蓝)
- 确保 playwright-cli 已安装可用
- **配色建议**：优先使用 `theme`，默认选 `blue-purple`；MCP 系列建议加 `series: "mcp"`
- **code 类型**：优先使用 `segments` 格式；行数控制在 8~12 行效果最佳
- **chart 类型**：bars 数量建议 3~5 个，height 差异明显才有层次感
- 生成过程会校验 PNG 尺寸是否为 `1200x675`
- 文本字段会自动 HTML escape，`custom` 类型除外
