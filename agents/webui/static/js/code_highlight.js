// code_highlight.js —— 零依赖、轻量级代码高亮。
// 输入必须是已经 HTML-escape 的代码文本；本模块只插入 <span> 包裹 token。

const LANG_ALIASES = new Map([
  ["js", "javascript"], ["jsx", "javascript"], ["javascript", "javascript"],
  ["ts", "typescript"], ["tsx", "typescript"], ["typescript", "typescript"],
  ["py", "python"], ["python", "python"],
  ["sh", "bash"], ["shell", "bash"], ["bash", "bash"], ["zsh", "bash"],
  ["json", "json"],
  ["html", "html"], ["xml", "html"], ["svg", "html"],
  ["css", "css"],
  ["md", "markdown"], ["markdown", "markdown"],
]);

const KEYWORDS = {
  javascript: ["async", "await", "break", "case", "catch", "class", "const", "continue", "debugger", "default", "delete", "do", "else", "export", "extends", "finally", "for", "from", "function", "if", "import", "in", "instanceof", "let", "new", "of", "return", "static", "super", "switch", "this", "throw", "try", "typeof", "var", "void", "while", "yield", "true", "false", "null", "undefined"],
  typescript: ["abstract", "as", "async", "await", "boolean", "break", "case", "catch", "class", "const", "continue", "declare", "default", "delete", "do", "else", "enum", "export", "extends", "finally", "for", "from", "function", "if", "implements", "import", "in", "instanceof", "interface", "keyof", "let", "namespace", "new", "number", "of", "private", "protected", "public", "readonly", "return", "static", "string", "super", "switch", "this", "throw", "try", "type", "typeof", "var", "void", "while", "yield", "true", "false", "null", "undefined"],
  python: ["and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except", "False", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "None", "nonlocal", "not", "or", "pass", "raise", "return", "True", "try", "while", "with", "yield"],
  bash: ["case", "do", "done", "elif", "else", "esac", "export", "fi", "for", "function", "if", "in", "local", "return", "select", "then", "until", "while"],
  css: ["@media", "@keyframes", "@import", "@supports", "from", "to", "important"],
  markdown: [],
};

function normalizeLang(lang) {
  return LANG_ALIASES.get(String(lang || "").toLowerCase()) || "";
}

function wrap(cls, body) {
  return `<span class="tok-${cls}">${body}</span>`;
}

function withPlaceholders(input, patterns) {
  const saved = [];
  let out = input;
  for (const [cls, re] of patterns) {
    out = out.replace(re, (m) => {
      const id = saved.length;
      saved.push(wrap(cls, m));
      return `\u0000TOK${id}\u0000`;
    });
  }
  return {
    text: out,
    restore: (s) => s.replace(/\u0000TOK(\d+)\u0000/g, (_, n) => saved[Number(n)]),
  };
}

function keywordRegex(words) {
  return new RegExp(`\\b(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\b`, "g");
}

function highlightGeneric(escaped, lang) {
  const stringRe = /(&quot;.*?&quot;|&#39;.*?&#39;|`[^`\n]*`)/g;
  const commentRe = lang === "python" || lang === "bash"
    ? /(^|\n)(\s*#.*?)(?=\n|$)/g
    : /(\/\/.*?(?=\n|$)|\/\*[\s\S]*?\*\/)/g;

  let comments;
  if (lang === "python" || lang === "bash") {
    comments = [];
    escaped = escaped.replace(commentRe, (full, nl, body) => {
      const id = comments.length;
      comments.push(wrap("comment", body));
      return `${nl}\u0000COM${id}\u0000`;
    });
  }

  const protectedText = withPlaceholders(escaped, [["string", stringRe], ["comment", commentRe]]);
  let out = protectedText.text;

  const words = KEYWORDS[lang] || [];
  if (words.length) out = out.replace(keywordRegex(words), wrap.bind(null, "keyword"));

  out = out
    .replace(/\b([A-Za-z_$][\w$]*)(?=\s*\()/g, wrap.bind(null, "function"))
    .replace(/\b(0x[\da-fA-F]+|\d+(?:\.\d+)?)\b/g, wrap.bind(null, "number"));

  out = protectedText.restore(out);
  if (comments) out = out.replace(/\u0000COM(\d+)\u0000/g, (_, n) => comments[Number(n)]);
  return out;
}

function highlightJson(escaped) {
  const protectedText = withPlaceholders(escaped, [["string", /&quot;(?:\\.|[^&])*?&quot;/g]]);
  return protectedText.restore(
    protectedText.text
      .replace(/\b(true|false|null)\b/g, wrap.bind(null, "keyword"))
      .replace(/-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b/gi, wrap.bind(null, "number"))
  );
}

function highlightHtml(escaped) {
  return escaped
    .replace(/(&lt;\/?)([A-Za-z][\w:-]*)/g, (_, open, tag) => `${open}${wrap("tag", tag)}`)
    .replace(/([\w:-]+)(=)(&quot;.*?&quot;|&#39;.*?&#39;)/g, (_, name, eq, value) => `${wrap("attr", name)}${eq}${wrap("string", value)}`);
}

function highlightCss(escaped) {
  const protectedText = withPlaceholders(escaped, [["comment", /\/\*[\s\S]*?\*\//g], ["string", /(&quot;.*?&quot;|&#39;.*?&#39;)/g]]);
  return protectedText.restore(
    protectedText.text
      .replace(/([.#]?[A-Za-z_-][\w-]*)(?=\s*\{)/g, wrap.bind(null, "selector"))
      .replace(/([A-Za-z-]+)(?=\s*:)/g, wrap.bind(null, "attr"))
      .replace(/\b(#[\da-fA-F]{3,8}|\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw)?|var\(--[\w-]+\))\b/g, wrap.bind(null, "number"))
  );
}

function highlightMarkdown(escaped) {
  return escaped
    .replace(/(^|\n)(#{1,6}\s.*)/g, (_, nl, h) => `${nl}${wrap("keyword", h)}`)
    .replace(/(^|\n)(\s*[-*+]\s+)/g, (_, nl, bullet) => `${nl}${wrap("keyword", bullet)}`);
}

export function highlightCode(escapedCode, lang) {
  const normalized = normalizeLang(lang);
  if (!normalized) return escapedCode;
  if (normalized === "json") return highlightJson(escapedCode);
  if (normalized === "html") return highlightHtml(escapedCode);
  if (normalized === "css") return highlightCss(escapedCode);
  if (normalized === "markdown") return highlightMarkdown(escapedCode);
  return highlightGeneric(escapedCode, normalized);
}
