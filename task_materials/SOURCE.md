# 源码来源

- 公开仓库：https://github.com/scrapy/scrapy
- 上游版本：2.13.3
- 固定提交：`155a504f24b9dd180b8786a4fa709f87cfe8fb66`
- 固定链接：https://github.com/scrapy/scrapy/commit/155a504f24b9dd180b8786a4fa709f87cfe8fb66
- 上游 tree：`23070c2e49cb5f267cff4b6ad682ac0788b31ab7`
- 许可证：BSD-3-Clause；根目录 `LICENSE`、`AUTHORS` 及各文件版权声明原样保留。
- 取得方式：GitHub API `repos/scrapy/scrapy/tarball/155a504f24b9dd180b8786a4fa709f87cfe8fb66`，解包固定提交归档；随后成功通过 Git 获取该固定提交，以它作为本题提交的父提交，并核对原有文件未改动。来源 SHA 并非本题初始快照 SHA。

上游代码与测试未预先改动。现有缓存按请求指纹读取一份响应，尚未实现本题可选的变体保存和 304 元数据刷新。新增内容仅为根目录 `prompt.txt`、`task_materials/` 中的题目契约、依赖约束、合成本地源站和公开检查，以及 `.gitignore` 中的本地产物排除项。未加入目标功能的完整解法或修复版本。

本地源站返回的语言、地区、正文、ETag 和旧缓存内容均为自建测试数据；没有生产数据、外部网站授权或真实服务凭据的依赖。`legacy_seed.py` 的格式取自上述固定版本 `scrapy/extensions/httpcache.py`，只生成原有格式，不规定待开发的新格式。

起点验证环境为 Windows / CPython 3.13.15：HTTP 缓存、请求和指纹的 237 项上游回归通过，9 项源站协议检查通过，依赖一致性检查通过。81 项新增契约与真实爬虫检查中，24 项现有行为对照通过，57 项因尚未实现本题能力而失败；没有导入、收集或进程启动错误。这是初始状态记录，不表示目标功能已经完成。
