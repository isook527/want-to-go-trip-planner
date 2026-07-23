# 想去成行

把用户主动分享的小红书、抖音、大众点评、地图链接、截图或文字整理进本地“想去库”，先解释地图候选并确认正确分店，再为后续真实路线规划准备可靠的结构化地点。

## 免费版能做什么

- 保存原链接、截图名、OCR 文字和地点线索；
- 提取城市、商场、区域、楼层、分店和多语言别名；
- 对地图候选进行可解释评分，避免把同名门店直接当成正确地址；
- 用户确认后记录地图 POI ID、坐标系、证据指纹和核验时间；
- 按目的地查看、去重和导出想去地点。

免费 Skill 不会绕过登录读取私密收藏，不会上传 Cookie 或账号 Token，也不会把未经确认的地点直接排进路线。

## 安装

支持从 GitHub 安装 Skill 的 Agent 可以使用：

```bash
npx skills add <你的账号>/want-to-go-trip-planner
```

也可以下载 Release 中的 `want-to-go-trip-planner-skill-<version>.zip`，按宿主 Agent 的本地 Skill 安装方式导入。

## 快速自检

```bash
python3 skills/want-to-go-trip-planner/scripts/want_to_go.py doctor
```

完整用法、数据格式和隐私边界在 `skills/want-to-go-trip-planner/` 中。

## 许可证

公开 Skill 使用 MIT License。地图、社交平台和支付品牌归各自权利人所有。
