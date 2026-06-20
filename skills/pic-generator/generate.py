#!/usr/bin/env python3
"""
Cover Image Generator - 从 JSON 配置自动生成封面 PNG
用法: python3 generate.py config.json [--output-dir ../]
依赖: playwright-cli (自动调用)
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import http.server
import threading
import socket
from copy import deepcopy
from functools import partial
from html import escape

# ── HTML 模板 ──────────────────────────────────────────────

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title_line1} {title_line2}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1200px;
    height: 675px;
    font-family: -apple-system, "PingFang SC", "Helvetica Neue", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, {bg[0]} 0%, {bg[1]} 40%, {bg[2]} 100%);
    overflow: hidden;
    position: relative;
  }}

  .glow {{
    position: absolute; border-radius: 50%; filter: blur(80px); opacity: 0.5;
  }}
  .glow-1 {{ width: 500px; height: 500px; background: {glow[0]}; top: -150px; right: -100px; }}
  .glow-2 {{ width: 400px; height: 400px; background: {glow[1]}; bottom: -120px; left: -80px; }}
  .glow-3 {{ width: 300px; height: 300px; background: {glow[2]}; top: 50%; left: 40%; opacity: 0.25; }}

  .grid {{
    position: absolute; inset: 0;
    background-image:
      linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
  }}

  .container {{
    position: relative; width: 100%; height: 100%;
    display: flex; align-items: center; padding: 60px 70px; z-index: 2;
  }}

  .left {{ flex: 1; color: white; z-index: 3; }}

  .badge {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 8px 16px;
    background: rgba({badge_rgb}, 0.15);
    border: 1px solid rgba({badge_rgb}, 0.4);
    border-radius: 50px; font-size: 14px; color: {badge_color};
    margin-bottom: 28px; backdrop-filter: blur(10px);
  }}
  .badge-dot {{
    width: 8px; height: 8px; background: {badge_color};
    border-radius: 50%; box-shadow: 0 0 12px {badge_color};
    animation: pulse 2s infinite;
  }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}

  h1 {{ font-size: 52px; font-weight: 900; line-height: 1.2; margin-bottom: 24px; letter-spacing: -1px; }}
  .h1-line1 {{ color: #ffffff; }}
  .h1-line2 {{
    background: linear-gradient(90deg, {title2_gradient[0]} 0%, {title2_gradient[1]} 50%, {title2_gradient[2]} 100%);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  }}

  .subtitle {{
    font-size: 20px; color: rgba(255,255,255,0.75); line-height: 1.5;
    margin-bottom: 32px; max-width: 520px;
  }}

  .tags {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .tag {{
    padding: 8px 16px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 8px; font-size: 14px; color: rgba(255,255,255,0.9);
    backdrop-filter: blur(10px);
  }}

  .right {{
    width: 500px; height: 500px; position: relative;
    display: flex; align-items: center; justify-content: center;
  }}

  .spark {{
    position: absolute; width: 6px; height: 6px;
    background: {spark_color}; border-radius: 50%;
    box-shadow: 0 0 12px {spark_color};
  }}
  .spark-1 {{ top: 12%; left: 18%; }}
  .spark-2 {{ top: 22%; right: 8%; width: 4px; height: 4px; background: {glow[1]}; box-shadow: 0 0 10px {glow[1]}; }}
  .spark-3 {{ bottom: 20%; left: 10%; width: 5px; height: 5px; background: {glow[2]}; box-shadow: 0 0 10px {glow[2]}; }}
  .spark-4 {{ bottom: 15%; right: 20%; }}
  .spark-5 {{ top: 55%; left: 5%; width: 4px; height: 4px; }}

  .footer {{
    position: absolute; bottom: 28px; left: 70px;
    color: rgba(255,255,255,0.4); font-size: 13px; letter-spacing: 2px; z-index: 3;
  }}

  {graphic_css}
</style>
</head>
<body>
  <div class="glow glow-1"></div>
  <div class="glow glow-2"></div>
  <div class="glow glow-3"></div>
  <div class="grid"></div>

  <div class="spark spark-1"></div>
  <div class="spark spark-2"></div>
  <div class="spark spark-3"></div>
  <div class="spark spark-4"></div>
  <div class="spark spark-5"></div>

  <div class="container">
    <div class="left">
      <div class="badge">
        <span class="badge-dot"></span>
        <span>{badge_text}</span>
      </div>
      <h1>
        <div class="h1-line1">{title_line1}</div>
        <div class="h1-line2">{title_line2}</div>
      </h1>
      <div class="subtitle">{subtitle}</div>
      <div class="tags">
        {tags_html}
      </div>
    </div>

    <div class="right">
      {graphic_html}
    </div>
  </div>

  <div class="footer">{footer}</div>
</body>
</html>"""

