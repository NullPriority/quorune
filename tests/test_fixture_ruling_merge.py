from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from quorune.carddb import CardDatabase
from quorune.compiler.program_generation import rulings_source_hash
from scripts.build_test_database import build_fixture_database
from test_compact_ci_dependencies import card


class FixtureRulingMergeTests(unittest.TestCase):
    def test_overlapping_fixtures_preserve_maximum_ruling_multiplicity_and_provenance(self):
        row=card('Murder','fixture:ruling')
        ruling={'oracle_id':row['oracle_id'],'published_at':'2026-01-01','source':'wotc','comment':'Same publication.'}
        dated={**ruling,'published_at':'2026-01-02'}
        other={**ruling,'source':'other'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);first=root/'first.json';second=root/'second.json'
            first.write_text(json.dumps({'schema_version':1,'cards':[row],'rulings':[ruling,ruling,dated,other]}),encoding='utf-8')
            second.write_text(json.dumps({'schema_version':1,'cards':[row],'rulings':[ruling,dated]}),encoding='utf-8')
            expected=root/'expected.sqlite3';combined=root/'combined.sqlite3';reversed_db=root/'reversed.sqlite3'
            build_fixture_database(first,expected)
            build_fixture_database([first,second],combined)
            build_fixture_database([second,first],reversed_db)
            hashes=[]
            for path in (expected,combined,reversed_db):
                with CardDatabase(path) as db:
                    record=db.lookup('Murder')
                    rows=db.rulings(record)
                    self.assertEqual(4,len(rows))
                    self.assertEqual(2,Counter((r.published_at,r.source,r.comment) for r in rows)[('2026-01-01','wotc','Same publication.')])
                    hashes.append(rulings_source_hash(db,record))
            self.assertEqual(1,len(set(hashes)))
