# SAGE Sandbox

A shell for the agent to write and test its own analysis code, with a built-in firewall. It is
Open WebUI's Open Terminal, run with these restrictions:

- It can connect **only** to the gateway (and any `extra_allowed_domains`). Other names do not
  resolve and other connections are dropped, including to the internet.
- It has no Home Assistant token and no Home Assistant folder.
- It has no network port on your Home Assistant host. Open WebUI reaches it over the add-on network.
- Its home folder lives in add-on storage, so Home Assistant backups include the tools it writes.

Python code in it can `from greenhouse_gateway import Gateway` to read the gateway, and
`import greenhouse_forecast` for baselines, backtests, forecast bands, a Chronos-2 forecaster and a LightGBM runtime
forecaster, and helpers to record and score predictions (see `docs/forecasting.md`). Chronos-2 and LightGBM are
installed in the image with their weights, so forecasting needs no internet; it runs on the CPU. For web lookups use
Open WebUI's web search (the SAGE Agent add-on's `web_search_engine` option): the sandbox itself stays closed.

**Protection mode must be off** for this add-on, because the firewall needs a network capability.
If the capability is missing the add-on refuses to start instead of running without its firewall.

See `docs/agent-side-setup.md` in the repository for setup.
