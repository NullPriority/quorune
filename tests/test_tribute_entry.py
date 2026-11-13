"""Pinned CR702.104: Tribute is decided before entry, by the chosen opponent."""
from dataclasses import replace
import json,unittest
from unittest.mock import patch
from common import ROOT
import tribute_native_fixture as fixtures
from quorune.tribute_model import TributeSpec,TRIBUTE_CAPABILITY,TRIBUTE_HANDLER
from quorune.semantic_runtime.tribute import TributeEntryHandler
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry
from quorune.card_programs.binding import bind_semantic_program_runtime


class TributeEntryCompilerTests(fixtures.TributeNativeFixture,unittest.TestCase):
    def test_intrinsic_and_unpaid_result_retain_separate_owned_programs(self):
        c=self.db.lookup('Snake of the Golden Grove',fuzzy=False)
        ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        intrinsic=next(n for f in ir.faces for n in f.nodes if n.handlers)
        spec=TributeEntryHandler().validate(intrinsic.handlers[0]);self.assertEqual(3,spec.amount)
        trigger=next(n for f in ir.faces for n in f.nodes if n.event=='permanent.enter.self')
        self.assertEqual({'field':'tribute_unpaid','op':'contains_any','value':['tribute']},trigger.event_condition)
        self.assertIn(TRIBUTE_CAPABILITY,intrinsic.capability_dependencies)
        self.assertIn(TRIBUTE_CAPABILITY,trigger.capability_dependencies)

    def test_descriptor_fields_types_and_unknown_mechanic_bodies_fail_closed(self):
        descriptor=TributeSpec(2,'tribute').descriptor()
        self.assertEqual(2,TributeEntryHandler().validate(descriptor).amount)
        for change in ({'schema_version':True},{'amount':True},{'amount':0},{'event':'damage'},
                {'handler_id':TRIBUTE_HANDLER+'x'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):TributeEntryHandler().validate({**descriptor,**change})
        for name in ('Oracle of Bones','Flame-Wreathed Phoenix'):
            c=self.db.lookup(name,fuzzy=False)
            self.assertNotEqual('exact',compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review').status)

    def test_verified_capability_withdrawal_preserves_an_independent_flying_source(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==TRIBUTE_CAPABILITY).update(status='blocked',blockers=['Tribute withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Ornitharch'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Vampire Nighthawk'),capability_registry=registry).status)


class TributeEntryNativeTests(fixtures.TributeNativeFixture,unittest.TestCase):
    def test_counter_replacements_use_the_chosen_placing_player_and_receiving_controller(self):
        # CR702.104a and the pinned 2017-04-18 ruling: B places counters
        # on A's creature. Scales checks the receiving controller.
        for name,seat,amount in (('Hardened Scales','A',4),('Hardened Scales','B',3)):
            with self.subTest(name=name,seat=seat):
                def before(s,source):
                    control=self.add(s.engine,name,seat=seat)
                    self.assert_closed(s,control)
                s,source,private=self.scene('Snake of the Golden Grove',before=before)
                self.begin_entry(s,source)
                self.choose(s,'B',self.option(s,'B'))
                self.assertIsNone(self.resolve(s))
                self.assertEqual(amount,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual(40,s.state.players['A'].life)
                self.finish(s,private)

    def test_solemnity_makes_tribute_unpayable_and_preserves_the_unpaid_trigger(self):
        # Pinned Solemnity ruling: the chosen opponent cannot pay tribute.
        def before(s,source):
            control=self.add(s.engine,'Solemnity',seat='C')
            self.assert_closed(s,control)
        s,source,private=self.scene('Snake of the Golden Grove',before=before)
        self.cast(s,'A',source)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.choose(s,'A',self.option(s,'A',ends='opponent:B'))
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.assertEqual(44,s.state.players['A'].life)
        self.finish(s,private)

    def test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                s,source,private=self.scene('Snake of the Golden Grove')
                self.begin_entry(s,source)
                s=self.replay(s,load=True)
                self.choose(s,'B',self.option(s,'B',decline=not paid))
                self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.assertEqual(3 if paid else 0,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual((40 if paid else 44,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_unpaid_condition_dependency_is_required_without_coverage_annotations(self):
        s,source,private=self.scene('Snake of the Golden Grove')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='permanent.enter.self')
        deps=tuple(c for c in program.capability_dependencies if c!=TRIBUTE_CAPABILITY)
        missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+TRIBUTE_CAPABILITY,bound['blockers'])

    def test_fresh_paid_entry_witness_kills_an_unconditionally_unpaid_event_provider(self):
        baseline=type(self)('test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger')
        baseline.test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger()
        with patch('quorune.tribute_entry_results.tribute_entry_context',return_value={'tribute_unpaid':['tribute']}):
            witness=type(self)('test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger')
            with self.assertRaises(AssertionError):witness.test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger()
