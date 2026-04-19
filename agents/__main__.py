"""
python -m agents 的入口。

单独保留此文件是为了让 `python -m agents` 与 `python -m agents.cli.main`
两种写法都能工作，并且顶层包 __init__ 可以继续做懒加载。
"""

from .cli.main import main


if __name__ == "__main__":
    main()
