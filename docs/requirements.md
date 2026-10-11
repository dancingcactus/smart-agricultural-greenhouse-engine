# Greenhouse AI Operator: Requirements

Oct 4, 2026 · @Justin Grover

## Purpose and goals

The add-on runs an LLM as a virtual head grower for the hanging-basket greenhouse: it reads what Home Assistant and the cameras see, proposes automation changes, and is scored against what people actually did, before it is allowed to control anything.

Goals:

- Shadow-run an LLM through OpenRouter, so models can be swapped and compared in minutes, on live greenhouse data with no ability to actuate.
- Let the agent write its own analysis tools on top of provided resources: Home Assistant read APIs, weather APIs, VictoriaMetrics history, and the hourly image library.
- Define effectiveness metrics before the trial starts, and score every later change to the prompt, model, or tools the same way.
- Package it as a Home Assistant add-on with a web UI, so it runs beside the automations it studies.

Non-goals for this version: direct actuator control; replacing the fast Home Assistant loops (heating, venting, irrigation valves, pH dosing); diagnosing plant disease from images.

## Phases and autonomy levels

&#91;embedded content: phases and gates · 4 phases, 3 gates\]

Shadow is the scope of this version. Each later phase starts only when the gate before it is passed, and every gate's thresholds are set before the trial starts.

## Architecture

&#91;embedded content: architecture · 3 add-ons and 1 external model server\]

The gateway is the only container with Home Assistant access. The agent side reads a mirror and writes only proposals, so a tool the agent builds has nothing to actuate. Growers reach Open WebUI from the Home Assistant sidebar, and models are reached through OpenRouter over the internet.

## User interface

Open WebUI is the human front end and Open Terminal is the agent's sandboxed shell; the two are separate containers, and only Open WebUI is shown to people. Home Assistant's own docs now call add-ons "apps"; this document uses add-on.

| Component | Role | Used by | Requirements |
| --- | --- | --- | --- |
| Open WebUI | Chat with the agent, review proposals, browse logs | Growers | Connects to OpenRouter through its OpenAI-compatible API; shown in the HA sidebar via Ingress |
| Open Terminal | Shell, file, and Python access for the agent's own tools | The agent (through Open WebUI); a person only to debug | Runs in its own container; protected by an API key; not exposed on a host port |

Requirements:

1. Open Terminal is registered under Open WebUI's Integrations settings, not as a tool server. Its requests are proxied through the Open WebUI backend, so it only has to be reachable from that backend.
2. The OpenRouter API key (stored as a secret) and the model name are add-on options, so the model can be swapped without rebuilding.
3. Each proposal appears in the UI as a diff with its rationale and a falsifiable prediction, plus a verdict: better, equivalent, worse, or dangerous, with an optional comment. This verdict feeds the evaluation data.
4. A grower can add a log entry (an action taken or something observed) from the UI or from Home Assistant in under 30 seconds.
5. Every conversation, tool call, and proposal is stored with the prompt, model, and tool versions that produced it.

## Resources for the agent

Beyond sensor history, the agent needs the context a head grower carries in their head: what is planted, what people did, and how the greenhouse is built. Eight resources are required for the shadow trial; the rest raise quality and can follow.

| Resource | What it gives the agent | Form | Priority |
| --- | --- | --- | --- |
| Home Assistant state and config | Current states, logbook, entity names, automation YAML | Read-only gateway endpoints; YAML as a git copy | Must |
| VictoriaMetrics history | About three years of sensors, actuator states, and settings | Read-only queries through its Prometheus-compatible API, with an as-of cutoff | Must |
| Weather actuals and archived forecasts | What was forecast at each past moment, not just what happened | Forecast pulls saved on a schedule, starting now | Must |
| Hourly images | What the crop looked like, and when | Image library plus a manifest (time, camera, zone) | Must |
| Grower log | Manual actions and observations the sensors cannot see | Timestamped entries, quick to add | Must |
| Crop and basket context | What is planted where, growth stage, target ranges | `targets.yaml` plus a basket map | Must |
| Greenhouse description | Zones, sensor heights, actuator capacities, what each automation controls | One markdown file plus a floor plan | Must |
| Automation change history | Past human decisions with the data around them | Git history of the HA config | Must |
| Outcomes | Yield, quality, losses, pest and disease events | Weekly notes | Should |
| Sensor metadata and maintenance log | Calibration dates, known faults, replacements | Table | Should |
| Reference library | Breeder culture guides, pH and EC by species group, scouting guides | One short markdown file per topic | Should |
| Modeling tools | Forecasts, anomaly bands, and daily or per-run predictions | Chronos-2, LightGBM, and TabPFN pre-installed in the sandbox | Should |
| Labeled reference images | Healthy, wilting, Botrytis, and stretch examples from our own cameras | Tagged subset of the image library | Should |
| Production calendar | Planting, pinch, and ship dates to reason backward from | Table per crop | Should |
| Thermal and water models | What-if estimates for setting changes | Fitted from our own data | Later |
| Energy and water costs | Lets it trade comfort against cost | Rates table | Later |
| New sensors | A lux sensor at basket height (and optionally outside); ground temperature sensors for the GAHT; basket-height temperature and humidity; a second basket scale on a different basket | Added to Home Assistant | Later |

