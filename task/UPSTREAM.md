# 代码来源

- 上游公开仓库：https://github.com/scrapy/scrapy
- 固定版本：Scrapy 2.13.3。
- 上游提交：155a504f24b9dd180b8786a4fa709f87cfe8fb66
- 固定链接：https://github.com/scrapy/scrapy/commit/155a504f24b9dd180b8786a4fa709f87cfe8fb66
- 许可证：BSD-3-Clause；根目录 LICENSE、AUTHORS 和源码版权声明均保留。
- 取得方式：从本机已有的上游 Git 对象副本按 2.13.3 标签克隆，使用 --no-hardlinks 隔离对象；GitHub API 已独立核验标签及完整提交。没有带入上一题的附加文件或改动。
- 本题新增：prompt.txt、task/ 中的契约、本地合成服务及采集检查、依赖版本与运行说明；根 .gitignore 增加运行产物和秘密文件排除项。没有预先实现本题要求的恢复能力。
- 源码关注点：scrapy/core/scheduler.py、scrapy/core/engine.py、scrapy/core/scraper.py、scrapy/dupefilters.py、scrapy/squeues.py、scrapy/pqueues.py 及相关 tests。任务实质依赖这些既有调用链和兼容语义。
- 上游已有 JOBDIR 限制见 docs/topics/jobs.rst：异常退出可能损坏作业；本题属于新增保证，不把该限制伪称为已承诺能力的缺陷。

初始环境的实际检查结果见同目录 README.md。合成站点只监听 127.0.0.1；素材不来自真实生产站点，也不需要第三方采集授权或凭据。