# ── 预设图形模板 ───────────────────────────────────────────

GRAPHICS = {

    "database": {
        "css": """
  .db {{ width: 280px; position: relative; display: flex; flex-direction: column; align-items: center; }}
  .db-top {{
    width: 280px; height: 50px;
    background: linear-gradient(180deg, #3b82f6 0%, #2563eb 100%);
    border-radius: 50%; border: 3px solid #60a5fa;
    box-shadow: 0 0 30px rgba(59,130,246,0.4); z-index: 3;
  }}
  .db-body {{
    width: 280px; height: {db_body_height}px;
    background: linear-gradient(180deg, #1e40af 0%, #1e3a8a 100%);
    border-left: 3px solid #60a5fa; border-right: 3px solid #60a5fa;
    position: relative; z-index: 2;
    display: flex; flex-direction: column; justify-content: center; align-items: center;
    gap: 12px; margin-top: -25px;
  }}
  .db-row {{ display: flex; align-items: center; gap: 8px; font-family: 'Menlo','Monaco',monospace; font-size: 13px; }}
  .db-row .icon {{
    width: 18px; height: 18px; border-radius: 4px;
    display: flex; align-items: center; justify-content: center; font-size: 10px;
  }}
  .icon-safe {{ background: rgba(34,197,94,0.3); color: #4ade80; }}
  .icon-timeout {{ background: rgba(245,158,11,0.3); color: #fbbf24; }}
  .icon-async {{ background: rgba(139,92,246,0.3); color: #a78bfa; }}
  .icon-schema {{ background: rgba(236,72,153,0.3); color: #f472b6; }}
  .icon-info {{ background: rgba(96,165,250,0.3); color: #60a5fa; }}
  .db-row .text {{ color: rgba(255,255,255,0.85); }}
  .db-line {{ width: 220px; height: 1px; background: rgba(96,165,250,0.3); }}
  .db-bottom {{
    width: 280px; height: 50px;
    background: linear-gradient(180deg, #1e3a8a 0%, #172554 100%);
    border-radius: 50%; border: 3px solid #3b82f6;
    margin-top: -25px; z-index: 1;
    box-shadow: 0 10px 40px rgba(0,0,0,0.5);
  }}
  .mcp-badge {{
    position: absolute; top: -10px; right: 30px;
    width: 100px; height: 100px;
    background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
    border-radius: 50%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; color: white; font-weight: 900;
    box-shadow: 0 8px 32px rgba(59,130,246,0.5);
    border: 4px solid rgba(255,255,255,0.2); transform: rotate(12deg);
  }}
  .mcp-badge .mcp-text {{ font-size: 24px; line-height: 1; }}
  .mcp-badge .mcp-sub {{ font-size: 9px; margin-top: 4px; letter-spacing: 1px; opacity: 0.95; }}
  .perf-badge {{
    position: absolute; bottom: 40px; right: 40px;
    background: linear-gradient(135deg, #f59e0b 0%, #ef4444 100%);
    color: white; padding: 6px 14px; border-radius: 6px;
    font-size: 14px; font-weight: 700;
    box-shadow: 0 4px 12px rgba(245,158,11,0.4); transform: rotate(-6deg);
  }}
""",
        "html": """
      <div class="db">
        <div class="db-top"></div>
        <div class="db-body">
          {db_rows_html}
        </div>
        <div class="db-bottom"></div>
        <div class="mcp-badge">
          <div class="mcp-text">{badge_text}</div>
          <div class="mcp-sub">{badge_sub}</div>
        </div>
      </div>
      <div class="perf-badge">{perf_label}</div>
""",
    },

    "mac": {
        "css": """
  .mac {{
    width: 420px; height: 290px;
    background: linear-gradient(180deg, #f5f5f0 0%, #d8d4c8 100%);
    border-radius: 14px 14px 18px 18px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5),
      inset 0 2px 4px rgba(255,255,255,0.6),
      inset 0 -2px 4px rgba(0,0,0,0.1);
    padding: 18px; position: relative;
  }}
  .mac::after {{
    content: ''; position: absolute; bottom: -22px; left: 50%; transform: translateX(-50%);
    width: 220px; height: 22px;
    background: linear-gradient(180deg, #d8d4c8 0%, #b8b3a4 100%);
    border-radius: 0 0 24px 24px; box-shadow: 0 6px 16px rgba(0,0,0,0.3);
  }}
  .screen {{
    width: 100%; height: 100%; background: #0a0e1a;
    border-radius: 4px; padding: 14px;
    font-family: 'Menlo','Monaco',monospace; font-size: 13px; color: #00ff88;
    overflow: hidden; position: relative;
    box-shadow: inset 0 0 40px rgba(0,255,136,0.1);
  }}
  .screen-header {{
    display: flex; gap: 6px; margin-bottom: 12px; padding-bottom: 10px;
    border-bottom: 1px solid rgba(0,255,136,0.2);
  }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .dot-r {{ background: #ff5f56; }}
  .dot-y {{ background: #ffbd2e; }}
  .dot-g {{ background: #27c93f; }}
  .screen-title {{ margin-left: 10px; color: rgba(255,255,255,0.5); font-size: 11px; }}
  .line {{ margin-bottom: 6px; line-height: 1.4; }}
  .prompt {{ color: #4f8cff; }}
  .cmd {{ color: #ffffff; }}
  .out {{ color: #00ff88; }}
  .ok {{ color: #ffbd2e; }}
  .cursor {{
    display: inline-block; width: 8px; height: 14px;
    background: #00ff88; vertical-align: middle; animation: blink 1s infinite;
  }}
  @keyframes blink {{ 0%, 50% {{ opacity: 1; }} 51%, 100% {{ opacity: 0; }} }}
  .ai-badge {{
    position: absolute; top: -10px; right: -10px;
    width: 110px; height: 110px;
    background: linear-gradient(135deg, #00d4aa 0%, #4f8cff 100%);
    border-radius: 50%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; color: white; font-weight: 900;
    box-shadow: 0 8px 32px rgba(79,140,255,0.5);
    border: 4px solid rgba(255,255,255,0.2); transform: rotate(12deg);
  }}
  .ai-badge .ai-text {{ font-size: 28px; line-height: 1; }}
  .ai-badge .ai-sub {{ font-size: 10px; margin-top: 4px; letter-spacing: 1px; opacity: 0.95; }}
  .reborn {{
    position: absolute; bottom: -30px; right: 10px;
    background: linear-gradient(135deg, #ff6b6b 0%, #ffbd2e 100%);
    color: white; padding: 6px 14px; border-radius: 6px;
    font-size: 14px; font-weight: 700;
    box-shadow: 0 4px 12px rgba(255,107,107,0.4); transform: rotate(-6deg);
  }}
""",
        "html": """
      <div class="mac">
        <div class="screen">
          <div class="screen-header">
            <div class="dot dot-r"></div>
            <div class="dot dot-y"></div>
            <div class="dot dot-g"></div>
            <div class="screen-title">{screen_title}</div>
          </div>
          {terminal_lines_html}
          <div class="line"><span class="prompt">$</span> <span class="cursor"></span></div>
        </div>
        <div class="ai-badge">
          <div class="ai-text">{badge_text}</div>
          <div class="ai-sub">{badge_sub}</div>
        </div>
        <div class="reborn">{reborn_label}</div>
      </div>
""",
    },

    "code": {
        "css": """
  .terminal-window {{
    width: 440px; height: 300px;
    background: linear-gradient(180deg, #eceae4 0%, #d6d2c6 100%);
    border-radius: 12px;
    padding: 12px; position: relative;
    box-shadow:
      0 30px 80px rgba(0,0,0,0.55),
      0 0 0 1px rgba(255,255,255,0.5),
      inset 0 1px 0 rgba(255,255,255,0.7),
      inset 0 -1px 2px rgba(0,0,0,0.15);
  }}
  /* 窗口底部支架 */
  .terminal-window::after {{
    content: ''; position: absolute; bottom: -18px; left: 50%; transform: translateX(-50%);
    width: 180px; height: 18px;
    background: linear-gradient(180deg, #d6d2c6 0%, #bfbab0 100%);
    border-radius: 0 0 20px 20px;
    box-shadow: 0 8px 18px rgba(0,0,0,0.25);
  }}
  .screen {{
    width: 100%; height: 100%;
    background: linear-gradient(180deg, #161922 0%, #0d1017 100%);
    border-radius: 7px; overflow: hidden;
    box-shadow: inset 0 0 30px rgba(0,0,0,0.5);
    display: flex; flex-direction: column;
  }}
  .screen-header {{
    display: flex; align-items: center; gap: 8px;
    padding: 13px 16px 10px;
    background: rgba(22,25,34,0.95);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    position: relative; z-index: 2;
  }}
  .tdot {{ width: 11px; height: 11px; border-radius: 50%; box-shadow: 0 1px 3px rgba(0,0,0,0.35); }}
  .tdot-r {{ background: #ff5f57; }}
  .tdot-y {{ background: #febc2e; }}
  .tdot-g {{ background: #28c840; }}
  .screen-title {{
    margin-left: 12px; color: rgba(255,255,255,0.4); font-size: 12px;
    font-family: 'Menlo','Monaco',monospace; letter-spacing: 0.3px;
  }}
  /* 行号 + 代码区域 */
  .editor-inner {{
    display: flex; flex: 1; min-height: 0; overflow: hidden;
  }}
  .line-numbers {{
    padding: 10px 0; text-align: right;
    font-family: 'Menlo','Monaco','Consolas',monospace; font-size: 12.5px;
    color: rgba(255,255,255,0.12); line-height: 1.7;
    user-select: none; min-width: 38px; padding-right: 10px;
    border-right: 1px solid rgba(255,255,255,0.05);
    background: rgba(0,0,0,0.15);
  }}
  .editor-body {{
    padding: 10px 14px; font-family: 'Menlo','Monaco','Consolas',monospace;
    font-size: 13px; line-height: 1.7; color: #c5d0e0;
    flex: 1; overflow: hidden; position: relative; z-index: 1;
  }}
  /* 语法高亮 - Catppuccin 风格 */
  .kw {{ color: #cba6f7; font-weight: 500; }}
  .fn {{ color: #89b4fa; }}
  .str {{ color: #a6e3a1; }}
  .cmt {{ color: #6c7086; font-style: italic; }}
  .num {{ color: #fab387; }}
  .op {{ color: #89dceb; }}
  .var {{ color: #f9e2af; }}
  .cls {{ color: #f5c2e7; }}
  .prompt {{ color: #89dceb; }}
  .out {{ color: #a6e3a1; }}
  .ok {{ color: #f9e2af; }}
  /* 光标闪烁 */
  .cursor {{
    display: inline-block; width: 8px; height: 16px;
    background: #89dceb; vertical-align: middle; animation: blink-cursor 1s step-end infinite;
  }}
  @keyframes blink-cursor {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0; }} }}
  /* 圆形徽章 */
  .lang-badge {{
    position: absolute; top: -12px; right: -8px;
    width: 100px; height: 100px;
    background: linear-gradient(145deg, {editor_accent[0]} 0%, {editor_accent[1]} 100%);
    border-radius: 50%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; color: white; font-weight: 900;
    box-shadow:
      0 12px 40px rgba({editor_accent_rgb},0.45),
      inset 0 2px 0 rgba(255,255,255,0.25);
    border: 3px solid rgba(255,255,255,0.22);
    transform: rotate(12deg); z-index: 10;
  }}
  .lang-badge .lang-text {{ font-size: 26px; line-height: 1; letter-spacing: -1px; }}
  .lang-badge .lang-sub {{ font-size: 9px; margin-top: 3px; letter-spacing: 1.5px; opacity: 0.95; font-weight: 600; }}
  /* 底部标签 */
  .code-tag {{
    position: absolute; bottom: -26px; left: 20px;
    background: rgba({editor_accent_rgb}, 0.15);
    border: 1px solid rgba({editor_accent_rgb}, 0.3);
    color: {editor_accent[0]}; padding: 5px 14px;
    border-radius: 20px; font-size: 12px; font-weight: 600;
    backdrop-filter: blur(8px);
    z-index: 10;
  }}
""",
        "html": """
      <div class="terminal-window">
        <div class="screen">
          <div class="screen-header">
            <div class="tdot tdot-r"></div>
            <div class="tdot tdot-y"></div>
            <div class="tdot tdot-g"></div>
            <div class="screen-title">{filename}</div>
          </div>
          <div class="editor-inner">
            <div class="line-numbers">
{line_numbers}
            </div>
            <div class="editor-body">
              {code_lines_html}
              <span class="cursor"></span>
            </div>
          </div>
          <div class="lang-badge">
            <div class="lang-text">{badge_text}</div>
            <div class="lang-sub">{badge_sub}</div>
          </div>
        </div>
      </div>
      <div class="code-tag">{code_label}</div>
""",
    },

    "chart": {
        "css": """
  .chart-card {{
    width: 420px; height: 310px;
    background: linear-gradient(160deg, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0.04) 100%);
    border-radius: 20px;
    backdrop-filter: blur(24px);
    border: 1px solid rgba(255,255,255,0.14);
    padding: 26px 28px; position: relative;
    box-shadow:
      0 25px 70px rgba(0,0,0,0.4),
      inset 0 1px 0 rgba(255,255,255,0.1);
  }}
  /* 卡片顶部微光 */
  .chart-card::before {{
    content: ''; position: absolute;
    top: 0; left: 20%; right: 20%; height: 1px;
    background: linear-gradient(90deg, transparent, rgba({chart_rgb},0.5), transparent);
  }}
  .chart-title {{
    color: #fff; font-size: 17px; font-weight: 700;
    margin-bottom: 6px; letter-spacing: -0.3px;
  }}
  .chart-subtitle {{
    color: rgba(255,255,255,0.45); font-size: 12px;
    margin-bottom: 22px;
  }}
  /* 柱状图区域 - 带底部基线 */
  .bars-container {{
    position: relative; padding-bottom: 28px;
  }}
  .baseline {{
    position: absolute; bottom: 28px; left: 0; right: 0;
    height: 1px; background: rgba(255,255,255,0.08); border-radius: 1px;
  }}
  .bars {{
    display: flex; align-items: flex-end; gap: 18px;
    height: 170px; padding: 0 8px; position: relative; z-index: 1;
  }}
  .bar-group {{ flex: 1; display: flex; flex-direction: column; align-items: center; gap: 10px; }}
  .bar {{
    width: 100%; max-width: 52px;
    border-radius: 8px 8px 3px 3px;
    background: linear-gradient(180deg, {chart_gradient[0]} 0%, {chart_gradient[1]} 60%, rgba({chart_rgb},0.5) 100%);
    min-height: 16px;
    box-shadow:
      0 -2px 12px rgba({chart_rgb},0.35),
      0 4px 20px rgba({chart_rgb},0.15) inset;
    transition: all 0.3s ease;
    position: relative;
  }}
  /* 柱顶高光 */
  .bar::after {{
    content: ''; position: absolute;
    top: 0; left: 15%; right: 15%; height: 40%;
    background: linear-gradient(180deg, rgba(255,255,255,0.18) 0%, transparent 100%);
    border-radius: 8px 8px 50% 50%;
  }}
  .bar-label {{ color: rgba(255,255,255,0.55); font-size: 11px; font-weight: 500; letter-spacing: 0.3px; }}
  .bar-value {{
    color: {chart_gradient[0]}; font-size: 13px; font-weight: 800;
    text-shadow: 0 0 12px rgba({chart_rgb},0.35);
  }}
  /* 徽章 */
  .data-badge {{
    position: absolute; top: -8px; right: -6px;
    width: 96px; height: 96px;
    background: linear-gradient(145deg, {chart_gradient[0]} 0%, {chart_gradient[1]} 100%);
    border-radius: 50%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; color: white; font-weight: 900;
    box-shadow:
      0 10px 35px rgba({chart_rgb},0.45),
      inset 0 2px 0 rgba(255,255,255,0.25);
    border: 3px solid rgba(255,255,255,0.22);
    transform: rotate(12deg); z-index: 5;
  }}
  .data-badge .data-text {{ font-size: 22px; line-height: 1; }}
  .data-badge .data-sub {{ font-size: 9px; margin-top: 3px; letter-spacing: 1.5px; opacity: 0.95; font-weight: 600; }}
  /* 底部标签 */
  .stat-tag {{
    position: absolute; bottom: -24px; left: 24px;
    background: rgba({chart_rgb}, 0.15);
    border: 1px solid rgba({chart_rgb}, 0.3);
    color: {chart_gradient[0]}; padding: 5px 14px;
    border-radius: 20px; font-size: 12px; font-weight: 600;
    backdrop-filter: blur(8px);
  }}
""",
        "html": """
      <div class="chart-card">
        <div class="chart-title">{chart_title}</div>
        <div class="chart-subtitle">{chart_subtitle}</div>
        <div class="bars-container">
          <div class="baseline"></div>
          <div class="bars">
            {bars_html}
          </div>
        </div>
        <div class="data-badge">
          <div class="data-text">{badge_text}</div>
          <div class="data-sub">{badge_sub}</div>
        </div>
      </div>
      <div class="stat-tag">{stat_label}</div>
""",
    },

    "custom": {
        "css": "",
        "html": "{custom_html}",
    },
}