Withheld on purpose:

- Home Assistant tokens of any kind, and every write or service-call endpoint.
- Direct internet access from the sandbox; package installs come from a mirror only.
- Edit rights on the protected automations (frost, overheat, overflow, pump dry-run), which stay readable.
- The ability to create or edit its own scheduled runs in Open WebUI; an admin sets schedules.

Requirements:

1. Every resource has an entry in `RESOURCES.md`: what it is, schema or endpoints, entity names and units, sampling rate, known gaps and quirks, and what it cannot tell you.
2. During evaluation runs the gateway clips every query to an as-of time, so replayed episodes cannot see the future. For VictoriaMetrics it clips the query end time and rejects constructs that can read past it, such as negative offsets and the @ modifier.
3. The gateway logs every call, which shows which resources the agent's tools actually use.

## Config snapshot and catalog

The agent learns what the entities and automations are from a read-only snapshot and two generated catalogs, never from the live config folder or a Home Assistant token.

Requirements:

1. The gateway add-on mounts the Home Assistant config folder read-only, and the agent's containers never see it.
2. A snapshot job copies an allowlist of files into the read-only mirror: automations, scripts, packages, helper definitions, and dashboards. It never copies `secrets.yaml` or the `.storage` authentication files.
3. The mirror is committed to git on every change, which provides the change history used to build the episode library.
4. A narrow, read-only gateway API serves live data the files do not hold: entity states, the entity, device, area, and label registries, available services, automation traces, and the logbook.
5. The same job builds an entity catalog: each entity's ID, friendly name, type, unit, area, labels, and the automations that reference it.
6. It also builds an automation catalog: each automation's purpose, triggers, actions, the entities it touches, its last run, and whether it is managed or protected.
7. A glossary in the owners' own words explains cryptic entity names, for example which sensor is the GAHT output air temperature. The agent drafts entries and an owner approves them.
8. The catalogs and glossary are regenerated on every snapshot and listed in `RESOURCES.md`.
9. Home Assistant's built-in MCP server is not used, because it can control devices.
10. Acceptance test: a search of the mirror and the catalogs for known secret values finds none.

## Agent-built tools

The agent writes its own tools as tested Python modules in the sandbox, and a tool counts as part of the system only after it has a passing test and a one-line index entry.

Lifecycle:

1. Draft: a Builder-role run writes the tool in its own working branch, calling only the gateway, never a raw source.
2. Test: every tool has a test against frozen historical data with a known answer.
3. Promote: the tool is committed to the tools repo with an entry in `tools/INDEX.md`. A person reviews new tools at first, then only changes to existing ones.

Requirements:

1. The agent reads `tools/INDEX.md` before writing anything new, so it extends existing tools instead of duplicating them.
2. The tools directory is committed to git after every run, and every proposal records the tool-set version it used.
3. Tools are frozen during evaluation runs; a tool change is treated as a harness change and must pass the episode library before adoption.
4. The first phase is a curriculum of questions with answers we already know (peak temperature in zone 1 on a given day, water use in a given week, nights above 90% humidity). It builds the toolkit and gives each tool a scored test.

Pre-installed modeling tools:

