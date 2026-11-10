# 运行材料

根目录 prompt.txt 为题面，contract.md 为完整行为约定。仓库包含 Scrapy 2.13.3 原始源码、原有文档和测试；本题新增能力尚未实现。来源和许可见 UPSTREAM.md。

## 环境

验证环境为 Windows、CPython 3.13.15，无显卡或外部采集服务要求。requirements.txt 固定运行与针对性测试依赖；pyproject.toml 保留上游的构建依赖声明。首次安装需要访问 Python 包索引，完成后下述集成检查只访问本机。

在仓库根目录使用 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r task/requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m task.check_recovery --mode baseline
.\.venv\Scripts\python.exe -m task.check_recovery --mode reproduce
```

后续命令中的 python 指该虚拟环境解释器。也可把 venv 建到仓库外。不要使用全局已安装的另一个 Scrapy 代替本地源码。

Linux 可用 python3.13 -m venv .venv，随后把解释器换为 .venv/bin/python；其余模块参数相同。本次实际验证 Windows，未执行 Linux 验证。

## 合成采集输入与确定中断点

local_site.py 是标准库本地 HTTP 服务，自动分配 127.0.0.1 动态端口。crawl_worker.py 是普通 Scrapy Spider 与 SQLite item pipeline，不含任何请求恢复逻辑。check_recovery.py 创建并管理这些进程、读取落库记录与服务端请求日志。

样本正常完成后有 29 条业务记录：25 个页面、1 个派生子页、1 个 404 errback 记录和 2 条同指纹 dont_filter POST 记录。输入还包括重复链接、一次 503、一次 302、callback 异常、DropItem 和 pipeline 异常。POST 样本检查二进制请求体、多值请求头、Cookie、cb_kwargs 与嵌套 meta。预期的业务异常会写入 worker 日志，不表示测试基础设施故障。

中断窗口共三种：download 在服务端接收 /page/7 后尚未发响应时阻塞；callback 在该页异步生成器产出子 Request 后阻塞；pipeline 在该页的异步 item 管线写入前阻塞。控制器等待线程事件或标记文件确认进入窗口，然后强杀自己启动的 worker；不以猜测睡眠时长作为中断依据。恢复使用同一个 JOBDIR、同一仍在运行的站点和空 start，不重新灌入起始 URL。

所有 JOBDIR、SQLite、标记及子进程日志使用系统临时目录，在检查结束时清理。JSON 摘要输出到终端，若需保留可自行重定向到 task/results/ 或仓库外。每次命令创建独立输入。单进程 45 秒超时、窗口等待 35 秒只是防挂死限制，不是性能指标。

## 检查入口

```text
python -m task.check_recovery --mode baseline
python -m task.check_recovery --mode reproduce
python -m task.check_recovery --mode reproduce --window pipeline
python -m task.check_recovery --mode acceptance
python -m task.check_recovery --mode acceptance --window callback
```

baseline 关闭新模式，验证完整正常采集及已有重试、重定向和去重行为。

reproduce 关闭新模式，分别展示原版在三个强杀窗口恢复后漏采 /page/7。该模式退出成功表示既有能力缺口被复现，不表示目标功能通过；其他页丢失数量受并发顺序影响，不作为固定答案。

acceptance 开启 SCHEDULER_DURABLE，要求各窗口恢复后得到完整 29 条业务记录、/page/7 确有重投递，并在完成后再次空起点启动时没有新增 HTTP 请求。初始源码不认识新设置，此命令应失败；完成目标实现后才应通过。公开脚本仅覆盖集成场景，contract.md 表格所列的生命周期、存储失败、身份、锁与兼容边界也须有针对性回归覆盖。

## 上游兼容回归

```text
python -m pytest tests/test_scheduler.py tests/test_dupefilters.py tests/test_squeues.py tests/test_pqueues.py tests/test_engine.py tests/test_spidermiddleware.py tests/test_pipeline_media.py -q -p no:cacheprovider
```

初始源码已实际完成上述回归：348 passed、2 skipped、12 xfailed。跳过和预期失败来自上游现有测试标记；这组结果不代表新增恢复能力通过。pip check 已通过。

新增材料也已实跑：baseline 完成 35 次 HTTP 请求并得到全部 29 条业务记录；reproduce 的 download、callback、pipeline 三种窗口均在空起点重启后仍缺失 /page/7。具体缺失数量随并发进度变化；本次不以该数量作为实现要求。

本题只提供初始输入和外部可观察检查，没有提供持久调度实现、参考答案或完成后的产物。