# ── 主题与系列预设 ──────────────────────────────────────────

THEMES = {
    "blue-purple": {
        "bg_gradient": ["#0a1628", "#0d2137", "#1a0a3e"],
        "glow_colors": ["#0077ff", "#8b5cf6", "#f59e0b"],
        "title2_gradient": ["#60a5fa", "#8b5cf6", "#f59e0b"],
        "badge_color": "#60a5fa",
        "spark_color": "#60a5fa",
    },
    "cyan-blue": {
        "bg_gradient": ["#0f1c3f", "#1a2952", "#2d1b69"],
        "glow_colors": ["#4f8cff", "#b366ff", "#00d4aa"],
        "title2_gradient": ["#00ffd1", "#4f8cff", "#b366ff"],
        "badge_color": "#00ffd1",
        "spark_color": "#00ffd1",
    },
    "amber-purple": {
        "bg_gradient": ["#1a0f0a", "#2d1a12", "#3e2018"],
        "glow_colors": ["#f59e0b", "#ef4444", "#ec4899"],
        "title2_gradient": ["#fbbf24", "#f97316", "#ec4899"],
        "badge_color": "#f59e0b",
        "spark_color": "#fbbf24",
    },
}

SERIES_PRESETS = {
    "mcp": {
        "theme": "blue-purple",
        "graphic": "database",
        "footer": "MCP 实战系列 · 高性能工具设计",
        "badge_text": "MCP 实战",
    },
}

