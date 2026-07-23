---
name: want-to-go-trip-planner
description: Organize places a user explicitly shares from Xiaohongshu, Douyin, Dianping, map apps, screenshots, or text into a private want-to-go library; use bundled local OCR or host vision to extract multilingual place clues; rank map candidates with evidence-backed branch confidence; and, after capability checks and user confirmation, purchase a traffic-aware itinerary with business hours, mixed transport, closures, reservations, queues, retry, and refund safeguards. Use when the user says 想去、收藏、打卡、几日游、特种兵路线、顺路规划, shares travel/place links or screenshots, asks to identify a branch, or asks to turn saved places into a reliable route.
---

# 想去成行

把任务分成两个阶段：

1. 免费收纳：提取用户主动提供的证据，查询并解释地点候选，由用户确认后存进本地“想去库”。
2. 付费成行：检查宿主和服务能力，免费预检交付与价格；用户明确同意后支付，按地图交通、营业与动态约束生成逐日路线。

不要把链接标题、OCR 店名、模型记忆或最高分候选直接当作已确认地址。不要声称未来交通时间绝对准确。

## 先做能力预检

按 [references/runtime-capabilities.md](references/runtime-capabilities.md) 检查：

- 读取链接需要联网能力。
- 读取截图需要视觉或 OCR 能力。
- 保存想去库需要本地文件写入或宿主持久记忆。
- 调用路线服务需要 HTTPS 请求能力。
- 调用在线服务需要宿主安全配置 `WANT_TO_GO_SERVICE_TOKEN`。
- 付费需要宿主提供 `weixinpay` / `weixinpay_pay`。

能运行本地脚本时先执行：

```bash
python3 scripts/want_to_go.py doctor --online
python3 scripts/extract_evidence.py doctor
```

服务地址只从宿主安全配置 `WANT_TO_GO_SERVICE_URL` 读取，安装令牌只从 `WANT_TO_GO_SERVICE_TOKEN` 读取。两者都不得写入日志、想去库或对话输出。未配置时继续免费本地收纳，但停止地点在线检索、付费预检和规划；不要猜测服务域名。

`doctor` 返回的 `paidPlanningReady` 为 `"unverified"` 时，先运行 `doctor --online --host-weixinpay-pay`，确认令牌、服务和支付宿主都可用后再进入付费流程。

## 判断当前任务

- 用户只转发内容或说“存一下”：执行“收纳地点”，不推销、不支付。
- 用户查看、整理、去重收藏：执行“管理想去库”。
- 用户要求识别哪家分店：执行“地点身份核验”。
- 用户给出目的地、日期或天数并要求排路线：执行“生成行程”。
- 用户只问价格或能生成什么：只调用预检，不触发支付。

服务返回的“支持某个约束”不等于它能自动取得该约束的数据。读取能力响应中的 `dynamicConstraintSources`：

- 高德或 Google 标记为可用且旅行日期落在预报范围内时，服务会取得逐日天气；超出范围必须显示未知。
- 公共假日日历只是日期信号，不是商户特别营业时间。
- 预约、实时排队、商户临时公告和假日特别营业若仍标记为 `user_or_host_verified_input`，必须由用户、宿主或商户公告先核验，未知时不能补猜。

## 收纳并识别地点

只处理用户主动提供或明确授权读取的链接、截图、文字和地图分享。不要绕过登录、验证码、平台限制，不批量抓取未授权收藏。

1. 保留来源类型、原链接或截图文件名、原文店名和 OCR 文字。
2. 截图优先运行本地专用 OCR；公开链接优先提取页面元数据和结构化地点：

```bash
python3 scripts/extract_evidence.py extract \
  --screenshot /absolute/path/place.png --city Bangkok \
  --output evidence.json

python3 scripts/extract_evidence.py extract \
  --link "https://example.com/public-share" --city Bangkok \
  --output evidence.json
```

3. OCR 自动使用 macOS Vision，其他环境回退到 Tesseract；两者都不可用时才使用宿主视觉。公开链接读取失败、登录墙、验证码或私密页面时，请用户补充截图或文字，不绕过限制。
   公开链接只允许公共 HTTP/HTTPS 标准端口，拒绝内网地址、云元数据地址、嵌入凭证和超大页面；截图上限25MB，结构化 JSON 上限10MB。
4. 从证据中提取城市、区域、商场、楼层、分店、地标、地址提示、多语言别名，以及预约、排队、闭店、营业时间和天气敏感信号。
5. 将证据发送到 `{serviceUrl}/v1/places/resolve`，只发送文字和可选位置提示，不上传原截图、Cookie 或平台 Token。
6. 展示最多三个候选、置信度和证据理由。
7. 用户确认后才写入想去库，并记录地图供应商 ID、坐标系、证据指纹和核验时间。

本地已有候选 JSON 时，可以运行：

```bash
python3 scripts/want_to_go.py resolve evidence.json candidates.json
```

