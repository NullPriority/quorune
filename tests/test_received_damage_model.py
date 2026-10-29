"""Committed recipient events are canonical, bounded and independently owned."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.damage import DamageEvent, DamageBatchResult, _collect_damage_result_triggers
from quorune.damage_received_events import received_damage_occurrences
from quorune.received_damage_model import (
    ReceivedDamageOccurrence, RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT,
    RECEIVED_DAMAGE_CAPABILITY,
)
from test_qualified_zone_event_queries import query_record


def packet(source, amount, *, combat=True, target='receiver', identity='receiver@1'):
    return DamageEvent(
        source=source, source_object_id=source, source_logical_object_id=source+'@1',
        source_oracle_id=None, source_commander_designation_id=None,
        source_controller='B', source_owner='B', source_types=('creature',),
        source_subtypes=('bear',), source_colors=('G',), source_keywords=(),
        source_is_commander=False, target=target, target_kind='permanent',
        target_object_id=target, target_logical_object_id=identity, target_controller='A',
        target_types=('creature',), target_subtypes=('dinosaur',),
        assigned_amount=max(1,amount), dealt_amount=amount, prevented_amount=0, combat=combat,
    )


class ReceivedDamageModelTests(unittest.TestCase):
    def test_received_damage_sums_actual_recipient_amount_without_absorbing_other_incarnations(self):
        events=(replace(packet('first',2),assigned_amount=5,prevented_amount=3),packet('second',3,combat=False),packet('prevented',0),packet('different',4,target='another',identity='another@1'))
        rows=received_damage_occurrences(events,event_id='batch1');self.assertEqual(2,len(rows));row=next(r for r in rows if r.target=='receiver');self.assertEqual((5,2),(row.amount,row.combat_amount));self.assertEqual(5,row.semantic_context()['amount']);self.assertEqual(2,row.semantic_context(combat_only=True)['amount']);self.assertNotIn('source',row.semantic_context())
        self.assertEqual(2,len(received_damage_occurrences((packet('first',1),packet('second',1,identity='receiver@2')),event_id='batch1')))

    def test_received_damage_order_and_separate_transactions_are_canonical(self):
        events=(packet('z',2,target='z',identity='z@1'),packet('b',1),packet('a',3))
        a=received_damage_occurrences(events,event_id='first');b=received_damage_occurrences(tuple(reversed(events)),event_id='first');self.assertEqual(a,b);self.assertNotEqual(a,received_damage_occurrences(events,event_id='second'))
        self.assertEqual((),received_damage_occurrences((packet('none',0),),event_id='empty'))

    def test_received_damage_rejects_malformed_and_inconsistent_committed_facts(self):
        row=received_damage_occurrences((packet('first',2),),event_id='batch')[0]
        for change in ({'amount':0},{'amount':True},{'combat_amount':3},{'target_logical_object_id':None},{'target_types':['creature']},{'target_kind':'hand'}):
            with self.assertRaises(ValueError):replace(row,**change)
        with self.assertRaises(ValueError):received_damage_occurrences((packet('one',1),replace(packet('two',1),target_controller='B')),event_id='batch')
        with self.assertRaises(ValueError):received_damage_occurrences((replace(packet('one',1),dealt_amount=True),),event_id='batch')
        with self.assertRaises(ValueError):replace(row,combat_amount=0).semantic_context(combat_only=True)

    def test_receiving_dispatch_preserves_dealer_packets_and_kills_omitted_or_split_group_mutants(self):
        events=(packet('first',2),packet('second',3))
        def witness():
            calls=[];host=SimpleNamespace(state=SimpleNamespace(monarch=None),_semantic_pause_annotation=lambda:None,_dispatch_semantic_event=lambda event,context,**kw:calls.append((event,context)))
            result=DamageBatchResult(events=events,changed_objects=(),changed_players=(),lifelink_gains=(),event_batch_id='batch')
            _collect_damage_result_triggers(host,result,trigger_sources=(),trigger_source_zones={},trigger_batch=[])
            self.assertEqual(2,sum(event=='damage.dealt' for event,_ in calls));received=[c for e,c in calls if e==RECEIVED_DAMAGE_EVENT];self.assertEqual(1,len(received));self.assertEqual(5,received[0]['amount']);self.assertEqual(1,sum(e==RECEIVED_COMBAT_DAMAGE_EVENT for e,_ in calls))
        witness()
        with patch('quorune.damage_received_events.dispatch_received_damage_events',return_value=None),self.assertRaises(AssertionError):witness()
        original=received_damage_occurrences
        def split(events,*,event_id):return tuple(row for event in events for row in original((event,),event_id=event_id))
        with patch('quorune.damage_received_events.received_damage_occurrences',side_effect=split),self.assertRaises(AssertionError):witness()


class ReceivedDamageCompilerTests(unittest.TestCase):
    def test_new_receiving_subscriptions_bind_grouped_all_or_combat_transport(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        registry=load_default_capability_registry()
        for text,event in (('Whenever this creature is dealt damage, draw a card.',RECEIVED_DAMAGE_EVENT),('Whenever this creature is dealt combat damage, draw a card.',RECEIVED_COMBAT_DAMAGE_EVENT),('Whenever this creature is dealt damage, put a +1/+1 counter on it.',RECEIVED_DAMAGE_EVENT)):
            ir=compile_oracle_card(replace(query_record(text),type_line='Creature — Dinosaur'),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0];self.assertEqual(event,node.event);self.assertIn(RECEIVED_DAMAGE_CAPABILITY,node.capability_dependencies)
        ir=compile_oracle_card(replace(query_record('Whenever this creature deals combat damage to a player, draw a card.'),type_line='Creature — Dinosaur'),capability_registry=registry);self.assertEqual('exact',ir.status);self.assertNotIn(RECEIVED_DAMAGE_CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Whenever a creature with an unrepresented badge is dealt damage, draw a card.'),type_line='Enchantment'),capability_registry=registry).status)

    def test_received_damage_runtime_minimum_does_not_depend_on_coverage(self):
        from quorune.card_programs import compile_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.capabilities import load_default_capability_registry
        registry=load_default_capability_registry();db=type('NoRulings',(),{'rulings':lambda self,record:()})();p=compile_card_program(db,replace(query_record('Whenever this creature is dealt damage, draw a card.'),type_line='Creature — Dinosaur'),trust_level='trusted',capability_registry=registry,capability_profile='commander_review');program=p.abilities[0];deps=tuple(c for c in program.capability_dependencies if c!=RECEIVED_DAMAGE_CAPABILITY);changed=replace(program,coverage=[],capability_dependencies=deps,capability_closure=registry.closure(deps,profile='commander_review').to_dict() if deps else None);binding=bind_semantic_program_runtime(changed,capability_registry=registry,profile='commander_review');self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+RECEIVED_DAMAGE_CAPABILITY,binding['blockers'])

    def test_verified_received_owner_withdrawal_keeps_ordinary_outgoing_damage(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner in (RECEIVED_DAMAGE_CAPABILITY,'trigger.event.normalized_damage'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==owner).update(status='blocked',blockers=['damage owner withdrawn']);registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for text,exact in (('Whenever this creature is dealt damage, draw a card.',False),('Whenever this creature deals combat damage to a player, draw a card.',owner==RECEIVED_DAMAGE_CAPABILITY),('Draw a card.',True)):
                kind='Sorcery' if text=='Draw a card.' else 'Creature — Dinosaur'
                ir=compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry)
                self.assertEqual(exact,ir.status=='exact',(owner,text,ir.material_residuals))
