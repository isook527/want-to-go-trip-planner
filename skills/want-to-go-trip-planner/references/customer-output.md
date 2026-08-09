# 客户交付白名单

## HTML 允许展示

- 目的地、地点/路线名称、当地名称、分店
- 地址或位置、营业/开放信息、建议时长
- 想去理由、出发提醒、路线
- 顾客实际提交的对应地点原始链接
- 合格展示裁切图；无合格图片时不显示伪造主图
- 未交付线索数量
- visitor mode 提示、deep/standard/compact 中的客户可见字段
- 来自唯一配置的免费、¥39.9 出发前复核、¥199 人工逐日行程内测范围
- 页内需求表单；提交动作仍由顾客主动触发

原始链接必须位于对应地点卡下方。图片、视频或文字没有顾客 URL 时，不生成链接模块。

## HTML 禁止展示

- `schemaVersion / sourceIds / mediaIds / sourcePolicy / detailLookupAudit / events / tombstones`
- OCR、置信度、候选分数、错误堆栈、诊断和提示词注入扫描细节
- 绝对路径、隐藏目录、localhost、宿主、模型、接口变量
- 联系方式、Cookies、token、密钥、支付诊断
- “¥39.9 完整逐日行程”或任何超出唯一配置的承诺
- 线上商业页面已同步、真实 POST 已成功、已付款或已接单等未经验证状态

纯文字页、评论页和大面积纯色说明页只作证据，不作地点主图。

## 交付前检查

1. renderer 只读取客户字段白名单。
2. HTML 含配置指定的模板标记。
3. 每个原始链接都来自对应地点 source 的 `submittedUrl`。
4. `scan --mode customer` 返回 `clean`。
5. 移动端单栏、键盘焦点、表单 label、图片 alt 和打印样式可用。

收纳和护照完成回复从 `config/product.json.copy` 读取，不在本文重复维护。
