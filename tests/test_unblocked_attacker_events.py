"""CR508.3f/509.3g: unblocked triggers are a declare-blockers occurrence."""
import unittest
import json
from copy import deepcopy
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock,patch
from common import ROOT
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class UnblockedAttackerCompilerTests(unittest.TestCase):
    def test_verified_unblocked_event_withdrawal_preserves_ordinary_attack(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='trigger.event.unblocked_attacker').update(status='blocked',blockers=['post-declaration event withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record("Whenever this creature attacks and isn't blocked, draw a card.",'Creature — Human'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Whenever this creature attacks, draw a card.','Creature — Human'),capability_registry=registry).status)

    def test_unblocked_source_heads_use_the_post_declaration_event(self):
        registry=load_default_capability_registry()
        for body in ('draw a card.','you gain 2 life.'):
            ir=compile_oracle_card(record("Whenever this creature attacks and isn't blocked, "+body,'Creature — Human'),
                capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual('creature.unblocked',node.event)
            self.assertIn({'field':'card','op':'eq','value':'$source.ref'},node.event_condition['all'])
            self.assertIn({'field':'card_object_identity','op':'eq','value':'$source.logical_object_id'},node.event_condition['all'])


class UnblockedAttackerOwnerTests(unittest.TestCase):
    def test_unblocked_event_checkpoint_format_agreement_and_historical_absence_remain_strict(self):
        from quorune.model import GameState,GameConfig
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        state=GameState(game_id='unblocked-version',config=GameConfig(),players={},cards={},deck_names={},
            commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        raw=state.to_dict();self.assertEqual(1,raw['unblocked_attacker_event_version'])
        self.assertEqual(raw,GameState.from_dict(raw).to_dict())
        old=deepcopy(raw);old.pop('unblocked_attacker_event_version');legacy=GameState.from_dict(old)
        self.assertIsNone(legacy.unblocked_attacker_event_version);self.assertEqual(old,legacy.to_dict())
        validate_state_versions({'format':format_state_versions(state)},state)
        with self.assertRaises(ValueError):validate_state_versions({'format':{**format_state_versions(state),'unblocked_attacker_event_version':0}},state)
        for value in (True,0,2,'1'):
            with self.assertRaises(ValueError):GameState.from_dict({**raw,'unblocked_attacker_event_version':value})
    def test_forgetting_historical_blocked_membership_is_killed(self):
        import quorune.rules.unblocked_attackers as owner
        original=owner.unblocked_attacker_occurrences
        def omitted(host):
            prior=host.state.combat.blockers
            host.state.combat.blockers={k:v for k,v in prior.items() if v}
            try:return original(host)
            finally:host.state.combat.blockers=prior
        with patch.object(owner,'unblocked_attacker_occurrences',side_effect=omitted):
            result=unittest.TestResult();UnblockedAttackerOwnerTests('test_later_empty_blocker_list_does_not_become_a_new_unblocked_occurrence').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_declaration_membership_excludes_historical_blocks_removed_attackers_and_wrong_timing(self):
        from quorune.rules.unblocked_attackers import unblocked_attacker_occurrences
        def card(ref,**changes):return SimpleNamespace(ref=ref,object_id=ref,logical_object_id=ref+'@0',controller='A',zone='battlefield',phased_out=False,**changes)
        cards={k:card(k) for k in ('declared','entered-attacking','blocked','removed-blocker','gone')};cards['gone'].zone='hand'
        combat=SimpleNamespace(blockers_declared=True,attackers={k:'B' for k in cards},blockers={'blocked':['blocker'],'removed-blocker':[]})
        host=SimpleNamespace(state=SimpleNamespace(unblocked_attacker_event_version=1,combat=combat,cards=cards),active_seats=('A','B','C','D'),
            _effective_card_data=lambda card:{'type_line':'Creature'},_type_parts=lambda line:({'creature'},set(),set()),
            _defending_player_for_attacker=lambda oid,target:target)
        rows=unblocked_attacker_occurrences(host)
        self.assertEqual({'declared','entered-attacking'},{r.reference for r in rows})
        cards['declared'].logical_object_id='declared@1';self.assertEqual('declared@0',next(r.logical_object_id for r in rows if r.reference=='declared'))
        combat.blockers_declared=False
        with self.assertRaises(ValueError):unblocked_attacker_occurrences(host)
        host.state.unblocked_attacker_event_version=None
        self.assertEqual((),unblocked_attacker_occurrences(host))

    def test_unblocked_occurrence_version_and_roundtrip_have_closed_identities(self):
        from quorune.unblocked_attacker_model import UnblockedAttackerOccurrence,unblocked_attacker_event_version
        row=UnblockedAttackerOccurrence('source','source-id','source@0','A','C')
        self.assertEqual(('source','source@0','C'),tuple(row.semantic_context()[k] for k in ('card','card_object_identity','defending_player')))
        with self.assertRaises(ValueError):replace(row,logical_object_id='')
        for value in (True,0,2,'1'):
            with self.assertRaises(ValueError):unblocked_attacker_event_version(value)

    def test_later_empty_blocker_list_does_not_become_a_new_unblocked_occurrence(self):
        from quorune.rules.unblocked_attackers import unblocked_attacker_occurrences
        source=SimpleNamespace(ref='source',object_id='source',logical_object_id='source@0',controller='A',zone='battlefield',phased_out=False)
        combat=SimpleNamespace(blockers_declared=True,attackers={'source':'B'},blockers={'source':[]})
        host=SimpleNamespace(state=SimpleNamespace(unblocked_attacker_event_version=1,combat=combat,cards={'source':source}),active_seats=('A','B'),
            _effective_card_data=lambda card:{'type_line':'Creature'},_type_parts=lambda line:({'creature'},set(),set()),_defending_player_for_attacker=lambda oid,target:target)
        self.assertEqual((),unblocked_attacker_occurrences(host))
