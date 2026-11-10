# 运行材料

在仓库根目录开始。题面在 `prompt.txt`，完整行为在 `task_materials/contract.md`，上游许可证与版权信息位于根目录 `LICENSE` 和 `AUTHORS`。

## 环境

使用 CPython 3.13。当前目录已建立本地 `.venv` 并完成安装，可直接执行下列测试命令；从 GitHub 重新取得材料时先执行安装命令。无需浏览器、数据库服务、GPU 或真实网站账号。依赖首次安装需要访问 Python 包源；安装完成后，本题源站与指定检查均在本机运行。本地虚拟环境不进入 Git 快照，以锁文件重建。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r task_materials/requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q tests/test_downloadermiddleware_httpcache.py tests/test_utils_request.py tests/test_http_request.py
.\.venv\Scripts\python.exe -m pytest -q task_materials/test_origin.py
.\.venv\Scripts\python.exe -m pytest -q task_materials/test_vary_contract.py task_materials/test_http_flow.py
```

Linux/macOS 对应使用 `python3.13 -m venv .venv` 与 `.venv/bin/python`，其余参数相同。锁文件包含平台条件。Windows 为实际检查环境，其他系统未实测。`requirements-test.in` 和根目录 `pyproject.toml` 是锁文件的输入；锁定了运行、构建和本次检查需要的传递依赖。固定 w3lib 2.3.1 是为保持该上游版本的 URL 规范化回归；不是本题待修内容。

最后一条为待完成功能的公开验收，初始源码出现失败属于题目的起点；前两组分别检查上游基线和源站本身。测试会使用临时目录与随机本地端口，不依赖固定端口或绝对目录。pytest 自动生成的 localhost 测试证书来自上游测试夹具，不需要提供真实证书。

## 文件用途

- `contract.md`：完整行为、兼容边界与检查方法。
- `origin.py`、`test_origin.py`：本地 HTTP 源站及其协议自检。
- `test_vary_contract.py`：存储与缓存中间件的公开行为用例。
- `test_http_flow.py`：真实 HTTP 请求、命中次数与重开缓存检查。
- `crawl_probe.py`：供真实 HTTP 检查启动独立 Scrapy 进程，记录响应。
- `legacy_seed.py`：生成上游旧格式缓存输入，供兼容检查使用。
- `requirements-test.in`、`requirements.lock`：固定依赖。
- `SOURCE.md`：固定上游、取得方式及自建夹具边界。

源站可单独运行：`python task_materials/origin.py --port 0`，启动后输出本机地址。自动测试会自行管理启动与关闭。实现应修改现有缓存链路；这些材料不包含变体缓存、304 合并或迁移的参考实现。