1. The owners pre-install Chronos-2, LightGBM, and TabPFN in the sandbox image, with model weights downloaded in advance because the sandbox has no internet access. The builder wraps them in tools and installs nothing.
2. Each has its own job. Chronos-2 handles continuous series (forecasts and anomaly bands). LightGBM handles daily or per-run tables (water use, GAHT run efficiency, misting and venting runtime, what drives humidity). TabPFN handles very small tables (crop-cycle outcomes, new sensors, image-score calibration).
3. TabPFN is used only where LightGBM would overfit, and its license is checked for commercial use before it is installed.
4. Every modeling tool validates with a time split, never a random one, and respects the as-of cutoff. A model is trusted only if it beats a simple baseline, such as yesterday at this hour, on a rolling backtest.
5. What-if predictions are trusted only inside the range of settings seen in the training data.

Toolkit measures: curriculum accuracy, reuse of existing tools versus new ones written, tool count over time against accuracy, and gateway coverage of the resources.

## Monitoring loop

The agent checks the greenhouse in three tiers, cheapest first, and how deep it goes follows the existing mode switch.

| Tier | When | What runs |
| --- | --- | --- |
| Watch | Hourly in growing mode | Plain code run through a tool, no reasoning: stale or flat sensors, readings outside the forecast band, and equipment commanded with no matching change in the sensors. It wakes the daily review early only when a check fires. |
| Daily review | Every morning in growing mode, plus an evening check | The LLM reviews the last 24 hours and the next 24 to 72 hours. |
| Weekly review | Once a week in every mode | Trends, an improvement review, and proposals for automation changes. |

The mode comes from the existing three-state switch, which the owners set:

| Mode | Cadence |
| --- | --- |
| Growing | Hourly watch, daily review with an evening check, weekly review |
| GAHT charging | Daily review with charging checks instead of crop checks (provisional), weekly review |
| Off | Weekly review only |

The daily review looks for, in this order:

1. Data health: stale, flat, or implausible sensors, gaps in VictoriaMetrics, and missed camera shots. It does not reason from a bad reading.
2. Environment against targets: temperature and humidity at basket height, night humidity (Botrytis risk), heat stress, and light for the crop stage.
3. Water and nutrients: use against what the weather predicts, sudden jumps or drops (a leak or a dead pump), pH and EC drift, and the fertilizer tank level.
4. Equipment behaviour: what was commanded against what the sensors saw, and automations that ran far more or less than usual.
5. Coming weather: frost, heat, or wind in the next 72 hours, and whether the automations are ready for it.
6. Cameras: the image scores (see Model tiers) and the fixed views against the same views a week earlier. It flags wilting, condensation, or obvious change for a person and does not diagnose.
7. Its own earlier predictions, scored against what happened.

The weekly review adds trend and change checks:

1. Trends: time in range per zone, water and energy use, sensor drift, and progress toward the ship date.
2. Last week's changes: how each applied change performed against its own prediction, plus a replay of its tools' tests.
3. An improvement review, described next.

The improvement review looks for:

1. Waste: energy or water use above what weather and crop stage explain, and equipment working against itself, such as heating and venting at the same time.
2. Time out of range: where and when conditions leave targets, and the setting or automation most likely responsible.
3. Repeated manual work: actions in the grower log that people do again and again and that could become automations.
4. Automations that rarely fire, always fire, or never change the outcome.
5. Data gaps: a measurement that would have explained a problem but is not recorded, such as a missing sensor at basket height.
6. Its own toolkit and dashboard: slow or unused tools, and views nobody needs.

Each improvement carries what it is, the evidence, expected benefit, effort, risk, and a falsifiable prediction. At most five are reported per week, ranked by expected benefit. Those inside the control scope can become proposals; the rest are listed as suggestions for the owners.

The greenhouse's ground-to-air heat transfer (GAHT) system is charged in summer and winter when the air is warm enough, then used to heat in early spring and to cool in late spring. The reviews add these checks, using ground and duct sensors where they exist and listing missing ones as data gaps:

1. While charging: whether charging ran only when the air was warm enough, how much the ground temperature rose, fan runtime and energy use, and any fault.
2. While in use in spring: heat delivered or removed against what the greenhouse needed, ground temperature drawdown, and whether the system ran when it should have.
3. Weekly: energy stored against energy delivered across the season, charging efficiency, and settings that could be tuned, such as the air temperature that starts charging or when to switch from heating to cooling.

