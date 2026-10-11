# Setting up the agent side

Two add-ons, installed after the gateway is working with its `api_key` set:

| Add-on | What it is | Can reach |
| --- | --- | --- |
| **SAGE Sandbox** (Open Terminal) | The agent's shell, for writing and testing its own analysis code | Only the gateway (and anything you list). Nothing else, including the internet |
| **SAGE Agent** | The chat front end; talks to models through OpenRouter and runs commands in the sandbox | OpenRouter, the sandbox. No Home Assistant credential |

Neither add-on has a Home Assistant token, a Home Assistant folder, or any host access. Data reaches
them only through the gateway's read-only API.

## What you need first

- The gateway running, with `api_key` set (a long random value).
- Each add-on's **hostname** from its Info page, for example `a0d7b954-greenhouse-gateway`.
- An OpenRouter key. Create a dedicated one and **set a credit limit on it** in the OpenRouter
  dashboard; that is where spending is capped. Review OpenRouter's account privacy settings (data
  collection and zero-data-retention routing) to match what you are comfortable with.
- Roughly 2 GB of free memory and several GB of disk for the Open WebUI image.

## 1. Sandbox

Options: `api_key` (make up a long random value; you will enter it again below), `gateway_host`
(the gateway's hostname), `gateway_key` (the gateway's `api_key`), and optionally
`extra_allowed_domains`.

The sandbox builds its firewall with a network capability that Home Assistant grants only when
**Protection mode is off** for this add-on (Info tab). Turn it off, then start it. The log should
show `Egress: DNS whitelist — <gateway hostname>` and `Egress firewall active`.

If the add-on stops with an `iptables` or `ipset` error, the capability was not granted. That is
deliberate: the image refuses to start without its firewall rather than run open.

## 2. Open WebUI

Options: `openrouter_api_key`, `terminal_host` (the sandbox's hostname), `terminal_api_key` (the same
value as the sandbox's `api_key`), and optionally `allowed_models` (OpenRouter model ids; empty
means all of them).

Start it and browse to `http://<your Home Assistant address>:8080`. **The first account you create
becomes the admin.** Then set `allow_signup` off and restart so nobody else can register.

Open WebUI cannot run under a path prefix, so it has no Home Assistant sidebar entry; it has its own
port and its own login. If you want a shortcut in Home Assistant, add a Webpage dashboard card that
links to that address.

## 3. Connect the pieces

1. **Terminal.** The sandbox connection is created for you from the options. Check Admin Panel →
   Settings → Integrations shows "Greenhouse sandbox".
2. **Tool.** Workspace → Tools → New tool, paste `owui/tools/greenhouse_gateway.py`, save. Open its
   settings (Valves) and set `GATEWAY_URL` (for example `http://a0d7b954-greenhouse-gateway:8099`)
   and `GATEWAY_KEY`.
3. **Run log filter.** Admin Panel → Functions → New function (+), paste `owui/functions/sage_run_log.py`,
   save and enable it. In its Valves set `gateway_url` and `gateway_key` (same as the tool). It
   stamps each SAGE answer (role, model, prompt hash, tokens, cost) in the gateway and blocks a role
   after its daily cap (defaults: small $0.50, analyst $1, builder $1, reviewer $1 and 10 calls).
   Review spend at `/runs/spend`.
4. **Presets.** Run `python3 scripts/build_presets.py`. Your own entity ids live in a separate
   private repo (a clone of it next to this one, or `SAGE_PRIVATE_DIR`); the script writes presets that
   include them to `presets/sage-presets.json` there. Import that file under Workspace → Models →
   Import (the public `owui/presets/sage-presets.json` is generic and has no entity ids). The import also marks the
   four underlying OpenRouter models hidden, so the model selector lists only the SAGE presets. To trim OpenRouter's
   long list further, set the SAGE Agent add-on's **Allowed models** option to those four ids. Install the tool and the filter first; the presets refer to
   them by id (`sage_gateway`, `sage_run_log`).
5. **Roles.** In the SAGE panel (Home Assistant sidebar) give entities role aliases such as
   `role:greenhouse_temperature` (see `docs/roles.md`). To enter a first set, open the panel's **Import roles**
   box, paste the lines of `roles.yaml` (`role_name: entity_id`), click Import, then approve the drafts below.
   (`scripts/seed_roles.py` does the same from a terminal.)
6. **Try it.** Start a chat with a SAGE model, switch on the SAGE Gateway tool and the terminal, and
   ask: *"Find the sensors that measure humidity and tell me how they are recorded."*

## Checking the safety properties

From a chat that has the terminal enabled, ask the agent to run:

```
curl -m 8 -sS https://example.com        # must fail: names do not resolve
python3 -c "from greenhouse_gateway import Gateway; print(Gateway().catalog_status())"   # must work
```

## Not done yet (set by hand for now)

- **Automation-management tools.** Open WebUI lets a model create and edit its own scheduled
  automations. The requirements say only the owners may. Turn those built-in tools off for every
  model in Admin Panel → Models; I could not find a setting that does this from the add-on options.
- **Provider routing per request.** Requiring OpenRouter's no-data-collection routing on every call
  is done through the account setting above, not in code.
- **Request routing, the review loops and tool building** are the next steps.
