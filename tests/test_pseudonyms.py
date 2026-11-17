"""One-way numbering: allocation, masking parity and operator surfaces."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from plugin import MASKING_PROMPT, NUMBERED_PROMPT, register
from plugin.cli import main
from plugin.scout import menu, privacy, pseudonyms
from plugin.scout.commands import slash_handler, status
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout.prompt_guard import PromptGuard


def plain(text):
    return re.sub(r'\[(NAME|EMAIL|PHONE|ADDRESS)_[1-9][0-9]{0,4}\]', r'[\1]', text)


class Numbering(unittest.TestCase):
    def test_exact_values_and_category_namespaces(self):
        book = pseudonyms.Pseudonyms()
        for category in pseudonyms.CATEGORIES:
            self.assertEqual(book.token(category, 'same'), '[%s_1]' % category.upper())
            self.assertEqual(book.token(category, 'different'), '[%s_2]' % category.upper())
            self.assertEqual(book.token(category, 'same'), '[%s_1]' % category.upper())
            self.assertEqual(book.token(category, 'SAME'), '[%s_3]' % category.upper())
        for category in ('secret', 'card', 'pin', 'cvv', 'code', 'iban', 'pesel', 'ssn'):
            self.assertIsNone(book.token(category, 'sensitive'))

    def test_reservations_are_sparse_bounded_and_strict(self):
        book = pseudonyms.Pseudonyms(limit=8)
        book.reserve({'[EMAIL_1]': ['[EMAIL_3]', {'later': '[NAME_8]'}]})
        book.reserve('[EMAIL_0] [EMAIL_01] [EMAIL_999999] [CARD_1]')
        self.assertEqual(book.token('email', 'a'), '[EMAIL_2]')
        self.assertEqual(book.token('email', 'b'), '[EMAIL_4]')
        self.assertEqual(book.token('name', 'a'), '[NAME_1]')
        size = book.bytes_used
        for _ in range(20):
            book.reserve('[EMAIL_8] [NAME_8] ' * 100)
        self.assertEqual(book.bytes_used, size)

    def test_category_exhaustion_does_not_evict(self):
        book = pseudonyms.Pseudonyms(limit=2)
        self.assertEqual(book.token('email', 'a'), '[EMAIL_1]')
        book.reserve('[EMAIL_2]')
        self.assertIsNone(book.token('email', 'b'))
        self.assertIsNone(book.token('email', 'c'))
        self.assertEqual(book.token('email', 'a'), '[EMAIL_1]')
        self.assertEqual(book.token('phone', 'a'), '[PHONE_1]')

    def test_reservation_failure_degrades_to_plain_masks(self):
        book = pseudonyms.Pseudonyms()
        cyclic = []
        cyclic.append(cyclic)
        book.reserve(cyclic)
        self.assertIsNone(book.token('email', 'a@example.com'))

    def test_memory_exhaustion_preserves_existing_values(self):
        book = pseudonyms.Pseudonyms()
        book.max_bytes = book.bytes_used + 256
        self.assertEqual(book.token('email', 'first'), '[EMAIL_1]')
        self.assertIsNone(book.token('email', 'next'))
        self.assertIsNone(book.token('name', 'next'))
        self.assertEqual(book.token('email', 'first'), '[EMAIL_1]')
        self.assertLessEqual(book.bytes_used, book.max_bytes)

    def test_long_values_are_not_retained(self):
        book = pseudonyms.Pseudonyms()
        value = 'unusual-long-contact-' * 10000
        book.token('address', value)
        self.assertNotIn(value, repr(vars(book)))
        self.assertEqual(len(next(iter(book.numbers))), 32)
        self.assertLess(book.bytes_used, 100000)

    def test_full_table_stays_within_memory_budget(self):
        tracemalloc.start()
        try:
            book = pseudonyms.Pseudonyms()
            for number in range(20000):
                if book.token('email', 'contact-%d@example.com' % number) is None:
                    break
            else:
                self.fail('memory budget did not stop allocation')
            current, peak = tracemalloc.get_traced_memory()
            self.assertLess(current, pseudonyms.MAX_BYTES)
            self.assertLess(peak, pseudonyms.MAX_BYTES)
            self.assertEqual(book.token('email', 'contact-0@example.com'), '[EMAIL_1]')
        finally:
            tracemalloc.stop()

    def test_concurrent_allocation_has_no_aliases(self):
        book = pseudonyms.Pseudonyms()
        values = ['person-%d' % (n % 30) for n in range(120)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            tokens = list(pool.map(lambda value: book.token('email', value), values))
        mapping = dict(zip(values, tokens))
        self.assertEqual(len(set(mapping.values())), 30)
        self.assertTrue(all(mapping[value] == token for value, token in zip(values, tokens)))


class NumberedMasking(unittest.TestCase):
    def mask(self, text, found, book=None):
        active = pseudonyms.CURRENT.set(book)
        try:
            with patch.object(privacy, 'local_spans', return_value=found):
                return privacy.mask_local_details(text)
        finally:
            pseudonyms.CURRENT.reset(active)

    def test_union_keeps_the_same_coverage_and_categories(self):
        text = 'abcdefghij ABCD end'
        found = [(0, 5, 'email'), (3, 10, 'name'), (11, 15, 'phone')]
        off, cats = self.mask(text, found)
        on, numbered_cats = self.mask(text, found, pseudonyms.Pseudonyms())
        self.assertEqual(plain(on), off)
        self.assertEqual(cats, numbered_cats)
        self.assertEqual(on, '[NAME_1] [PHONE_1] end')

    def test_any_sensitive_member_blocks_the_entire_contact_region(self):
        for category in ('secret', 'card', 'pin', 'cvv', 'code', 'iban', 'pesel', 'ssn'):
            with self.subTest(category=category):
                book = pseudonyms.Pseudonyms()
                found = [(0, 10, 'email'), (4, 6, category)]
                self.assertEqual(self.mask('abcdefghij', found, book), self.mask('abcdefghij', found))
                self.assertEqual(book.numbers, {})

    def test_sensitive_overlap_is_transitive(self):
        book = pseudonyms.Pseudonyms()
        found = [(0, 5, 'email'), (4, 8, 'address'), (7, 10, 'secret')]
        self.assertEqual(self.mask('abcdefghij', found, book)[0], '[EMAIL]')
        self.assertEqual(book.numbers, {})

    def test_allocator_failure_and_exhaustion_keep_full_masks(self):
        for book in (pseudonyms.Pseudonyms(max_bytes=0), pseudonyms.Pseudonyms()):
            with patch.object(book, 'token', side_effect=ValueError) if book.max_bytes else contextlib.nullcontext():
                self.assertEqual(self.mask('abcdef', [(0, 6, 'email')], book)[0], '[EMAIL]')

    def test_unicode_span_mapping_and_field_context_match_off(self):
        for text, kwargs in (('Contact a\u200b@example.com now', {}),
                             ('ａ＠example.com', {}), ('a@example.com', {'field': 'email'}),
                             ('402', {'field': 'cvv'}), ('543918', {'field': 'otp_code'})):
            with self.subTest(text=text):
                off = privacy.mask_local_details(text, **kwargs)
                active = pseudonyms.CURRENT.set(pseudonyms.Pseudonyms())
                try:
                    on = privacy.mask_local_details(text, **kwargs)
                finally:
                    pseudonyms.CURRENT.reset(active)
                self.assertEqual((plain(on[0]), on[1]), off)


class NumberedIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = LocalContext(self.tmp.name)
        self.scout = Scout(self.ctx)
        self.guard = PromptGuard(self.scout)

    def tearDown(self):
        self.tmp.cleanup()

    def enable(self, **kwargs):
        self.scout.configure_local('background', grants={'mask': ['profile']}, pseudonyms='on', **kwargs)

    def request(self, text):
        request = {'messages': [{'role': 'user', 'content': text}]}
        result = self.guard(request=request)
        return result['request']['messages'][0]['content'] if result else text

    def test_default_off_and_invalid_setting(self):
        self.assertIsNone(self.scout.numbered_masks())
        self.assertIn('[EMAIL]', self.request('a@example.com'))
        self.assertNotIn('numbered_masks', self.scout.profile())
        for invalid in ('maybe', True, 1):
            with self.assertRaises(ValueError):
                self.scout.configure_local(pseudonyms=invalid)

    def test_off_on_off_cache_and_toggle_stability(self):
        self.assertIn('[EMAIL]', self.request('a@example.com'))
        self.enable()
        self.assertIn('[EMAIL_1]', self.request('a@example.com'))
        self.assertIn('[EMAIL_2]', self.request('b@example.com'))
        self.scout.configure_local('background', pseudonyms='off')
        self.assertNotIn('[EMAIL_1]', self.request('a@example.com'))
        self.enable()
        self.assertIn('[EMAIL_1]', self.request('a@example.com'))

    def test_same_book_across_prompt_auxiliary_tools_terminal(self):
        self.enable()
        self.assertIn('[EMAIL_1]', self.request('a@example.com'))
        auxiliary = [{'role': 'user', 'content': 'a@example.com b@example.com'}]
        self.guard.auxiliary(request_messages=auxiliary)
        self.assertIn('[EMAIL_1] [EMAIL_2]', auxiliary[0]['content'])
        result = self.scout.transform_tool_result(result={'contact': 'b@example.com'})
        self.assertIn('[EMAIL_2]', str(result))
        result = self.scout.transform_terminal_output(output='a@example.com')
        self.assertIn('[EMAIL_1]', str(result))
        self.assertIsNone(pseudonyms.CURRENT.get())

    def test_restart_reserves_later_history_before_first_allocation(self):
        self.enable()
        request = {'messages': [{'role': 'user', 'content': 'a@example.com'},
                                {'role': 'assistant', 'content': '[EMAIL_1] [EMAIL_3]'}]}
        original = copy.deepcopy(request)
        result = self.guard(request=request)['request']
        self.assertIn('[EMAIL_2]', result['messages'][0]['content'])
        self.assertEqual(request, original)
        self.assertIn('[EMAIL_4]', self.request('b@example.com'))

    def test_auxiliary_and_tool_reserve_all_leaves_before_masking(self):
        self.enable()
        items = [{'role': 'user', 'content': 'a@example.com'},
                 {'role': 'assistant', 'content': '[EMAIL_1]'}]
        self.guard.auxiliary(request_messages=items)
        self.assertIn('[EMAIL_2]', items[0]['content'])
        result = self.scout.transform_tool_result(result={'first': 'b@example.com', '[EMAIL_3]': 'history'})
        self.assertIn('[EMAIL_4]', str(result))

    def test_tools_json_strings_preserve_history_numbers(self):
        self.enable()
        serialized = json.dumps({'first': 'a@example.com', 'later': '[EMAIL_1]'})
        serialized = serialized.replace('[EMAIL_1]', '\\u005bEMAIL_1]')
        result = self.scout.transform_tool_result(result=serialized)
        self.assertIn('[EMAIL_2]', str(result))

    def test_escaped_function_arguments_reserve_before_earlier_messages(self):
        self.enable()
        request = {'messages': [{'role': 'user', 'content': 'a@example.com'},
                                {'role': 'assistant', 'content': None, 'tool_calls': [
                                    {'id': 'c1', 'type': 'function', 'function': {
                                        'name': 'lookup', 'arguments': '{"id": "\\u005bEMAIL_1]"}'}}]}]}
        result = self.guard(request=request)['request']
        self.assertIn('[EMAIL_2]', result['messages'][0]['content'])

    def test_profiles_have_separate_tables(self):
        self.enable()
        first = self.scout.numbered_masks()
        self.request('a@example.com')
        self.ctx.home = Path(self.tmp.name) / 'other-profile'
        self.enable()
        self.assertIsNot(first, self.scout.numbered_masks())
        self.assertIn('[EMAIL_1]', self.request('b@example.com'))

    def test_no_restoration_propagation_or_disk_values(self):
        self.enable(name_masking='basic')
        self.request('My name is John Smith. Email a@example.com')
        book = self.scout.numbered_masks()
        self.assertFalse(any(hasattr(book, name) for name in ('restore', 'reapply', 'grants', 'values')))
        self.assertIsNone(self.scout.notify_output('[EMAIL_1] [NAME_1]'))
        self.assertIsNone(self.scout.transform_tool_result(result='John Smith'))
        self.assertIsNone(self.guard(request={'messages': [{'role': 'assistant', 'content': '[EMAIL_1]'}]}))
        for path in self.scout.root().rglob('*'):
            if path.is_file():
                self.assertNotIn('a@example.com', path.read_text())
                self.assertNotIn('John Smith', path.read_text())

    def test_memory_limit_fallback_and_no_consent(self):
        self.enable()
        self.scout.numbered_masks().max_bytes = 0
        self.assertIn('[EMAIL]', self.request('a@example.com'))
        self.scout.configure_local('background', grants={})
        self.assertEqual(self.request('a@example.com'), 'a@example.com')
        self.assertIsNone(self.scout.transform_tool_result(result='a@example.com'))

    def test_failed_reservation_keeps_auxiliary_and_tool_scanning(self):
        self.enable()
        cyclic = []
        cyclic.append(cyclic)
        self.scout.numbered_masks().reserve(cyclic)
        items = [{'role': 'user', 'content': 'a@example.com'}]
        self.guard.auxiliary(request_messages=items)
        self.assertIn('[EMAIL]', items[0]['content'])
        self.assertNotIn('a@example.com', items[0]['content'])
        result = self.scout.transform_tool_result(result='a@example.com')
        self.assertIn('[EMAIL]', str(result))
        self.assertNotIn('a@example.com', str(result))
        self.assertIsNone(pseudonyms.CURRENT.get())

    def test_prompt_section_and_stream_registration(self):
        self.enable()
        sections, hooks = {}, []
        self.ctx.register_system_prompt_section = lambda name, fn, **kw: sections.update({name: (fn, kw)})
        original_hook = self.ctx.register_hook
        def hook(name, callback, **kwargs):
            hooks.append(kwargs)
            return original_hook(name, callback)
        self.ctx.register_hook = hook
        register(self.ctx)
        render, options = sections['local-scout.masking']
        self.assertEqual(render({}), MASKING_PROMPT + NUMBERED_PROMPT)
        self.assertLessEqual(len(render({})), options['max_chars'])
        self.assertTrue(all(not kwargs for kwargs in hooks))

    def test_slash_menu_cli_and_remote_refusal(self):
        handle = slash_handler(self.scout)
        self.assertIn('Saved:', handle('numbered on', platform='cli'))
        self.assertEqual(self.scout.profile()['config']['pseudonyms'], 'on')
        self.assertIn('numbered masks on', ' '.join(status(self.scout).split()))
        self.assertIn('Numbered masks', menu.render(menu.state_for(self.scout)))
        self.assertIn('local terminal', handle('numbered off', platform='discord'))
        self.assertEqual(self.scout.profile()['config']['pseudonyms'], 'on')
        state = menu.state_for(self.scout)
        command, _, _ = menu.command_and_grants(self.scout, state)
        self.assertIn('--pseudonyms on', command)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            main(['--home', self.tmp.name, 'configure', '--mode', 'background', '--pseudonyms', 'off'])
        self.assertEqual(json.loads(output.getvalue())['pseudonyms'], 'off')
        self.assertIn('No changes', handle('numbered off', platform='cli'))
