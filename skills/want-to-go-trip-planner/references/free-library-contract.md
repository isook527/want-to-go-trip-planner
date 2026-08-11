# 免费想去库与 v2 数据契约

## 商业边界

价格、版本、表单地址和正式文案只读取 `../config/product.json`。

- 免费自己整理：截图、链接、视频和文字收纳；按目的地分库；地点卡与配图；Kornvia 想去护照；基础片区分组；修改、删除、恢复、排序、撤销和本地导出。
- 黄色推荐 ¥199：一个城市、3–7 天、2–15 个地点的人工逐日行程内测，含一次范围内修改和一次双方约定日期的出发前复核；不代订、不持续监控。
- 独立复核能力保留为非公开能力，不在客户交付中展示独立销售入口、价格卡或直接购买表达，也不称为赠品。
- 客户 CTA 只提交服务意愿，不立即扣款。收款顺序固定为：提交意愿 → 确认范围、档期和交付时间 → 顾客确认 → 单独发送付款方式 → 人工登记付款 → 开始交付。

## 共享契约

机器契约是 `shared-data-contract-v2.schema.json`。根对象就是 `library`，必填：

- `schemaVersion / id / revision / createdAt / updatedAt`
- `destinations / places / sources / media`
- `verificationSnapshots / tripRequests / events / tombstones`

关键语义：

- `destination`：一个目的地分区，维护 place/source ID 和显示顺序。
- `place`：可编辑地点；`sourceIds` 指向证据，`mediaIds` 指向媒体，`sortOrder` 控制护照排序。
- `source`：版本化证据账本；`submittedUrl` 只有顾客真实提交 URL 时存在。
- `media`：原图与展示裁切分离；original 记录 SHA-256 且不可覆盖。
- `verificationSnapshot`：一次约定日期复核；保存约定日、实际检查时间、统一事实状态、分层执行风险、能力边界和 diff。它不是持续监控记录。
- `tripRequest`：本地需求记录和人工服务阶段；不等于已 POST、付款、接单或线上同步。状态必须按收款闭环顺序推进。

`bundles` 仅用于 v1.2.3 无损迁移。官网和新调用方必须消费 `sources / media`，不得依赖 `bundles`。

## 必填与可选

Schema 的 `required` 是跨端最低必填；未列入 `required` 的属性为可选。`place` 允许保留地点类型所需的扩展字段，但网站只从对客白名单投影。`source / media / destination / verificationSnapshot / tripRequest` 拒绝未声明字段，避免契约漂移。

### 需求与复核字段

- 新的公开 `tripRequest.offerId` 只能由表单提交 `manual-itinerary-beta`。Schema 继续识别 `pre-trip-review`，只为旧记录和非公开独立能力兼容，不能据此生成销售入口。
- `tripRequest` 最低必填为 `id / offerId / destinationKey / status / createdAt / updatedAt`。人工服务范围确认后补 `agreedReviewDate / deliveryDueAt / scopeConfirmedAt`；之后依次记录顾客确认、单独发送付款方式、人工登记付款和开始交付时间。
- 阶段顺序为 `draft → submitted → scope_schedule_confirmed → customer_confirmed → payment_instructions_sent → payment_recorded → in_delivery → completed`；`declined / cancelled` 是终止状态。不得跳级或倒退。
- 新 `verificationSnapshot` 使用 `trigger: agreed_date_once`，并要求 `agreedReviewDate / agreementConfirmed / serviceContext`。人工服务内还要求 `tripRequestId`，且一个需求只能记录一次包含的复核。
- `factAudits` 统一使用 `unverified / verified / conflict / not_found / stale`；除 `unverified` 外必须有 `checkedAt` 和官方或可信公开来源。地点/路线风险写入 `executionRisks`，行程天气风险写入 `tripRisks`。
- `pre_trip_on_demand_legacy` 只标识迁移进来的旧快照，并要求 `legacyImported: true`。它不证明双方约定过日期，也不能被渲染为当前销售承诺。

