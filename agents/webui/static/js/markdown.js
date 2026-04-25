// markdown.js —— 极简安全的 markdown → HTML 渲染器。
//
// 设计目标：
//   * 纯 JS、零依赖（符合本项目"纯 ES module、无 npm 构建"的基因）
//   * safe-by-design：所有输入先 HTML-escape，再在**转义后的文本**上做 markdown 片段替换；
//     任何 <script>、onerror="..." 之类注入在 escape 阶段就变成纯文本，
//     无需 DOMPurify 等外部过滤库
//   * 覆盖 LLM 回复里 95% 的真实用例；**故意**不支持 table、嵌套列表、
//     HTML passthrough 等复杂/有风险特性，宁缺勿滥
//
// 支持的子集：
//   * 标题 `# ## ### #### ##### ######`（只在行首）
//   * 代码块 ```lang\n...\n```（带可选语言标签；内部不再做 markdown 解析）
//   * 行内代码 `code`
//   * 粗体 **text** / __text__
//   * 斜体 *text* / _text_
//   * 链接 [text](url)     —— 仅允许 http/https/mailto 协议，其它会被原样保留为文本
//   * 无序列表 `- `、`* `、`+ `
//   * 有序列表 `1. `、`2. ` 等
//   * 引用 `> `（连续几行会被合并进同一个 <blockquote>）
//   * 水平线 `---`、`***`、`___`（独占一行）
//   * 表格 GFM 风格 `| a | b |` + 分隔行 `| --- | :---: |`
//           支持 :---（左）、---:（右）、:---:（中）对齐
//   * 段落：连续非空行构成一个 <p>，空行分段
//
// 不支持（故意）：
//   * 嵌套列表（按单层解析）
//   * HTML 原样透传（安全风险）
//   * 图片（规避加载本地 / 第三方资源的风险）
//   * 脚注 / 定义列表 / 任务列表 / 双删除线等
//
// 如果将来确实需要更完整的支持，推荐接 marked + DOMPurify 的 CDN 版；
// 本实现刻意不走那条路，保持项目"零构建、零依赖"的承诺。

