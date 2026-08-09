# 免费想去库与 v2 数据契约

## 商业边界

价格、版本、表单地址和正式文案只读取 `../config/product.json`。

- 免费：截图、链接、视频和文字收纳；按目的地分库；地点卡；Kornvia 想去护照；基础片区分组；修改、删除、恢复、排序、撤销和导出。
- ¥39.9：用户按需触发的出发前复核，交付本次检查事实和相对上一快照的 diff；不包含完整逐日行程，不持续监控。
- ¥199：一个城市、1–3 天、2–10 个地点的人工逐日行程内测，含一次范围内修改；不代订、不持续监控。

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
- `verificationSnapshot`：一次按需复核；保存检查时间、事实、能力边界和 diff。
- `tripRequest`：本地需求记录；不等于已 POST、付款、接单或线上同步。

`bundles` 仅用于 v1.2.3 无损迁移。官网和新调用方必须消费 `sources / media`，不得依赖 `bundles`。

## 必填与可选

Schema 的 `required` 是跨端最低必填；未列入 `required` 的属性为可选。`place` 允许保留地点类型所需的扩展字段，但网站只从对客白名单投影。`source / media / destination / verificationSnapshot / tripRequest` 拒绝未声明字段，避免契约漂移。

## v1.2.3 迁移

迁移步骤固定：

1. `schemaVersion` 升到 `2.1.0`；补 `library id / revision`；2.0.0 来源无损迁移并补平台访问字段。
2. `destinationCollections` 转为 `destinations`。
3. `bundles.evidence/failures` 无损投影到版本化 `sources`。
4. 截图/视频路径与 SHA-256 投影到 `media`；原始媒体为 immutable。
5. 旧 places 保留 ID 和来源关联，补 `mediaIds / sortOrder / timestamps`。
6. 建立 migration event；原 v1.2.3 ZIP、预览和源不被修改。

迁移先运行 `migrate --dry-run`。正式迁移使用锁、临时文件、fsync 和原子替换。`repair` 只能重建可推导索引；原图哈希变化必须阻塞。

## 关联示例

下面展示 `destination → place → source/media` 的引用关系，以及复核快照和本地需求记录。截图来源没有 `submittedUrl`，所以对客地点卡不会显示原始链接；只有顾客真实提交的 link source 才带 `submittedUrl`。

```json
{
  "schemaVersion": "2.1.0",
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
      "sourcePolicy": {"version": "2.1.0", "accessLevel": "local_only", "canSupport": ["screenshot_content"], "cannotProve": ["original_url"], "untrustedInstructionsDetected": false}
    },
    {
      "id": "source-link-commons", "ledgerVersion": 1, "batchId": "batch-demo", "group": "place-commons",
      "type": "link", "status": "captured", "destinationKey": "bangkok",
      "submittedAt": "2026-08-09T00:00:00Z", "submittedUrl": "https://example.com/customer-submitted", "mediaIds": [],
      "sourcePolicy": {"version": "2.1.0", "accessLevel": "public_readable", "canSupport": ["original_url", "public_page_facts"], "cannotProve": ["future_opening_status"], "untrustedInstructionsDetected": false}
    }
  ],
  "media": [{
    "id": "media-shot-commons-original", "sourceId": "source-shot-commons", "kind": "image", "role": "original",
    "path": "media/originals/commons.png", "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "immutableOriginal": true, "createdAt": "2026-08-09T00:00:00Z"
  }],
  "verificationSnapshots": [{
    "id": "snapshot-bangkok-20260809", "destinationKey": "bangkok", "trigger": "pre_trip_on_demand",
    "checkedAt": "2026-08-09T00:00:00Z", "sourcePolicyVersion": "2.1.0",
    "items": [{"placeId": "place-commons", "status": "checked"}], "changes": []
  }],
  "tripRequests": [{
    "id": "request-demo", "offerId": "pre-trip-review", "destinationKey": "bangkok", "status": "draft",
    "startDate": "2026-09-01", "createdAt": "2026-08-09T00:00:00Z", "updatedAt": "2026-08-09T00:00:00Z"
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
