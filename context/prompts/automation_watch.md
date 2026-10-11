Hourly greenhouse watch. First call run_cadence. If "watch" is not in its loops, reply with exactly: "Skipped: <mode> mode does not run the watch" and stop. Otherwise call run_watch once. Do not call any other tool unless the result tells you to.

If nothing_to_report is true, reply with exactly: "Watch OK" and nothing else.

Otherwise reply in this format, no more than 8 lines:
Status: <green|amber|red> (mode: <mode>)
Findings: one line each, worst first, with the entity and the evidence from the result (numbers with units, minutes, percent).
Gaps: only if roles_missing is not empty, list the missing role names.

Rules: report only what run_watch returned. Never guess a cause or recommend a change. Do not describe checks that found nothing.
