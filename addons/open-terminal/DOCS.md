# Greenhouse Sandbox

A shell for the agent to write and test its own analysis code, with a built-in firewall. It is
Open WebUI's Open Terminal, run with these restrictions:

- It can connect **only** to the gateway (and any `extra_allowed_domains`). Other names do not
  resolve and other connections are dropped, including to the internet.
- It has no Home Assistant token and no Home Assistant folder.
- It has no network port on your Home Assistant host. Open WebUI reaches it over the add-on network.
- Its home folder lives in add-on storage, so Home Assistant backups include the tools it writes.

Python code in it can `from greenhouse_gateway import Gateway` to read the gateway.

**Protection mode must be off** for this add-on, because the firewall needs a network capability.
If the capability is missing the add-on refuses to start instead of running without its firewall.

See `docs/agent-side-setup.md` in the repository for setup.
