from __future__ import annotations

"""Lossless JSON storage for the existing expanded evidence documents."""

from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path


class EvidenceStorageError(ValueError):
    """An evidence storage envelope is malformed or ambiguous."""


def unpack_evidence_document(value):
    if not isinstance(value, Mapping):
        raise EvidenceStorageError('Evidence document must be an object')
    if 'storage_schema_version' not in value:
        return dict(value)
    version = value['storage_schema_version']
    fields = {'storage_schema_version', 'document', 'groups'}
    if version == 2:
        fields.add('test_ids')
    if type(version) is not int or version not in (1, 2) or set(value) != fields:
        raise EvidenceStorageError('Unsupported evidence storage envelope')

    test_ids = None
    referenced_test_ids = set()
    if version == 2:
        test_ids = value['test_ids']
        if (not isinstance(test_ids, list)
                or any(type(test_id) is not str for test_id in test_ids)
                or len(set(test_ids)) != len(test_ids)):
            raise EvidenceStorageError('Invalid evidence test identity table')
    document, groups = value['document'], value['groups']
    if (not isinstance(document, Mapping) or 'declarations' in document
            or 'storage_schema_version' in document or not isinstance(groups, list)):
        raise EvidenceStorageError('Invalid evidence document metadata or groups')
    rows = []
    for group in groups:
        if (not isinstance(group, Mapping) or set(group) != {'common', 'references'}
                or not isinstance(group['common'], Mapping)
                or {'evidence_class', 'test_id'} & set(group['common'])
                or not isinstance(group['references'], list) or not group['references']):
            raise EvidenceStorageError('Invalid evidence metadata group')
        # JSON scalar metadata is immutable. Copy independent flat arrays at
        # C speed; retain a defensive deep copy for any other metadata shape.
        scalars, arrays, nested = {}, {}, {}
        for key, item in group['common'].items():
            if item is None or type(item) in (str, int, float, bool):
                scalars[key] = item
            elif isinstance(item, list) and all(x is None or type(x) in (str, int, float, bool) for x in item):
                arrays[key] = item
            else:
                nested[key] = item
        for reference in group['references']:
            if (not isinstance(reference, list) or len(reference) != 3
                    or type(reference[0]) is not int or reference[0] < 0
                    or type(reference[1]) is not str):
                raise EvidenceStorageError('Invalid evidence reference')
            position, evidence_class, test = reference
            if test_ids is None:
                if type(test) is not str:
                    raise EvidenceStorageError('Invalid evidence test identity')
                test_id = test
            else:
                if type(test) is not int or not 0 <= test < len(test_ids):
                    raise EvidenceStorageError('Invalid evidence test identity reference')
                referenced_test_ids.add(test)
                test_id = test_ids[test]
            rows.append((position, {**scalars, **{key: list(item) for key, item in arrays.items()},
                **{key: deepcopy(item) for key, item in nested.items()},
                'evidence_class': evidence_class, 'test_id': test_id}))
    if test_ids is not None and len(referenced_test_ids) != len(test_ids):
        raise EvidenceStorageError('Evidence test identity table includes unreferenced entries')
    rows.sort(key=lambda row: row[0])
    if [position for position, _row in rows] != list(range(len(rows))):
        raise EvidenceStorageError('Evidence ordinals must be unique and contiguous')
    return {**deepcopy(dict(document)), 'declarations': [row for _position, row in rows]}


def pack_evidence_document(value):
    document = unpack_evidence_document(value)
    declarations = document.pop('declarations', None)
    if not isinstance(declarations, list):
        raise EvidenceStorageError('Evidence declarations must be a list')
    groups, positions = [], {}
    test_positions = {}
    for position, row in enumerate(declarations):
        if (not isinstance(row, Mapping) or type(row.get('evidence_class')) is not str
                or type(row.get('test_id')) is not str):
            raise EvidenceStorageError('Evidence declaration must include its class and test')
        common = {key: item for key, item in row.items()
            if key not in {'evidence_class', 'test_id'}}
        key = json.dumps(common, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        if key not in positions:
            positions[key] = len(groups)
            copied_common = {name: deepcopy(item) for name, item in common.items()}
            groups.append({'common': copied_common, 'references': []})
        test_index = test_positions.setdefault(row['test_id'], len(test_positions))
        groups[positions[key]]['references'].append([position, row['evidence_class'], test_index])
    return {'storage_schema_version': 2, 'document': document, 'groups': groups,
        'test_ids': list(test_positions)}


@lru_cache(maxsize=2)
def _expanded_evidence_text(text):
    """Cache only immutable, content-addressed storage expansion, never trust."""
    return json.dumps(unpack_evidence_document(json.loads(text)),
        ensure_ascii=False, separators=(',', ':'))


def read_evidence_document(path):
    text = Path(path).read_text(encoding='utf-8')
    return json.loads(_expanded_evidence_text(text))


def evidence_document_text(value):
    stored = pack_evidence_document(value)

    def encoded(item, *, pretty=False):
        return json.dumps(item, ensure_ascii=False, sort_keys=True,
            **({'indent': 2} if pretty else {'separators': (',', ':')}))

    # Keep metadata and each reference on one line so expanding the evidence
    # inventory does not spend the repository blob budget on indentation.
    document = encoded(stored['document'], pretty=True).replace('\n', '\n  ')
    groups = []
    for group in stored['groups']:
        references = ',\n'.join('        ' + encoded(row) for row in group['references'])
        groups.append('    {\n      "common": ' + encoded(group['common'])
            + ',\n      "references": [\n' + references + '\n      ]\n    }')
    body = '[\n' + ',\n'.join(groups) + '\n  ]' if groups else '[]'
    test_ids = encoded(stored['test_ids'], pretty=True).replace('\n', '\n  ')
    return ('{\n  "document": ' + document + ',\n  "groups": ' + body
        + ',\n  "storage_schema_version": ' + str(stored['storage_schema_version'])
        + ',\n  "test_ids": ' + test_ids + '\n}\n')