MAX_TAGS = 4
VALID_GRAPHICS = set(GRAPHICS.keys())

# ── 工具函数 ────────────────────────────────────────────────

def escape_html(value):
    return escape(str(value), quote=True)


def safe_class(value):
    raw = str(value)
    return "".join(ch for ch in raw if ch.isalnum() or ch in "_-") or "txt"


def deep_merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def normalize_config(raw):
    cfg = deepcopy(raw)

    series_name = cfg.get("series")
    if series_name in SERIES_PRESETS:
        cfg = deep_merge(SERIES_PRESETS[series_name], cfg)

    theme_name = cfg.get("theme", "blue-purple")
    theme = THEMES.get(theme_name, THEMES["blue-purple"])
    cfg["theme"] = theme_name if theme_name in THEMES else "blue-purple"
    for key, value in theme.items():
        cfg.setdefault(key, deepcopy(value))

    cfg.setdefault("graphic", "database")
    if cfg["graphic"] not in VALID_GRAPHICS:
        cfg["graphic"] = "database"

    cfg.setdefault("title_line1", "")
    cfg.setdefault("title_line2", "")
    cfg.setdefault("subtitle", "")
    cfg.setdefault("badge_text", "")
    cfg.setdefault("footer", "")
    cfg.setdefault("graphic_config", {})
    cfg.setdefault("output_name", "cover")

    cfg["tags"] = cfg.get("tags", [])[:MAX_TAGS]

    return cfg


