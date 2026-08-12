# 客户交付白名单

## HTML 允许展示

- 目的地、地点/路线名称、当地名称、分店
- 地址或位置、营业/开放信息、建议时长
- 想去理由、出发提醒、路线
- 顾客实际提交的对应地点原始链接
- 合格展示裁切图；无合格图片时不显示伪造主图
- 未交付线索数量
- visitor mode 提示、deep/standard/compact 中的客户可见字段
- 安全核验摘要：当前状态、最近检查时间、适用期限、可公开来源、尚待确认事项和出发前动作
- 分层执行风险：地点、路线和行程层的客户可见风险标签与建议动作
- 来自唯一配置的两档结构：免费自己整理、黄色推荐的 ¥199 人工逐日行程内测
- ¥199 范围：一个城市、3–7 天、2–15 个地点、一次范围内修改、一次双方约定日期的出发前复核
- “内测期限量接单，提交后确认档期”以及不代订、不持续监控边界
- 页内行程需求表单；提交动作仍由顾客主动触发，按钮写“提交行程需求，不会立即扣款”
- 最少必要信息用途、人工处理方式、服务边界和提交后人工收款闭环

原始链接必须位于对应地点卡下方。图片、视频或文字没有顾客 URL 时，不生成链接模块。
内部来源账本保留顾客提交 URL 的原值；对客投影必须移除 `xsec_token / access_token / signature` 等凭证式查询参数，其他用于定位内容的普通参数继续保留。

## HTML 禁止展示

- `schemaVersion / sourceIds / mediaIds / sourcePolicy / detailLookupAudit / events / tombstones` 等原始内部审计结构
- OCR、置信度、候选分数、错误堆栈、诊断和提示词注入扫描细节
- 绝对路径、隐藏目录、localhost、宿主、模型、接口变量
- 联系方式、Cookies、token、密钥、支付诊断
- 独立复核价格、销售入口、服务下拉选项或直接购买表达
- 未确认的划线正式价、固定每周限单数字、公开个人静态收款码或自动扣款表达
- 把约定日期的一次复核称为赠品、实时监控、持续主动通知或未来状态保证
- 线上商业页面已同步、真实 POST 已成功、已付款或已接单等未经验证状态

纯文字页、评论页和大面积纯色说明页只作证据，不作地点主图。

## 交付前检查

1. renderer 只读取客户字段白名单。
2. HTML 含配置指定的模板标记。
3. 每个原始链接都来自对应地点 source 的 `submittedUrl`。
4. 对客链接不含凭证式查询参数，内部 `submittedUrl` 仍保持原始值以供追溯。
4. `scan --mode customer` 返回 `clean`。
5. 移动端单栏、键盘焦点、表单 label、图片 alt 和打印样式可用。
6. 客户可见 HTML 只出现免费与 ¥199 两档；不出现虚构正式价或独立复核购买入口。
7. 表单只提交服务意愿；没有静态收款码、自动扣款或提交即付款逻辑。
8. 免费自填地点没有实际复核证据时只能显示“尚未人工复核”；只有带检查时间和公开来源的记录才能显示其他状态。

收纳和护照完成回复从 `config/product.json.copy` 读取，不在本文重复维护。

## English customer-output rules

### Allowed in customer HTML

- Destination, place/route name, local name, branch, address, opening information, suggested duration, reason to visit, departure reminder and route.
- A qualified display crop and only the original URL actually submitted for that place. Put the link below its matching place card; do not create a link block for screenshots, videos or text without a customer URL.
- Customer-safe verification summaries and layered execution risks: current status, check time, validity window, public sources, unresolved facts and the next action.
- The two public options from `config/product.json`: Free self-organization and the recommended ¥199 manual day-by-day itinerary beta for one city, 3–7 days and 2–15 places, including one in-scope revision and one review on an agreed date.
- The intent-only request form, minimum-necessary data notice, service boundary and manual payment sequence. Submission must not charge the customer.

### Never expose

- Raw audit or storage fields such as `schemaVersion`, source/media IDs, source policy, lookup audits, events or tombstones.
- OCR confidence, candidate scores, prompts, injection details, stack traces, host diagnostics, absolute paths, hidden directories, cookies, tokens, secrets or customer contact details.
- A standalone review price or purchase entry, a struck-through future price, fixed weekly capacity, static personal payment QR code, automatic charge language, booking or continuous-monitoring claims.
- Unverified claims that a production page, POST, payment or booking succeeded.

Before delivery, require the configured template marker, a clean customer scan, mobile single-column layout, keyboard focus, form labels, image alt text and print-safe output. Free user-entered places without review evidence must remain “Not manually reviewed.”