What the checks can use today:

| System | Data and settings available |
| --- | --- |
| GAHT | Input and output air temperature and humidity, and on/off |
| Venting | Draws in outside air for heating, cooling, and humidity reduction; interior and exterior temperature and humidity settings |
| Misting | Cools the plants when it gets too hot |
| Basket weight sensor | One sensor at the end of the drip line: daily water use and growth for that one basket |
| Light | No sensor yet; a lux sensor can be added |

Consequences for the checks:

- No ground temperature sensor is listed for the GAHT, so the agent treats the output air temperature at the start of a run, after the system has rested, as a rough stand-in. A ground sensor goes on the data-gap list.
- No airflow measurement is listed, so energy moved by the GAHT is a relative estimate (temperature difference times runtime), not a measured figure.
- The venting settings are the agent's main tuning targets and the likeliest first helpers in the control scope.
- Misting is checked for running when it should not (cool air, night) and for its effect on humidity, because high night humidity raises Botrytis risk.
- The single basket scale, at the end of the drip line, gives water use (weight drops between waterings) and a growth trend (weight after watering over time) for that one basket. It cannot show differences between baskets, and an end-of-line basket may not represent the others, so the agent reports these as indicators for the line and flags scale drift.
- Without light data the agent cannot tell a sunny day from a mild one. A lux sensor at basket height, and optionally one outside, would be the most useful addition.

Requirements:

1. Every run writes the same short report: green, amber, or red, the top findings with evidence, and any questions for people. It is stored and shown on the agent dashboard.
2. "Nothing to report, no change proposed" is a valid result, because restraint is one of the evaluation metrics.
3. Notifications go out for amber and red results only, and a failed run, or one skipped for any reason other than being paused or not due in the current mode, always notifies.
4. Urgent conditions such as frost or a dead pump come from existing Home Assistant alerts, not from the LLM schedule.
5. All loops run as Open WebUI Automations. The watch loop's checks are plain code run through a tool and need no reasoning.
6. The mode switch is read at the start of every run, so a mode change takes effect on the next run.

Configuration:

1. Each loop (hourly watch, morning review, evening check, weekly review) is an Open WebUI Automation, so its on/off switch (pause and resume), schedule, prompt, and model are all set on the Automations page.
2. The hourly watch is also an Automation. It runs on the small role and calls a code-only watch tool, and it reports only when a check fires, so a paused watch costs nothing and a running one costs very little.
3. Each run begins by reading the current mode and exits if the mode's cadence does not allow that loop.
4. Prompts are edited on the Automations page and take effect on the next run. Each run's chat stores the exact prompt used, and the run log records a hash of it, so results can still be traced to a prompt version.
5. The owners can replay the episode library against an edited prompt to compare before and after, but nothing requires it.
6. A prompt changes what the agent looks for and how it reports, not what it is allowed to do. Permissions, the control scope, and the privacy settings are enforced in code.
7. The automation-management tools (create, update, toggle, delete) are turned off for every model role, so only the owners change loops, from the UI.
8. A paused loop is silent. A failed run notifies, and the last report time for each loop is shown on the agent dashboard, so a loop that is enabled but has stopped reporting stands out.

## Model tiers

Work is routed to model roles by workflow stage, and each role is pinned to a model and provider, with the mid-tier model able to escalate on request.

| Role | First pick | Alternates | Used for |
| --- | --- | --- | --- |
| Small | Claude Haiku 5.5 | DeepSeek V4.1 Flash, GPT-5.4 nano | Running known tools and formatting reports |
| Mid | Qwen3.8 27B | MiniMax M3, GPT-5.6 Luna, Claude Sonnet 5.5 | Chat, triage, investigating findings, camera comparison |
| Builder | GLM-5.2 | DeepSeek V4.1 Flash, DeepSeek V4.1 Pro when released | Building tools, the weekly improvement review, drafting changes |
| Reviewer | Claude Opus 5.5 | GPT-5.6 Sol | Checking new tools and proposals, from a different vendor than the builder |
| Image scorer | Qwen3.8 27B | Claude Haiku 5.5, DeepSeek V4.1 Flash, MiniMax M3 | Scheduled scoring of camera images |

