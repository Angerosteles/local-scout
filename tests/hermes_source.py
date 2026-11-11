"""Locate a Hermes 0.23 source tree for the contract tests that load real Hermes files.

Set HERMES_SOURCE to a Hermes checkout or install (the directory that contains hermes_cli/). Without one,
those tests are skipped; every other test runs without Hermes."""
import os
from pathlib import Path

CANDIDATES = (os.environ.get('HERMES_SOURCE'), '/opt/hermes', str(Path.home() / '.hermes' / 'hermes-agent'))
SKIP_REASON = 'needs a Hermes 0.23 source tree: set HERMES_SOURCE'


def hermes_file(relative):
    """Path of a file inside the first Hermes source tree found, or None."""
    for root in CANDIDATES:
        if root and (Path(root) / relative).is_file():
            return Path(root) / relative
    return None
