# Changelog

All notable changes to the SAGE Agent add-on (Open WebUI). Versions follow `version` in `config.yaml`.

## 0.3.1

- Open WebUI updated from v0.12.0 to v0.12.3 (v0.12.1, v0.12.2 and v0.12.3 are patch releases). This release adds two database migrations (an `app` table and a change to the channel members table, plus a backfill of missing sign-in rows), so **take a Home Assistant backup before updating**: going back to the old add-on version after the new one has started may not work.
- Checked against the 0.12.0 and 0.12.3 packages: every environment variable the add-on sets still exists, and the terminal connection and its admin-only access rule are unchanged.

## 0.3.0

- New options `web_search_engine` (none, duckduckgo, brave or tavily) and `web_search_api_key`. Web search is still off by default. It runs inside Open WebUI; the SAGE Sandbox stays closed to the internet.

## 0.2.1

- Renamed to **SAGE Agent**. The add-on slug, options and data are unchanged.

## 0.2.0

- Open WebUI updated from v0.11.4 to v0.12.0. This release changes the database schema. Take a Home Assistant backup before updating; going back to the old add-on version after the new one has started may not be possible.
- Renamed with the SAGE (Smart Agricultural Greenhouse Engine) branding; the chat is titled **SAGE**. The add-on slug, options and data are unchanged.

## 0.1.0

- First version: Open WebUI on Open WebUI v0.11.4 with OpenRouter models, the SAGE Sandbox as its terminal, and sign-up, web search, code execution and sharing off by default.