def read_png_size(path):
    with open(path, "rb") as f:
        header = f.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"不是有效 PNG 文件: {path}")
    return struct.unpack(">II", header[16:24])


def ensure_playwright_cli():
    if shutil.which("playwright-cli"):
        return
    raise RuntimeError("未找到 playwright-cli，请先安装或确保它在 PATH 中")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)}"


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def build_tags_html(tags):
    parts = []
    for t in tags:
        if isinstance(t, dict):
            icon = t.get("icon", "")
            text = t.get("text", "")
        else:
            icon = ""
            text = t
        label = f"{escape_html(icon)} {escape_html(text)}".strip()
        parts.append(f'<span class="tag">{label}</span>')
    return "\n        ".join(parts)


def build_database_rows(rows):
    parts = []
    for r in rows:
        icon = escape_html(r.get("icon", "●"))
        icon_type = safe_class(r.get("icon_type", "info"))
        text = escape_html(r.get("text", ""))
        parts.append(f'<div class="db-row"><div class="icon icon-{icon_type}">{icon}</div><span class="text">{text}</span></div>')
        parts.append('<div class="db-line"></div>')
    if parts:
        parts.pop()  # remove last line
    return "\n          ".join(parts)


def build_terminal_lines(lines):
    parts = []
    for ln in lines:
        ltype = safe_class(ln.get("type", "cmd"))
        text = escape_html(ln.get("text", ""))
        if ltype == "prompt":
            parts.append(f'<div class="line"><span class="prompt">$</span> <span class="cmd">{text}</span></div>')
        elif ltype == "output":
            parts.append(f'<div class="line"><span class="out">→ {text}</span></div>')
        elif ltype == "success":
            parts.append(f'<div class="line"><span class="ok">✓ {text}</span></div>')
        elif ltype == "error":
            parts.append(f'<div class="line"><span style="color:#ff5f56">✗ {text}</span></div>')
        else:
            parts.append(f'<div class="line"><span class="{ltype}">{text}</span></div>')
    return "\n          ".join(parts)


