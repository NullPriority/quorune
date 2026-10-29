"""Native state transitions use the ordinary stack and mandatory source owner."""
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from test_qualified_zone_event_queries import query_record
import inherited_composition_fixture as actions
import state_trigger_fixture as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class StateTriggerCompilerTests(unittest.TestCase):
    def test_public_state_source_sacrifice_compiles_with_exact_condition_and_owner(self):
        registry=load_default_capability_registry()
        for text in ('When you control no other creatures, sacrifice this creature.','When you control a Dwarf, sacrifice this creature.','When you control no snow lands, sacrifice this creature.','When you control a creature with toughness 2 or less, sacrifice this creature.'):
            ir=compile_oracle_card(query_record(text),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0];self.assertEqual('state.condition',node.event);self.assertIn('trigger.state.fixed_public_condition',node.capability_dependencies);self.assertIsNone(node.effects[0]['payment'])
        for text in ('When you control no permanents of the chosen color, sacrifice this creature.','When you control no Islands, sacrifice another creature.','When you control no Islands, sacrifice this creature. Draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_verified_state_or_sacrifice_owner_withdrawal_keeps_unrelated_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in ('trigger.state.fixed_public_condition','trigger.source.fixed_maintenance','zone.change.destination_replacement'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['state trigger owner withdrawn']);registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record('When you control no artifacts, sacrifice this creature.'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)

    def test_omitted_state_lowerer_mutant_is_killed(self):
        from unittest.mock import patch
        def witness():self.assertEqual('exact',compile_oracle_card(query_record('When you control no artifacts, sacrifice this creature.'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch('quorune.compiler.state_trigger_nodes.public_state_trigger_node',return_value=None),self.assertRaises(AssertionError):witness()

class PublicStateTriggerNativeTests(fixtures.StateTriggerFixture,unittest.TestCase):

    def test_actual_crocodile_queues_once_and_condition_relief_does_not_cancel_sacrifice(self):
        session=self.session(611651001);source=self.add(session.engine,'Emperor Crocodile',zone='hand');other=self.add(session.engine,'Grizzly Bears');bounce=self.add(session.engine,'Unsummon',zone='hand');self.add(session.engine,'Island');self.add(session.engine,'Forest');self.add(session.engine,'Wastes');self.add(session.engine,'Vedalken Orrery');private=self.add(session.engine,'Shock',seat='D',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'G':1});self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.assertEqual(0,self.trigger_count(session,source));self.cast(session,'A',bounce,[other.ref]);self.resolve_top(session);self.assertEqual(1,self.trigger_count(session,source));self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        for _ in range(4):session.engine.pump();self.assertEqual(1,self.trigger_count(session,source))
        session=self.replay(session,load=True);self.cast(session,'A',other);self.resolve_top(session);self.assertEqual('battlefield',session.state.cards[other.object_id].zone);self.assertEqual(1,self.trigger_count(session,source));self.settle(session);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_countered_state_trigger_requeues_while_the_condition_stays_true(self):
        session=self.session(611651010);source=self.add(session.engine,'Emperor Crocodile',zone='hand');counter=self.add(session.engine,'Stifle',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source);self.prepare(session,mana={'C':3,'G':1});self.checkpoint(session);self.cast(session,'A',source);self.resolve_top(session)
        self.assertEqual(1,self.trigger_count(session,source));first=next(i.ref for i in session.state.stack if i.source_object_id==source.object_id);self.cast(session,'B',counter,[first]);self.resolve_top(session);self.assertEqual(1,self.trigger_count(session,source));self.assertNotEqual(first,next(i.ref for i in session.state.stack if i.source_object_id==source.object_id));session=self.replay(session,load=True);self.settle(session);self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.replay(session,load=True)

    def test_runtime_state_and_result_owners_are_inferred_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.state_triggers import state_trigger_runtime_dependencies
        session=self.session(611651020);source=self.add(session.engine,'Emperor Crocodile');p=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='state.condition');required=state_trigger_runtime_dependencies(p)
        for cap in required:
            deps=tuple(c for c in p.capability_dependencies if c!=cap);changed=replace(p,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict());bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
