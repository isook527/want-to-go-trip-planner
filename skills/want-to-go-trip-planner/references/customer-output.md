# 客户交付白名单

## 允许展示

- 目的地、地点或路线名称
- 地址、位置描述、营业或开放信息
- 想去理由、特色、出发提醒
- 用户原始收藏链接按钮（仅限用户实际提交的 URL；纯图片来源不显示链接模块）
- 顾客上传的地点图片或截图；OCR 失败不影响原图保留
- 地点主图使用 4:3 展示裁切；同组多图只选照片信息最丰富的一张
- “另有 N 条地点线索已保留，核实后可补进下一版”
- 页内“¥39.9 完整逐日行程”需求表单；公开需求页只作提交失败兜底

## 禁止展示

内部字段、执行过程、候选分数、错误堆栈、绝对路径、隐藏目录、localhost、宿主名称、模型名称、OCR 过程、下载目录扫描、附件时间推断、支付测试价、测试或内测状态。

纯文字页、评论页或大面积纯色说明页只作内部证据，不得显示为地点主图；没有照片时应请求补图，不得从文字截图中虚构图片。

## 收纳完成文案

中文：

> 已收进你的{destination}想去库：{received}项。{failed_note}这次只做收纳，没有生成护照。想把收藏整理成一份能直接查看的网页攻略，可以说“生成{destination}想去护照”；护照会保留地点、公开可核实信息、想去理由、出发提醒和原始收藏入口。

英文：

> Saved {received} item(s) to your {destination} want-to-go library. {failed_note}No passport was generated. When you are ready, say “Create my {destination} Go passport” to turn the collection into a visual web guide with place details, verified public information, visit notes, reminders, and original source links.

## 护照完成文案

中文：

> 已整理成{destination}想去护照，并已打开网页版本。完整地点已先交付；仍待核实的线索继续保留在想去库。想把这些地点排成可以直接照着走的逐日安排，可以提交 ¥39.9 完整逐日行程需求。

英文：

> Your {destination} Go passport is ready and the web version has been opened. Complete places are included now; unresolved clues remain safely stored. For a day-by-day route with timing and transport, request the ¥39.9 complete itinerary.
