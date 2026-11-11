# Local Scout — local privacy for everyday agent work

Local Scout is a local privacy layer for Hermes Agent, built for people working with customer and colleague data: support tickets, inboxes and internal documents. Hermes already redacts secrets such as API keys in file and terminal output. Scout adds masking for **detected personal data** in the content the model reads, plus prompt-injection flags and optional local ticket triage.

The practical idea: the model can understand an overdue invoice without receiving the customer's detected e-mail address or card number. Detectors make mistakes; Scout reduces exposure within supported text paths, rather than making the agent a secure sandbox.

## See what the model receives

In this live Discord test, the user typed an e-mail address and card number. The recorded provider request contained `Hi, I'm [NAME], [EMAIL], card [CARD]`. Hermes then read a mailbox containing a phishing instruction to collect card numbers and send them away. Scout masked the detected values and flagged the instruction. Asked where the cards were supposed to go, the bot quoted: **`send them to [EMAIL]`**.

<p align="center"><img src="docs/discord-demo.png" width="620" alt="Live Discord demo: synthetic personal data entered by the user, a phishing message identified, and its destination quoted as send them to [EMAIL]."></p>

The data is synthetic; the Discord session and the model calls (DeepSeek V4.1 Flash through OpenRouter) were real. This shows one observed payload and response, not general attack resistance. The bot's refusal is model behaviour; injection is **flagged, not blocked**. [Gateway evidence and caveats](docs/SECURITY.md#gateways).

**What changes with Scout** (the same synthetic content in a live Hermes 0.23 process; observed test cases, not a guarantee of full coverage):

| Observed in Hermes 0.23 | Hermes alone | With Scout |
|---|---|---|
| E-mail, phone, card, SSN, IBAN, address in `read_file`, terminal and MCP results | unmasked | masked |
| Stripe, GitHub and AWS keys in an MCP tool result | unmasked | masked |
| A hidden instruction in a tool result | no Scout alert | flagged |

## How it works

With explicit `mask` consent, Scout runs locally in Hermes' request middleware and tool-output hooks. It replaces detected e-mails, phones, cards and CVVs, IBANs, PESEL and SSN numbers, addresses and secrets with placeholders. Person-name masking applies to user prompts. Supported paths include file and terminal results, MCP tool results, conversation text, system instructions and tool-call arguments; auxiliary calls such as session titles receive best-effort masking too. No network calls, no GPU.

**Masking is one-way.** Placeholders are not restored in replies or outgoing arguments. Reversible tokenization would let a hidden instruction turn a placeholder back into real data when the agent sends it. Here the model receives the placeholder instead of the detected value. The tradeoff is deliberate: the model cannot address an e-mail using `[EMAIL]`.

Customer, order and ticket IDs, dates, e-mail subjects and **person names in tool results** stay readable. Work by ID, then look up the address in your own system. Source files remain unchanged, and Scout does not prevent an agent from transferring a file it can read. [Data handling and security scope](docs/SECURITY.md).

## What ships, and what is still experimental

- **Masking — the core.** Local detectors and placeholders on supported Hermes text paths, including MCP output. Enabled through consent, independently of triage.
- **Injection flags — in this release.** Suspicious instructions remain visible as content, with an alert for the model. Detection has misses and false alarms.
- **Local triage — optional, needs calibration.** A classifier trained on your labelled tickets classifies routine tickets locally and returns the category label as the reply, with **0 main-call tokens**; uncertain cases go to the model. Hermes title calls may still run. The shortcut supports chat completions; Codex Responses always forwards to the model. Writing-style changes and typos can invalidate a fitted threshold.
- **basal — optional local engine.** Zero-shot triage through a basal engine you install and run on loopback, with its threshold calibrated on your validation data. It takes seconds per ticket, so in-turn local answers forward rather than wait. The basal cascade and learning-from-basal results are offline experiments outside this release.
- **Insights — in this release.** Local summaries of masking categories and sources, injection alerts, local answers, forwarding reasons and observed usage. Never the text itself; exports are explicit and local.
- **Context guard — branch only.** The `context-guard` branch experiments with asking for human approval of suspicious outgoing calls using session context. **It is not part of this release.** It has known misses and false alarms. [Branch results and limitations](docs/CONTEXT_GUARD.md).

