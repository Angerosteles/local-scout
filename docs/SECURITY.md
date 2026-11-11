# Security model, data handling and disclosures

Part of the [Local Scout README](../README.md).

## One-way masking in practice

| Kept (verified unchanged) | Masked |
|---|---|
| customer and account IDs (`CUST-004211`), order, ticket and invoice numbers (`ORD-2026-11-0042`, `2026-11-10-0142`), dates, e-mail subjects, person names in tool output | e-mail addresses, phones, cards, CVVs, IBANs, SSNs, PESEL numbers, addresses, secrets; person names in prompts |

Work by ID: let the agent answer with the customer or ticket ID, and look the real address or phone up in your own system. There is deliberately no reversible tokenization. If placeholders were restored in what the agent sends, a hidden instruction in an e-mail could make it deliver real data to an attacker. With one-way masking the model cannot hand that instruction the detected values, because it never saw them; files on disk are unchanged; the context guard on the `context-guard` branch watches such transfers. In our gateway test even the attacker's own address reached the model as `[EMAIL]`.

## Gateways

Telegram, Discord, e-mail and API-server sessions run the same Hermes agent turn, so the same masking, flags and Insights apply. Verified live on two platforms of a real Hermes 0.23 gateway: the API-server platform, with a local fake provider that recorded every request (no raw personal data reached it except person names inside tool output, which are kept on purpose), and Discord, with a bot on a private server and the recorded requests to OpenRouter (DeepSeek V4.1 Flash): the message reached the provider as `Hi, I'm [NAME], [EMAIL], card [CARD]`, the six mailbox tool results arrived with no raw values and the phishing e-mail flagged, and asked where that e-mail wanted the card numbers sent, the model answered `send them to [EMAIL]`. Telegram and e-mail adapters were not run.

**Known limit on chat gateways:** Hermes also copies the start of the first message of a Discord thread into the system prompt as the thread title. E-mails, cards and other detected values are masked there too, but person names are masked only in user messages, so a name typed in the first message of a thread can reach the provider through that title.

## Security scope and limits

- Protection covers tool results and terminal output, including `read_file` and `cat`, and Scout's own data handling. A terminal-capable agent can read files directly; the resulting terminal output still passes through the masking hook when enabled. Earlier subscribers or logs may see content before a result transform; terminal spill files, other channels and binary content may not be covered.
- With `mask` consent and an enabled mode, Hermes 0.23 `llm_request` middleware masks detected PII in every user message sent to the model, including earlier turns. The model sees placeholders such as `[EMAIL]` and an alert identifying detected categories. Tool outputs are also scanned as a second layer. System and developer messages and top-level `system` / `instructions` fields are also scanned (they can carry user memory or profile data); detected PII there is replaced by placeholders without an alert line, so the instructions themselves are not rewritten. Tool-call arguments are scanned as well. Detection runs on a Unicode-normalised copy, so zero-width characters, full-width forms and Unicode hyphens do not hide e-mails, cards or phones. Masking is one-way: placeholders are not restored in the model's reply or in tool-call arguments, so the model cannot act on the real values (for example, it cannot address an e-mail to `[EMAIL]`); turn masking off for tasks that need them. Person names in user prompts are masked as `[NAME]` by a small local classifier (`scout/name_model.py`, pure Python). Thresholds were selected on held-out training data and negative slices only, then measured once on data unused for training: Gretel test token recall 95.7% (precision 67.9%; full name masked in 90.8% of documents), synthetic chat with unseen names 91.6%; false positives on texts without names 0.21% (TechQA/BANKING77/CLINC), 1.27% on AdvPIIBench hard negatives, 0.33% on AdvPII negatives, 1.08% of source-code lines. Because of the code false positives, names are not masked in tool results (e.g. files the agent reads) or in the setup-triage training copy. Overlapping detections are united, so a name inside an e-mail masks the whole e-mail. Names: English, capitalised ASCII names only (non-ASCII or lowercase names are not detected). Detection is incomplete. Unsupported or oversized user content and scan errors are withheld by default; `LOCAL_SCOUT_GUARD_MODE=open` explicitly opts out of fail-closed behavior. Without middleware support or mask consent, prompts are not protected and observation in report mode can warn after exposure. Hermes safe-worker mode can skip middleware, and later middleware or transport behavior must be verified in the deployed system.
- With `mask` enabled, scanning failures and unsupported or oversized content are withheld by default. `LOCAL_SCOUT_GUARD_MODE=open` explicitly chooses fail-open behavior. Masking and alerting require consent and an enabled mode.
- Heuristics and classifiers have false negatives and false positives. Quoted attack phrases in documentation can trigger alerts. Injection alerts flag content; they do not remove the instruction or guarantee that a model will ignore it. Absence of an alert is not proof of safety.
- Local training artifacts can contain a feature vocabulary derived from approved text, and search artifacts can contain document IDs and titles. Local Scout creates its directories with mode 0700 and its files with mode 0600; still protect the profile and its artifacts. Insights export is narrower than local state. Deletion is profile-scoped; retention is applied on activity rather than by an idle cleanup daemon.