## v1.2.3 迁移

迁移步骤固定：

1. `schemaVersion` 升到 `2.3.0`；补 `library id / revision`；2.0.0–2.2.0 来源无损迁移并补平台访问字段。2.3.0 新增抖音、TikTok、Instagram 和 YouTube 的明确平台枚举，不改写旧来源 URL。
2. `destinationCollections` 转为 `destinations`。
3. `bundles.evidence/failures` 无损投影到版本化 `sources`。
4. 截图/视频路径与 SHA-256 投影到 `media`；原始媒体为 immutable。
5. 旧 places 保留 ID 和来源关联，补 `mediaIds / sortOrder / timestamps`。
6. 旧 `pre_trip_on_demand` 快照改标为 `pre_trip_on_demand_legacy`，不伪造约定日期；旧范围或旧状态需求加 `legacyImported`，不篡改原天数和地点数。
7. 建立 migration event；原 v1.2.3 ZIP、预览和源不被修改。

迁移先运行 `migrate --dry-run`。正式迁移使用锁、临时文件、fsync 和原子替换。`repair` 只能重建可推导索引；原图哈希变化必须阻塞。

## 关联示例

下面展示 `destination → place → source/media` 的引用关系，以及复核快照和本地需求记录。截图来源没有 `submittedUrl`，所以对客地点卡不会显示原始链接；只有顾客真实提交的 link source 才带 `submittedUrl`。

```json
{
  "schemaVersion": "2.3.0",
  "id": "library-demo",
  "revision": 1,
  "createdAt": "2026-08-09T00:00:00Z",
  "updatedAt": "2026-08-09T00:00:00Z",
  "destinations": [{
    "id": "destination-bangkok", "key": "bangkok", "name": "曼谷", "status": "confirmed",
    "placeIds": ["place-commons"], "sourceIds": ["source-shot-commons", "source-link-commons"],
    "sortOrder": 0, "createdAt": "2026-08-09T00:00:00Z", "updatedAt": "2026-08-09T00:00:00Z"
  }],
  "places": [{
    "id": "place-commons", "destinationKey": "bangkok", "destinationStatus": "confirmed",
    "name": "theCOMMONS Thonglor", "placeType": "business", "branch": "Thonglor",
    "sourceIds": ["source-shot-commons", "source-link-commons"],
    "mediaIds": ["media-shot-commons-original"], "displayMediaId": "media-shot-commons-original",
    "sortOrder": 0, "createdAt": "2026-08-09T00:00:00Z", "updatedAt": "2026-08-09T00:00:00Z"
  }],
  "sources": [
    {
      "id": "source-shot-commons", "ledgerVersion": 1, "batchId": "batch-demo", "group": "place-commons",
      "type": "screenshot", "status": "captured", "destinationKey": "bangkok",
      "submittedAt": "2026-08-09T00:00:00Z", "mediaIds": ["media-shot-commons-original"],
      "sourcePolicy": {"version": "2.3.0", "accessLevel": "local_only", "canSupport": ["screenshot_content"], "cannotProve": ["original_url"], "untrustedInstructionsDetected": false}
    },
    {
      "id": "source-link-commons", "ledgerVersion": 1, "batchId": "batch-demo", "group": "place-commons",
      "type": "link", "status": "captured", "destinationKey": "bangkok",
      "submittedAt": "2026-08-09T00:00:00Z", "submittedUrl": "https://example.com/customer-submitted", "mediaIds": [],
      "sourcePolicy": {"version": "2.3.0", "accessLevel": "public_readable", "canSupport": ["original_url", "public_page_facts"], "cannotProve": ["future_opening_status"], "untrustedInstructionsDetected": false}
    }
  ],
  "media": [{
    "id": "media-shot-commons-original", "sourceId": "source-shot-commons", "kind": "image", "role": "original",
    "path": "media/originals/commons.png", "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "immutableOriginal": true, "createdAt": "2026-08-09T00:00:00Z"
  }],
  "verificationSnapshots": [{
    "id": "snapshot-bangkok-20260809", "destinationKey": "bangkok", "trigger": "agreed_date_once",
    "agreedReviewDate": "2026-08-09", "agreementConfirmed": true,
    "serviceContext": "manual_itinerary_beta", "tripRequestId": "request-demo",
    "checkedAt": "2026-08-09T00:00:00Z", "sourcePolicyVersion": "2.3.0",
    "items": [{
      "placeId": "place-commons", "accessLevel": "public_readable",
      "canSupport": ["opening_hours_observed"], "cannotProve": ["future_queue"],
      "facts": {"openingHoursText": "每日 08:00–01:00"},
      "factAudits": [{
        "field": "openingHoursText", "status": "verified", "checkedAt": "2026-08-09T00:00:00Z",
        "sources": [{"url": "https://example.com/official-hours", "label": "商户公开营业信息", "kind": "official"}],
        "cannotProve": ["future_queue"], "nextAction": "出发当天再次确认"
      }],
      "executionRisks": []
    }],
    "tripRisks": [{
      "type": "weather_sensitive", "scope": "trip", "status": "unverified",
      "summary": "临近出发日再查官方天气预警", "sources": [],
      "cannotProve": ["future_weather"], "nextAction": "约定复核日查看官方预警"
    }],
    "changes": []
  }],
  "tripRequests": [{
    "id": "request-demo", "offerId": "manual-itinerary-beta", "destinationKey": "bangkok", "status": "completed",
    "startDate": "2026-09-01", "agreedReviewDate": "2026-08-09", "deliveryDueAt": "2026-08-20T09:00:00Z",
    "scopeConfirmedAt": "2026-08-10T09:00:00Z", "customerConfirmedAt": "2026-08-10T10:00:00Z",
    "paymentInstructionsSentAt": "2026-08-10T11:00:00Z", "paymentRecordedAt": "2026-08-11T09:00:00Z",
    "deliveryStartedAt": "2026-08-11T10:00:00Z", "createdAt": "2026-08-09T00:00:00Z", "updatedAt": "2026-08-11T10:00:00Z"
  }],
  "events": [],
  "tombstones": [],
  "ingestBatches": [{
    "id": "batch-demo", "destination": "曼谷", "sourceIds": ["source-shot-commons", "source-link-commons"],
    "createdAt": "2026-08-09T00:00:00Z"
  }]
}
```

