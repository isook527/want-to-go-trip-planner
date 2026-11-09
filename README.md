# 想去就出发 · Want to Go

把旅行截图、链接、视频和随手记收进本地想去库。准备出发时，让你的 Agent 把收藏变成带地图入口的地点护照和逐日行程。

**所有功能免费，MIT 开源。** 收藏、OCR 与视频证据、地点核实、带图护照、逐日安排、修改与撤销、迁移修复、出发前复核和导出都在一个 Skill 里。无需注册、激活、配置地图 API Key 或连接 Kornvia 服务。

## 安装

1. 下载 [单 Skill ZIP](dist/want-to-go-trip-planner-skill-3.0.0.zip) 并解压，或从源码复制整个 [Skill 文件夹](skills/want-to-go-trip-planner)。
2. 将 `want-to-go-trip-planner` 文件夹放入支持本地 Skill 的 Agent（例如 Codex 的 `~/.codex/skills/`，或 Claude Code 的 `~/.claude/skills/`），重新打开 Agent。已装旧版时，请停用旧 Skill 以免重复触发。
3. 发一张旅行截图或一条链接，说：“用想去就出发收藏这些地方。”准备好后，可以说：“从我收藏的地方里，给我排一个三天行程。每天留午休，避开太多步行。”

基础流程使用 Python 3.9+ 标准库。证据流程的详细图文版需要 Node.js 18+，图片处理使用 Pillow；本地 OCR 与视频拆帧分别需要可用的 Vision/Tesseract 和 FFmpeg。平台内容能否读取由实际访问情况决定；失败时仍可保留链接和本地素材。已有 SQLite 想去库可继续使用，旧 GitHub 版的 JSON 库可用证据流程打开。迁移前先复制库和资产；两种库不会自动合并。

地图按钮用于地点搜索：大陆用高德，其他地区用 Google Maps，未知地区暂不生成链接。HTML 可离线查看，可打印为 PDF，也可另存 XLSX 和 ICS。行前复核按请求执行。

- [Skill 入口](skills/want-to-go-trip-planner/SKILL.md) · [证据和路线历史](skills/want-to-go-trip-planner/references/evidence-workflow.md) · [规划与导出](skills/want-to-go-trip-planner/references/planning-and-exports.md)
- [中文样例](examples/itinerary.zh-CN.json) · [English example](examples/itinerary.en-US.json) · [MIT License](LICENSE)

## English

Save screenshots, links, original videos and notes. Ask your agent to organize saved places, build a map-linked passport and daily itinerary, revise it, or check changes before your trip. Every feature is free and open source under MIT. No account, activation, map API key or Kornvia backend is needed.

Unzip the [Skill download](dist/want-to-go-trip-planner-skill-3.0.0.zip) and place the `want-to-go-trip-planner` folder in your agent’s skills directory. Try: “Use Want to Go to save these places,” then “Make me a three-day trip from my saved places, with fewer transfers and a lunch break each day.”

The lightweight workflow only needs Python 3.9+. The evidence workflow uses Node.js 18+ for the photo-rich HTML renderer and Pillow for image processing. OCR, video frames and platform adapters use optional local tools. See the [English workflow](skills/want-to-go-trip-planner/references/workflow.en-US.md) and [evidence workflow](skills/want-to-go-trip-planner/references/evidence-workflow.md).

## Development

```bash
python3 -m pip install Pillow
python3 -m unittest discover -s tests
python3 skills/want-to-go-trip-planner/scripts/build_release.py --zip dist/want-to-go-trip-planner-skill-3.0.0.zip
```
