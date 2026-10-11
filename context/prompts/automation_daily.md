Daily greenhouse review. Follow these steps in order and stop early where told.

1. Call run_cadence. If "daily" is not in its loops, reply with exactly: "Skipped: <mode> mode does not run the daily review" and stop.
2. Call run_daily_review once. Do not call other tools unless a finding needs a closer look; then call at most 3 more, only to read data for that finding.
3. Reply in this format, no more than 14 lines:
Status: <green|amber|red> (mode: <mode>)
Last 24 hours: temperature (min, max, share of time in the band), night humidity hours, equipment that ran longest. One line.
Findings: one line each, worst first, with the evidence from the result (numbers with units). Watch findings come first.
Next 72 hours: the coldest, hottest and windiest forecast moments, only if a finding mentions them. Say if no forecast is archived.
Gaps: data gaps from the result, and "not measured: <list>" once.
Questions: anything a person must answer. Omit if none.

If nothing_to_report is true, reply with: "Status: green (mode: <mode>). Nothing to report." and one line of the key numbers. Restraint is a valid result.

Rules: report only what the tools returned. Do not guess causes and do not recommend or propose changes in this review. Do not diagnose plants. The share of time in the band uses today's setpoints, say so only if the share is below 100%.
