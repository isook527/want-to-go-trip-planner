# Kornvia 想去就出发

将用户的旅行灵感整理成可分享的地点护照和逐日行程。直接使用当前 Agent 已有的读图、文件、联网或地图能力，不要求用户注册服务、配置地图 API。

## 收纳与核对

用户分享旅行相关图片、链接、视频或文字，即使没有说“收藏”，也默认收进想去库；同一消息视为一批，逐项清点，不把多张图直接当作多个地点。无旅行意图的普通图片不要擅自收纳。读不到的平台内容、视频画面或语音如实说明并保留来源；不要绕过登录和访问限制。

### 截图识别顺序

先使用下面的 `capture` 把本批原图和其他来源保存到想去库，图片来源先标记 `processing_status: pending`，尚未识别的地点留空；确认返回的 `saved_path` 可用后再识别。每张截图先使用当前 Agent 自带的读图能力；能读出所需地点线索时直接整理，不调用包内 OCR。

仅在 Agent 没有读图能力、读图失败或关键文字看不清时，对已保存的原图调用本 Skill 的 `scripts/ocr.py`：

```bash
python3 /absolute/path/to/skill/scripts/ocr.py --locale zh-CN /absolute/path/to/saved-image.png
```

脚本自动尝试当前环境已有的 macOS Vision 或 Tesseract，不安装软件、不要求配置。`status: ok` 的文字只作为地点线索；将确认后的地点通过 `add-places` 关联原批次，再用 `source-processed` 标记已读来源。无法读取、OCR 不可用或文字仍不明确时保留原图与待处理状态，告诉用户并请其补充或确认，不能将未处理来源标记为完成。

提取地点名称、城市、分店、地址、营业信息及来源；利用宿主现有的联网或地图工具核对。信息有歧义时请用户确认，不编造地点、分店、营业时间、交通耗时、距离、价格或坐标。地图链接是按文字搜索的入口，不能称为已确认的精确导航。

地图按目的地选择：中国大陆使用高德地图；中国大陆以外（含港澳台及其他国家和地区）使用 Google 地图。地理范围不明确时先向用户确认，不以用户当前位置推断。Google 地图无法访问时如实说明，不把高德当作海外替代结果。无需客户配置地图 API。

### 持久保存与跨会话召回

默认库目录为用户主目录下的 `Documents/Kornvia想去库`，与旧本地版兼容；同一设备的后续会话继续读取这个位置，不要每次建立新库。使用本 Skill 目录下 `scripts/library.py` 管理，不覆盖旧库或删除用户原件。始终保持当前目录为用户的项目目录，通过技能脚本的绝对路径调用；不要先切换到 Skill 安装目录，否则备用库位置会错误。下面命令中的 `scripts/library.py` 表示该脚本的绝对路径。将每条来源和识别出的地点写入临时 JSON 清单，再运行：

```bash
python3 scripts/library.py --locale zh-CN capture /absolute/path/manifest.json
```

格式：`{"sources":[{"type":"image","path":"/absolute/path/a.jpg"},{"type":"link","url":"https://example.com/..."},{"type":"text","text":"用户原文"}],"places":[{"name":"店名","city":"曼谷","city_aliases":["Bangkok"],"status":"identified","address":"","hours":"","note":""}]}`。图片、视频、文件使用本地 `path`，脚本复制原件到库中；链接和原文原样保存。未能识别或城市、分店有歧义的地点用 `pending`，城市不确定可留空；暂时没有地点则用空数组，但来源仍保存。只有有证据支持城市与地点身份时设为 `identified`；不要因为用户说了城市就推断所有图片都在该城市。保留原文、别名、地址线索等；不确定分店不要合并。检查返回的 received、saved、failed 和每个失败项，失败来源保留记录并告知用户；核对无遗漏后自然回复保存结果。只收藏时不生成护照。

用户要查看全部收藏时，运行 `python3 scripts/library.py --locale zh-CN catalog` 查看所有地点和批次数。用户说“想去曼谷”“生成曼谷护照”等明确目的地时，先查所有历史批次，之后再生成护照：

```bash
python3 scripts/library.py --locale zh-CN find 曼谷
python3 scripts/library.py --locale zh-CN export 曼谷 /absolute/path/passport.json --map-region outside_mainland
```

