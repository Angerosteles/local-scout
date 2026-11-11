# Configuration and operation

Part of the [Local Scout README](../README.md).

## Setup-triage in detail

Prepare a UTF-8 JSONL file with one object per line, for example `{"text":"Please help with my invoice","label":"billing"}`. Use at least 20 examples and two classes, with multiple training examples per class. Do not include personal information. You may specify `"split":"train"` or `"split":"validation"` on every line; `test` is rejected. If omitted, a deterministic text-hash split assigns about 80% to training and 20% to validation. Duplicate texts stay together. Make sure the resulting validation set has at least five examples.

```sh
python -m plugin.cli --home /path/to/profile setup-triage --history /path/to/history.jsonl --yes
```

Before confirmation, the command explains the local permissions. It saves a local copy of the examples and a model in the profile. Omit `--yes` for interactive confirmation. Add `--mask-prompts` to enable profile prompt masking (Insights then also counts those masks by category and source, never text), `--marker TEXT` for an answer marker, or `--json` for machine-readable output. Turn Scout off with `python -m plugin.cli --home /path/to/profile configure --mode off`.

The output shows a validation coverage/error curve, including the number of accepted examples. `--max-error 0.10` selects the lowest displayed threshold with at least five accepted validation examples and error at most 10%. Without it, the existing validation gate selects the threshold. A threshold fitted on validation data is **not a guarantee** for future user data. `status` also shows the retained curve. Check one request with `python -m plugin.cli --home /path/to/profile handle --task triage --text "example message"`, and view usage with `python -m plugin.cli --home /path/to/profile insights`.


**basal instead of the built-in classifier.** Start a basal engine on this machine, for example `basal-serve --mode gguf --host 127.0.0.1 --port 8765 --model <model dir> --gguf <file>.gguf`. Register it with `configure --mode report --basal-url http://127.0.0.1:8765`, optionally adding `--basal-descriptions labels.json` (one short description per label; otherwise label names are used). Then run `setup-triage --recipe basal_zero_shot`. Nothing is trained: masked validation tickets go to the local engine once to choose the threshold. Expect about 2 s per ticket on a laptop CPU with pico. `handle` uses it, and in-turn local answers forward instead of waiting. Only loopback addresses are accepted.

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

Modes are `off` (no observation; retained data remains), `background` (observe without notices), `report` (also show notices), and `on_demand` (no background collection). Grants are explicit and scoped to the selected dataset; adding `mask` for `profile` is a separate action. The CLI replaces the full grant map when `--approve` is supplied, so the following example replaces the earlier dataset grants. Review `status` before continuing. For example:

```sh
python -m plugin.cli --home /path/to/profile configure --mode report \
  --dataset profile --approve mask,observe,report,insights
python -m plugin.cli --home /path/to/profile insights
python -m plugin.cli --home /path/to/profile insights --preview
python -m plugin.cli --home /path/to/profile insights --export /local/path/summary.json
```

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
local replies are disabled. Optionally add `--answer-marker 'TASK:'`: only the text after
the marker must occur **exactly once** in the final user message and only the text after it is
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

Insights records `kind=local_answer` only with `insights` or `observe` consent and while mode
is enabled: answered/forwarded counters, a small allowlist of forwarding reasons, and
`estimated_input_tokens_avoided`. This is an estimate; one live comparison showed about 4.7×
above measured input, and it is not an upper-bound guarantee. TUI and HTML show measured input
usage beside it when available. No prompt, class label, session ID or exception prose is
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

Local summaries and HTML views show operational metadata, alerts and available usage. `--preview` shows the exact allowlisted aggregate; `--export` writes it locally and requires `insights` consent. The journal and community export exclude prompts, tool arguments, response bodies, free-text reasons, identifiers, paths, dataset names and PII values. Scout does not upload the export. Local model and state files have a different, more sensitive scope. See [INSIGHTS.md](INSIGHTS.md).

## Requirements and interface

Hermes >=0.23.0,<0.24.0 and Python >=3.11,<3.15 are required. Base dependencies are NumPy, SciPy, scikit-learn, PyYAML and threadpoolctl; the optional CNN recipe needs PyTorch, and the optional basal method needs a basal engine that you install and run locally ([rkinas/basal](https://github.com/rkinas/basal)). The scientific stack is substantial: roughly 200 MB or more depending on platform and installation; measure the target environment before deployment. Model weights are separate.

The manifest declares 9 tools: `scout_handle`, `scout_calibrate`, `scout_inspect`, `scout_recipes`, `scout_choose`, `scout_train`, `scout_predict`, `scout_report`, `scout_calculate`. Make one Scout tool call at a time. The 8 hooks are `post_tool_call`, `pre_api_request`, `pre_auxiliary_call`, `post_api_request`, `post_auxiliary_call`, `transform_llm_output`, `transform_tool_result`, and `transform_terminal_output`.

The middleware kinds are `llm_request` and `llm_execution`, declared as `provides_middleware`. Hermes 0.23 uses that field in `hermes plugins validate` and the catalog, but its plugin loader does not list it yet, so loading logs `unknown manifest field(s) ignored: provides_middleware`. The warning is harmless: the middleware is registered in code and runs (verified live).