def build_code_lines(lines):
    """构建代码行，支持新格式 segments 与旧格式 {cls: text}。"""
    parts = []
    for ln in lines:
        if isinstance(ln, dict) and "segments" in ln:
            segments = []
            for seg in ln.get("segments", []):
                cls = safe_class(seg.get("cls", "txt"))
                txt = escape_html(seg.get("text", ""))
                segments.append(f'<span class="{cls}">{txt}</span>')
            parts.append("".join(segments))
        elif isinstance(ln, dict):
            segments = []
            for cls, txt in ln.items():
                segments.append(f'<span class="{safe_class(cls)}">{escape_html(txt)}</span>')
            parts.append("".join(segments))
        else:
            parts.append(escape_html(ln))
    return "<br>\n          ".join(parts)


def build_bars(bars):
    parts = []
    for b in bars:
        label = escape_html(b.get("label", ""))
        value = escape_html(b.get("value", ""))
        height = int(b.get("height", 50))
        height = max(16, min(170, height))
        parts.append(
            f'<div class="bar-group">'
            f'<div class="bar-value">{value}</div>'
            f'<div class="bar" style="height:{height}px"></div>'
            f'<div class="bar-label">{label}</div>'
            f'</div>'
        )
    return "\n          ".join(parts)


# ── 主流程 ──────────────────────────────────────────────────

