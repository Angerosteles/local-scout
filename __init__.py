"""Hermes Local Scout registration; no state creation or model imports here."""
from .scout.core import Scout
from .schemas import SCHEMAS
from .tools import handler_for


def register(ctx):
    scout = Scout(ctx)
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
    register_skill = getattr(ctx, 'register_skill', None)
    if callable(register_skill):
        from pathlib import Path
        path=Path(__file__).resolve().parent/'skills'/'decide'/'SKILL.md'
        register_skill('decide',path,
                       description='Choose a local method using train/validation facts and a premise audit.',
                       frontmatter={'name':'decide','description':'Facts, requirements and audit for choosing a local method.'})
