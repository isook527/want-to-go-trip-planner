# Kornvia Go Passport

Save travel inspiration and make a shareable local HTML Places Passport and day-by-day trip plans. Use the host agent's existing image, file and web tools. No map API key or separate service registration is required.

## Save every travel source

When a user shares a travel-related image, link, video, or text, save it even if they don't explicitly say “save.” Treat each message as a batch and account for every source individually. Don't automatically save unrelated images. Keep links and files even if the host cannot access them; explain access limitations without bypassing login or access controls.

For each screenshot, first run `capture` with its `processing_status` set to `pending` and an empty places array. Confirm that `saved_path` exists before processing. Prefer the host agent's own image-reading capability. If it can extract the necessary place clues, do not call OCR. Only if image reading is unavailable, fails, or misses essential text, run `python3 /absolute/path/to/skill/scripts/ocr.py /absolute/path/to/saved-image.png`. The fallback tries locally available Tesseract or macOS Vision without installation or API configuration. Treat OCR text as clues, not verified facts. When a source has been understood, add its places to the same batch and then mark the source processed. If it cannot be read or remains ambiguous, keep the original and pending status and ask the user for clarification.

Extract place name, city, branch, address, hours, aliases, and source evidence when available. Use the host's existing web tools to verify details if possible. Ask about ambiguous branches. Never invent an address, coordinates, opening hours, transit time, distance, cost or current status. Preserve unverified information as unconfirmed. Don't assume every image belongs to a city mentioned by the user.

For destinations in mainland China use Amap search; for destinations outside mainland China, including Hong Kong, Macao and Taiwan, use Google Maps search. If location is unclear ask first; do not infer from the traveler's current location. Map links are text searches, not verified navigation.

## Persistent library and retrieval

Use `scripts/library.py` at its absolute installed path, keeping the working directory in the user's project rather than the skill directory. The library is compatible with earlier Go Passport local editions and defaults to `~/Documents/Kornvia想去库`; the script falls back to `Kornvia想去库` in the current project if necessary. Always check the returned `library_path` and `storage_mode`. Tell the user if the project library is temporary and provide the entire library for safekeeping. If an existing library cannot be read, do not silently replace it. If both a Documents and project library exist, ask which to use and select it with `--root` rather than silently merging them.

Write a temporary UTF-8 JSON manifest with sources and places, then run:

```bash
python3 /absolute/path/to/skill/scripts/library.py capture /absolute/path/manifest.json
```

Example: `{"sources":[{"type":"image","path":"/absolute/path/a.jpg","processing_status":"pending"},{"type":"link","url":"https://example.com/"},{"type":"text","text":"original text"}],"places":[]}`. Source paths must be accessible local files. Images and videos are copied into the library; original links and text are recorded. Review `received`, `saved`, `failed`, and source errors and tell the user about failures. Only save when the user is collecting; do not generate a passport unless requested.

When a source is understood, create an array such as `[{"name":"Wat Arun","city":"Bangkok","city_aliases":["曼谷"],"status":"identified","address":"","hours":"","note":""}]` and run `library.py add-places BATCH_ID /absolute/path/places.json`, then `library.py source-processed SOURCE_ID`. Set uncertain places to `pending`, preserve original evidence, and leave unknown cities empty. Never mark unread sources processed.

For a destination, search all saved batches with `python3 /absolute/path/to/skill/scripts/library.py find "Bangkok"`, review pending and failed sources, resolve relevant ambiguity with the traveler, and avoid guessing about unrelated sources. Use `update PLACE_ID /absolute/path/update.json` for corrections; for verified facts record `source_urls` and `verified_at`. Only merge confirmed identical branches with `merge KEEP_ID DUPLICATE_ID`; distinguish different branches by name. Export with `library.py export "Bangkok" /absolute/path/passport.json --map-region outside_mainland`. Use `mainland` for mainland China and `unknown` when unconfirmed. `catalog` lists saved places and batch counts. Do not invent saved places if no matches exist.

## Create the passport and itinerary

When requested, first save any new sources and search the historical library. Render the exported JSON using:

```bash
python3 /absolute/path/to/skill/scripts/render.py /absolute/path/passport.json /absolute/path/passport.html
python3 /absolute/path/to/skill/scripts/want_to_go.py scan --mode customer --path /absolute/path/passport.html
```

Lightweight HTML uses the shared passport template and requires Node.js 18+. Set optional `area` with `library.py update` using the area name supported by a verified address. Use traveler-facing language. The `scan --mode customer` gate blocks internal codes and raw timestamps. For an evidence route, use `lint-route` to resolve `blocking` findings, explain `suggest` items and treat `info` as reminders.

Input format: `{"locale":"en-US","title":"Bangkok Places Passport","city":"Bangkok","map_region":"outside_mainland","places":[{"name":"Wat Arun","address":"","hours":"","note":""}],"days":[{"title":"Day 1","visits":[{"period":"Morning","place":"Wat Arun","detail":"Visit"}]}]}`. For mixed regions set `map_region` per place. For unknown regions use `unknown` until confirmed. Each map link searches for the name, address and city and requires the traveler to confirm the right location. Deliver the HTML and input JSON and say where they were saved. The HTML can be opened offline; opening a map requires internet access.

When the user requests a trip plan, ask for the destination, dates or number of days, and relevant traveler needs unless already known. Prioritize saved and verified places; group visits by day and allow breaks. Do not invent opening hours or travel times; use morning, afternoon and evening when exact times are unsupported. Put uncertain places in an optional list. Each visit place must exactly match a name in `places`. Update files if the user asks for changes.

Do not repeat the same places merely to fill the requested number of days. Revisit only when the traveler requests it or a distinct activity gives a clear reason. If saved places are insufficient, allow free time or ask whether to add more places. Use traveler-facing notes such as “Opening hours to be confirmed” or “Transport to be confirmed”; keep internal phrases such as “no reliable data,” “do not invent,” and validation commentary out of the finished guide.

Map search uses each place's `city` when present, falling back to the passport city. For multi-city guides, set both `city` and `map_region` per place.

For rest, meals without a named venue, free time or other non-place activities, add a visit such as `{"type":"activity","title":"Rest","period":"Afternoon","detail":"Free time"}`. Do not add these to `places` or give them a map button. A place visit uses `type: place` (or omits type) and must reference a name in `places`.
