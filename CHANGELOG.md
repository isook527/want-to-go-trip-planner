# 更新记录 · Changelog

## 3.2.3

- 修正中英文护照页面标题，并补充页面描述和分享预览信息。
- 仓库历史已于 2026 年 11 月重写以移除提交元数据中的个人信息。改写前后的文件树和已发布 ZIP 一致。已有克隆或 fork 请按公告同步，已下载 ZIP 无需操作。[同步公告](https://github.com/isook527/want-to-go-trip-planner/issues/5)

English: Updated the passport titles, descriptions and social previews. In November 2026 the repository history was rewritten to remove personal information from commit metadata. File trees and previously published ZIPs stayed identical across the rewrite. See the notice to sync existing clones and forks. ZIP users need no action. [Sync notice](https://github.com/isook527/want-to-go-trip-planner/issues/5)

## 3.2.2

- 中英文 README 和仓库首页链接直达 GitHub Pages 的曼谷护照预览。
- 桌面票根显示日期范围或行程天数，以及最多三个片区。更新曼谷示例与预览图。

English: Added a live Bangkok passport preview on GitHub Pages. The desktop stub now shows trip dates or day count and up to three areas. Regenerated the example and overview image.

## 3.2.1

- 对齐 Kornvia 开源仓库结构。增加中英文 README、贡献指南、私密安全报告入口、曼谷示例和六组 CI。
- 安装包只含根目录下的 Skill。测试、文档和示例留在仓库里。
- 隐藏未知片区的空摘要，精简通用 Day 胶囊和过期核验文案。轻量渲染在标题天数与实际不符时给出非阻断提示。
- 说明小红书、携程和公众号默认只保留链接，自动读取需要通过校验的可选适配器。

English: Reorganized the public repository and documented installation, testing and private vulnerability reporting. Kept the ZIP scoped to the Skill. Hid empty area summaries, simplified Day links and expired-hour copy, and added a non-blocking warning for title/day mismatches. Documented the default link-only path for optional platform adapters.

## 3.2.0

- 以对客日期和文案替代内部代码，扫描拦截旧 HTML 泄露。核验默认收起，打印展开。
- 压缩手机首屏，加入逐日胶囊导航。地点片区支持显式字段和地址分组。
- `lint-route` 按阻断、建议、提示分级并合并同类项。轻量版和证据版统一护照模板。

English: Added traveler-facing verification copy, collapsed details, compact mobile layout, area grouping, severity-based route linting and a shared passport template.

## 3.1.0

- 新增行程数值检查、结构化开放时间与跨城航班。过期事实降级，并支持 T-7、T-3、T-1 复核。
- ICS 保持稳定 UID，XLSX 增加预算表、冻结表头和日期单元格。轻量 HTML 增加泄露扫描。

English: Added route checks, structured opening hours, flights, fact expiry, review milestones, stable calendar identity, styled budget exports and a lightweight HTML leak scan.
