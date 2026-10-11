# Charts in the chat

The chat tool's `show_chart` draws a chart for the grower inline in the conversation. The agent only passes role
names (see `docs/roles.md`) and a time window; the tool fetches the readings from the gateway and builds the
picture itself, so the model never types data and cannot mis-quote it. The model gets the figures back (latest,
low, high, average, share of time below / inside / above the target band) and quotes those instead of describing
the picture.

- Up to 5 roles, 1 to 168 hours. Readings with different units go in separate panels, never on a second axis.
- The target band comes from the live setpoint roles and is drawn for `greenhouse_temperature`. It is time-weighted
  and follows setpoint changes.
- Colours follow the kind of sensor (greenhouse blue, outside orange, ...), so a role keeps its colour from chart to
  chart. Hover or use the arrow keys for a readout; "Numbers" lists the figures as a table. Light and dark follow
  the browser.
- Open WebUI shows it as an inline embed. The embed is a sandboxed frame: no network, no access to the chat. The
  chart data is passed as JSON and drawn as text, never as markup. Long-term dashboards stay in Grafana.
- During a replay (the tool's AS_OF setting) the window ends at that time and the gateway clips the data.

Not built yet: forecast bands, equipment timelines, heatmaps.
