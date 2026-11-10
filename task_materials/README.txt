Scrapy 按下载槽处理 Retry-After

本目录之外是完整的 Scrapy 2.13.3 公开仓库基线。根目录 prompt.txt 是本题需求，contract.txt 给出具体完成标准，UPSTREAM.txt 记录固定来源。源码与运行入口均在仓库中，不需要克隆其他仓库。所附本地站点是合成输入，运行时不访问真实采集目标。

环境准备
本题验证环境为 Windows、CPython 3.13.15。使用 Python 3.13 创建隔离环境；requirements.txt 固定了本次验证的运行和测试依赖。上游本身支持更早的 Python 版本，但本题依赖锁定结果以 Python 3.13 为基准。初次安装需要访问 Python 包索引，之后本地复现不需要联网。

Windows PowerShell，在仓库根目录执行：
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r task_materials/requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe task_materials/check_materials.py

Linux 的对应命令：
python3.13 -m venv .venv
.venv/bin/python -m pip install -r task_materials/requirements.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python task_materials/check_materials.py

下文 python 指向上述虚拟环境的解释器。Windows 已实际验证；Linux 命令遵循同一接口，但本次没有执行 Linux 验证。初始快照保存源码、依赖版本与启动方式，虚拟环境和缓存不提交到 Git。

检查已有实现与新增能力缺口
python task_materials/check_materials.py
python task_materials/probe_crawl.py --disabled --scenario basic
python task_materials/probe_crawl.py --enabled --scenario basic

check_materials 只确认仓库可导入、本地站点可运行和已有重试功能正常。两条 probe 命令均输出实际请求时间、响应、统计与关闭情况。起点尚无新增设置的处理代码，因此即使传 --enabled，原版仍会立即发出同槽后续请求。这是需要补齐的功能，不能通过让 probe 暂停或修改测试输入代替实现。

完成后的公开集成检查
python task_materials/acceptance.py
python task_materials/acceptance.py --list
python task_materials/acceptance.py --case test_slot_pause_and_other_slot_progress

每条探针在独立进程启动 Scrapy 和动态端口的127.0.0.1 HTTP站点，避免 Twisted reactor 重启限制。无需手工启动服务。探针明确关闭环境代理对本地请求的影响，输出 JSON 到标准输出；需要保留结果时可重定向到 task_materials/results/（该目录不会进入快照）。

公开脚本覆盖：默认设置、启用与关闭、整数与HTTP日期、空白/非法/多值头、上限和0上限、同槽排队及重试等待、不同槽继续、多个响应合并、在途请求保留、重试开关和次数耗尽、状态码过滤、普通下载间隔和 AutoThrottle 基本兼容。正常结束时检查无活动定时器。

计时口径
所有先后次序均来自本地站点的单调时钟日志。秒数冷却下界给80毫秒采样容差，HTTP日期给120毫秒；无需新增冷却时，同槽后续请求应在650毫秒内启动；独立槽在800毫秒内继续；等待合并不超过目标时刻650毫秒，普通1秒下载间隔允许100毫秒采样误差。它们针对空闲本机的小型请求组，用于区分立即重试、错误全局等待与重复累加，不是吞吐量指标。每个子进程35秒为防挂死界限。机器明显繁忙时，先单独复测失败用例并核对输出中的原始时序；不能仅凭繁忙机器上的一次时间上界失败认定实现错误。

需要补充的定向回归
公开脚本不能代替以下受控时钟/定向单测，这些同样属于完成标准：UTC墙钟前后调整不改变已建立等待；空槽跨过垃圾回收时刻仍保留未到期截止时间；按IP分组与显式同槽跨主机的实际映射；Downloader.close后定时器取消且不再发起下载；随机下载延迟与冷却下界同时满足；保留请求复制属性、优先级和原有重试统计。可以沿用上游的测试设施，不规定新增测试的私有函数名或文件拆分。

已有相关回归
python -m pytest tests/test_downloadermiddleware_retry.py tests/test_core_downloader.py tests/test_downloaderslotssettings.py tests/test_extension_throttle.py -q

初始环境已实际通过上述86项已有测试；它们证明基线可运行，不证明本题新增能力通过。当前新增能力的公开检查应失败。没有预先提供冷却实现、参考答案或完成后的产物。

文件用途
requirements.txt：已验证的运行与测试依赖版本。
rate_limit_site.py：标准库本地限流站点，记录请求和响应的时刻。
probe_crawl.py：使用当前仓库 Scrapy 的实际采集探针。
check_materials.py：初始环境与材料检查。
acceptance.py：新增能力的公开集成检查。
contract.txt：外部行为和边界约定。
UPSTREAM.txt：公开仓库来源、固定提交与许可信息。
