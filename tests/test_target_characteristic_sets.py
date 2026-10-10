from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_characteristic_sets import decode_target_characteristics
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class TargetCharacteristicSetCompilerTests(unittest.TestCase):
    def test_fixed_target_sets_keep_count_range_modifier_and_source_span(self):
        for text,count in [('Up to two target creatures each get +2/+0 until end of turn.',('up_to',2)),
                           ('Two target creatures each get -1/-1 until end of turn.',('count',2)),
                           ('Up to one target creature an opponent controls gets -2/-0 until end of turn.',('up_to',1))]:
            ir=compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual(count[1],node.target_schema[count[0]])
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))
            self.assertEqual(6,node.effects[0]['schema_version'])
            self.assertIn('continuous.resolution.fixed_target_characteristic_set',node.capability_dependencies)

    def test_descriptor_unknown_quantities_and_dynamic_variants_fail_closed(self):
        effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':6,'cards':'$targets','maximum_targets':2,'power':2,'toughness':0,'keywords':[]}
        for changes in ({'schema_version':True},{'maximum_targets':7},{'power':'$x'},{'keywords':['Unknown']},{'extra':1}):
            with self.assertRaises(ValueError):decode_target_characteristics({**effect,**changes})
        text='Up to X target creatures each get +2/+0 until end of turn.'
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=load_default_capability_registry()).status)
        from quorune.rules.target_characteristic_sets import target_characteristic_set_capabilities
        schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'up_to':2}
        args={'effects':(effect,),'target_schema':schema,'mechanic_ids':('cr-115-targets','cr-611-continuous-effects')}
        self.assertTrue(target_characteristic_set_capabilities(**args))
        for changes in ({'maximum_targets':3},{'cards':'$source'},{'keywords':['Flying']}):
            self.assertEqual((),target_characteristic_set_capabilities(**{**args,'effects':({**effect,**changes},)}))


class TargetCharacteristicSetRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'sets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/target-characteristic-sets.json',ROOT/'tests/fixtures/batched-public-assurance-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Target sets',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_nahiri_accepts_zero_one_or_two_distinct_targets_and_replays(self):
        for count in (0,1,2):
            session=self.session(309001+count);engine=session.engine
            bodies=[self.add(engine,'Generic Bound Body',ref='body-'+str(i)) for i in range(2)]
            spell=self.add(engine,"Nahiri's Stoneblades",zone='hand')
            action=self.ready(session,spell,{'R':1,'C':1});self.checkpoint(session)
            before=authoritative_state_hash(session.state)
            rejected=session.act('pilot:B',{'action_id':action['id'],'targets':[bodies[0].ref],'pay':'auto'})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in bodies[:count]],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
            for index,body in enumerate(bodies):self.assertEqual(4 if index<count else 2,engine._numeric_stat(body.object_id,'power'))
            self.replay(session,load=True)

    def test_actual_sick_and_tired_applies_to_surviving_original_target(self):
        session=self.session(309004);engine=session.engine
        bodies=[self.add(engine,'Generic Bound Body',ref='body-'+str(i)) for i in range(2)]
        spell=self.add(engine,'Sick and Tired',zone='hand');bounce=self.add(engine,'Unsummon',zone='hand')
        action=self.ready(session,spell,{'B':1,'U':2,'C':8});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'targets':[bodies[0].ref,bodies[0].ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in bodies],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[bodies[1].ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual((1,5),(engine._numeric_stat(bodies[0].object_id,'power'),engine._numeric_stat(bodies[0].object_id,'toughness')))
        self.assertEqual('hand',session.state.cards[bodies[1].object_id].zone);self.replay(session)

    def test_up_to_one_entry_trigger_choice_saves_replays_and_applies_fixed_modifier(self):
        session=self.session(309005);engine=session.engine
        target=self.add(engine,'Generic Bound Body',seat='B')
        source=self.add(engine,'Nebelgast Intruder',zone='hand')
        action=self.ready(session,source,{'U':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('semantic.target',pending.kind)
        packet=session.packet('pilot:A',full=True)['decision']
        self.assertIn(target.ref,str(packet))
        resumed=self.replay(session,load=True)
        chosen=resumed.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(chosen.ok,chosen.summary)
        self.resolve(resumed)
        self.assertEqual(0,resumed.engine._numeric_stat(target.object_id,'power'));self.replay(resumed)

    def test_exact_scalar_amount_composes_with_optional_single_target_set(self):
        session=self.session(309006);engine=session.engine
        target=self.add(engine,'Generic Bound Body',seat='B')
        gain=self.add(engine,'Generic Target Set Life Gain',zone='hand')
        source=self.add(engine,'Generic Scalar Target Set Entrant',zone='hand')
        action=self.ready(session,gain,{'W':1,'C':8});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('semantic.target',pending.kind)
        resumed=self.replay(session,load=True)
        chosen=resumed.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(chosen.ok,chosen.summary);self.resolve(resumed)
        self.assertEqual((0,4),(resumed.engine._numeric_stat(target.object_id,'power'),resumed.engine._numeric_stat(target.object_id,'toughness')))
        self.replay(resumed)

    def test_actual_wind_sail_grants_only_selected_original_creatures_and_expires(self):
        session=self.session(309007);engine=session.engine
        bodies=[self.add(engine,'Generic Bound Body',ref='body-'+str(i)) for i in range(3)]
        spell=self.add(engine,'Wind Sail',zone='hand')
        action=self.ready(session,spell,{'U':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in bodies[:2]],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        for i,body in enumerate(bodies):self.assertEqual(i<2,'flying' in engine._combat_keywords(session.state.cards[body.object_id]))
        self.replay(session)
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        self.assertTrue(all('flying' not in engine._combat_keywords(session.state.cards[c.object_id]) for c in bodies))

    def test_omitted_target_set_effect_mutant_is_killed_by_actual_nahiri(self):
        session=self.session(309008);engine=session.engine
        target=self.add(engine,'Generic Bound Body');spell=self.add(engine,"Nahiri's Stoneblades",zone='hand')
        action=self.ready(session,spell,{'R':1,'C':1})
        with patch('quorune.continuous_effect_state.apply_target_characteristics',return_value=()):
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(4,engine._numeric_stat(target.object_id,'power'))

    def test_real_blink_does_not_apply_queued_modifier_to_new_target_incarnation(self):
        session=self.session(309009);engine=session.engine
        bodies=[self.add(engine,'Generic Bound Body',ref='body-'+str(i)) for i in range(2)]
        spell=self.add(engine,'Sick and Tired',zone='hand');blink=self.add(engine,'Cloudshift',zone='hand')
        action=self.ready(session,spell,{'B':1,'W':2,'C':8});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in bodies],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==blink.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[bodies[1].ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(1,engine._numeric_stat(bodies[0].object_id,'power'))
        self.assertEqual(2,engine._numeric_stat(bodies[1].object_id,'power'));self.replay(session)


    def test_public_quantity_target_set_resolves_current_count_for_original_targets_and_replays(self):
        session=self.session(311102);engine=session.engine
        bodies=[self.add(engine,'Generic Bound Body',ref='query-body-'+str(i)) for i in range(2)]
        foreign=self.add(engine,'Generic Bound Body',seat='B')
        spell=self.add(engine,'Generic Query Target Set',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in bodies]});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        for body in bodies:self.assertEqual((4,8),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertEqual(2,engine._numeric_stat(foreign.object_id,'power'));self.replay(session,load=True)

    def test_original_saga_target_set_with_unrepresented_chapter_rejects_runtime_admission(self):
        from high_risk_interaction_support import _observed_piece_ids
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.semantics import SemanticRegistry
        record=self.db.by_oracle_id('bce30bc5-b059-42bb-a9a4-5886f5607160')
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        row=analyze_card_unlocks(ir,program=program,program_error=None,capabilities=self.registry,profile='commander_review')
        self.assertLessEqual({'capability.continuous.resolution.fixed_target_characteristic_set',
            'residual.card_form.ordinary-saga-chapter-event-binding'},_observed_piece_ids(row))
        self.assertEqual('residual',row['card_program_status']);self.assertIsNone(row['hard_construction_failure'])
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict_capability_ready']);self.assertFalse(binding['compatible_ready'])
        self.assertIn('trust_basis:unresolved',binding['blockers'])


if __name__=='__main__':unittest.main()