The picks are starting guesses, and each is tested against its alternates on the episode library. The cheapest one that holds up wins. DeepSeek V4 Pro is being retired in favor of V4.1 Flash, so it is not a pick. Steady-state cost is estimated at about $5 to $10 a month, assuming about 50k tokens in and 5k out per daily review; logged spend replaces that estimate.

Routing rules:

1. The role is set by workflow stage, or by the routing classifier for unscheduled requests, and is never chosen by the generative model. The mid-tier model has an escalate tool with a daily budget.
2. Work moves down a role once a promoted tool and its test exist for it.
3. A task retries one role up when a tool test, the proposal validator, or a repeat run disagrees. The model's own confidence is not a trigger.
4. A model from a different vendor than the author reviews new tools and proposals.
5. Each role is pinned to a model and provider, stamped on every logged run, and changing one is a harness change that must pass the episode library. Auto routers are not used for evaluated runs. Fallback models within a role are allowed for outages, and the model that answered is logged.
6. Roles are chosen by capability, not size: camera work needs an image-capable model.
7. Spend is capped and logged per role, with a daily cap on reviewer calls.
8. Every request uses the privacy options in the non-functional requirements, and a pick without a qualifying host is replaced by its alternate.

Image scoring:

1. A scheduled job scores each new camera image (wilting, condensation, leaf color, visible pests) and writes the scores into VictoriaMetrics as time series. The agent reads them like sensors and opens an actual image only when a score looks off.
2. Scoring prompts are graded against the labeled reference images before use, and graded again whenever the model or prompt changes.
3. Plain image processing runs alongside it without a model: change against the same view a week earlier, and the green-pixel fraction as a canopy-cover measure.
4. Camera framing stays fixed so scores do not drift, and night images are skipped to save cost.
5. The mid-tier model can still be asked to look at any image directly.

## Routing and decision model

A small non-generative decision model, Jev, classifies each incoming request to pick the role and the agent process, and is also available to the agent as a `decide` tool for bounded judgments such as whether a forecast warrants running the fan early.

Jev is TypeSafe's decision model. It takes a state and typed questions and returns probabilities, with no generated text. It costs about $0.042 per 1M input tokens with free output, and OpenRouter lists it.

Request routing:

1. Before any generative call, Jev classifies the request. A choice question picks the agent process (follow-up to a daily review, investigating a finding, tool build, weekly review, quick lookup, or chat), a score question rates difficulty, and yes/no questions flag whether it needs an image or touches the control scope.
2. The process and difficulty map to a role through a table kept in config.
3. High-confidence results route automatically, moderate ones go to the mid-tier model to confirm, and low ones go up one role or to the owners. Thresholds are set on the episode library.
4. Jev only chooses among roles and processes that already exist. It never widens permissions, the control scope, or the privacy settings, and those checks stay in code.
5. Text that did not come from the owners or the system is not passed to Jev as instructions, because Jev does not treat its input as hostile by default.

The `decide` tool:

1. `decide` is a read-only tool that takes a state (for example forecast quantiles, thresholds, recent readings, mode, and settings) and a question, and returns a probability, a confidence, and the inputs it used.
2. It advises and never acts. Its answer goes into the agent's reasoning, a proposal, or an alert, and any action still passes the control scope and the control tools.
3. Numeric threshold checks are computed in code first, for example whether a forecast quantile crosses the venting threshold within the fan's lead time. Jev is asked for judgments that rules handle poorly, or as a second opinion when the rule and the context disagree.
4. The first decision type is whether a forecast warrants starting the fan early. Others are added one at a time.
5. Each decision type has a labeled test set from the episode library and goes live only when its accuracy and calibration pass. Thresholds are set per decision type, and are stricter for anything that affects water, misting, or the crop.
6. In the Shadow phase every decision is logged next to what a person did, and that comparison is the evaluation data.
7. The hourly watch tier may call it when a check fires, to decide whether to wake the daily review early.

Running it:

1. Jev is pinned to a specific version, not the `jev-latest` alias, because thresholds depend on its behavior. The version is stamped on every logged decision, and changing it is a harness change that must pass the episode library.
2. Every call is logged with its probabilities, and routing accuracy and decision calibration are measured against what the owners would have chosen.
3. Requests go through OpenRouter with the same privacy options as the other roles.
4. Jev is never the agent: it makes judgments inside deterministic code and does not plan or write.

