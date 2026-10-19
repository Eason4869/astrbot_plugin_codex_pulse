# 开发与验证

Python 3.12，测试使用真实 AstrBot 4.29.1 的消息组件与插件基类。

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q -p no:cacheprovider
python -m ruff check .
python -m ruff format --check .
```

测试可直接在本仓库运行，也可在 AstrBot 插件目录中运行。`tests/conftest.py` 只设置测试用包路径，不替代 AstrBot 框架。

测试使用合成数据及模拟群发送，不向真实群发送消息。覆盖订阅、预告、失败重试、图片与文字回退、独立发卡进度、来源链接开关和管理员测试指令。GitHub Actions 自动执行这些检查。

发布前另行验证 Chromium 排版、320/390 像素缩略阅读、长文本转义和无横向溢出。通过真实 `Star.html_render` 与本地 HTTP/Jinja/Chromium 服务检查图片下载及 HTTP 503 回退。最终平台收图需在实际 AstrBot 中执行 `/codex 测试` 验收。

插件运行不额外依赖 Playwright 或 Jinja2 Python 包，而是调用 AstrBot 已配置的 HTML 文转图服务。上游接口为 `/api/forecast` 与 `/api/timeline`，分别缓存和检查新鲜度。
