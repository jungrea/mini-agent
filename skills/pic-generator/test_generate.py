import importlib.util
import os
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "generate.py")
spec = importlib.util.spec_from_file_location("covergen", MODULE_PATH)
covergen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(covergen)


class CoverGeneratorTests(unittest.TestCase):
    def test_normalize_config_applies_theme_series_and_limits_tags(self):
        raw = {
            "series": "mcp",
            "theme": "amber-purple",
            "title_line1": "MCP 代码实战",
            "title_line2": "高性能 MySQL 工具",
            "subtitle": "Python + FastMCP 打造安全高效的数据查询工具",
            "badge_text": "Py",
            "tags": ["Python", "FastMCP", "SQL拦截", "异步并发", "结构化返回"],
            "graphic_config": {},
            "output_name": "demo",
        }

        cfg = covergen.normalize_config(raw)

        self.assertEqual(cfg["graphic"], "database")
        self.assertEqual(cfg["bg_gradient"], covergen.THEMES["amber-purple"]["bg_gradient"])
        self.assertEqual(cfg["badge_color"], covergen.THEMES["amber-purple"]["badge_color"])
        self.assertEqual(cfg["footer"], "MCP 实战系列 · 高性能工具设计")
        self.assertEqual(len(cfg["tags"]), 4)

    def test_text_builders_escape_html(self):
        html = covergen.build_tags_html([{"icon": "<i>", "text": "A&B"}])

        self.assertIn("&lt;i&gt;", html)
        self.assertIn("A&amp;B", html)
        self.assertNotIn("<i>", html)

    def test_build_code_lines_supports_segments_and_escapes_text(self):
        html = covergen.build_code_lines([
            {
                "segments": [
                    {"cls": "kw", "text": "def "},
                    {"cls": "fn", "text": "x<y"},
                    {"cls": "op", "text": "():"},
                ]
            }
        ])

        self.assertIn('<span class="kw">def </span>', html)
        self.assertIn('<span class="fn">x&lt;y</span>', html)
        self.assertNotIn("x<y", html)

    def test_read_png_size_reads_dimensions(self):
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x04\xb0"
            b"\x00\x00\x02\xa3"
            b"\x08\x02\x00\x00\x00"
            b"\x00\x00\x00\x00"
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            path = f.name
        try:
            self.assertEqual(covergen.read_png_size(path), (1200, 675))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