## Quick start

Requires Hermes `>=0.23.0,<0.24.0` and Python `>=3.11,<3.15`. The operator CLI runs as the package `plugin` with the Python environment Hermes creates for plugins:

```sh
hermes plugins install Angerosteles/local-scout --enable
mkdir -p ~/.local/share/local-scout && ln -sfn ~/.hermes/plugins/local-scout ~/.local/share/local-scout/plugin
cd ~/.local/share/local-scout
PY="$(ls -td ~/.hermes/installs/*/environments/*/venv | head -1)/bin/python"

# core: masking for this profile
"$PY" -m plugin.cli --home ~/.hermes configure --mode report \
  --dataset profile --approve observe,report,insights,mask

# optional: local triage from your labelled tickets (also enables local answers)
"$PY" -m plugin.cli --home ~/.hermes setup-triage \
  --history my_tickets.jsonl --marker "Customer message:" --mask-prompts

# what happened so far
"$PY" -m plugin.cli --home ~/.hermes insights
```

Commands explain permissions in plain English before changing anything. Start a new Hermes session after installation. `configure --mode off` disables Scout; retained data remains. `--approve` replaces the whole grant map, so list every scope you want. For triage, use labelled examples without personal information and review the validation coverage/error curve. [Setup, profiles and local-answer configuration](docs/CONFIGURATION.md).

## Evidence you can inspect

- **Masking:** on the Gretel synthetic PII test, span recall was 100% for e-mails, 96% for cards, 94% for SSNs and 88% for phones. Complete address masking was 75.2%; prompt-name token recall/precision was 95.7%/67.9%. These are dataset measurements. [Detector evaluation](docs/EVIDENCE.md#content-safety).
- **Speed and integration:** masking a 331-character e-mail took a median 0.8 ms on a laptop CPU (warm). 234 unit tests and 33/33 contract cases per seed with live Hermes lifecycle checks, bound to the tested plugin file tree; `hermes plugins validate --install-deps` passes. These are distinct checks, not end-to-end latency guarantees. [Reliability](docs/EVIDENCE.md#reliability).
- **Injection:** recall was 97% on Gandalf and 57% on deepset, with false positives up to 2% on the reported negative sets. [Scope and datasets](docs/EVIDENCE.md#content-safety).
- **Triage savings:** a paired 20-message benchmark measured 18.8–19.4% fewer **main-call tokens** on supported chat providers; title calls reduced the overall saving, and Codex Responses showed no saving. Small runs do not establish statistical significance or predict your workload. [Measured savings](docs/EVIDENCE.md#measured-savings).

## Limits and disclosures

- English only, with US-first phone and address formats plus IBAN and PESEL. Name detection covers capitalised ASCII forms; dates, including dates of birth, remain readable. Images and binary content are not scanned.
- Consent and compatible Hermes middleware are required. Scan failures withhold user content by default (system prompts are never withheld); `LOCAL_SCOUT_GUARD_MODE=open` is the explicit fail-open option. If a Hermes middleware call itself fails, Hermes continues with the original request, and safe-worker mode can bypass plugins. Auxiliary masking is best effort: verify outgoing payloads for your deployment.
- On Discord, Hermes names a new thread after its first message and copies that title into the system prompt. Detected values are masked there too, but person names are not. [Operational caveats](docs/SECURITY.md#auxiliary-calls-and-prompt-privacy-hermes-023).
- Scout makes no network calls except optional basal requests to loopback (no proxies, no redirects). It reads content Hermes passes to plugin hooks and middleware, the selected profile's `config.yaml` (title settings), and history files you name. State stays in its plugin-data directory. It runs no shell commands, starts no background processes and stores no provider credentials. Local training artifacts can retain vocabulary; protect them separately from the narrower Insights export. [Full disclosures](docs/SECURITY.md#disclosures).

Local Scout is [MIT licensed](LICENSE). Thanks to Remigiusz Kinas for basal; dataset, dependency and optional-model attributions are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