def generate(config_path, output_dir=None):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = normalize_config(json.load(f))

    if output_dir is None:
        output_dir = os.path.dirname(config_path)

    # ── 解析配置 ──
    graphic_type = cfg["graphic"]
    graphic_cfg = cfg["graphic_config"]

    # 通用字段
    title_line1 = cfg.get("title_line1", "")
    title_line2 = cfg.get("title_line2", "")
    subtitle = cfg.get("subtitle", "")
    badge_text = cfg.get("badge_text", "")
    footer = cfg.get("footer", "")

    # 颜色配置（有默认值）
    bg = cfg.get("bg_gradient", ["#0a1628", "#0d2137", "#1a0a3e"])
    glow = cfg.get("glow_colors", ["#0077ff", "#8b5cf6", "#f59e0b"])
    title2_gradient = cfg.get("title2_gradient", ["#60a5fa", "#8b5cf6", "#f59e0b"])
    badge_color = cfg.get("badge_color", "#60a5fa")
    badge_rgb = hex_to_rgb(badge_color)
    spark_color = cfg.get("spark_color", glow[0])

    # 标签
    tags = cfg.get("tags", [])
    tags_html = build_tags_html(tags)

    # ── 构建图形 ──
    gfx = GRAPHICS.get(graphic_type, GRAPHICS["database"])
    graphic_css = gfx["css"]
    graphic_html_raw = gfx["html"]

    if graphic_type == "database":
        rows = graphic_cfg.get("rows", [])
        db_body_height = max(160, 60 + len(rows) * 44)
        graphic_css = graphic_css.format(db_body_height=db_body_height)
        graphic_html = graphic_html_raw.format(
            db_rows_html=build_database_rows(rows),
            badge_text=escape_html(graphic_cfg.get("badge_text", "MCP")),
            badge_sub=escape_html(graphic_cfg.get("badge_sub", "SERVER")),
            perf_label=escape_html(graphic_cfg.get("perf_label", "高性能")),
        )
    elif graphic_type == "mac":
        graphic_html = graphic_html_raw.format(
            screen_title=escape_html(graphic_cfg.get("screen_title", "terminal ~ zsh")),
            terminal_lines_html=build_terminal_lines(graphic_cfg.get("lines", [])),
            badge_text=escape_html(graphic_cfg.get("badge_text", "AI")),
            badge_sub=escape_html(graphic_cfg.get("badge_sub", "POWERED")),
            reborn_label=escape_html(graphic_cfg.get("reborn_label", "焕新")),
        )
    elif graphic_type == "code":
        editor_accent = graphic_cfg.get("editor_accent", ["#82aaff", "#c792ea"])
        editor_accent_rgb = hex_to_rgb(editor_accent[0])
        editor_glow = graphic_cfg.get("editor_glow", "96,165,250")
        lines = graphic_cfg.get("lines", [])
        # 自动生成行号
        line_nums = "\n".join(f"  {i+1}" for i in range(len(lines)))
        graphic_css = graphic_css.format(
            editor_glow=editor_glow,
            editor_accent=editor_accent,
            editor_accent_rgb=editor_accent_rgb,
        )
        graphic_html = graphic_html_raw.format(
            filename=escape_html(graphic_cfg.get("filename", "main.py")),
            line_numbers=line_nums,
            code_lines_html=build_code_lines(lines),
            badge_text=escape_html(graphic_cfg.get("badge_text", "Py")),
            badge_sub=escape_html(graphic_cfg.get("badge_sub", "CODE")),
            code_label=escape_html(graphic_cfg.get("code_label", "Auto")),
        )
    elif graphic_type == "chart":
        chart_gradient = graphic_cfg.get("chart_gradient", ["#60a5fa", "#8b5cf6"])
        chart_rgb = hex_to_rgb(chart_gradient[0])
        graphic_css = graphic_css.format(
            chart_gradient=chart_gradient, chart_rgb=chart_rgb,
        )
        graphic_html = graphic_html_raw.format(
            chart_title=escape_html(graphic_cfg.get("chart_title", "数据分析")),
            chart_subtitle=escape_html(graphic_cfg.get("chart_subtitle", "")),
            bars_html=build_bars(graphic_cfg.get("bars", [])),
            badge_text=escape_html(graphic_cfg.get("badge_text", "📊")),
            badge_sub=escape_html(graphic_cfg.get("badge_sub", "DATA")),
            stat_label=escape_html(graphic_cfg.get("stat_label", "实时")),
            chart_gradient=chart_gradient, chart_rgb=chart_rgb,
        )
    elif graphic_type == "custom":
        graphic_html = graphic_html_raw.format(
            custom_html=graphic_cfg.get("html", ""),
        )
        graphic_css = graphic_cfg.get("css", "")

    # ── 渲染完整 HTML ──
    html = TEMPLATE.format(
        title_line1=escape_html(title_line1),
        title_line2=escape_html(title_line2),
        subtitle=escape_html(subtitle),
        badge_text=escape_html(badge_text),
        badge_color=badge_color,
        badge_rgb=badge_rgb,
        title2_gradient=title2_gradient,
        bg=bg,
        glow=glow,
        spark_color=spark_color,
        tags_html=tags_html,
        graphic_css=graphic_css,
        graphic_html=graphic_html,
        footer=escape_html(footer),
    )

    output_name = cfg.get("output_name", "cover")
    os.makedirs(output_dir, exist_ok=True)
    html_path = os.path.join(output_dir, f"{output_name}-cover.html")
    png_path = os.path.join(output_dir, f"{output_name}-cover.png")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[1/3] HTML 已生成: {html_path}")

    # ── 启动 HTTP 服务器 ──
    ensure_playwright_cli()
    port = find_free_port()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=output_dir)
    session = "covergen"
    with http.server.HTTPServer(("127.0.0.1", port), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        print(f"[2/3] HTTP 服务器启动: http://127.0.0.1:{port}")

        html_filename = os.path.basename(html_path)
        url = f"http://127.0.0.1:{port}/{html_filename}"

        # ── playwright-cli 截图 ──
        try:
            subprocess.run(["playwright-cli", f"-s={session}", "open", url], check=True, capture_output=True, text=True, timeout=30)
            time.sleep(0.5)
            subprocess.run(["playwright-cli", f"-s={session}", "resize", "1200", "675"], check=True, capture_output=True, text=True, timeout=30)
            subprocess.run(
                ["playwright-cli", f"-s={session}", "screenshot", "--filename", png_path],
                check=True, capture_output=True, text=True, timeout=30,
            )
            if read_png_size(png_path) != (1200, 675):
                raise RuntimeError(f"PNG 尺寸异常: {read_png_size(png_path)}，期望 (1200, 675)")
            print(f"[3/3] PNG 已生成: {png_path}")
        except subprocess.TimeoutExpired as e:
            print(f"截图超时: {e}", file=sys.stderr)
            sys.exit(1)
        except (subprocess.CalledProcessError, RuntimeError, ValueError) as e:
            stderr = getattr(e, "stderr", "")
            print(f"截图失败: {stderr or e}", file=sys.stderr)
            sys.exit(1)
        finally:
            subprocess.run(["playwright-cli", f"-s={session}", "close"], capture_output=True, text=True, timeout=10)
            httpd.shutdown()

    return png_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="封面图生成器")
    parser.add_argument("config", help="JSON 配置文件路径")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认与配置文件同目录）")
    args = parser.parse_args()
    generate(args.config, args.output_dir)
