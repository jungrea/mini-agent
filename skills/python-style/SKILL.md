---
name: python-style
description: Python 代码风格与最佳实践速查（PEP8、类型注解、异常处理）
---

# Python 代码风格速查

## 命名
- 模块/函数/变量：`snake_case`
- 类：`PascalCase`
- 常量：`UPPER_SNAKE_CASE`
- 私有：前缀下划线 `_name`

## 类型注解
- 公共 API 一律加类型注解
- 优先使用 `from __future__ import annotations`，延迟解析注解
- 集合类型偏好 `list[int]` / `dict[str, int]`（Python 3.9+）

## 异常处理
- 只捕获具体异常，避免裸 `except:`
- 需要重抛时使用 `raise ... from e` 保留链路
- 资源管理优先 `with` 语句

## 其他
- 行宽 100（黑格式化器默认 88 也可）
- 导入顺序：stdlib / 第三方 / 本项目，组间空行
- f-string 优先于 `%` 与 `.format()`
