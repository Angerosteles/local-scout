"""Scan-failure policy (guard mode) is a per-profile setting, not a process-wide environment variable."""
import os
import tempfile
import unittest
from unittest.mock import patch

from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout import tool_guard


class GuardModePerProfile(unittest.TestCase):
    def setUp(self):
        self.tmp_open = tempfile.TemporaryDirectory()
        self.tmp_closed = tempfile.TemporaryDirectory()
        grants = {'mask': ['profile'], 'observe': ['profile']}
        self.open = Scout(LocalContext(self.tmp_open.name))
        self.open.configure_local('report', grants=grants, guard_mode='open')
        self.closed = Scout(LocalContext(self.tmp_closed.name))
        self.closed.configure_local('report', grants=grants)

    def tearDown(self):
        self.tmp_open.cleanup()
        self.tmp_closed.cleanup()

    def test_setting_is_stored_per_profile(self):
        self.assertEqual(self.open.profile()['config'].get('guard_mode'), 'open')
        self.assertNotIn('guard_mode', self.closed.profile()['config'])
        with self.assertRaises(ValueError):
            self.closed.configure_local('report', guard_mode='sometimes')

    def test_two_profiles_in_one_process_keep_their_own_policy(self):
        # A scan failure in the same process: the open profile passes the result through,
        # the default profile still withholds it, and the context does not leak between calls.
        with patch('plugin.scout.tool_guard._transform', side_effect=RuntimeError('scan failed')):
            open_result = self.open.transform_tool_result(result='user@example.com', session_id='s', tool_call_id='a')
            closed_result = self.closed.transform_tool_result(result='user@example.com', session_id='s', tool_call_id='b')
        self.assertIsNone(open_result)
        self.assertIn('action=withheld', closed_result)
        self.assertNotIn('user@example.com', closed_result)
        self.assertFalse(tool_guard.GUARD_OPEN.get())

    def test_unreadable_state_is_closed_even_inside_an_open_call(self):
        # A transform for a profile whose state cannot be read must not inherit `open` from an
        # enclosing call made for another profile.
        token = tool_guard.GUARD_OPEN.set(True)
        try:
            with patch.object(self.closed, 'profile', side_effect=OSError('unreadable')):
                tool = self.closed.transform_tool_result(result='user@example.com', session_id='s', tool_call_id='d')
                terminal = self.closed.transform_terminal_output(output='user@example.com', task_id='s', tool_call_id='e')
        finally:
            tool_guard.GUARD_OPEN.reset(token)
        for result in (tool, terminal):
            self.assertIn('action=withheld', result)
            self.assertNotIn('user@example.com', result)

    def test_environment_variable_is_ignored(self):
        with patch.dict(os.environ, {'LOCAL_SCOUT_GUARD_MODE': 'open'}), \
                patch('plugin.scout.tool_guard._transform', side_effect=RuntimeError('scan failed')):
            result = self.closed.transform_tool_result(result='user@example.com', session_id='s', tool_call_id='c')
        self.assertIn('action=withheld', result)


if __name__ == '__main__':
    unittest.main()
