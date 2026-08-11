# 地点、分店与完整度

## 名称来源

- `user_named`：用户明确写出的名称。
- `public_page`：实际读取的官方或合法公网页面名称。
- `material_ocr`：截图 OCR 草稿；必须 `nameRequiresConfirmation: true`。
- `visual_review`：实际回看素材后确认出现的名称；地址和开放时间仍需公开来源。
- `unresolved`：未确认。

禁止用模型记忆替代公开来源。

## 地点/分店消歧

至少比较目的地、名称和地址/分店。provider ID 或实际公开 URL 可作为额外证据。

- 同名同地址/分店：可合并来源。
- 同名不同地址/分店：保留多个候选并要求确认。
- 只有名称、没有地址/分店：不得自动选择同名候选。
- route 使用起点、终点和途经点建立 identity。

候选必须声明 `canSupport` 和 `cannotProve`。`same_name_means_same_branch` 始终属于不能证明的事项，除非地址/分店证据一致。

## 公开查询记录

```json
{
  "field": "openingHoursText",
  "status": "not_found",
  "checkedAt": "2026-08-09T08:00:00Z",
  "validUntil": "2026-08-16T08:00:00Z",
  "sources": [
    {
      "url": "https://example.com/place",
      "label": "商户公开页面",
      "kind": "official"
    }
  ],
  "cannotProve": ["future_opening_status"],
  "nextAction": "出发当天再次确认是否临时调整"
}
```

没有实际 URL 的查询不算完成。查询 URL 只进入 audit，不能变成原始收藏按钮。

状态只允许 `unverified / verified / conflict / not_found / stale`。`unverified` 可以没有查询时间和来源，其余状态必须同时有 `checkedAt` 和至少一个官方或可信公开来源。客户只看到安全摘要，不看到内部审计对象。

执行风险分层：地点层记录最后一公里、预约购票和临时关闭；路线层记录换乘与时间缓冲；行程层记录天气敏感。每条风险都要写明当前状态、尚不能证明的部分和下一步动作。

## 分类型完整度

`business`：核实名称、地址、想去理由、出发提醒、营业时间。实际查询未找到营业时间且 audit 完整时，才可提示出发当天复核。

`venue`：核实名称、地址、真实开放时间、想去理由、出发提醒。缺开放时间不得交付。

`public_space`：核实名称、地址/位置、想去理由、出发提醒。无统一时间可说明场内商户各自安排。

`route`：核实路线名、起终点或至少两个途经点、建议时长、路线亮点、出发提醒。

缺项地点保留在库中并计入 `retainedClueCount`，不阻塞其他完整地点。

## English resolution rules

- Treat `user_named`, an actually read public page and confirmed visual evidence as distinct name sources. OCR remains a draft and requires confirmation. Never replace a public-source check with model memory.
- Compare destination, name and address/branch before merging. Same name plus different address means separate candidates; same name without branch evidence must remain unresolved.
- A lookup is complete only when its audit contains the actual public URL. Lookup URLs stay in the audit and never become customer “original saved link” buttons.
- Use only `unverified`, `verified`, `conflict`, `not_found` or `stale`. Every status except `unverified` requires `checkedAt` and at least one official or reliable public source.
- Business places require confirmed name, address, opening hours, reason and departure reminder. Venues require real opening information. Public spaces may state that individual tenants keep their own hours. Routes require a start/end or at least two waypoints, duration, highlight and departure reminder.
- Keep incomplete places in the library and count them in `retainedClueCount`; do not block complete places.