const URL_SAFE_RE = /^(https?:\/\/|mailto:)/i;

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// 行内级别：粗体 / 斜体 / 行内代码 / 链接。
// 处理顺序重要——先抽行内代码再做粗体斜体，避免代码里的星号被误判。
function renderInline(escapedLine) {
  let s = escapedLine;

  // 1) 行内代码：先把内容用占位符抠出来（反引号之间的不再参与后续替换）
  //    placeholders 复原阶段再写回真正 <code>
  const codes = [];
  s = s.replace(/`([^`\n]+?)`/g, (_, body) => {
    codes.push(body);
    return `\u0000CODE${codes.length - 1}\u0000`;
  });

  // 2) 粗体：** ** 优先于 __ __（两种风格都允许），保证最长匹配
  s = s.replace(/\*\*([^\s*][^*]*?[^\s*]|\S)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^\s_][^_]*?[^\s_]|\S)__/g, "<strong>$1</strong>");

  // 3) 斜体：* * 与 _ _ 各一次，且避免与上面的 ** 冲突
  //    （上一步已经把 **xxx** 替换走了，现在看到的 * 必然是单个）
  s = s.replace(/(^|[^*])\*([^\s*][^*]*?[^\s*]|\S)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^_])_([^\s_][^_]*?[^\s_]|\S)_(?!_)/g, "$1<em>$2</em>");

  // 4) 链接 [text](url)。url 只允许 http/https/mailto；否则原样不做链接化
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (full, text, url) => {
    if (!URL_SAFE_RE.test(url)) return full;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });

  // 5) 回填行内代码
  s = s.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => {
    return `<code>${codes[Number(idx)]}</code>`;
  });

  return s;
}

// 渲染主函数：把 markdown 字符串变成受限的 HTML 片段。
export function renderMarkdown(src) {
  if (src == null) return "";
  const text = String(src);

  // —— 第一步：全文 escape（HTML 危险字符全部处理掉）
  // 后续所有"markdown 片段替换"操作都在 escape 之后的文本上做，
  // 所以如果 LLM 回复里含 <script>...</script>，在这一步已经变成
  // &lt;script&gt;... ，不可能作为 HTML 元素生效。
  const escaped = escapeHTML(text);

  const lines = escaped.split("\n");
  const out = [];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // --- 代码块 ```lang ... ``` ---
    // 开头三个反引号被 escape 成 `&#39;`？不——反引号本身不在 escape 列表里，
    // 所以这里的 `\`\`\``` 原样存在。注意：escape 只处理 &<>"'，反引号保留。
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const lang = fence[1] || "";
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;  // 跳过闭合 ```
      const body = buf.join("\n");
      const cls = lang ? ` class="lang-${escapeHTML(lang).replace(/[^a-zA-Z0-9_\-]/g, "")}"` : "";
      const langLabel = lang ? `<span class="code-lang">${escapeHTML(lang)}</span>` : "";
      out.push(`<pre class="code-block">${langLabel}<code${cls}>${body}</code></pre>`);
      continue;
    }

    // --- 水平线 ---
    if (/^\s*(\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
      out.push("<hr/>");
      i += 1;
      continue;
    }

    // --- 表格（GFM） ---
    // 形如：
    //   | 列1 | 列2 | 列3 |
    //   | --- | :---: | ---: |
    //   | a   | b     | c    |
    // 为减少误判：第 i 行必须含 `|`，第 i+1 行必须是合法的分隔行。
    // escape 不影响 `|`、`-`、`:` 这些字符，所以可以直接在 escape 后的文本上匹配。
    if (/\|/.test(line) && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headerCells = splitTableRow(line);
      const aligns = parseTableAligns(lines[i + 1]);
      i += 2;
      const rows = [];
      while (i < lines.length && /\|/.test(lines[i]) && !/^\s*$/.test(lines[i])) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      out.push(renderTable(headerCells, aligns, rows));
      continue;
    }

    // --- 标题 ---
    const h = line.match(/^(#{1,6})\s+(.*\S)\s*$/);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level}>${renderInline(h[2])}</h${level}>`);
      i += 1;
      continue;
    }

    // --- 引用 > ... （可跨多行合并）---
    // 注意：escape 把 > 转成了 &gt;，所以这里匹配的是 &gt; 而不是 >。
    // 这是 "先全文 escape、再做 markdown 模式匹配" 这种 safe-by-design
    // 架构的必然结果——用户无法通过 > 注入任何 HTML，我们也只需在
    // &gt; 基础上识别引用块。
    if (/^&gt;\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^&gt;\s?/, ""));
        i += 1;
      }
      out.push(`<blockquote>${renderInline(buf.join("<br>"))}</blockquote>`);
      continue;
    }

    // --- 无序列表 ---
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i += 1;
      }
      out.push("<ul>" + items.map(t => `<li>${renderInline(t)}</li>`).join("") + "</ul>");
      continue;
    }

    // --- 有序列表 ---
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      out.push("<ol>" + items.map(t => `<li>${renderInline(t)}</li>`).join("") + "</ol>");
      continue;
    }

    // --- 空行：段落分隔 ---
    if (/^\s*$/.test(line)) {
      i += 1;
      continue;
    }

    // --- 普通段落：连续非空 & 非特殊块的行合并成一个 <p>，行间以 <br> 保留换行 ---
    const buf = [];
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^#{1,6}\s+/.test(lines[i]) &&
      !/^```/.test(lines[i]) &&
      !/^&gt;\s?/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\s*(\*{3,}|-{3,}|_{3,})\s*$/.test(lines[i]) &&
      !(/\|/.test(lines[i]) && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
    ) {
      buf.push(lines[i]);
      i += 1;
    }
    if (buf.length) {
      out.push(`<p>${renderInline(buf.join("<br>"))}</p>`);
    }
  }

  return out.join("\n");
}

// ----------------- 表格辅助 -----------------

// 把一行 `| a | b | c |` 切成 ["a","b","c"]。
// - 允许可选的首/尾 `|`
// - 支持转义 `\|` —— 先把 \| 替换为占位符切分后再还原
function splitTableRow(line) {
  const PLACEHOLDER = "\u0001PIPE\u0001";
  let s = line.replace(/\\\|/g, PLACEHOLDER);
  // 去掉首尾的一个 `|`（有些写法没首/尾分隔符，也要兼容）
  s = s.replace(/^\s*\|/, "").replace(/\|\s*$/, "");
  return s.split("|").map(c => c.replace(new RegExp(PLACEHOLDER, "g"), "|").trim());
}

// 判定一行是否为合法的"分隔行"：
//   ` --- `、` :--- `、` ---: `、` :---: `，单元格之间由 `|` 分隔
function isTableSeparator(line) {
  if (!/\|/.test(line)) return false;
  const cells = splitTableRow(line);
  if (cells.length === 0) return false;
  return cells.every(c => /^:?-{3,}:?$/.test(c));
}

// 返回各列对齐方式：'left' | 'right' | 'center' | null
function parseTableAligns(sepLine) {
  return splitTableRow(sepLine).map(c => {
    const left = c.startsWith(":");
    const right = c.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return null;
  });
}

function alignAttr(a) {
  return a ? ` style="text-align:${a}"` : "";
}

function renderTable(headerCells, aligns, rows) {
  const thead = "<thead><tr>" +
    headerCells.map((c, idx) =>
      `<th${alignAttr(aligns[idx])}>${renderInline(c)}</th>`
    ).join("") +
    "</tr></thead>";

  const tbody = rows.length
    ? "<tbody>" + rows.map(r => {
        // 单元格数量不足/过多时兜底：截断或补空
        const cells = [];
        const n = headerCells.length;
        for (let k = 0; k < n; k++) {
          cells.push(`<td${alignAttr(aligns[k])}>${renderInline(r[k] == null ? "" : r[k])}</td>`);
        }
        return "<tr>" + cells.join("") + "</tr>";
      }).join("") + "</tbody>"
    : "";

  return `<table class="md-table">${thead}${tbody}</table>`;
}
