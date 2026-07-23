# SkillPay 付费流程

## Agent 侧

1. `POST /v1/preflight`，把交付内容、问题和价格告诉用户。
2. 用户同意后，以同一请求调用 `POST /v1/plan`。
3. 收到 HTTP `402` 时保存：
   - 响应头 `WeixinPay-Required`
   - 响应体 `out_trade_no`
4. 调用宿主的 `weixinpay_pay(paymentCode=...)`。
5. 用户确认后，以原请求加 `out_trade_no` 重试 `/v1/plan`；规范化后的请求必须与预下单一致。
6. 只接受服务端返回的 HTTP `200 SUCCESS`。

## 服务侧

1. 创建微信 Native 订单。
2. 把 `code_url` 包进 SkillPay L2 `payment_required`。
3. 用 SkillHub 开发者 RSA 私钥签名 L1 预下单请求。
4. 将 `payment_code` 放入 HTTP `402`。
5. 重试时主动查询微信订单；仅 `trade_state=SUCCESS` 才生成路线。
6. 用请求哈希绑定订单，防止一个已支付订单替换为另一套行程。
7. 原子保存首次交付结果；重复请求返回缓存并标记 `already_fulfilled: true`。
8. 通过微信通知验签和定时主动查单双重对账。
9. 连续规划失败达到上限时，使用确定的退款单号自动申请全额退款。
10. 自动退款接口失败时自动创建人工退款复核工单并发送外部告警。
11. 用户持结果凭证调用 `/v1/support/refund-request` 创建幂等售后工单。
12. 管理员通过受保护接口查看工单；核验后可以对已交付订单执行人工原路退款。
13. 支付、退款、对账和售后异常只发送最小化订单标识到 HTTPS 告警端点。

## 请求哈希规范

- 哈希对象是 `PlanRequest` 的 JSON 规范化形式：UTF-8、对象键按字典序排序、无多余空白，数字使用 JSON 的最短有效表示。
- 哈希前剔除顶层 `out_trade_no` 和顶层 `replanToken`。
- 使用 SHA-256。服务端以哈希、安装身份和订单号共同约束支付归属。

## 订单内免费重排

- 首次成功交付返回随机 `replanToken`、剩余次数和到期时间。
- 每个已支付订单包含 3 次免费重排，支付后 72 小时失效。
- 重排调用 `POST /v1/plan`，提交原 `out_trade_no`、`replanToken` 和修改后的 `PlanRequest`。
- 可以增删地点，总数仍为 2—20；可以调整地点 `priority`、`dwellMinutes`、`reservationStatus`，以及 `trip.dailyStart` 和 `trip.dailyEnd`。
- 不允许更换目的地、`startDate` 或其他旅行设置。越界、过期、令牌错误或额度用尽返回 HTTP `409`，用户可以创建新订单。
- 只有新路线原子写入成功后才扣一次额度。同一重排请求重试返回缓存，不重复扣次数；生成失败不扣次数，也不会错误退款已经成功交付的原订单。

## 断链恢复

`GET /v1/plan/claim?out_trade_no=...` 要求安装 Bearer 令牌与订单归属一致：

- 微信订单已支付且结果存在：返回缓存并标记 `already_fulfilled: true`。
- 微信订单已支付但结果尚未生成：使用订单中加密保存的结构化 `PlanRequest` 继续生成；临时失败时允许继续领取。
- 未支付且支付挑战仍有效：返回 HTTP `402` 与原 `paymentCode`。
- 支付挑战或私有结果已经过期：返回明确的过期状态，不重新扣款。

本地 `claim` 命令不会在标准输出显示 `paymentCode` 明文；如需继续支付，只把原值写入权限为 `0600` 的临时文件。

## 两套密钥不可混用

- 微信商户 API 私钥与平台证书：创建、查询并验证微信订单。
- SkillHub 开发者私钥：签名 Agent Pay 预下单。

私钥只允许放在服务端环境变量或密钥管理系统。Skill 包、日志和结果中不得出现私钥。

生产订单必须使用带唯一约束和事务的 PostgreSQL，并加密订单载荷。生产前必须用真实企业 SkillHub 账户、微信商户号和官方预下单地址完成0.01元支付、交付、退款和对账联调。没有这些条件时只能使用 `PAYMENT_MODE=mock` 测试状态机。