## Delegation and tool builds

Tool builds always run on the Builder role, started through a dedicated tool, because Open WebUI's built-in sub-agents run on the parent's model and have no way to choose another.

Tool builds:

1. The mid-tier model starts a build by calling `start_build` with a brief: the tool's purpose, the resources it may use, and acceptance tests with known answers. The tool is installed by an admin and is the only way to start a build.
2. `start_build` runs the build on the Builder role's Open WebUI model preset (its own prompt and the Open Terminal tools) and returns a short summary with the branch name.
3. The build works in its own directory and git branch, writes the module and its tests, runs the tests, and commits. The build never promotes its own tool.
4. Each build has an iteration limit and a spend limit. If its tests still fail at the limit, one retry runs on the Reviewer role's model, given the failed attempt's notes.
5. Builds commit to git as they go, because background runs stop when the server restarts.
6. A passing build goes to the Reviewer role, then to promotion as set out in Agent-built tools.
7. Builds are capped per day, and frontier retries are capped separately.

Built-in sub-agents:

1. An admin enables them, with concurrency capped at about three.
2. They are used only for independent analysis, for example one per question in the weekly review. Each writes its findings to a file and returns a short summary.
3. Sub-agents share one sandbox, so any that write files work in their own directory or branch.
4. Sub-agents cannot start builds. Whether tool access can be limited per sub-agent is verified before this is relied on.
5. Third-party sub-agent tools are not installed.

## Safety and permissions

The agent cannot actuate anything because no credential that can write to Home Assistant exists anywhere it can reach; a prompt instruction is not part of the guarantee.

Requirements:

1. Only the gateway add-on holds Home Assistant access. The Open Terminal and Open WebUI containers get no Home Assistant or Supervisor token and no `homeassistant_api` or `hassio_api` permission.
2. Add-ons share an internal network by default, so isolation rests on credentials, not network rules. Verify this on our install and add firewall rules if it can be done.
3. The gateway exposes read endpoints only: states, history, logbook, and the automation config. Service-call routes do not exist in it.
4. The sandbox mounts the data mirror read-only. Its only outputs are commits to a local proposals repo and notifications.
5. The Home Assistant config is split into a protected package (frost, overheat, overflow, pump dry-run interlocks) and a managed package. The agent can read both and propose changes to the managed one only.
6. Every proposal passes automatic checks before a person sees it: YAML lint, Home Assistant config check on a staging copy, entity and value bounds, no deletions, and a flip-flop check against recent proposals.
7. Only an admin can add tools, functions, or integrations to Open WebUI, and community skills are not installed.
8. Acceptance test before the trial: from inside the sandbox, every attempt to call a Home Assistant service fails, and attempts made through the gateway are refused and logged.

## Control scope

In later phases the agent may change only a named set of automations and a named set of entities, and both limits must hold at once.

| Limit | What it names | What it prevents |
| --- | --- | --- |
| Managed automations | The automation IDs the agent may edit | Edits to anything else, including the protected interlocks |
| Allowed entities and services | The entities its automations may act on, the services they may call, and the value range for each | An edited automation reaching a device or setting outside the list |

Proposed first scope: helpers, not devices. The agent edits setpoint and schedule helpers (`input_number`, `input_datetime`, `input_boolean`) that human-written automations already read, so the heating, venting, misting, and dosing logic stays human-written. Devices follow only after the helpers have run cleanly. The venting system's interior and exterior temperature and humidity settings are the likely first candidates.

Each device or automation that joins the scope should meet these criteria:

- A wrong change takes hours, not minutes, to do damage.
- A change can be reverted by restoring the previous helper value or automation version.
- A protected interlock covers the worst case and does not depend on the agent.
- The effect is visible in sensors or cameras, so it can be measured.

Irrigation and pH dosing are the last candidates, because baskets dry out quickly and a bad change can cost the crop within a day.

Requirements:

1. The two lists are set by a person in the Home Assistant UI through labels (see Dashboard and scope controls), and the agent has no way to change them.
2. The proposal validator rejects any change to an automation not on the first list, and any automation that references an entity, service, or value outside the second.
3. Reads are unrestricted within the gateway; the lists limit writes only.
4. Approved changes are applied by the gateway, never by the sandbox, and each can be reverted in one step.
5. The scope grows one entity or automation at a time, and each addition records who approved it and why.

