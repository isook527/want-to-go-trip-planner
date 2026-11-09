# Planning and exports · 行程规划与导出

Use the traveler’s saved and verified places first. Include city transitions, transfer buffers, breaks and meal windows. Keep high-priority stops, an optional list, and room for changes. A route that looks efficient on a map may be worse for the traveler if it adds walking, changes modes too often, misses an opening window or assumes a reservation. Compare a balanced route, a lower-transfer route and a more relaxed option when useful. Choose using documented opening windows, travel times and preferences; never assign false precision. Check official or reliable current sources for closures, weather, holidays, reservations and public transport when those facts affect the route. For unknown dates, deliver day blocks instead of dated tickets or calendar events. Record any city-to-city leg as a distinct segment.

按目的地和日期安排，保留换乘、排队、用餐及休息余量；按优先级安排，未落实的地点保留为备选。必要时补预算、住宿区域、订票和行前清单。把已核实的交通时间、预约和价格与待确认项分开。交通时间没有可靠依据时只给顺序与时段；不凭空写车次、天气、价格或预约结果。

## Files · 导出

- **HTML**: use `scripts/render.py` for the local library, or `renderer/render_report.mjs` for the evidence workflow. To save **PDF**, use the browser’s Print → Save as PDF on the local HTML. The print styles are built in; no upload is needed.
- **XLSX** and **ICS**: use `scripts/export_itinerary.py` with the same travel-facing JSON passed to the renderer. For example:

  ```bash
  python3 /absolute/path/to/skill/scripts/export_itinerary.py passport.json --xlsx itinerary.xlsx --ics itinerary.ics
  ```

- **ICS dates, evidence workflow**: Set `tripProfile.startDate` in the route JSON, using a confirmed date in `YYYY-MM-DD` format. Run `route-plan` and `passport` again before exporting. The exporter calculates the remaining dates from the start date. The saved route uses the trip profile rather than per-day date inputs.
- **ICS dates, lightweight workflow**: Set a confirmed `days[].date` in `YYYY-MM-DD` format for every day you want on the calendar. Each dated day becomes an all-day event. Undated plans can still produce HTML and XLSX. The ICS is a snapshot.

证据流程用路线 JSON 的 `tripProfile.startDate` 推算各天日期，例如 `2030-05-10`。补日期后先保存路线，再重新生成护照 JSON。轻量流程可在每天的 `days[].date` 填写日期。
- The spreadsheet includes the itinerary, places, transfers and trip notes. You can supply optional `trip_notes` arrays for budget, lodging, reservations, backup plans and departure checklists; entries are plain text. The spreadsheet exports only selected travel fields and never adds raw evidence or private library records.
- Deliver each format from the same approved travel JSON. When a plan changes, regenerate requested formats together and keep an earlier confirmed copy unless the user asks for replacement.