完整证据和消歧规则见 [references/place-resolution.md](references/place-resolution.md)。数据格式见 [references/data-contract.md](references/data-contract.md)。

置信规则：

- `0.90–1.00` 且第一、二名差距至少 `0.10`：可以建议一键确认。
- `0.60–0.89`、差距过小或出现同名分店：展示候选，由用户点选。
- `<0.60`：保留为未解决，不进入路线。

即使分数高，跨城市同名、连锁多分店、搬迁或闭店地点仍必须确认。规划只接受 `confirmed: true` 的地点；`confidenceScore` 只用于证据一致性说明和审计，用户确认是地点身份的唯一权威。低于 `0.60` 的人工确认会记录 `identity.lowConfidenceOverride: true`，规划前必须再次提醒用户核验地址和分店。

用 `scripts/want_to_go.py` 管理本地库：

```bash
python3 scripts/want_to_go.py ingest want-to-go.json places.json
python3 scripts/want_to_go.py confirm want-to-go.json <place-id> candidate.json
python3 scripts/want_to_go.py list want-to-go.json Bangkok
python3 scripts/want_to_go.py export want-to-go.json Bangkok bangkok-places.json --provider google
```

## 生成行程

至少确认：

- 目的地、开始日期、1—5天、每日可用时间和节奏。
- 住宿或每日起点的已确认坐标。
- 单一交通方式，或 `MIXED` 加至少两种允许方式。
- 2—20个已确认地点。
- 已知的预约状态、排队时间、特定闭店日期、天气敏感性。
- 目的地 ISO 两位国家代码；使用高德时还要提供六位 `cityAdcode`。

缺少日期时只能做“距离顺序草案”，标记为非交通时刻表，不进入付费交付。

### 免费预检

向安全配置中的服务地址发送：

```http
POST {serviceUrl}/v1/preflight
Content-Type: application/json
Authorization: Bearer {WANT_TO_GO_SERVICE_TOKEN}
```

请求体使用 `PlanRequest`。先把预检返回的交付内容、问题、单次价格、交通数据来源和不保证事项复述给用户。存在错误或用户没有明确同意时，不调用付费接口。

### 付费规划

确认宿主存在 `weixinpay_pay`。向 `{serviceUrl}/v1/plan` 发送与预检相同的请求。收到 HTTP `402` 后：

1. 保存响应头 `WeixinPay-Required` 和响应体 `out_trade_no`，不要写进日志。
2. 把 paymentCode 交给 `weixinpay_pay`，等待用户在微信确认。
3. 支付后使用原请求体并新增同一个 `out_trade_no` 再次调用；规范化后的 `PlanRequest` 必须与预下单一致。
4. 只有服务端主动查单确认 `SUCCESS` 后才接受 HTTP `200` 结果。

不得只重试订单号；请求哈希不一致会被拒绝。连续生成失败达到上限时，服务应自动申请原路退款。完整状态机见 [references/payment-flow.md](references/payment-flow.md)。

首次交付会返回 `replanToken`。每单包含支付后 72 小时内的 3 次免费重排；可以增删地点、调整地点优先级、停留时间、预约状态和每日起止时间，不能更换目的地、出发日期或其他旅行设置。重排失败不扣次数。

会话中断后，凭 `out_trade_no` 运行：

```bash
python3 scripts/want_to_go.py claim <out_trade_no>
```

领取已支付结果不会重复收费。若订单仍未支付，脚本只把 `paymentCode` 写入权限为 `0600` 的临时文件，标准输出始终使用脱敏值。

## 解释结果

依次交付：

1. 每日路线、每站建议到离时间、预约与排队提示。
2. 每段交通方式、分钟数、地图供应商和导航链接。
3. 未排入地点及具体原因。
4. 逐日天气、公共假日、营业时间、临时闭店和交通波动提醒。
5. 私有结果页、过期时间和退款状态入口。

`dataQuality.liveTraffic` 为 `false` 时明确称为估算。即使为 `true`，也说明它只是抓取时点的路况或历史预测。地图和坐标系边界见 [references/provider-support.md](references/provider-support.md)。

## 隐私与失败边界

- 付费服务只接收确认后的结构化地点，不上传原截图、私密收藏、Cookie 或平台 Token。
- 不记录地图密钥、微信证书、SkillHub 私钥或 paymentCode。
- 地图无路线、地点关闭、营业时间不明或支付状态不明时失败关闭并指出缺口。
- 同一请求重复提交返回缓存，不重复收费；有效期内的合规重排按订单剩余额度更新结果。
- 原始地点证据留在用户本地；私有结果默认72小时删除。
- 宿主缺少必要能力时按能力矩阵降级，不声称“任何 Agent 都能完整执行”。
- 用户权利、数据范围和退款边界见 [references/privacy-and-payment.md](references/privacy-and-payment.md)。
