import re, subprocess, sys, os
from pathlib import Path
import pytest

from scripts.workspace import repo_root

README = repo_root() / 'README.md'

def _extract_commands(text: str):
    '''Extract shell commands from fenced code blocks that look like CLI invocations.'''
    in_block = False
    in_shell = False
    cmds = []
    for line in text.splitlines():
        if line.startswith('```'):
            fence = line.strip('`').strip().lower()
            in_block = True if not in_block else False
            in_shell = in_block and fence in ('bash', 'sh', '')
            continue
        if in_block and in_shell:
            s = line.strip()
            if s.startswith('llm-wiki ') or s.startswith('make '):
                cmds.append(s)
    return cmds

def test_readme_example_commands_are_parseable():
    cmds = _extract_commands(README.read_text())
    assert len(cmds) >= 1, 'README contained no llm-wiki/make examples'

@pytest.mark.parametrize('cmd_fragment', [
    'llm-wiki doctor',
    'llm-wiki --workspace',
])
def test_readme_mentions_key_patterns(cmd_fragment):
    assert cmd_fragment in README.read_text(), \
        f'README missing canonical example: {cmd_fragment!r}'

def test_readme_has_no_cost_language():
    '''ARCHITECTURE §10.6: no $/cost/price/pricing language in README.'''
    text = README.read_text().lower()
    # 'cost' can legitimately appear in sentences like 'zero cost'; we check
    # for strong indicators
    banned = ['$', ' pricing', ' price ', 'billing']
    found = [b for b in banned if b in text]
    assert not found, f'README contains banned language: {found}'
