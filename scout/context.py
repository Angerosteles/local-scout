"""Minimal local context used by the CLI and demo adapters."""


class LocalContext:
    def __init__(self, home):
        self.home = home
        self.tools = {}
        self.hooks = {}
        self.skills = {}

    def register_tool(self, **kwargs):
        self.tools[kwargs['name']] = kwargs

    def register_hook(self, event, callback):
        self.hooks.setdefault(event, []).append(callback)

    def register_skill(self, name, path, **kwargs):
        self.skills[name] = dict(path=path, **kwargs)