## Dashboard and scope controls

The agent gets one dedicated Home Assistant dashboard that it may edit, and a person sets what it may manage with labels in the Home Assistant UI, not by editing files.

Agent dashboard:

1. It is the agent's display surface: its latest review, open proposals, forecasts with uncertainty bands, anomaly flags, and its predictions against what happened. A grower can ask it in chat to add or change a view.
2. The gateway applies dashboard edits to that one dashboard only, never to any other, including the owners' current working dashboard.
3. Edits are display-only: the validator allows only listed card types and properties and rejects everything else (service-calling actions, tile features, custom cards, embedded pages, and rows that toggle), with tap actions limited to none or navigation, so card YAML cannot actuate anything. Controls are managed only through the gateway's control tools and appear on this dashboard, never as card YAML the agent writes. The allowed list is a short file the two owners maintain, and it can include custom cards they have already vetted. A small set of known-bad dashboards must be rejected before the validator is trusted.
4. Display edits are allowed from the Shadow phase because they cannot actuate, and each edit is kept in version history so it can be reverted.

Dashboard preview:

1. The agent can call a preview tool that returns screenshots of its dashboard at a phone, a tablet, and a desktop width, so it can check and fix layout before a person sees it.
2. Preview runs only after the validator passes, so unvalidated content is never rendered in a logged-in browser.
3. The renderer runs inside the gateway under a dedicated Home Assistant user. Safety does not rely on that user's role; the validator is the control.
4. Screenshots go back to the agent as images and are logged with the dashboard version they show.

Agent-created controls:

1. The agent creates a control (a button or toggle) by calling a gateway tool with an entity, a service, and values, never by writing card YAML. The gateway builds the card and places it on the agent's dashboard.
2. The agent can create, update, and delete its own controls through gateway tools, and has no other way to put an action on the dashboard.
3. A person can change or remove any control at any time in the Home Assistant UI.
4. A control may target only entities labeled `agent-control`, within helper limits, and never anything on the deny list. The gateway checks this when a control is created or changed.
5. A control's label is generated by the gateway from its real target, for example "Vent 2: open for 10 minutes" built from the entity, service, and values. The agent supplies no label text, so a label cannot describe something different from what the control does.
6. Controls ask for confirmation before acting, and every press is logged with the control and the person who pressed it.
7. Controls are off until the owners enable them with an add-on setting, because a press is an executed change.
8. An add-on option, require approval for new controls, is off by default. When it is on, a new control waits on the agent's dashboard as a non-actuating preview until a person approves it, so the step can return during testing.

Scope controls:

1. Scope is set with two labels (names are suggestions): `agent-managed` on the automations the agent may edit, and `agent-control` on the entities its automations may act on. A person adds or removes them in the normal Home Assistant UI, in bulk if needed.
2. Value ranges for helpers come from each helper's own minimum and maximum, which a person also sets in the UI.
3. The gateway reads labels fresh on every validation, so a change takes effect on the next run without a restart, and it records the scope in force with every proposal.
4. A short deny list in the gateway config (frost, overheat, overflow, pump dry-run) overrides labels, so no label can put an interlock in scope.
5. The agent has no write path to labels, the deny list, or helper limits.
6. A human-owned dashboard lists everything currently labeled and carries a pause switch (an `input_boolean`) that stops all agent writes at once. It is not on the agent's dashboard, because the agent can edit that one.

## Evaluation and logging

Because the agent's proposals are never executed in shadow mode, effectiveness is judged from several imperfect signals together, defined before the trial starts.

| Metric | What it measures | Source |
| --- | --- | --- |
| Diagnosis accuracy | Whether the problem the agent reports is real | Weekly spot-check against data and cameras |
| Human verdict | Share of proposals judged better, equivalent, worse, or dangerous | Verdicts entered in the UI |
| Dangerous proposals | Count; must trend to zero before any autonomy | Verdicts plus automatic bound checks |
| Agreement with human edits | Whether it finds the same problem and proposes something similar | Episode library from automation git history |
| Restraint | Share of nothing-to-fix episodes where it proposes no change | Episode library |
| Validation pass rate | Proposals that parse and pass the policy checks | Automated checks |
| Churn | Proposals that reverse or repeat its own earlier ones | Proposals repo |
| Prediction calibration | Whether its logged, falsifiable predictions came true | Prediction field, scored once changes are applied |
| Forecast skill | Forecast error against the yesterday-at-this-hour baseline | Modeling tools backtest |

