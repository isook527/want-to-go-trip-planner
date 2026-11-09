# 曼谷两日护照 · Bangkok passport

示例数据仅用于演示渲染，营业时间等信息为示意，不能作为出行参考。These fixtures demonstrate rendering. Hours and other trip details are illustrative.

- `bangkok-evidence-library.json`：证据库示例。
- `bangkok-route.json`：两日路线与地点顺序。
- `bangkok-lightweight.json`：轻量护照示例，标题故意写三日游，用于验证天数不符提示。
- [在线查看 · View online](https://isook527.github.io/want-to-go-trip-planner/)。离线文件为 [`passport.html`](passport.html)。

在仓库根目录运行以下命令。先把示例库复制到临时目录，保留原样例。

```bash
skill="want-to-go-trip-planner"
demo="examples/bangkok"
out="$(mktemp -d)"
cp "$demo/bangkok-evidence-library.json" "$out/library.json"
python3 "$skill/scripts/want_to_go.py" lint-route --library "$out/library.json" --destination 曼谷 --plan "$demo/bangkok-route.json"
python3 "$skill/scripts/want_to_go.py" route-plan --library "$out/library.json" --destination 曼谷 --plan "$demo/bangkok-route.json"
python3 "$skill/scripts/want_to_go.py" passport --library "$out/library.json" --destination 曼谷 --map-region outside_mainland --output "$out/passport.json"
node "$skill/renderer/render_report.mjs" "$out/passport.json" "$out/passport.html"
python3 "$skill/scripts/want_to_go.py" scan --mode customer --path "$out/passport.html"
python3 "$skill/scripts/render.py" "$demo/bangkok-lightweight.json" "$out/lightweight.html"
```
