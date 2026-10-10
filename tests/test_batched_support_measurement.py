from __future__ import annotations

from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry
from scripts.build_test_database import build_fixture_database
from scripts.batched_support_measurement import current_census_delivery_measurement, PROBE_ID
from scripts.work_selection_cohort_measurements import _measurement


class BatchedSupportMeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'cards.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/fading-cards.json', ROOT/'tests/fixtures/vanishing-cards.json', ROOT/'tests/fixtures/umbra-armor-cards.json'], path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def fixture(self):
        records={record.oracle_id:record for record in (self.db.lookup(name) for name in ('Skyshroud Ridgeback','Calciderm','Hyena Umbra'))}
        rows=[{'oracle_id':ident,'card_program_trust_basis':'unresolved','abilities':[]} for ident in records]
        base={'card_data_snapshot':{'pinned_fixture':'same'},'cards':rows}
        current={'card_data_snapshot':base['card_data_snapshot'],'cards':[{**row,'card_program_trust_basis':'capability_closed'} for row in rows],
                 'capability_registry_fingerprint':self.registry.fingerprint,'capability_evidence_fingerprint':self.registry.evidence_fingerprint,
                 'limited':False,'commander_legal_only':True}
        return base,current,records

    def run_probe(self,base,current,records):
        path=Path(self.temporary.name)/'frontier.json.gz'
        path.write_bytes(gzip.compress(json.dumps(current).encode(),mtime=0))
        with patch('scripts.batched_support_measurement.FRONTIER',path):
            return _measurement(frontier=base,bundle={'bundle_id':'bundle:batch','measurement_probe_id':PROBE_ID},
                cards_by_oracle_id=records,coverage={'minimum_complete_card_gain':2,'minimum_exact_ability_gain':999,'minimum_material_residual_reduction':999},
                cohort_fingerprint='typed-fixture',database=self.db)

    def test_versioned_delivery_probe_rebinds_real_distinct_whole_cards(self):
        base,current,records=self.fixture()
        current['cards'].append(deepcopy(current['cards'][0]))
        measured=self.run_probe(base,current,records)
        self.assertEqual(3,measured['complete_card_gain'])
        self.assertEqual(3,measured['affected_commander_cards'])
        self.assertEqual('bounded_executable',measured['decision'])
        self.assertFalse(measured['grants_gameplay_trust'])
        base['cards'][0]['card_program_trust_basis']='capability_closed'
        self.assertEqual(2,self.run_probe(base,current,records)['complete_card_gain'])

    def test_stale_snapshot_and_untrusted_candidate_never_become_a_positive_hand_count(self):
        base,current,records=self.fixture()
        stale=deepcopy(current);stale['card_data_snapshot']={'pinned_fixture':'different'}
        with self.assertRaises(ValueError):self.run_probe(base,stale,records)
        stale=deepcopy(current);stale['capability_registry_fingerprint']='unrelated'
        with self.assertRaises(ValueError):self.run_probe(base,stale,records)
        cap=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in cap['capabilities']:
            if row['id']=='permanent.destroy.umbra_armor':row.update(status='blocked',blockers=['independent owner unavailable'])
        blocked=CapabilityRegistry(cap)
        current['capability_registry_fingerprint']=blocked.fingerprint
        current['capability_evidence_fingerprint']=blocked.evidence_fingerprint
        with patch('scripts.batched_support_measurement.load_default_capability_registry',return_value=blocked):
            measured=self.run_probe(base,current,records)
            self.assertEqual(0,measured['complete_card_gain'])
            self.assertEqual('retired_below_harvest_floor',measured['decision'])
