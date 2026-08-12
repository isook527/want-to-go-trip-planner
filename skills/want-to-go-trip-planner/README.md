# Kornvia 想去就出发 | Want to Go

当前版本：v2.4.3<br>
Current version: v2.4.3

共享数据契约 / Shared data contract: 2.3.0<br>
支持系统 / Supported systems: macOS and Windows

## 中文简介

想去就出发把小红书、抖音、TikTok、Instagram、YouTube、携程、公众号和马蜂窝里的旅行收藏，按目的地收进本地想去库；需要时生成带配图、地点信息与原始来源的中英文「想去护照」。

## Short description

Want to Go organizes travel saves from Xiaohongshu, Douyin, TikTok, Instagram, YouTube, Ctrip, WeChat articles and Mafengwo into a local library by destination. When requested, it creates a Chinese or English Want-to-go passport with place images, useful details and the original links you submitted.

## 中文介绍

### 把收藏夹里的旅行灵感，整理成能继续修改、能直接打开的「想去护照」

刷到一家店时顺手收藏很轻松。等到真的准备曼谷、上海或下一座城市的行程，地点散在不同平台，分店、营业时间和原帖又要重新翻。

把本轮的小红书完整笔记链接、抖音或 TikTok 公开视频、Instagram 公开帖子或 Reel、YouTube 视频、携程地点链接、公开公众号文章、马蜂窝链接、截图、原视频或文字交给「想去就出发」。它会先按目的地存进本地想去库。

只有你明确说「生成曼谷想去护照」后，它才会制作可浏览、可分享的网页，不会收到收藏就擅自写攻略。

一份想去护照可以包括：

1. 地点与分店名称、地址、营业信息、想去理由和出发提醒。
2. 原始图片与单独生成的展示裁切图；原图保留 SHA-256，不会被裁切图覆盖。
3. 只有你实际提交过网址时才出现的来源按钮；对客网页会移除 `xsec_token`、`access_token`、`signature` 等敏感查询参数。
4. 按片区整理的地点卡，以及 standard、compact、deep 三种内容深度。
5. 中文或英文想去护照。当地名称保留原文，无法确认的事实会明确标注。

想去库可以长期维护。地点卡支持修改、删除、恢复、排序、撤销、重新生成和本地导出。同一地点有多张素材时，会逐张判断用途，优先选照片做展示图；纯文字截图继续留作来源证据。OCR 识别出的地点名只是待确认草稿，不会直接冒充已核实结果。

### 平台读取边界

- 小红书自动读取需要含 `xsec_token` 的完整笔记网址。只有短链接时会先保留网址，再请你补完整链接或截图。
- 携程链接请同时提供地点名称和目的地；遇到同名分店时不会默认选择排序最前的结果。
- 公众号只读取可以公开打开的文章。马蜂窝触发安全检测时不会绕过验证，需要补截图、保存网页或文字。
- 抖音只检查本轮可以访问的公开作品页。TikTok 和 YouTube 读取公开视频的公开嵌入元数据，不下载视频或字幕。
- Instagram 支持公开帖子与 Reel，不支持 Story。页面资料不足时，需要补地点名、截图、原视频或文字。
- 页面被登录墙、地区限制或安全检测拦住时，会保留原链接并说明缺什么，不会假装已经读到正文。

### 免费功能与人工服务

- 免费功能：素材收纳、按目的地分库、地点卡与配图、想去护照、基础片区分组、修改和本地导出。
- 可选人工服务：¥199 逐日行程内测，覆盖一个城市、3–7 天、2–15 个地点，含一次范围内修改和一次双方约定日期的出发前复核。
- 服务限制：不代订，不持续监控；提交需求时不会立即扣款，需先人工确认范围、档期和交付时间。

## English introduction

### Turn scattered travel saves into an editable Want-to-go passport

Saving a restaurant or attraction takes a second. Planning a real trip later means reopening several apps, separating branches and checking whether the address or hours can still be confirmed.

Send the items from your current session to Want to Go: a full Xiaohongshu note URL, a public Douyin or TikTok video, an Instagram post or Reel, a YouTube video, a Ctrip place link, a public WeChat article, a Mafengwo link, screenshots, original videos or text.

The Skill saves them in a local library grouped by destination. It creates a shareable web passport only after you ask for one, such as “Create my Bangkok Want-to-go passport.”

A passport can include:

1. Place and branch names, addresses, opening information, reasons to visit and departure reminders.
2. Preserved original images plus separate display crops. Display processing never overwrites the original file.
3. Source buttons only for URLs you submitted. Credential-like parameters such as `xsec_token`, `access_token` and `signature` are removed from customer-facing pages.
4. Place cards grouped by area, with standard, compact and deep content modes.
5. Chinese or English output. Local names stay in their original script, and unconfirmed facts are labelled instead of filled in from guesswork.

The local library remains editable. You can update, delete, restore, reorder, undo, regenerate and export place cards. When one place has several images, the Skill evaluates each image separately and prefers a photo for display. Text-heavy screenshots remain attached as evidence. A name found only through OCR stays marked for confirmation.

### Platform limits

- Xiaohongshu automatic reading requires a full note URL containing `xsec_token`. A short link is retained, but you may be asked for the full URL or screenshots.
- Include the place name and destination with a Ctrip link. The Skill will not automatically select the top result when several branches match.
- WeChat articles must be publicly accessible. If Mafengwo presents a security check, provide screenshots, a saved page or pasted text.
- Douyin is limited to public video pages available in the current run. TikTok and YouTube use public embed metadata; videos and captions are not downloaded.
- Instagram supports public posts and Reels. Stories are unsupported. Incomplete pages require a place name, screenshots, the original video or text.
- If a login wall, regional restriction or security check blocks access, the original URL is retained and the missing evidence is reported. The Skill does not claim that blocked content was read.

### Free tier and manual service

- Free tier: capture, destination libraries, place cards and images, Want-to-go passports, basic area grouping, edits and local export.
- Optional manual service: ¥199 day-by-day itinerary beta for one city, 3–7 days and 2–15 places, including one in-scope revision and one pre-trip review on an agreed date.
- Service limits: no booking and no continuous monitoring. Sending a request does not charge the customer; scope, availability and delivery timing are confirmed manually first.

## 包内文件 | Package contents

- `README.md`: 你正在阅读的中英文产品介绍 / this bilingual product introduction.
- `SKILL.md`: Skill 的执行规则与命令 / operating rules and commands.
- `config/product.json`: 当前版本、品牌、平台与服务契约 / version, brand, platform and service contract.
- `references/`: 数据契约、来源规则和客户输出要求 / data, source and customer-output contracts.
- `scripts/` and `renderer/`: 本地整理、OCR、编辑、扫描和网页护照生成工具 / local capture, OCR, editing, scanning and passport rendering tools.

正式 ZIP 旁的 `.sha256` 文件用于检查下载是否完整。HTML 预览文件展示当前奶油色 `#F6E8C8` 的想去护照页面。

The `.sha256` file beside the release ZIP verifies download integrity. The HTML preview shows the current Want-to-go passport with the `#F6E8C8` cream canvas.