## Your data

`configure --source` reads UTF-8 JSONL, one object per line. Common fields are `id`, `group`, `kind`, `text`, `label`, and `destination` (`local`, `cloud`, or `unknown`); optional `split` is `train`, `validation`, or `test`. Keep each group in one split. For search, document `id` and `label` match; query `label` names the source document or `unknown`. For fields, `label` is the primary type and `entity_types` can hold all annotated types.

```jsonl
{"id":"ticket-a","group":"thread-a","kind":"triage","text":"Build fails on startup.","label":"build:failure","destination":"local","split":"train"}
{"id":"doc-install","group":"docs","kind":"doc","text":"Install locally.","label":"doc-install","title":"Install","destination":"local"}
{"id":"query-a","group":"query-a","kind":"search","text":"How to install?","label":"doc-install","destination":"local","split":"train"}
{"id":"fields-a","group":"case-a","kind":"fields","text":"Contact [EMAIL].","label":"email","entity_types":["email"],"destination":"local","split":"train"}
```

For explicitly synthetic data containing detectable PII, add `--synthetic-pii`; detected values are masked before model use. Without it, detected PII causes rejection. Detection is incomplete. Input files are not modified. The destination field describes the data; it does not turn on cloud fallback.

## Disclosures

What Scout does that you should know about (catalog rule 13), all only after the matching consent and mode:
- **Rewrites provider requests** (Hermes `llm_request` middleware): replaces detected personal data with placeholders in user, assistant, tool, system and developer messages, top-level `system`/`instructions`, tool-call arguments and native content blocks; appends a one-line alert to masked user/tool messages.
- **Can withhold content**: an unscannable or oversized user message (or a scan error) is replaced by a notice unless `LOCAL_SCOUT_GUARD_MODE=open` is set. System prompts are not withheld when their content cannot be scanned; a system message whose detected data only appears across text blocks is merged into one masked block. Only if the masking step itself fails is the whole request, system prompt included, replaced by a notice (fail-closed).
- **Can answer instead of the model** (Hermes `llm_execution` middleware, `answer` consent): a confident triage answer is returned locally with a visible "Answered locally by Scout" line; no main model call is made for that turn (Hermes' own title call may still run).
- **Changes auxiliary payloads**: masks Hermes title/compression calls in place from the `pre_auxiliary_call` hook (best-effort hardening that relies on Hermes 0.23 passing message dictionaries by reference; turning title generation off is the supported option).
- **Reads local files**: the Hermes profile `config.yaml` (read-only, to warn about title generation), the history file you pass to `setup-triage`/`calibrate`, approved dataset sources; it writes only under its own plugin-data directory.
- **Starts no processes and runs no shell commands.**

Scout itself makes no network calls except the optional basal method's requests to a basal engine on this machine (loopback only), and has no automatic cloud fallback. Installation and separately obtained models may involve external downloads chosen by the operator. Observation and masking require the applicable consent; telemetry stays in local files and community export requires explicit consent. The user decides whether to share an export. Dataset input and local artifacts need appropriate access controls; detector results and estimates have the limitations above.

## Auxiliary calls and prompt privacy (Hermes 0.23)

With `mask@profile` enabled, Scout also subscribes to `pre_auxiliary_call` and masks
text in the message dictionaries in place, including system messages that may contain
conversation data. This relies on Hermes 0.23 passing a shallow list copy whose message
dictionaries are shared with the actual provider arguments. Hook return values are ignored.
The operator must verify the actual provider payload live for each transport and task;
this mechanism is not a stable Hermes security boundary. Hermes can skip hooks or continue
on hook/middleware failure, and safe-worker can bypass middleware. Separate top-level
`system`/`instructions`, non-dictionary messages, metadata and transports that copy or
rebuild payloads can escape the hook. Detection is incomplete; no notice proves delivery.
The main request and the auxiliary channel use the same rules: user, assistant, tool, system and
developer messages, top-level `system`/`instructions`, tool-call arguments (as JSON) and native content
blocks (`text`, `input_text`, `output_text`, `tool_result`, `tool_use` input). System prompts are masked
without an alert line and are not withheld when a message cannot be scanned (detected data split across text
blocks is merged into one masked block); only a failure of the whole masking step replaces the request (fail-closed);
`thinking` blocks, assistant/tool images and unknown block types pass unchanged; unscannable user blocks
(e.g. images) are withheld. Verified live on Hermes 0.23 (09–10.11): a tool loop on OpenRouter chat
completions and on Codex Responses, and a real Hermes system prompt (8,357 characters) left unchanged.

For the safest title configuration, edit the selected Hermes profile's `config.yaml`:

```yaml
auxiliary:
  title_generation:
    enabled: false
```

Restart Hermes and verify the effective setting and outgoing payload. Scout's CLI
`configure` and `status` read this file only and warn when titles are enabled or their
setting cannot be verified. Turning titles off does not disable other auxiliary tasks.
Multiple triage markers, detected injection, and withheld requests are forwarded to the
model rather than answered locally. The marker is not a trusted boundary by itself.
