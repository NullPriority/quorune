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
    if (set(value) != {'storage_schema_version', 'document', 'groups'}
            or type(value['storage_schema_version']) is not int
            or value['storage_schema_version'] != 1):
        raise EvidenceStorageError('Unsupported evidence storage envelope')
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
                    or type(reference[1]) is not str or type(reference[2]) is not str):
                raise EvidenceStorageError('Invalid evidence reference')
            rows.append((reference[0], {**scalars, **{key: list(item) for key, item in arrays.items()},
                **{key: deepcopy(item) for key, item in nested.items()},
                'evidence_class': reference[1], 'test_id': reference[2]}))
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
    for position, row in enumerate(declarations):
        if (not isinstance(row, Mapping) or type(row.get('evidence_class')) is not str
                or type(row.get('test_id')) is not str):
            raise EvidenceStorageError('Evidence declaration must include its class and test')
        common = {key: deepcopy(item) for key, item in row.items()
            if key not in {'evidence_class', 'test_id'}}
        key = json.dumps(common, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        if key not in positions:
            positions[key] = len(groups)
            groups.append({'common': common, 'references': []})
        groups[positions[key]]['references'].append([position, row['evidence_class'], row['test_id']])
    return {'storage_schema_version': 1, 'document': document, 'groups': groups}


@lru_cache(maxsize=2)
def _expanded_evidence_text(text):
    """Cache only immutable, content-addressed storage expansion, never trust."""
    return json.dumps(unpack_evidence_document(json.loads(text)),
        ensure_ascii=False, separators=(',', ':'))


def read_evidence_document(path):
    text = Path(path).read_text(encoding='utf-8')
    return json.loads(_expanded_evidence_text(text))


def evidence_document_text(value):
    return json.dumps(pack_evidence_document(value), indent=2, ensure_ascii=False, sort_keys=True)+'\n'