完整实体、枚举和条件必填以 JSON Schema 为准。

## English contract summary

Read version, commercial copy, form URL and service scope only from `../config/product.json`. The public offer structure is Free self-organization plus the recommended ¥199 manual day-by-day itinerary beta. The paid beta covers one city, 3–7 days and 2–15 places, one in-scope revision and one pre-trip review on an agreed date; it excludes booking and continuous monitoring. The form records intent only and does not charge on submission.

`shared-data-contract-v2.schema.json` is the machine contract. The library root requires `schemaVersion`, identity/revision timestamps, `destinations`, `places`, `sources`, `media`, `verificationSnapshots`, `tripRequests`, `events` and `tombstones`.

- `destination` owns place/source membership and display order.
- `place` is editable and references evidence/media IDs.
- `source` is a versioned evidence ledger. `submittedUrl` exists only for a URL actually supplied by the customer.
- `media` separates immutable originals from display crops; originals keep SHA-256.
- `verificationSnapshot` records one agreed-date review and its diff, not continuous monitoring.
- `tripRequest` is a local intent/service-stage record, not proof of POST, payment, acceptance or production synchronization.

Keep machine keys and enums language-neutral. Chinese and English customer views must project from the same library without duplicating or translating IDs. Migrate with `migrate --dry-run` first; use file locks, same-directory temporary files, `fsync` and atomic replacement. Repair only derivable indexes; safely merge resolvable duplicate place IDs, and block on changed original hashes, dangling source references or duplicate IDs that cannot be resolved safely.
