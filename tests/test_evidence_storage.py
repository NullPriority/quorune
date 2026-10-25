from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from quorune.rules.evidence_storage import (
    EvidenceStorageError, evidence_document_text, pack_evidence_document,
    read_evidence_document, unpack_evidence_document,
)
from quorune.rules import evidence_storage


class EvidenceStorageTests(unittest.TestCase):
    def document(self):
        common = dict(capability_id='example.exact', official_rule_ids=['400.7'],
            supported_profiles=['commander_review'], applicability_note='Exact source identity — independent evidence.')
        a = dict(common, evidence_class='replay', test_id='tests.test_example.Case.test_replay')
        b = dict(common, evidence_class='positive', test_id='tests.test_example.Case.test_positive')
        c = dict(common, evidence_class='negative', test_id='tests.test_example.Case.test_negative',
            applicability_note='Distinct scope', official_rule_ids=['400.7', '113.7a'])
        return dict(schema_version=1, declarations=[a, c, b], fingerprint='unchanged-semantic-fingerprint')

    def test_grouping_preserves_every_field_and_original_noncanonical_row_order(self):
        value = self.document()
        before = deepcopy(value)
        stored = pack_evidence_document(value)
        self.assertEqual(2, len(stored['groups']))
        self.assertEqual([0, 2], [r[0] for r in stored['groups'][0]['references']])
        self.assertEqual(value, unpack_evidence_document(stored))
        self.assertEqual(before, value)
        self.assertEqual(stored, pack_evidence_document(stored))
        self.assertEqual(evidence_document_text(value), evidence_document_text(stored))

    def test_decoded_metadata_arrays_are_independent_across_rows_and_input(self):
        stored = pack_evidence_document(self.document())
        before = deepcopy(stored)
        value = unpack_evidence_document(stored)
        value['declarations'][0]['official_rule_ids'].append('999.1')
        value['declarations'][0]['supported_profiles'].append('other')
        self.assertEqual(['400.7'], value['declarations'][2]['official_rule_ids'])
        self.assertEqual(['commander_review'], value['declarations'][2]['supported_profiles'])
        self.assertEqual(before, stored)

    def test_malformed_envelopes_ordinals_and_references_fail_closed(self):
        stored = pack_evidence_document(self.document())
        mutations = []
        for key, value in (('future', True), ('storage_schema_version', True),
                ('storage_schema_version', 2), ('document', []), ('groups', {})):
            mutations.append({**deepcopy(stored), key: value})
        changed = deepcopy(stored)
        changed['document']['declarations'] = []
        mutations.append(changed)
        for field, value in (('future', True), ('common', []), ('references', []),
                ('references', [[0, 'positive']]), ('references', [[True, 'positive', 'test']]),
                ('references', [[-1, 'positive', 'test']]), ('references', [[99, 'positive', 'test']]),
                ('references', [[0, 'positive', False]]),
                ('references', [[0, 'positive', 'a'], [0, 'replay', 'b']])):
            changed = deepcopy(stored)
            changed['groups'][0][field] = value
            mutations.append(changed)
        changed = deepcopy(stored)
        changed['groups'][0]['common']['evidence_class'] = 'override'
        mutations.append(changed)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(EvidenceStorageError):
                unpack_evidence_document(value)

    def test_flat_and_grouped_utf8_files_share_the_existing_expanded_interface(self):
        value = self.document()
        with tempfile.TemporaryDirectory() as directory:
            flat, grouped = (Path(directory)/name for name in ('flat.json', 'grouped.json'))
            flat.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            grouped.write_text(evidence_document_text(value), encoding='utf-8')
            self.assertEqual(value, read_evidence_document(flat))
            self.assertEqual(value, read_evidence_document(grouped))
        self.assertEqual({'schema_version': 1, 'declarations': []},
            unpack_evidence_document(pack_evidence_document({'schema_version': 1, 'declarations': []})))

    def test_cached_expansion_returns_independent_values_and_preserves_validation_inputs(self):
        value = self.document()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'evidence.json'
            path.write_text(evidence_document_text(value), encoding='utf-8')
            evidence_storage._expanded_evidence_text.cache_clear()
            first = read_evidence_document(path)
            first['declarations'][0]['official_rule_ids'].clear()
            second = read_evidence_document(path)
            self.assertEqual(value, second)
            second['declarations'].clear()
            self.assertEqual(value, read_evidence_document(path))
            self.assertEqual(1, evidence_storage._expanded_evidence_text.cache_info().misses)
            self.assertEqual(2, evidence_storage._expanded_evidence_text.cache_info().hits)
            self.assertEqual(2, evidence_storage._expanded_evidence_text.cache_info().maxsize)

    def test_same_size_and_timestamp_edits_and_malformed_data_cannot_reuse_expansion(self):
        value = self.document()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'evidence.json'
            text = evidence_document_text(value)
            path.write_text(text, encoding='utf-8')
            before = path.stat()
            self.assertEqual(value, read_evidence_document(path))
            changed = text.replace('Distinct scope', 'Changed  scope')
            self.assertEqual(len(text), len(changed))
            path.write_text(changed, encoding='utf-8')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(before.st_size, path.stat().st_size)
            self.assertEqual(before.st_mtime_ns, path.stat().st_mtime_ns)
            self.assertEqual('Changed  scope', read_evidence_document(path)['declarations'][1]['applicability_note'])
            path.write_text(text.replace('"storage_schema_version": 1', '"storage_schema_version": 9'), encoding='utf-8')
            with self.assertRaises(EvidenceStorageError):
                read_evidence_document(path)


if __name__ == '__main__':
    unittest.main()