Requirements:

1. The episode library holds frozen snapshots of ordinary days, hard days, real incidents, and nothing-to-fix periods, each with its inputs and what happened, built from the automation git history and the grower log.
2. Evaluation periods are held out of any forecaster fine-tuning.
3. Every logged decision carries the prompt, model and serving provider, tool-set, and context-building code versions.
4. Each episode is re-run 3 to 5 times per version, changing one thing at a time.
5. Old and new versions can run side by side on live data in shadow mode.
6. Once changes are applied, each is tested with an alternating-day crossover of the old and new automation for 2 to 4 weeks, switched by an HA toggle helper.
7. An optional run of the episode library on a frontier API model sets a ceiling for what the local model could reach.

## Non-functional requirements

The add-on must not assume the Home Assistant host can run a model; it needs only network access to OpenRouter.

1. Models are reached through OpenRouter's OpenAI-compatible API, so the Home Assistant host needs no GPU. Only the Open WebUI add-on needs outbound internet access; the sandbox gets none.
2. Modeling inference runs on a CPU in the sandbox. Fine-tuning and backtests run on a separate machine or a rented GPU, unless the Home Assistant host has one, and produce adapters that are copied in.
3. Data prepared for the agent is resampled (15 minutes or hourly for forecasting; full resolution kept for anomaly work), and images are available at reduced size.
4. The tools repo, proposals repo, logs, and fine-tuned adapters live in add-on storage that Home Assistant backups include.
5. Images and logs have a retention policy so storage does not grow without bound.
6. Model, prompt, and tool-set versions are pinned, and any of them can be rolled back to a previous version without rebuilding the add-on.
7. Daily reviews run unattended through Open WebUI Automations, and a failed run produces a notification.

Data and cost:

- Requests set the OpenRouter provider preference to deny data collection, and can require zero-data-retention endpoints, which narrows the models available.
- The provider is pinned for each run and the provider that answered is logged, because routing can otherwise change it between calls.
- The API key has a credit limit set in the OpenRouter dashboard, and spend per run is logged.

## Open questions

These fourteen answers decide the architecture and the first build.

- [ ] Is it acceptable for sensor data and camera images to go to third-party model hosts, or should the mid tier be self-hosted instead?
- [ ] What hardware (CPU, RAM, storage) does Home Assistant OS run on, and can it host three add-ons (gateway, Open WebUI, Open Terminal) alongside its current load, plus a headless browser for dashboard screenshots?
- [ ] Home Assistant OS runs only Supervisor-managed add-ons, so Open WebUI and Open Terminal each need a local add-on wrapper. Is that acceptable, or should they run on a separate machine with only the gateway on Home Assistant OS?
- [ ] Does Open WebUI work behind Home Assistant Ingress, or does it need its own port and login?
- [ ] Can firewall rules between add-ons be set on our install, or does isolation rely on credentials alone?
- [ ] Which weather APIs, how does Home Assistant write into VictoriaMetrics (Prometheus integration or InfluxDB write protocol), and what are the metric names, sampling interval, and retention?
- [ ] Which baskets (varieties, mixes, zones) go into `targets.yaml` and the basket map?
- [ ] Which devices or automations form the first control scope, and which automations are protected?
- [ ] How do approved changes reach Home Assistant: a config sync followed by an automation reload, or the Home Assistant API?
- [ ] Can the gateway read labels, helper limits, and dashboard config, and write the agent dashboard, through the Home Assistant API available to add-ons?
- [ ] What air temperature starts GAHT charging, is airflow or fan speed measured, and which review cadence should apply while it charges?
- [ ] Can an admin-installed Open WebUI tool start a run on another model preset through Open WebUI's own API, with the Open Terminal attached, and keep it running after the parent chat ends?
- [ ] Where do grower-log entries come from: a Home Assistant form, chat, or voice?
- [ ] Who reviews proposals, how often, and how long does the shadow trial run before we judge it?
