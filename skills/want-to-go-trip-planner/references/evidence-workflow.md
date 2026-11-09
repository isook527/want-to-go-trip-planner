# Evidence, route history and pre-trip review · 证据、路线历史与行前复核

This workflow uses a versioned JSON library and a sibling asset directory. Use it for batches, photo and source evidence, reversible editing, or evidence-based review. The [v2 schema](shared-data-contract-v2.schema.json) includes a synthetic end-to-end example. Use absolute script paths when running from the traveler's project.

证据流程使用版本化 JSON 库和旁边的资产目录，适合批量采集、原图溯源、可撤销修改和出发前复核。命令与字段中英文共用；回复和成品按用户语言输出。不要把该库和 SQLite 想去库直接合并。

## Capture and verify · 收纳和核验

1. Run `extract_evidence.py doctor --locale en-US` (or `--locale zh-CN`) before using its adapters. Missing tools disable the corresponding capability; the local library remains available. For Windows setup see [provider support](provider-support.md).
2. Create a UTF-8 manifest. Example:

   ```json
   {
     "bundleId": "example-trip-01",
     "outputLocale": "en-US",
     "storageMode": "durable",
     "sources": [
       {"id":"saved-1","group":"place-1","destination":"Example City","type":"text","name":"Riverside Museum","value":"Saved the riverside museum for my trip"},
       {"id":"saved-2","group":"place-1","destination":"Example City","type":"image","value":"/absolute/path/to/screenshot.png"}
     ]
   }
   ```

   Use `link`, `video`, `image`, or `text` as appropriate. For a text source with a known place, set its own `name` so batch capture can identify the place. Do not invent a name for an unidentified source. Inspect `batch --help` for local media options. Give each mixed-city item its own destination. A group means the sources refer to the same place. Split a multi-place source after checking the material; never infer that all screenshots show separate places or share a city. Do not download platform videos; extract frames from supplied originals with FFmpeg and retain timestamps. If a page is blocked, keep the original URL and ask for a screenshot, locally saved page, original video or text.
3. Run `extract_evidence.py batch --manifest manifest.json --output evidence.json --output-locale en-US`, then `want_to_go.py ingest --library want-to-go.json --evidence evidence.json` and `want_to_go.py present --library want-to-go.json --destination "Example City" --locale en-US`. Account for failures and pending items separately. Display crops are separate from original assets. Preserve original hashes and do not promote text-only screenshots as place photography.
4. Match city, address and branch using [place resolution](place-resolution.md). Run `want_to_go.py resolve --candidates candidates.json` for ambiguous candidates. Use `promote --library want-to-go.json --bundle-id example-trip-01 --selections selections.json` to create reviewed place cards. Selections use a `selections` array with `id`, `verifiedName`, `destination`, `placeType`, `sourceIds`, `address`, `openingHoursText`, `signature` and `visitTip` as applicable. Where needed, run `confirm --library want-to-go.json --place-id ID --candidate-file confirmation.json --operation-id UNIQUE_ID`, using actual confirmation evidence. Never invent a user quote. Use optional `area` in `promote` selections or `edit` patches for the public area name. Use the verified address and leave it blank when unsure.

   可在 `promote` 或 `edit` 里填写可选的 `area`。按已核实的地址填写片区名，不确定时留空。

## Passport, maps and edits · 护照、地图和修改

Create one plan per destination with `route-plan --library want-to-go.json --destination "Example City" --plan route.json --locale en-US --operation-id UNIQUE_ID`. Run the read-only `lint-route` first and resolve or explain its warnings using [itinerary quality](itinerary-quality.md). Use the schema’s `routePlan` example for `tripProfile`, `days[].stops`, `connections` and `unassigned`. Include each deliverable place once or explicitly leave it unassigned, with a reason. Put every confirmed must-visit place in the schedule. Do not fabricate connection times or distances. The schematic route shows sequence, not live navigation.

Run `passport --library want-to-go.json --destination "Example City" --output passport.json --locale en-US --map-region outside_mainland`. Use `mainland` or `unknown` where appropriate. Use `--places-only` for a rich place passport without an itinerary, `--content-depth compact|standard|deep` to change detail, or `--visitor-mode` for the public projection. Run `node renderer/render_report.mjs passport.json passport.html` and `want_to_go.py scan --path passport.html --mode customer`. Public source links must be safe HTTPS links and must omit credential-like parameters. HTML embeds approved display images and opens offline; external links require internet.

Use `list`, `present`, `destination-alias`, `edit`, `delete` (soft deletion), `restore` and `reorder`. Always use a unique `--operation-id` for a new change. Use `undo --library want-to-go.json --operation-id NEW_UNDO_ID` to undo the latest supported change. Add `--event-id EVENT_ID` to select a specific event where permitted. Saving a new route archives the prior route; it does not erase original evidence. For migrations, run `migrate --dry-run` first and prefer `--output` to keep the original. Run `validate` and `repair --dry-run` before repair. Hash mismatches and dangling references must be resolved rather than ignored. `export` copies the library for private portability; `share` directs the user to the traveler-facing HTML. Copy the asset directory alongside the JSON when migrating to another device.

## Pre-trip review · 行前复核

Run `want_to_go.py review-schedule --library want-to-go.json --destination "Example City"` once the active route has a `tripProfile.startDate`. Show the dates to the traveler and agree which checks to run.

| Node | Checks |
| --- | --- |
| T-7 | Opening hours, reservation rules and prices |
| T-3 | Weather, transit schedules and last services |
| T-1 | Final check of closures, tickets, price changes, weather and transport |

At each agreed check, supply `node` (`T-7`, `T-3` or `T-1`), `agreedReviewDate`, `agreementConfirmed: true`, and `checkedAt` with the local offset on that date. For a custom date, omit `node`. A repeat for the same trip, date and node keeps the original snapshot. A saved route revision keeps its calendar identity. A change to its departure date starts a separate review timeline.

Include `items` for the places checked. Use `facts` for what changed and `factAudits` for status and sources. A reviewed fact needs a timestamp and an official or reliable public source. Add trip-wide issues to `tripRisks`. For blocking evidence, use explicit values such as `openingStatus: "closed"`, `reservationStatus: "sold_out"`, or a verified risk with `severity: "blocking"`. Unverified blocking claims are returned as adjustments to check.

```bash
python3 scripts/want_to_go.py review --library want-to-go.json --destination "Example City" --checks checks.json
```

Compare the results with the preceding completed review. The CLI groups changes into `blocking`, `adjust` and `info`, and returns a checklist for travel documents, reservation confirmations, offline maps and addresses in the local language. A node may check only the fields relevant to its date. The passport retains earlier audited fields and flags expiry. Keep earlier snapshots and route versions. Run checks when the traveler asks on the agreed day. The Skill does not monitor in the background or make bookings.

中文使用时，先让旅行者确认节点日期，再按约定日执行。`blocking` 是已核实且影响成行的变更，`adjust` 是需要改安排或继续核对的事项，`info` 是提示。说明对路线的影响，并带上出发清单。

See [fact freshness](place-resolution.md#fact-freshness--事实时效) for expiry rules.
