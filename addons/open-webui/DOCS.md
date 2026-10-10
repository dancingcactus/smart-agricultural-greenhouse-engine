# SAGE Agent (Open WebUI)

The chat front end. Models come from OpenRouter; commands run in the SAGE Sandbox. This
add-on has no Home Assistant credential: it sees greenhouse data only through the gateway's
read-only API (through the SAGE Gateway tool, and through the sandbox).

The add-on options are applied on every start and override what the admin screens hold, so change
them here, not in Open WebUI.

Browse to `http://<your Home Assistant address>:8080`. The first account created becomes the admin;
then turn `allow_signup` off. See `docs/agent-side-setup.md` in the repository for setup.
