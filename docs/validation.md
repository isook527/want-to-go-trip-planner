# 实测记录

样例使用公开仓库内的曼谷合成数据。护照由本地 CLI 生成，版式用 Chromium 的 390px 手机视口和 1280px 桌面视口查看，未把示例营业时间作为实际出行信息。

| 场景 | 条件 | 观察与判定 |
| --- | --- | --- |
| v3.1 → v3.2 手机护照 | 390px 视口，同一曼谷证据样例 | 原报告基线为 6,307px，发布验收同环境复测为 6,294px。v3.2.0 为 2,854px，首日标题距页顶 328px。 |
| v3.2.1 手机护照 | 同样例及 390px 视口 | 整页 2,834px，首日标题仍距页顶 328px。四个片区均可见。 |
| 行程提示 | 原曼谷两日路线 | 原问题报告为 16 条，当前夹具复测有 12 条明细。v3.2 起同类项合并为 6 组，无阻断。带片区的 A→B→A 合成路线触发折返建议。 |
| 对客扫描 | 旧样式片段含 `future_queue` 和原始 ISO 时间戳 | 旧片段被拦截，新护照中英文均通过。 |
| 轻量版 | 同一 Kornvia 模板，无图或无路线时也可渲染 | 中英文扫描通过。零片区时隐藏摘要，通用 Day 标签去重，标题与路线天数不符时提醒并继续生成。 |
| XLSX / ICS | 两天日期为 2030-05-10 至 2030-05-11 | 工作簿可解包并解析 XML，ICS 能解析两条全天事件及其正确的起止日期。 |
| 发布包 | 用当前 Skill 构建并校验 ZIP | 包内文件数量、哈希和 SHA-256 边车文件一致。仓库 README、示例、测试与 CI 不进入安装包。 |

本地软件验收使用 macOS Apple Silicon、Python 3.9 和 Node.js 22。CI 使用 Ubuntu、macOS、Windows，分别搭配 Python 3.9 与 3.12 和 Node 20。Ubuntu 26.04 缺少 setup-python 的 3.9 构建，Ubuntu 与 Windows 的 3.9 任务用 uv 固定 CPython 3.9.25。[查看 GitHub Actions](https://github.com/isook527/want-to-go-trip-planner/actions/workflows/test.yml)
