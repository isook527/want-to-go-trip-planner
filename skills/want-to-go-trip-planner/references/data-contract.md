# 数据契约

## 地点证据

```json
{
  "sourceType": "xiaohongshu_url",
  "name": "原文店名",
  "ocrText": "截图中可见的店名 商场 楼层",
  "aliases": ["英文名", "当地语言名"],
  "city": "Bangkok",
  "area": "Pathum Wan",
  "mall": "centralwOrld",
  "branch": "3F",
  "addressHint": "Rama I Rd",
  "hintLocation": { "lat": 13.746, "lng": 100.539 }
}
```

原链接和截图名只写入本地 `sourceRefs`，不得出现在地点识别请求中。原截图不发送到地点识别服务。返回候选带 `confidenceScore`、`evidenceReasons`、地图 ID、坐标和营业状态。

## 想去库

```json
{
  "schemaVersion": "1.0",
  "updatedAt": "ISO-8601",
  "places": [
    {
      "id": "poi_xxx",
      "name": "地图正式名称",
      "branch": "分店或商场",
      "city": "Bangkok",
      "countryCode": "TH",
      "address": "地图返回的完整地址",
      "lat": 13.746,
      "lng": 100.493,
      "coordinateSystem": "WGS84",
      "providerIds": { "google": "place-id" },
      "aliases": ["Wat Phra Chetuphon"],
      "identity": {
        "canonicalProviderIds": { "google": "place-id" },
        "evidenceFingerprints": ["sha256"],
        "confirmedAt": "ISO-8601",
        "lastVerifiedAt": "ISO-8601",
        "lowConfidenceOverride": false
      },
      "confidenceScore": 0.94,
      "confirmed": true,
      "status": "confirmed",
      "sourceRefs": [
        {
          "type": "xiaohongshu_url",
          "value": "用户主动提供的链接",
          "originalText": "原始店名写法"
        }
      ]
    }
  ]
}
```

`sourceRefs` 只留在用户本地想去库，不发送到付费服务。

## PlanRequest

```json
{
  "trip": {
    "destination": {
      "name": "Bangkok",
      "countryCode": "TH",
      "timezone": "Asia/Bangkok"
    },
    "startDate": "2026-08-10",
    "days": 2,
    "mode": "standard",
    "transport": "MIXED",
    "allowedTransports": ["WALK", "TRANSIT", "DRIVE"],
    "dailyStart": "09:00",
    "dailyEnd": "21:00",
    "returnToStart": true,
    "conditions": {
      "weatherByDate": {
        "2026-08-10": {
          "risk": true,
          "summary": "雷雨",
          "riskReasons": ["severe-weather-condition"]
        }
      },
      "holidaysByDate": {
        "2026-08-11": [
          {
            "name": "Public Holiday",
            "nationalHoliday": true,
            "holidayTypes": ["Public"]
          }
        ]
      }
    },
    "start": {
      "name": "Hotel",
      "city": "Bangkok",
      "lat": 13.746,
      "lng": 100.534,
      "coordinateSystem": "WGS84"
    }
  },
  "places": [
    {
      "id": "poi_xxx",
      "name": "Wat Pho",
      "branch": "",
      "city": "Bangkok",
      "countryCode": "TH",
      "address": "2 Sanam Chai Rd",
      "lat": 13.7466,
      "lng": 100.493,
      "coordinateSystem": "WGS84",
      "providerIds": { "google": "provider-place-id" },
      "confidenceScore": 0.96,
      "confirmed": true,
      "priority": 5,
      "mustVisit": true,
      "dwellMinutes": 90,
      "queueMinutes": 20,
      "reservationStatus": "reserved",
      "closedDates": [],
      "weatherSensitive": false,
      "openingHours": {
        "1": [["08:00", "18:30"]],
        "2": [["08:00", "18:30"]]
      }
    }
  ]
}
```

约束：

- `days`: 1—5
- `places`: 2—20
- `priority`: 1—5
- `dwellMinutes`: 15—480
- `queueMinutes`: 0—360
- `reservationStatus`: `not_required`、`unknown`、`required` 或 `reserved`
- `MIXED` 至少提供两种 `allowedTransports`
- 星期使用 ISO 1—7，1 是星期一
- 跨午夜营业时间拆成两段
- 高德输入用 `GCJ02`；Google 输入用 `WGS84`
- `coordinateSystem` 是确认地点的必填字段；导出和 `PlanRequest` 中全部地点必须与目标地图供应商一致，不一致时整体拒绝，不做坐标转换
- `destination.countryCode` 必须是 ISO 两位代码；高德天气还要求六位 `cityAdcode`
- 逐日天气必须按 `YYYY-MM-DD` 提供；公共假日只作提醒，不直接决定营业
- 路线规划只接受 `confirmed: true`；`confidenceScore` 是证据一致性审计字段，不是确认门槛
- `confidenceScore < 0.6` 的人工确认必须带 `identity.lowConfidenceOverride: true`，预检会强提醒但不阻断
- 服务会丢弃 `sourceRefs` 和未列入契约的原始字段
