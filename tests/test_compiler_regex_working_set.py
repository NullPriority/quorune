"""Immutable compiler patterns preserve the ordinary regex API and live inputs."""
from contextlib import redirect_stdout
import io
import re
import unittest
from unittest.mock import patch

from quorune.compiler import regex_working_set as cached


class CompilerRegexWorkingSetTests(unittest.TestCase):
    def setUp(self):
        cached.purge()

    def tearDown(self):
        cached.purge()

    def result(self, match):
        return None if match is None else (match.span(), match.groups(), match.groupdict(), match.lastindex, match.lastgroup)

    def test_string_pattern_matrix_matches_stdlib_results_and_types(self):
        patterns = [r'(?P<head>[A-Z]+)(?: (?P<tail>[0-9]+))?', r'\A.+\Z', r'a.*b', r'(?i)alpha',
                    r'(a)?(b*)', r'[\w\s]+', r'(?P<literal>\{[WUBRG]\})', r'']
        strings = ['', 'ALPHA', 'ALPHA 12', 'alpha', 'a\nb', 'abb', '{U}', 'Æther']
        for pattern in patterns:
            for text in strings:
                for flags in (0,re.I,re.S,re.M,re.ASCII,re.I|re.M):
                    with self.subTest(pattern=pattern,text=text,flags=flags):
                        expected=re.fullmatch(pattern,text,flags)
                        actual=cached.fullmatch(pattern,text,flags)
                        self.assertEqual(self.result(expected),self.result(actual))
                        if actual is not None:self.assertIsInstance(actual,re.Match)
                        compiled=cached.compile(pattern,flags)
                        self.assertIsInstance(compiled,re.Pattern)
                        self.assertEqual((pattern,re.compile(pattern,flags).flags),(compiled.pattern,compiled.flags))

    def test_current_text_is_always_matched_and_match_verdicts_are_not_cached(self):
        self.assertIsNotNone(cached.fullmatch(r'current [0-9]+','current 1'))
        self.assertIsNone(cached.fullmatch(r'current [0-9]+','different 1'))
        self.assertIsNotNone(cached.fullmatch(r'current [0-9]+','current 2'))
        self.assertEqual(1,cached._compiled.cache_info().currsize)

    def test_bytes_and_precompiled_patterns_keep_stdlib_contract(self):
        for pattern,text,flags in [(br'([A-Z]+)',b'ABC',0),(br'([A-Z]+)',b'abc',re.I),
                (re.compile(r'(?P<n>[0-9]+)'),'42',0)]:
            self.assertEqual(self.result(re.fullmatch(pattern,text,flags)),
                             self.result(cached.fullmatch(pattern,text,flags)))
        compiled=re.compile('alpha')
        self.assertIs(compiled,cached.compile(compiled))
        with self.assertRaises(ValueError):cached.compile(compiled,re.I)

    def test_flags_and_pattern_types_have_independent_keys(self):
        self.assertIsNone(cached.fullmatch('alpha','ALPHA'))
        self.assertIsNotNone(cached.fullmatch('alpha','ALPHA',re.I))
        self.assertIsNotNone(cached.fullmatch(b'alpha',b'alpha'))
        self.assertEqual(3,cached._compiled.cache_info().currsize)

    def test_invalid_patterns_and_arguments_preserve_exception_types(self):
        cases=[('(',0),('[',0),(object(),0),([],0),('x','bad'),('x',None)]
        for pattern,flags in cases:
            with self.subTest(pattern=pattern,flags=flags):
                try:re.compile(pattern,flags)
                except Exception as expected:
                    with self.assertRaises(type(expected)):cached.compile(pattern,flags)
                else:self.fail('Boundary must be invalid for stdlib')

    def test_failed_compilations_are_not_retained(self):
        for _ in range(2):
            with self.assertRaises(re.error):cached.compile('(')
        self.assertEqual(0,cached._compiled.cache_info().currsize)
        self.assertEqual(2,cached._compiled.cache_info().misses)

    def test_changed_public_factory_bypasses_cached_stock_patterns_and_recovers(self):
        original=re.compile
        cached.compile('stable')
        with patch.object(re,'compile',side_effect=RuntimeError('factory withdrawn')) as factory:
            for _ in range(2):
                with self.assertRaises(RuntimeError) as failure:cached.compile('stable')
                self.assertEqual('factory withdrawn',str(failure.exception))
            self.assertEqual(2,factory.call_count)
        self.assertEqual(original('stable').pattern,cached.compile('stable').pattern)

    def test_in_place_factory_code_change_cannot_reuse_cached_pattern(self):
        factory=re.compile
        original_code=factory.__code__
        def withdrawn(pattern,flags=0):raise RuntimeError('changed factory code')
        cached.compile('stable')
        try:
            factory.__code__=withdrawn.__code__
            with self.assertRaises(RuntimeError) as failure:cached.compile('stable')
            self.assertEqual('changed factory code',str(failure.exception))
        finally:factory.__code__=original_code
        self.assertEqual('stable',cached.compile('stable').pattern)

    def test_changed_stdlib_entrypoint_remains_live(self):
        cached.compile('stable')
        with patch.object(re,'_compile',side_effect=RuntimeError('entrypoint withdrawn')):
            with self.assertRaises(RuntimeError) as failure:cached.compile('stable')
            self.assertEqual('entrypoint withdrawn',str(failure.exception))
        self.assertEqual('stable',cached.compile('stable').pattern)

    def test_debug_requests_keep_diagnostic_output_and_do_not_enter_working_set(self):
        with redirect_stdout(io.StringIO()) as expected:
            re.compile('alpha',re.DEBUG)
        with redirect_stdout(io.StringIO()) as output:
            cached.compile('alpha',re.DEBUG)
            cached.compile('alpha',re.DEBUG)
        self.assertEqual(expected.getvalue()*2,output.getvalue())
        self.assertEqual(0,cached._compiled.cache_info().currsize)

    def test_template_requests_use_the_ordinary_warning_path(self):
        import warnings
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter('always')
            cached.compile('literal',re.TEMPLATE)
        self.assertTrue(any(w.category is DeprecationWarning for w in seen))
        self.assertEqual(0,cached._compiled.cache_info().currsize)

    def test_capacity_is_bounded_and_purge_releases_both_caches(self):
        for i in range(4120):cached.compile('literal'+str(i))
        self.assertEqual(4096,cached._compiled.cache_info().currsize)
        cached.purge()
        self.assertEqual(0,cached._compiled.cache_info().currsize)
        self.assertFalse(re._cache)
        self.assertFalse(re._cache2)

    def test_ordinary_apis_flags_and_annotations_keep_stdlib_owners(self):
        for name in ('I','IGNORECASE','M','Match','Pattern','escape','findall','finditer','match','search','split','sub','subn'):
            self.assertIs(getattr(re,name),getattr(cached,name),name)
        self.assertEqual(re.sub('alpha','beta','alpha'),cached.sub('alpha','beta','alpha'))
        with self.assertRaises(AttributeError):getattr(cached,'__path__')
