"""Hermes Local Scout registration; no state creation or model imports here."""
from .scout.core import Scout
from .schemas import SCHEMAS
from .tools import handler_for


MASKING_PROMPT = (
    "Local Scout may replace values with intentional masks: "
    "[NAME], [EMAIL], [PHONE], [ADDRESS], [CARD], [IBAN], [PESEL], [SSN], "
    "[SECRET], [CODE], [PIN], [CVV]. They are not errors or injections. "
    "You do not know the originals; never guess them. Preserve masks exactly. "
    "If a task needs a real value, ask the user to fill it in locally, not in chat."
)


def register(ctx):
    scout = Scout(ctx)
    from .scout.commands import register_commands
    register_commands(ctx, scout)
    from .scout.prompt_guard import PromptGuard
    guard = PromptGuard(scout)
    ctx.register_hook('pre_auxiliary_call', guard.auxiliary)
    register_middleware = getattr(ctx, 'register_middleware', None)
    if callable(register_middleware):
        # llm_request (prompt PII mask) first, then llm_execution (local answers) sees the masked request
        # and the pre-mask original_request; Scout never forwards original_request.
        register_middleware('llm_request', guard)
        from .scout.local_answers import LocalAnswers
        register_middleware('llm_execution', LocalAnswers(scout))
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset="local_scout", schema=schema,
                          handler=handler_for(scout, name))
    for event in ("pre_api_request", "pre_auxiliary_call"):
        ctx.register_hook(event, scout.observe)
    for event in ("post_api_request", "post_auxiliary_call"):
        ctx.register_hook(event, scout.usage_hook)
    ctx.register_hook("post_tool_call", scout.post_tool_call)
    ctx.register_hook("transform_tool_result", scout.transform_tool_result)
    ctx.register_hook("transform_terminal_output", scout.transform_terminal_output)
    ctx.register_hook("transform_llm_output", scout.notify_output)
    register_section = getattr(ctx, 'register_system_prompt_section', None)
    if callable(register_section):
        def masking_section(_session_info):
            config = scout.profile()['config']
            return (MASKING_PROMPT if config.get('mode', 'off') != 'off'
                    and 'profile' in config.get('grants', {}).get('mask', []) else '')
        register_section('local-scout.masking', masking_section, position='after_memory', max_chars=600)
    register_skill = getattr(ctx, 'register_skill', None)
    if callable(register_skill):
        from pathlib import Path
        path=Path(__file__).resolve().parent/'skills'/'decide'/'SKILL.md'
        register_skill('decide',path,
                       description='Choose a local method using train/validation facts and a premise audit.',
                       frontmatter={'name':'decide','description':'Facts, requirements and audit for choosing a local method.'})
        path=Path(__file__).resolve().parent/'skills'/'masking'/'SKILL.md'
        register_skill('masking',path,
                       description='Work safely with Local Scout masks.',
                       frontmatter={'name':'masking','description':'Use Local Scout masks without reconstructing values.'})
