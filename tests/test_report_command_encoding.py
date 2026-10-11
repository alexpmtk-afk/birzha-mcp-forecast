"""Russian report commands have a stable wire encoding on Windows."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize('command', [
    'g2_candle_profile_research',
    'g2_combined_market_report',
    'g2_linked_level_report',
    'g2_six_market_report',
    'g2_timeframe_movement',
])
@pytest.mark.parametrize('legacy_encoding', ['cp1251', 'ascii'])
def test_russian_help_is_utf8_despite_inherited_encoding(command, legacy_encoding):
    env = dict(os.environ, PYTHONIOENCODING=legacy_encoding)
    result = subprocess.run(
        [sys.executable, f'scripts/{command}.py', '--help'],
        capture_output=True, env=env, check=True,
    )
    text = result.stdout.decode('utf-8')
    assert any('А' <= char <= 'я' for char in text)
    assert 'usage:' in text and result.stderr == b''
