# Itinerary quality · 行程质量标准

Use these thresholds when planning from saved places. They are defaults for trip design and may be changed for the traveler. Record why a threshold is exceeded. Run this check before saving the draft.

```bash
python3 scripts/want_to_go.py lint-route --library want-to-go.json --destination 杭州 --plan route.json
```

`lint-route` does not write to the library. `route-plan` returns the same warnings and metrics when it saves a new version. Review warnings before delivery and explain any accepted trade-offs.

| Check | Default and fields |
| --- | --- |
| Major stops | Aim for 2–3 per day, or 1–2 for children, older travelers, mobility constraints or a relaxed pace. Set `tripProfile.reducedMobility: true` for the lower thresholds. Count a stop as major unless `major: false`. Override with `maxMajorStops` if appropriate. |
| One-way transfer | Flag over 60 minutes without an explicit `tradeoff` on the leg or day. Override with `maxTransferMinutes`. For two same-day must-go stops, sum known legs along their path and explain why the combination is worthwhile or split the day. |
| Total transfers | Flag over 120 minutes per day, or 90 minutes with reduced mobility or a relaxed pace. Override with `maxDailyTransferMinutes`. Supply `durationMinutes` as whole minutes. Keep old `durationText` for display. If a leg is missing, show known minutes as a lower bound. Flag when more than one-third of legs are unverified, including missing adjacent legs. Provide `checkedAt` for a verified timetable so its status expires after three days or earlier when the source supplies `validUntil`. |
| Walking | If there is a numeric limit, store `walkingLimitMeters`. Supply `distanceMeters` for walk legs and `onsiteWalkingMeters` on the day. Flag over-limit or incomplete evidence. Older free-text walking notes remain visible. |
| Meals, rest and contingencies | Give each day a `lunch` and `rest` slot, a `backup` for rain or low energy, and a concrete `dropIfDelayed` stop or activity. Record a transfer-day exception explicitly when relevant. |
| Openings and booking | For dated visits, check the weekday and time against structured hours. Flag a conflict when the visit cannot fit. If a stop needs booking, set `reservationRequired` or record a venue's `reservationLeadDays`. Mark `reservationStatus` as `pending`, `confirmed`, `unavailable`, `unknown` or `not_required` as supported by evidence. Only count a booking as confirmed when the traveler confirms it or supplies a valid confirmation. |

Use `flight` for an air leg. Intercity flights and ground segments can use `intercity: true` with `fromLabel` and `toLabel` for external endpoints such as airports. Use a separate leg for each segment. Include airport transfers and known buffers in the day. For mixed map regions, set each place card’s `mapRegion` to `mainland`, `outside_mainland` or `unknown`. If a total is incomplete, tell the traveler which legs still need confirmation.

Structured venue hours use local times. A missing weekday is unknown. An empty window list marks a closed weekday. Split overnight windows across two weekdays, with `24:00` allowed as a closing time. The optional `lastEntry` is a local clock time, and `reservationLeadDays` is the number of advance days required. Keep a readable `openingHoursText` for legacy views when available.

```json
{
  "openingHours": {
    "weeklyHours": {"tue": [{"open": "09:00", "close": "17:00"}]},
    "closedDays": ["mon"],
    "lastEntry": "16:30",
    "reservationLeadDays": 2
  }
}
```

For a time-block visit, a partial overlap indicates that a slot may fit. Check the actual visit duration and last admission before presenting it as usable. Use exact `timing` only with reliable evidence. Do not infer numbers from generic descriptions like “not far” or invent a time for an ICS event.
