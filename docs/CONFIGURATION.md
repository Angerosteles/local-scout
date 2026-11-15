# Configuration and operation

Part of the [Local Scout README](../README.md).

## Commands inside Hermes

With plugin command APIs available, `/scout` or `/scout status` shows the effective state (including in-memory defaults) with a short measurement summary, `/scout details` shows detection and false masks for every type (also from gateways, read-only), and `/scout help` lists the commands. Change settings from a local CLI/TUI session with the examples below; gateway, absent or unknown origin is read-only, and changes preserve unrelated grants. Saved changes apply to the next message without a restart; turning masking on/off also requires a new session to refresh the masking system-prompt section. `hermes scout` or `hermes scout setup` opens the existing terminal menu for the active Hermes profile (`HERMES_HOME`); other arguments go to the existing CLI parser.

```text
/scout names off|basic|deep
/scout addresses on|off
/scout languages on|off
/scout masking on|off
/scout insights on|off
/scout note on|off
```

`note` is the reply note: `on` (mode `report`) adds a line under a reply naming the data types detected, never values; `off` (mode `background`) hides it, and masking works the same. A triage profile (`on_demand`, set by `setup-triage`) shows the note as off; `/scout note on` switches it to `report`.

```sh
hermes scout
hermes scout status
hermes scout configure --mode report --name-masking deep
```

## Running the operator CLI

After `hermes plugins install`, the code is in `~/.hermes/plugins/local-scout` and its Python dependencies are in the environment Hermes builds for plugins (`~/.hermes/installs/<id>/environments/<generation>/venv` in Hermes 0.23). The launcher `scout.sh` finds the newest of those environments that actually has numpy and scikit-learn and runs the CLI with it:

```sh
alias scout="bash ~/.hermes/plugins/local-scout/scout.sh"
scout            # interactive menu (needs a terminal)
scout status     # any CLI command
```

`HERMES_HOME` selects another profile (default `~/.hermes`), and `LOCAL_SCOUT_PYTHON` names an interpreter explicitly. The CLI runs as the package `plugin`, so the launcher keeps a link named `plugin` to the installed directory in `${XDG_CACHE_HOME:-~/.cache}/local-scout` and puts that directory on the module path; relative file paths still refer to your current directory. Without a terminal, `scout`, `scout menu` and `scout setup` print “Interactive mode requires a terminal.”; `configure` without `--mode` still exits with an error. Linux and macOS only.

Without the launcher, run `python -m plugin.cli --home ~/.hermes …` with the plugin environment's interpreter (`LOCAL_SCOUT_PYTHON` or the venv under `~/.hermes/installs`); see `scout.sh`.

The examples below write `python -m plugin.cli --home /path/to/profile …`; with the launcher that is `HERMES_HOME=/path/to/profile scout …`.

## Setup menu

Run `scout` or `scout setup` in a terminal. Up/Down or j/k selects a row. **Enter, Space and Left/Right change its value**; Names and Mode show every choice as radio buttons, with an explanation of the selected value below the rows. Enter on a setting keeps the menu open.

Choose **Save** (Enter) or press **s** to write the draft. **Cancel**, **q** or **Esc** leaves setup; unsaved changes trigger `Save changes? [y/n]` (y saves, n discards, Esc returns to editing). Ctrl-C or end of input discards the draft. Leaving any profile without edits reports `No changes.` without writing settings. Save on a fresh profile explicitly persists the active defaults; Save without edits on an already saved profile reports `No changes.` Without curses, enter a row number to change it, 7 to Save or 8 to Cancel; s/q and the confirmation work the same way. Blank input changes the selected row.

