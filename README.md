# 想去就出发 · Want to Go

把收藏的旅行截图、链接和视频变成能走通的逐日行程和地点护照。

![曼谷护照总览，左为手机，右为桌面](docs/images/bangkok-passport-overview.png)

[English](README.en.md) · [下载 Skill](https://github.com/isook527/want-to-go-trip-planner/releases/latest) · [查看曼谷示例](examples/bangkok/passport.html)

## 从收藏到出发

| 步骤 | 得到什么 |
| --- | --- |
| 收纳 | 保留截图、链接、视频和文字，按目的地收进本机想去库。 |
| 核对地点与分店 | 用地址和来源区分同名地点，待确认的保留为线索。 |
| 排路线 | 按天排必去、休息和备选。用 `lint-route` 处理阻断、解释建议、查看提示。 |
| 带走 | 打开带图地点护照，浏览器打印为 PDF，或导出 ICS 日历与 XLSX 表格。 |
| 行前复核 | 按约定在 T-7、T-3、T-1 核对开放、预约、天气和交通，查看变化及出发清单。 |

## 它不会做什么

不订票，不后台监控，不绕过登录、验证码或访问限制。营业时间和交通时长没有依据时会标为待确认。收藏库、原始素材和输出都保存在本机。

## 安装

从 [Release](https://github.com/isook527/want-to-go-trip-planner/releases/latest) 下载 ZIP 并解压，或直接从仓库复制 [`want-to-go-trip-planner/`](want-to-go-trip-planner/) 整个目录。

| 宿主 | 把 Skill 目录复制到 |
| --- | --- |
| Claude Code | 全局 `~/.claude/skills/`，或项目内 `.claude/skills/` |
| Codex | 项目内 `.agents/skills/` |

需要 Python 3.9+、Pillow 和 Node.js 18+。Tesseract、FFmpeg 和 `opencli` 可选。首次使用在包含 Skill 目录的位置运行 doctor。

```bash
python3 want-to-go-trip-planner/scripts/extract_evidence.py doctor --locale zh-CN
```

```powershell
.\want-to-go-trip-planner\scripts\doctor_windows.ps1 -Locale zh-CN
```

## 快速开始

1. 「这些收藏帮我存起来。」
2. 「生成曼谷想去护照。」
3. 「出发前帮我再核一遍。」

## 平台支持

小红书、携程和微信公众号**默认关闭自动读取**。没有通过完整性检查的可选 `opencli` 时，只保留链接并请你补截图或文字。安装受信版本并通过 doctor 后，才会启用这三类自动读取。[查看详细能力和限制](want-to-go-trip-planner/references/provider-support.md)

| 平台 | 能自动读到什么 | 读不到时怎么办 |
| --- | --- | --- |
| 小红书 | 适配器可读取可访问的完整笔记正文和配图。 | 保留链接，补截图或文字。 |
| 携程 | 适配器用地点名和链接核对候选地点。 | 补地点名、截图或文字，同名分店请确认。 |
| 微信公众号 | 适配器可读取可访问的公开文章标题、正文及图片链接。 | 保留链接，补截图、保存页或文字。 |
| 抖音 | 公开作品页本次返回的标题、描述等信息。 | 补原视频、关键截图或文字。 |
| TikTok、Instagram、YouTube | 公开 oEmbed 实际返回的标题、缩略图或嵌入信息。 | 补截图、文字或原视频。 |
| 马蜂窝、大众点评 | 保留链接和用户提供的本地证据。 | 补截图、保存页或文字。 |

## 隐私与合规

自动读取仅供个人整理自己已收藏的内容。使用者需自行遵守各平台服务条款。本项目不登录、不导出 Cookie，也不绕过验证码或访问限制。平台名称仅用于说明兼容性，本项目与各平台无关联。

## 测试与贡献

用 Python `unittest` 重放 [曼谷示例](examples/bangkok/README.md)，覆盖泄露扫描、片区、路线检查、轻量护照、导出与打包。[实测记录](docs/validation.md) · [贡献指南](CONTRIBUTING.md) · [安全问题报告](SECURITY.md)

## 许可证

代码以 [MIT](LICENSE) 发布。Kornvia 名称与标识不在 MIT 授权范围内。
