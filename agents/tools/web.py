"""
tools/web —— 网络类工具 `web_fetch` / `web_search`。

设计原则：**零额外依赖**。
    * 只用标准库 urllib + html.parser + json，避免让 requirements.txt 膨胀
    * 想升级到 BeautifulSoup / httpx / tavily 的用户可以替换实现而不改协议

两个工具：

    web_fetch(url, max_chars=20000)
        抓取 URL 文本；HTML 自动抽出可见内容（去 script/style/nav 等标签）。
        纯文本 / JSON 直接返回。返回前按 max_chars 截断防止炸上下文。

    web_search(query, max_results=5)
        优先 Tavily（需要 TAVILY_API_KEY 环境变量），
        否则回退 DuckDuckGo HTML 页面抓取（免费但结果不如 Tavily 干净）。
        返回人类可读的 "标题 | URL\n摘要" 列表。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from ..core.config import CONTEXT_TRUNCATE_CHARS


# 统一 UA：部分站点对没有 UA 的请求直接 403
_UA: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 请求超时（秒）
_TIMEOUT: int = 15

# 默认返回字符上限（调用方可覆盖）
_DEFAULT_FETCH_CHARS: int = 20000


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

def run_web_fetch(url: str, max_chars: int = _DEFAULT_FETCH_CHARS, **_ignored) -> str:
    """
    抓取 URL，返回文本内容（HTML 会被剥离标签）。

    参数：
        url:       必须是 http/https，防止 file://、gopher:// 等本地协议注入
        max_chars: 输出字符上限，超出后截断并追加 "... (truncated)"

    返回：纯文本；失败返回 "Error: ..."（不抛，让 LLM 自行处理）。
    """
    if not _is_http_url(url):
        return f"Error: only http(s) URLs are allowed, got: {url}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read()
    except Exception as e:
        return f"Error: fetch failed: {e}"

    # 猜编码：Content-Type 里的 charset 优先，否则按 utf-8 宽松解码
    charset = _charset_from_ctype(ctype) or "utf-8"
    try:
        body = raw.decode(charset, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")

    # HTML → 可见文本；JSON / 纯文本直接返回
    ctype_lower = ctype.lower()
    if "html" in ctype_lower or body.lstrip().startswith(("<!DOCTYPE", "<html", "<HTML")):
        text = _html_to_text(body)
    elif "json" in ctype_lower:
        # 美化输出，便于 LLM 阅读
        try:
            text = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except Exception:
            text = body
    else:
        text = body

    # 前置裁剪：先按 max_chars，再按全局兜底
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated at {max_chars} chars)"
    return text[:CONTEXT_TRUNCATE_CHARS]


def _is_http_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _charset_from_ctype(ctype: str) -> str | None:
    """从 'text/html; charset=utf-8' 里抽出 charset。"""
    m = re.search(r"charset=([\w\-]+)", ctype, re.IGNORECASE)
    return m.group(1) if m else None


class _VisibleTextExtractor(HTMLParser):
    """
    极简 HTML → 可见文本提取器。

    策略：
        * 跳过 script / style / noscript / template / nav / footer / aside 等标签
        * 其它标签的文本照收；块级标签后加换行让阅读更顺
        * 连续空白折叠成单空格/单换行
    """

    _SKIP_TAGS: frozenset[str] = frozenset({
        "script", "style", "noscript", "template", "svg",
        "nav", "footer", "aside", "header", "form",
    })
    _BLOCK_TAGS: frozenset[str] = frozenset({
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "pre", "section", "article", "blockquote", "hr",
    })

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        text = "".join(self._chunks)
        # 折叠连续空白
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    """解析 HTML 提取可见文本；解析失败时返回原串（兜底）。"""
    parser = _VisibleTextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        # 解析器炸了——退化为简单正则去标签
        no_tag = re.sub(r"<[^>]+>", "", html)
        return re.sub(r"\s+", " ", no_tag).strip()


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

def run_web_search(query: str, max_results: int = 5, **_ignored) -> str:
    """
    网络搜索。

    后端选择：
        1. 若设置了 TAVILY_API_KEY → 调 Tavily（结果最干净，有摘要）
        2. 否则 → DuckDuckGo HTML 版（免费，不要 key，但要解析 HTML）

    返回：每条一个多行块，形如
        [n] 标题
            URL
            摘要
    """
    if not query.strip():
        return "Error: empty query"

    api_key = os.getenv("TAVILY_API_KEY")
    if api_key:
        return _search_tavily(api_key, query, max_results)
    return _search_duckduckgo(query, max_results)


def _search_tavily(api_key: str, query: str, max_results: int) -> str:
    """Tavily API —— 结构化结果，有 title/url/content 三件套。"""
    payload = json.dumps({
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return f"Error: tavily search failed: {e}"

    results = data.get("results", [])
    if not results:
        return "(no results)"

    lines: list[str] = []
    for i, r in enumerate(results[:max_results], start=1):
        title = (r.get("title") or "").strip() or "(untitled)"
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip()
        lines.append(f"[{i}] {title}\n    {url}\n    {content}")
    return "\n\n".join(lines)[:CONTEXT_TRUNCATE_CHARS]


# DuckDuckGo 结果 URL 常被包裹成跳转链接 "//duckduckgo.com/l/?uddg=<encoded>"
_DDG_REDIR_RE: re.Pattern[str] = re.compile(r"/l/\?.*?uddg=([^&]+)")
# HTML 版结果条目锚点
_DDG_RESULT_RE: re.Pattern[str] = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_DDG_SNIPPET_RE: re.Pattern[str] = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _search_duckduckgo(query: str, max_results: int) -> str:
    """
    DuckDuckGo HTML 接口兜底。

    限制：
        * 解析基于 HTML 结构，DDG 改版后可能失效；保持最小正则，失败就降级
        * 没有显式 API，有反爬风险 —— 用于低频调试场景足够
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Error: duckduckgo search failed: {e}"

    anchors = _DDG_RESULT_RE.findall(html)
    snippets = _DDG_SNIPPET_RE.findall(html)
    if not anchors:
        return ("(no results — if you expected results, set TAVILY_API_KEY "
                "for a more reliable backend)")

    lines: list[str] = []
    for i, (href, title_html) in enumerate(anchors[:max_results], start=1):
        title = _strip_tags(title_html).strip() or "(untitled)"
        real_url = _unwrap_ddg_redirect(href)
        snippet = _strip_tags(snippets[i - 1]).strip() if i - 1 < len(snippets) else ""
        lines.append(f"[{i}] {title}\n    {real_url}\n    {snippet}")
    return "\n\n".join(lines)[:CONTEXT_TRUNCATE_CHARS]


def _unwrap_ddg_redirect(href: str) -> str:
    """把 DuckDuckGo 的 /l/?uddg=... 重定向链接还原为原始 URL。"""
    m = _DDG_REDIR_RE.search(href)
    if m:
        return urllib.parse.unquote(m.group(1))
    # 绝对 URL 或 //-schemeless 的情况直接补前缀
    if href.startswith("//"):
        return "https:" + href
    return href


def _strip_tags(fragment: str) -> str:
    """去掉 HTML 标签，折叠空白。"""
    text = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", text)