`find` 匹配已记录的城市及明确别名，列出已识别、待确认、尚未提取地点的批次和失败来源。处理与本次目的地相关的待确认项并请用户确认歧义；无关或无法判断的来源保留，不猜、不混入。用户确认某个地点后，把更正字段写入 JSON，运行 `python3 scripts/library.py --locale zh-CN update 地点ID /absolute/path/update.json`（例如 `{"city":"曼谷","status":"identified"}`），再重新检索。保留原始证据。补充联网查到的地址或营业信息时，同时在更正 JSON 记录 `source_urls` 与 `verified_at`；没有可靠来源时保持待确认。重复同名地点先核对分店，无法确认时不要擅自合并；确认不同分店时更正为可区分的名称。确认同一家后可运行 `python3 scripts/library.py --locale zh-CN merge 保留地点ID 重复地点ID`，保留两个原记录、来源与原文，再导出。导出遇到未处理的同名冲突会停止，不得静默删除。没有匹配内容时如实告知，不编造收藏。

此前来源未识别出地点，后来读取或用户补充后，将地点数组保存为 JSON，运行 `python3 scripts/library.py --locale zh-CN add-places 批次ID /absolute/path/places.json` 关联原始批次，再查找；不重复创建来源或丢掉原始证据。 对每条已经读懂且完成地点提取的来源，在清单中写 `"processing_status":"processed"`；读不到或尚未看完的来源写 `"processing_status":"pending"`。之后补充处理来源时，先关联地点，再运行 `python3 scripts/library.py --locale zh-CN source-processed 来源ID`。不要把无法读取的来源标为已处理。

脚本自动优先使用已有库。默认文稿目录因权限无法创建新库时，自动使用当前项目的 `Kornvia想去库`；每次结果中的 `library_path` 和 `storage_mode` 显示实际位置。若使用项目库，告知用户以后在同一项目使用；项目目录若是临时位置，则明确说明没有完成长期保存，交付可下载的整个库。已有库无法读取时不得静默创建替代库；发现文稿库和项目库同时存在时，先让用户选择原来的库，再用 `--root` 指定，不要自行合并。共享库不依赖地图 API。

## 规划与交付

用户只是收藏时只收纳；明确目的地时先查历史库并生成地点护照。明确要求规划时，先查历史库，了解目的地、出行日期或天数，以及影响安排的同行需求。能从上下文推断的无需重复询问；日期未知可以按天数做草案，并注明营业与交通待确认。

结合用户优先级和已核实的地理位置、营业时间及交通信息安排每日行程，留餐饮和休息时间。无可靠交通时长时不虚构，安排顺序并标记待确认。时间仅在有依据时使用，否则使用上午、下午、晚上等区段。已有证据的地点优先；未确认的地点列入备选，不冒充已落实。天气、开放、预约、交通状况随时间变化，靠近出发时可在用户请求下重新查询并提出调整；不承诺后台自动监控。

构造 JSON，在用户项目目录通过脚本的绝对路径运行；输入和输出也用绝对路径：

```bash
python3 scripts/render.py /absolute/path/input.json /absolute/path/output.html
python3 scripts/want_to_go.py scan --mode customer --path /absolute/path/output.html
```

输入格式：`{"locale":"zh-CN","title":"曼谷三日游","city":"曼谷","map_region":"outside_mainland","places":[{"name":"地点名","address":"地址或空字符串","hours":"营业信息或待确认","note":"特色或提醒"}],"days":[{"title":"第一天","visits":[{"period":"上午","place":"地点名","detail":"安排及待确认事项"}]}]}`。导出时 `--map-region mainland` 用于中国大陆，`--map-region outside_mainland` 用于大陆以外；直接构造 JSON 时填写对应 `map_region`。混合地区时可为单个地点单独设置 `map_region`。未确认时使用 `unknown`，护照不显示地图链接，确认后再生成。地点名称必填；每天的 `place` 须与地点清单名称完全一致。脚本为地点生成地图搜索链接，用户打开后核对分店。宿主支持时可另外提供 PDF 等格式。

交付 HTML 与输入清单，说明保存位置；支持用户提出修改，更新本地文件。成品只呈现旅行者需要的内容。

行程只安排用户实际需要的游览。同一地点不为凑满天数而重复安排；只有用户明确想再次到访，或确有不同活动且说明原因时才安排重访。收藏不足以覆盖旅行天数时，留出自由活动，或询问是否补充地点，不把相同景点复制到每天。成品使用面向旅行者的简短表达，例如“开放时间待确认”“交通待确认”；不要写“无可靠数据”“不编造车次”“不虚构分钟”等内部工作措辞。

混合城市的护照须为每个地点填写 `city` 和 `map_region`；地图搜索优先使用地点自己的城市，未填时才使用护照城市。

休息、未指定餐厅的用餐、自由活动等使用独立非地点行程项，例如 `{"type":"activity","title":"休息","period":"下午","detail":"自由活动"}`；不用添加到地点库，也不生成地图按钮。地点游览使用 `type: place`（或省略 type），`place` 必须对应地点清单中的名称。
