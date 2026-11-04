"""Printed follow-ups retain one announced homogeneous target set."""
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class TargetSetResultSequenceCompilerTests(unittest.TestCase):
    def test_same_set_counter_and_characteristic_results_are_ordered_and_fully_owned(self):
        registry=load_default_capability_registry()
        cases=(('Tap up to two target creatures. Put a stun counter on each of them.',('tap_targets','place_counters_on_targets')),
            ('Tap up to two target artifacts and/or creatures. Put two stun counters on each of them.',('tap_targets','place_counters_on_targets')),
            ('Untap two target creatures. Each of them gets +1/+1 until end of turn.',('untap_targets','apply_source_characteristics_until_end_of_turn')),
            ('Untap up to two target creatures. They each get +2/+2 until end of turn.',('untap_targets','apply_source_characteristics_until_end_of_turn')),
            ('Tap up to two target creatures and put a stun counter on each of them. Investigate.',('tap_targets','place_counters_on_targets','create_token')))
        for text,operations in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Instant'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual(operations,tuple(e['op'] for e in node.effects))
                self.assertIn('resolution.target_set.ordered_results',node.capability_dependencies)
                for effect in node.effects[:2]:self.assertEqual('$targets',effect['cards'])

    def test_new_choices_variable_sets_and_unknown_followups_remain_residual(self):
        registry=load_default_capability_registry()
        for text in ('Tap up to two target creatures. Put a stun counter on either of them.',
                'Tap up to two target creatures. Put a stun counter on each of them. Rewrite history.',
                'Untap any number of target creatures. They each get +2/+2 until end of turn.',
                'Tap up to two target creatures. Put a stun counter on each target creature.'):
            with self.subTest(text=text):self.assertNotEqual('exact',compile_oracle_card(record(text,'Instant'),capability_registry=registry).status)

    def test_shared_schema_and_runtime_minimum_are_proved_without_sequence_annotations(self):
        from types import SimpleNamespace
        from quorune.compiler.target_set_result_sequences import target_set_result_sequence_template
        from quorune.oracle_ir import _effect_template
        from quorune.rules.target_set_result_sequences import target_set_result_sequence_runtime_dependencies
        compile_one=lambda text:_effect_template(text,card_name='Printed sequence')
        value=target_set_result_sequence_template('Tap up to two target creatures. Put a stun counter on each of them.',compile_independent=compile_one)
        self.assertIsNotNone(value);_,effects,schema,mechanics=value
        program=SimpleNamespace(effects=effects,target_schema=schema,coverage=())
        self.assertIn('resolution.target_set.ordered_results',target_set_result_sequence_runtime_dependencies(program))
        for change in ({'maximum_targets':3},{'cards':'$target.0'},{'amount':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                target_set_result_sequence_runtime_dependencies(SimpleNamespace(**{**vars(program),'effects':(effects[0],{**effects[1],**change})}))
        with self.assertRaises(ValueError):
            target_set_result_sequence_runtime_dependencies(SimpleNamespace(**{**vars(program),'target_schema':{**schema,'count_parameter':{'parameter':'any_number'}}}))
        legacy_source={'op':'apply_source_characteristics_until_end_of_turn','schema_version':1,'source':'$source','power':1,'toughness':1}
        self.assertEqual((),target_set_result_sequence_runtime_dependencies(SimpleNamespace(**{**vars(program),'effects':(effects[0],legacy_source)})))
        boosted=target_set_result_sequence_template('Untap two target creatures. Each of them gets +1/+1 until end of turn.',compile_independent=compile_one)
        for change in ({'maximum_targets':3},{'power':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                target_set_result_sequence_runtime_dependencies(SimpleNamespace(effects=(boosted[1][0],{**boosted[1][1],**change}),target_schema=boosted[2],coverage=()))

    def test_sequence_followup_omission_mutation_is_killed(self):
        from unittest.mock import patch
        import quorune.compiler.target_set_result_sequences as owner
        original=owner.target_set_result_sequence_template
        def omitted(text,**kwargs):
            result=original(text,**kwargs)
            return (result[0],result[1][:1],result[2],result[3]) if result else None
        with patch.object(owner,'target_set_result_sequence_template',side_effect=omitted):
            result=unittest.TestResult()
            TargetSetResultSequenceCompilerTests('test_same_set_counter_and_characteristic_results_are_ordered_and_fully_owned').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_verified_sequence_withdrawal_preserves_the_ordinary_target_set_leaf(self):
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='resolution.target_set.ordered_results').update(status='blocked',blockers=['Shared set authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record('Tap up to two target creatures. Put a stun counter on each of them.','Instant'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Tap up to two target creatures.','Instant'),capability_registry=registry).status)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class TargetSetResultSequenceNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'target-set-sequences.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/target-set-result-sequence-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Same target set results',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def test_actual_cooldown_keeps_both_results_on_surviving_original_targets_after_blink_and_pending_load(self):
        s=self.session(608601271);spell=self.add(s.engine,'Involuntary Cooldown',zone='hand')
        first=self.add(s.engine,'Phyrexian Walker',seat='B',ref='shared-first');second=self.add(s.engine,'Phyrexian Walker',seat='D',ref='shared-second')
        blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand');self.add(s.engine,'Plains',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='shared-set-private')
        for card in (spell,blink):self.assert_closed(s,card)
        self.prepare(s,mana={'U':1,'C':3});self.checkpoint(s)
        self.reject(s,'C',{'action_id':'cast:'+spell.ref,'targets':[first.ref,second.ref],'pay':'auto'})
        self.cast(s,'A',spell,[first.ref,second.ref]);self.cast(s,'B',blink,[first.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[first.object_id].tapped);self.assertFalse(s.state.cards[first.object_id].counters)
        self.assertTrue(s.state.cards[second.object_id].tapped);self.assertEqual(2,s.state.cards[second.object_id].counters.get('stun',0))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_out_cold_zero_targets_investigates_but_losing_all_selected_targets_stops_the_program(self):
        for empty in (True,False):
            with self.subTest(empty=empty):
                s=self.session(608601280+int(empty));spell=self.add(s.engine,'Out Cold',zone='hand')
                target=self.add(s.engine,'Phyrexian Walker',seat='B')
                blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand');self.add(s.engine,'Plains',seat='B')
                self.assert_closed(s,spell);self.assert_closed(s,blink)
                self.prepare(s,mana={'U':1,'C':3});self.checkpoint(s);self.cast(s,'A',spell,[] if empty else [target.ref])
                if not empty:self.cast(s,'B',blink,[target.ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                clues=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Clue']
                self.assertEqual(int(empty),len(clues));self.assertFalse(s.state.cards[target.object_id].tapped)
                self.assertFalse(s.state.cards[target.object_id].counters);self.replay(s,load=True)

    def test_actual_copied_join_forces_keeps_count_and_rebinds_both_effects_to_the_copy_targets(self):
        s=self.session(608601290);spell=self.add(s.engine,'Join Forces',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'R':2})
        targets={seat:self.add(s.engine,'Phyrexian Walker',seat=seat,ref='shared-copy-'+seat) for seat in 'ABCD'}
        for target in targets.values():target.tapped=True
        self.assert_closed(s,spell);self.assert_closed(s,copy)
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s)
        self.cast(s,'A',spell,[targets[p].ref for p in 'BC']);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,'B',{'action_id':'choose','targets':[targets['A'].ref]})
        self.reject(s,'A',{'action_id':'choose','targets':[targets[p].ref for p in 'AD']})
        s=self.replay(s,load=True);result=s.act('pilot:B',{'action_id':'choose','targets':[targets[p].ref for p in 'AD']});self.assertTrue(result.ok,result.summary)
        self.resolve_top(s)
        for seat in 'AD':
            card=s.state.cards[targets[seat].object_id];self.assertFalse(card.tapped)
            self.assertEqual((2,5),tuple(s.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
        self.assertTrue(all(s.state.cards[targets[p].object_id].tapped for p in 'BC'))
        self.assertIsNone(self.resolve(s));self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in targets.values()))
        self.replay(s,load=True)
