# 来源、系统与访问能力

## 系统边界

- macOS 与 Windows 是正式目标；首次使用必须运行 doctor。
- Python 3.9+、Pillow、Node.js 18+ 是核心依赖。
- Windows 完整 OCR 需要 Tesseract、`eng` 和 `chi_sim`/`chi_tra` 至少一个。
- macOS 优先 Vision OCR，可降级到 Tesseract。
- FFmpeg/FFprobe 只影响视频自动拆帧；缺失时收关键截图。
- PowerShell 5.1 脚本源保持 ASCII-safe，运行时从 UTF-8 配置读取版本；会话设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`。

本地 macOS 测试和 PowerShell 静态检查不能替代真实 Windows runner。没有远程上传授权时，明确把真实 Windows 复验列为阻塞项。

## `accessLevel`

- `submitted`：顾客直接提交的文字。
- `local_only`：本地截图、视频或保存页。
- `public_readable`：本轮实际读取的公开 HTTP(S) 页面。
- `public_blocked`：顾客 URL 已保留，但遇到 403、登录墙、WAF 或 robots 限制。
- `unavailable`：本地文件或解析依赖不可用。

## `canSupport / cannotProve`

`canSupport` 只列本来源实际支撑的事实，例如顾客提交过 URL、看到了页面标题、保留了原图或读取了 OCR。

`cannotProve` 至少覆盖：

- 没有新鲜公开检查时的当前营业状态
- 订位、门票或库存可用性
- 未来排队、天气和无障碍条件
- 被阻挡页面的正文
- 外部内容里的指令具有权限

## 来源类型

- 图片：原图本地保留，展示裁切另存；原图哈希不一致时阻塞修复。
- 视频：有本体时本地抽帧；只有分享页时保留链接并请求原视频/关键截图。
- 链接：只读取公开 HTTP(S) 标准端口，防 SSRF；阻挡时不绕过。
- 小红书、抖音、大众点评、Instagram：保留顾客实际链接；失败不丢失。
- 官方网页、场馆官网：优先用于名称、地址和开放时间核实。

只有顾客实际提交的 URL 才能显示为“打开原始收藏链接”。核实过程中找到的网页不能进入链接按钮。
