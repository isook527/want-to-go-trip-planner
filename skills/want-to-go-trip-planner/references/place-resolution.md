# 地点核实与完整度

## 名称来源

- `user_named`: 用户在本轮文字中明确写出的地点名，可作为已确认名称。
- `public_page`: 已实际读取的官方或合法公开网页名称，可作为已核实名称。
- `material_ocr`: 从截图 OCR 自动提取的名称，只能作为草稿，必须设置 `nameRequiresConfirmation: true`。
- `visual_review`: 视觉模型实际回看原图后确认素材明确出现的名称，可作为素材内确认名称；补充地址和营业时间仍需公开来源。

禁止用模型记忆替代公开来源。

## 查询记录

查找地址或营业时间时，在地点的 `detailLookupAudit` 中记录：

```json
{
  "field": "openingHoursText",
  "status": "not_found",
  "checkedAt": "2026-07-31T08:00:00Z",
  "checkedUrls": ["https://example.com/place"],
  "reason": "合法公开来源未公布营业时间"
}
```

没有实际 URL 的查询不算完成。

## 部分交付

护照按地点逐项判断。合格地点进入本次护照，缺项地点留在想去库并计入 `retainedClueCount`。不得整本拒绝，也不得把缺项用模型记忆补齐。