Enabling the plugin in Hermes enables local masking on a fresh profile: `mode=report`, profile grants `observe`, `report` and `mask`, addresses on, names and other languages off. Insights is off (`insights: []` in the grant map, with `persist_observation: false` disabling the legacy observation journal); triage, local answers and learning remain opt-in. These defaults operate in memory without creating `settings.json`, aggregates or a usage journal. Only an explicit operator save (menu, `/scout` edit or `scout configure`) creates settings. Use `/scout masking off` or `scout configure --mode off` for a lasting opt-out. Saved keys take precedence; missing keys use defaults, and an explicitly saved empty grant map stays empty. Masking sends nothing off the machine; Hermes sends the resulting request to its configured provider. The top of the screen summarises the frozen synthetic holdout for the selected settings (detection range and false masks per 100 texts, names separately); the **Details** row opens one table with detection and false masks for every type, and **Reply note** is the `report`/`background` mode. These are measurements, not guarantees; see [definitions and full counts](EVIDENCE.md#180-frozen-holdout). The screen stays within 80 columns and reflows in narrow terminals.

After saving, the menu lists changes in words and prints the equivalent `scout configure …` command, preserving existing grants outside the menu's profile scopes. **Settings apply from your next message without restarting Hermes:** the running Scout reloads `settings.json` when its modification time or size changes. Only the masking system-prompt section needs a new session when masking is turned on or off; the menu adds that reminder only for that change. File-based triage remains available through `scout setup-triage --history PATH`.

## Setup-triage in detail

Prepare a UTF-8 JSONL file with one object per line, for example `{"text":"Please help with my invoice","label":"billing"}`. Use at least 20 examples and two classes, with multiple training examples per class. Do not include personal information. You may specify `"split":"train"` or `"split":"validation"` on every line; `test` is rejected. If omitted, a deterministic text-hash split assigns about 80% to training and 20% to validation. Duplicate texts stay together. Make sure the resulting validation set has at least five examples.

```sh
python -m plugin.cli --home /path/to/profile setup-triage --history /path/to/history.jsonl --yes
```

Before confirmation, the command explains the local permissions. It saves a local copy of the examples and a model in the profile. Omit `--yes` for interactive confirmation. Add `--mask-prompts` to enable profile prompt masking (Insights then also counts those masks by category and source, never text), `--marker TEXT` for an answer marker, or `--json` for machine-readable output. Turn Scout off with `python -m plugin.cli --home /path/to/profile configure --mode off`.

The output shows a validation coverage/error curve, including the number of accepted examples. `--max-error 0.10` selects the lowest displayed threshold with at least five accepted validation examples and error at most 10%. Without it, the existing validation gate selects the threshold. A threshold fitted on validation data is **not a guarantee** for future user data. `status` also shows the retained curve. Check one request with `python -m plugin.cli --home /path/to/profile handle --task triage --text "example message"`, and view usage with `python -m plugin.cli --home /path/to/profile insights`.


**basal instead of the built-in classifier.** Start a basal engine on this machine, for example `basal-serve --mode gguf --host 127.0.0.1 --port 8765 --model <model dir> --gguf <file>.gguf`. Register it with `configure --mode report --basal-url http://127.0.0.1:8765`, optionally adding `--basal-descriptions labels.json` (one short description per label; otherwise label names are used). Then run `setup-triage --recipe basal_zero_shot`. Nothing is trained: masked validation tickets go to the local engine once to choose the threshold. Expect about 2 s per ticket on a laptop CPU with pico. `handle` uses it, and in-turn local answers forward instead of waiting. Only loopback addresses are accepted. Once registered, the engine is also asked about uncertain password-like values during masking (one line at a time, at most 6 lines per text field and 8 s in total per tool result or provider request; see [Security](SECURITY.md#disclosures)); remove it with `configure --mode report --basal-url ""`. Registering an engine for triage therefore also turns on this masking check; `configure` says so in its output.

## Manual configuration

Install and enable the plugin through your Hermes plugin workflow with its dependencies, then start a new session. Select the same explicit profile as Hermes. From the directory containing `plugin/`, use the operator CLI:

```sh
python -m plugin.cli --home /path/to/profile configure --mode on_demand \
  --dataset support --source /path/to/approved.jsonl --approve observe,report,train
python -m plugin.cli --home /path/to/profile calibrate --dataset support
python -m plugin.cli --home /path/to/profile inspect --dataset support
python -m plugin.cli --home /path/to/profile choose --task triage --recipe logreg --reason 'label_count>=2'
python -m plugin.cli --home /path/to/profile train --task triage --recipe logreg --dataset support
python -m plugin.cli --home /path/to/profile handle --task triage --text 'A new approved task'
```

Review the facts and recipe before choosing; stop on a failed audit or quality gate. For `fields`, choose `regex` without training; for `search`, choose an applicable search recipe. After setup, the agent can use `scout_handle` with `task` and `text`; on abstention, the agent handles the task itself. Text passed to an agent tool has already been seen by the model.

Modes are `off` (Scout off; retained data remains), `background` (masking and in-memory counts without a reply note), `report` (also a reply note naming detected data types) and `on_demand` (triage: no reply note and no usage-hook counts). Grants are scoped to the selected dataset. A missing grant map defaults to `observe`, `report` and `mask` for `profile`, with Insights off; an explicitly saved map is retained, including an empty one. Other datasets, training and answers still need explicit grants. The CLI replaces the full grant map when `--approve` is supplied, so the following example replaces the earlier dataset grants. Review `status` before continuing. For example:

```sh
python -m plugin.cli --home /path/to/profile configure --mode report \
  --dataset profile --approve mask,observe,report,insights
python -m plugin.cli --home /path/to/profile insights
python -m plugin.cli --home /path/to/profile insights --preview
python -m plugin.cli --home /path/to/profile insights --export /local/path/summary.json
```

## Masking options and migration from 1.7.5

```sh
scout configure --mode report --name-masking basic --address-masking on --other-languages off
scout status
```

These options require profile-level `mask` permission and an enabled mode to mask provider-bound content; both are active by default on a fresh profile. Changing a name/address/language option preserves the effective grant map and does not re-enable a saved opt-out. The command above preserves the current grants because it omits `--approve`; supplying `--approve` replaces the entire grant map. Settings apply from the next message. Only the masking system-prompt section requires a new session after changing masking on/off.

| CLI flag / profile key | Values and default | Effect |
|---|---|---|
| `--name-masking` / `name_masking` | `off` (default), `basic`, `deep` | `off` skips names in every language. `basic` keeps detected full names, repeats of that person within the same text, titles with a surname (Mr./Dr./Pani…), and isolated “Surname, Name” pairs in EN, PL and uncertain paragraphs. `deep` additionally allows single names with contextual evidence in confident EN; PL, mixed and uncertain paragraphs retain the previous detector's single-name behaviour. |
| `--address-masking` / `address_masking` | `on` (default), `off` | Enables or disables the address category. Other categories can still overlap an address. |
| `--other-languages` / `other_languages` | `off` (default), `on` | `off` skips names and addresses in paragraphs recognized as another language (DE/FR/ES/IT function-word evidence). `on` permits addresses and basic-level names there, even with `name_masking=deep`. `name_masking=off` still skips all names. |

Names are masked only in **user-role prompt text**, not tool results, assistant history or system instructions. Keeping names in tool results is intentional for tasks such as mail categorization. E-mail, phone, card, IBAN, PESEL, SSN and secret detection runs regardless of language; recognition is incomplete. DE/FR/ES/IT text classified as uncertain can still take the EN/PL fallback, so foreign-name masking is not necessarily zero with `other_languages=off`.

Paragraph routing yields `en`, `pl`, `other` or `uncertain`. The English models require EN to be the most probable class with P(EN) ≥ 0.8 and no Polish veto. Text over 8192 characters, over 64 capitalized tokens, or a failure of the new route retains the previous detector. `basic` still applies its full-name/title/pair/repeat filter after fallback; `deep` retains single-name detections. A baseline failure reaches the normal scan-failure policy below. These limits bound the new name path, not the runtime of the entire mask.

**Upgrading an existing 1.7.5 profile:** absent keys resolve to `off` / `on` / `off`; names are opt-in in 1.8 (1.7.5 masked them in prompts). Saved choices survive when flags are omitted; existing grants and `guard_mode` are retained. With names on, the tradeoff versus 1.7.5: EN single mentions masked fall from 195/309 in 1.7.5 to 8/309 in basic; deep masks 139/309. EN visible false names are 229 in 973 texts in 1.7.5, 36 in basic and 82 in deep. PL deep is noisy: 229 false names in 470 texts (48.7/100). Review the [frozen holdout](EVIDENCE.md#180-frozen-holdout) and select deep or off explicitly if needed. No mode guarantees complete detection.

With masking enabled, the plugin adds a short `local-scout.masking` system-prompt section for new sessions when Hermes exposes that API. It also registers the `masking` skill when supported. They tell the model to preserve placeholders, avoid guessing and request local completion. Masking remains one-way; there is no restore map or numbered pseudonym implementation in 1.8.0.

**Scan-failure policy.** By default content that cannot be scanned is withheld (fail-closed). `scout configure --mode report --guard-mode open` passes it through unmasked for that profile only; `scout configure --mode report --guard-mode closed` restores the default. The same policy applies when masking is enabled by default; `/scout` has no guard-mode command. Unscannable individual system/developer messages are retained; a failure of the whole masking step withholds the request. The setting lives in the profile's `settings.json`; the older `LOCAL_SCOUT_GUARD_MODE` environment variable is no longer read, because it applied to every profile served by the same process.

Review the exact preview before choosing whether to share the local export. An installed CLI is also available as `scout`; `HERMES_HOME` can select the profile. Do not put private data in reasons or tool arguments: Hermes may log them.

## Local answers (llm_execution)

For a triage bot or gateway, Scout can finish a fresh user turn **without the main model call** (Hermes' own title call may still run).
This uses Hermes 0.23 `llm_execution` middleware rather than an agent tool. The plugin keeps
working on contexts without `register_middleware`, but cannot answer locally there.
Set up, inspect, choose and train an approved triage dataset using `setup-triage` or the Manual configuration commands,
then explicitly enable the shortcut for that same dataset:

```sh
python -m plugin.cli --home /path/to/profile configure --mode on_demand \
  --dataset support --approve observe,report,train,answer,insights --answer-task triage
```

`--approve` replaces all grants. `answer` is a separate, dataset-scoped permission; neither
training nor masking permission enables local replies. With no `--answer-task` configured,
local replies are disabled. Optionally add `--answer-marker 'TASK:'`:
the marker must occur **exactly once** in the final user message, and only the text after it is
classified; a missing or repeated marker forwards to the model. Without a configured marker, Scout classifies the entire final user message.
Omitted options preserve an existing configuration. Use an explicit dedicated triage profile:
the shortcut answers every eligible turn in that profile, rather than deciding whether a
general conversation is asking for triage.

The active measured triage decision must resolve to a valid local artifact. Scout reuses
`learning.predict` and the artifact's validation-selected threshold, as in `scout_handle`.
The shortcut does not replay calibration or reread the training dataset on each request.
It masks locally recognized PII before prediction, accepts only a known class with finite
confidence at or above that saved threshold, and never answers `unknown`. Training, choosing
and reviewing the dataset remain explicit operator steps. The original, pre-mask request is
used only locally; fallback receives the current `request`, preserving upstream middleware
changes. Neither request is modified by this shortcut.

The assistant message is exactly two lines (example):

```text
{"intent":"billing","answered_by":"local_scout","confidence":0.950}
Answered locally by Scout; the model was not asked to answer.
```

Supported: chat completions (Nous/OpenRouter), with streaming enabled or disabled. Hermes'
streaming wrapper returns a completed response object; Scout returns that same object shape,
with `finish_reason=stop`, no tool calls, and explicit zero input/output/total token usage.
There are no incremental local deltas; the gateway must display the final response. Codex
Responses and other API modes always call `next_call`. A tool/assistant/result suffix, missing
consent/artifact, low confidence, malformed or multimodal input, any Scout exception, or a
busy cache also forwards once. Exceptions from the downstream provider propagate unchanged.

Limits: final user message 8,000 characters; text blocks only; at most 512 messages; bounded
serialized input (messages/tools/functions) 262,144 characters. Cached CPU models: logreg,
KNN (up to 2,000 rows) and trees (up to 10,000 nodes); CNN and other engines forward. Artifacts
are bounded and validated without pickle; cache invalidates when selection/model files change.
A cooperative budget is checked after loading and prediction: 0.25 s once the process is warm,
up to 5 s for its first decision (cold imports and model load). A slow filesystem can exceed it,
so this is **not a hard real-time timeout**. Cold calls may forward while warming the cache. basal
models always forward here (seconds per ticket). There are no background loaders, downloads or threads.

Insights records `kind=local_answer` only with `insights` or legacy explicitly granted `observe` consent and while mode
is enabled and collection is not explicitly disabled (`persist_observation: false`): answered/forwarded counters, a small allowlist of forwarding reasons, and
`estimated_input_tokens_avoided` (an estimate, see the known limitation below). TUI and HTML show
measured input usage beside it when available. No prompt, class label, session ID or exception prose is
stored. `summarize` and `community_payload` expose
`local_answers {answered, forwarded, estimated_input_tokens_avoided}` and an allowlisted
forwarded-reason breakdown; public reason and PII category counts below five are omitted.
Avoided input tokens are an **estimate**: the character length
of compact serialized provider messages/tools/functions divided by four, only on actual local
answers. This is separate from measured provider usage (zero for a local answer), not an
invoice or an end-to-end A/B savings result. Forwarded counts mean delegation was attempted,
including downstream failures. Journal retention/rotation limits apply.

Known limitation (one live comparison, Hermes 0.23 + Nous, 08.11): this character-based estimate was
about 4.7× above the measured input of the same request when sent to the model
(11,914 estimated vs 2,538 measured). This is not an upper-bound guarantee; the measured A/B benchmark
below is the evidence for savings. A one-shot `hermes -z` process is always cold: the first
local decision may take up to 5 s (numpy/scikit-learn import and model load); later decisions
in the same process keep the 0.25 s budget, otherwise Scout forwards to the model.

An empty scope such as `insights@` in `--approve` saves an empty grant and `persist_observation: false`, explicitly disabling counter persistence, including legacy observation journals; the menu prints this syntax when Insights is off. Existing saved grant maps retain their legacy collection semantics (including a previously saved empty Insights list) unless collection is explicitly disabled through a new save. Counter persistence requires both an observe/insights grant and no explicit persistence opt-out. Status and setup show this effective state. `/scout insights off` always saves `persist_observation: false`, including on legacy profiles. Use `scout configure --mode report --insights off` to save the same opt-out without replacing other dataset grants; `--insights on` explicitly enables persistence when observe/insights consent exists. Menu-generated equivalent commands include this flag.

Grants for different datasets in one call use `scope@dataset`, e.g. prompt masking for the
profile next to local answers for the triage dataset:
`configure --mode on_demand --dataset support --approve observe,train,answer,mask@profile --answer-task triage`.

Disable immediately with `configure --mode off`, or revoke `answer` by supplying the full
desired grant list without it:

```sh
python -m plugin.cli --home /path/to/profile configure --mode on_demand \
  --dataset support --approve observe,report,train,insights
```

## Working model and upgrades

The default methods are non-neural, including rules and locally trained statistical models; the agent chooses from recipe cards rather than receiving a ranking. Cards also describe optional downloadable models or external weights. They are not automatically downloaded or used, and their expected gain is unknown until measured on an independent holdout. Calibration uses train and validation data; the operator controls the frozen test evaluation. Local files and CPU time are still required.

## Insights

Local summaries and HTML views show operational metadata, alerts and available usage. `--preview` shows the exact allowlisted aggregate; `--export` writes it locally and requires `insights` consent. The journal and community export exclude prompts, tool arguments, response bodies, free-text reasons, identifiers, paths, dataset names and PII values. Scout does not upload the export. Local model and state files have a different, more sensitive scope. See [INSIGHTS.md](../INSIGHTS.md).

## Requirements and interface

Hermes >=0.23.0,<0.24.0 and Python >=3.11,<3.15 are required, on Linux or macOS (file locking uses `fcntl`; Windows is not supported). Base dependencies are NumPy, SciPy, scikit-learn, PyYAML and threadpoolctl; the optional CNN recipe needs PyTorch, and the optional basal method needs a basal engine that you install and run locally ([rkinas/basal](https://github.com/rkinas/basal)). The scientific stack is substantial: roughly 200 MB or more depending on platform and installation; measure the target environment before deployment. Model weights are separate.

The manifest declares 9 tools: `scout_handle`, `scout_calibrate`, `scout_inspect`, `scout_recipes`, `scout_choose`, `scout_train`, `scout_predict`, `scout_report`, `scout_calculate`. Make one Scout tool call at a time. The 8 hooks are `post_tool_call`, `pre_api_request`, `pre_auxiliary_call`, `post_api_request`, `post_auxiliary_call`, `transform_llm_output`, `transform_tool_result`, and `transform_terminal_output`.

The middleware kinds are `llm_request` and `llm_execution`, declared as `provides_middleware`. Hermes 0.23 uses that field in `hermes plugins validate` and the catalog, but its plugin loader does not list it yet, so loading logs `unknown manifest field(s) ignored: provides_middleware`. The warning is harmless: the middleware is registered in code and runs (verified live).
