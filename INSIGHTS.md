# Scout Insights

Scout Insights provides local summaries of Scout and Hermes tool operation counts, success and error codes, durations, response sizes, PII alert categories and sources, choices, training, accepted and refused handling, calibration and observed token usage when available. Prompt and auxiliary masks are counted by source; prompt injection alerts have their own counter. Observation is opt-in: the local journal is recorded only while Scout is enabled with the relevant `observe` or `insights` consent. Community export requires a separate `insights` grant. `off` stops collection and retains existing data.

The local view can include ordinary Hermes tool names; the public export excludes those names. Retained state provides some lifetime totals while journal events have time windows and rotation. Calibration replay is simulated and is not counted as live handling. Local middleware answers and `scout_handle` telemetry are shown as distinct sources and combined in the handled-locally total. Forwarding reasons use a fixed allowlist. Missing usage is shown as not observed with the mode or consent reason; it is never treated as zero. `on_demand` does not collect usage-hook counters. CLI setup uses no agent tokens. A paired benchmark is still needed for measured savings.

Use the explicit profile with the operator CLI:

```sh
python -m plugin.cli --home /path/to/profile insights
python -m plugin.cli --home /path/to/profile insights --html /local/path/insights.html
python -m plugin.cli --home /path/to/profile insights --preview
python -m plugin.cli --home /path/to/profile insights --export /local/path/summary.json
```

The HTML is a local standalone view. Preview prints the exact aggregate that export writes to a local file. The user controls any subsequent sharing; Scout does not upload it. To grant export, configure an appropriate dataset or profile with a comma-separated grant list such as `--approve observe,report,insights`. Supplying `--approve` replaces the full grant map, including grants for other datasets; inspect `status` before continuing. Omitting `--approve` preserves existing grants.

The journal and community export exclude tool arguments, prompts, response bodies, free-text reasons, identifiers, paths, dataset names and PII values. Export uses an allowlist, rounds aggregate counts and omits rare PII categories and forwarding reasons with fewer than five events; it does not emit a `<5` marker because that would disclose that a rare event occurred. Aggregate writes merge under a profile file lock and apply retention on every write. These exclusions do not describe every local Scout artifact: model files can contain a feature vocabulary and search artifacts can contain document IDs and titles. Protect the profile; normal profile-scoped deletion removes Scout data. Retention is also applied on activity, not by an idle daemon.

The `estimated_input_tokens_avoided` value is an estimate from serialized request character count divided by four. One live comparison showed about 4.7× above measured input; this is not an upper-bound guarantee. TUI and HTML place this estimate beside measured usage when available.
