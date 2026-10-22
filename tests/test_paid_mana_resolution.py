from __future__ import annotations

"""CR 608.2c/609.4b: original paid mana selects ordered spell results."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
import test_paid_color_entry as controls
from scripts.build_test_database import build_fixture_database


class PaidManaResolutionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PaidColorEntryTests.session
    add=controls.PaidColorEntryTests.add
    prepare=controls.PaidColorEntryTests.prepare
    checkpoint=controls.PaidColorEntryTests.checkpoint
    resolve=controls.PaidColorEntryTests.resolve
    replay=controls.PaidColorEntryTests.replay
    binding=controls.PaidColorEntryTests.binding
    actions=controls.PaidColorEntryTests.actions
    priority=controls.PaidColorEntryTests.priority
    private_replay=controls.PaidColorEntryTests.private_replay
    cast_paid=controls.PaidColorEntryTests.cast_paid
    advance=controls.PaidColorEntryTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'paid-mana-resolution.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/paid-mana-resolution-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual paid mana results',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def foods(self,session,seat):
        return [c for c in session.state.cards.values() if c.controller==seat and c.zone=='battlefield'
            and 'food' in session.engine._type_parts(session.engine._effective_card_data(c)['type_line'])[1]]

    def test_actual_foreboding_fruit_gives_caster_food_and_target_private_draw(self):
        session=self.session(748100);source=self.add(session.engine,'Foreboding Fruit',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':3});self.checkpoint(session)
        before_hand=len(session.state.players['B'].zones['hand'])
        action=next(row for row in self.actions(session) if row['id']=='cast:'+source.ref)
        command={'action_id':action['id'],'targets':['B'],'pay':'manual','payment':{'B':3}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before_hand+2,len(session.state.players['B'].zones['hand']))
        self.assertEqual(38,session.state.players['B'].life);self.assertEqual(40,session.state.players['A'].life)
        self.assertEqual(1,len(self.foods(session,'A')));self.assertFalse(self.foods(session,'B'))
        self.private_replay(session,private)

    def test_fresh_paid_result_compiler_and_actual_count_reader_mutants_are_killed(self):
        method='test_actual_foreboding_fruit_gives_caster_food_and_target_private_draw'
        for target in ('quorune.compiler.paid_mana_resolution_templates.paid_mana_resolution_template',
                'quorune.rules.paid_mana_resolution.resolve_cast_mana_units'):
            with self.subTest(target=target),patch(target,return_value=None if 'template' in target else 0):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_searing_barrage_adds_damage_to_current_controller_not_owner_or_caster(self):
        session=self.session(748110);source=self.add(session.engine,'Searing Barrage',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B');session.engine.change_control(target.object_id,'C')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':3,'C':2});self.checkpoint(session)
        self.cast_paid(session,source,{'R':3,'C':2},[target.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.assertEqual(37,session.state.players['C'].life)
        self.assertEqual([40,40,40],[session.state.players[p].life for p in 'ABD'])
        self.private_replay(session,private)

    def test_fresh_target_controller_information_reader_omission_is_killed(self):
        method='test_actual_searing_barrage_adds_damage_to_current_controller_not_owner_or_caster'
        with patch('quorune.target_controller_information.resolve_target_controller_information',return_value='A'):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_rally_counts_created_humans_after_its_prefix(self):
        session=self.session(748120);source=self.add(session.engine,'Rally for the Throne',zone='hand')
        self.add(session.engine,'Grizzly Bears');self.prepare(session,mana={'W':3});self.checkpoint(session)
        self.cast_paid(session,source,{'W':3});session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual(43,session.state.players['A'].life)
        humans=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.controller=='A'
            and c.is_token and 'human' in session.engine._type_parts(session.engine._effective_card_data(c)['type_line'])[1]]
        self.assertEqual(2,len(humans));self.replay(session)

    def test_actual_slaying_fire_selects_four_instead_of_three_plus_four(self):
        for paid in (2,3):
            with self.subTest(paid=paid):
                session=self.session(748130+paid);source=self.add(session.engine,'Slaying Fire',zone='hand')
                payment={'R':paid,'C':3-paid};self.prepare(session,mana=payment);self.checkpoint(session)
                self.cast_paid(session,source,payment,['B']);session=self.replay(session,load=True)
                self.assertIsNone(self.resolve(session));self.assertEqual(36 if paid==3 else 37,session.state.players['B'].life)
                self.replay(session)

    def test_seven_originals_have_complete_nonempty_whole_spell_programs(self):
        names=('Foreboding Fruit','Rally for the Throne','Searing Barrage','Silverflame Ritual',
            'Slaying Fire','Turn into a Pumpkin','Unexplained Vision')
        for index,name in enumerate(names):
            with self.subTest(name=name):
                session=self.session(748150+index);source=self.add(session.engine,name,zone='hand')
                card=self.db.lookup(name);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                node,=ir.faces[0].nodes;self.assertEqual(card.oracle_text,card.oracle_text[node.span.start:node.span.end])
                programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve']
                self.assertEqual(1,len(programs));self.assertTrue(programs[0].effects)

    def test_two_black_units_keep_draw_and_life_but_do_not_create_food(self):
        session=self.session(748170);source=self.add(session.engine,'Foreboding Fruit',zone='hand')
        self.prepare(session,mana={'B':2,'C':1});self.checkpoint(session)
        old=len(session.state.players['B'].zones['hand']);self.cast_paid(session,source,{'B':2,'C':1},['B'])
        self.assertIsNone(self.resolve(session));self.assertEqual(old+2,len(session.state.players['B'].zones['hand']))
        self.assertEqual(38,session.state.players['B'].life);self.assertFalse(self.foods(session,'A'));self.replay(session)

    def test_actual_pumpkin_returns_target_draws_then_creates_caster_food(self):
        session=self.session(748180);source=self.add(session.engine,'Turn into a Pumpkin',zone='hand')
        target=self.add(session.engine,'Mind Stone',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':3,'C':1});self.checkpoint(session)
        old=len(session.state.players['A'].zones['hand']);self.cast_paid(session,source,{'U':3,'C':1},[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[target.object_id].zone)
        self.assertEqual(old,len(session.state.players['A'].zones['hand']))
        self.assertEqual(1,len(self.foods(session,'A')));self.private_replay(session,private)

    def test_actual_vision_draws_before_private_scry_and_resumes_exactly(self):
        session=self.session(748190);source=self.add(session.engine,'Unexplained Vision',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':3,'C':2});self.checkpoint(session)
        old=len(session.state.players['A'].zones['hand']);self.cast_paid(session,source,{'U':3,'C':2})
        pending=self.resolve(session);self.assertIsNotNone(pending)
        self.assertEqual(old+2,len(session.state.players['A'].zones['hand']))
        library=session.state.players['A'].zones['library']
        refs=[session.state.cards[oid].ref for oid in reversed(library[-3:])]
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        session=self.replay(session,load=True);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':{'top':refs,'bottom':[]}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':list(reversed(refs)),'bottom':[]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        actual=[session.state.cards[oid].ref for oid in reversed(session.state.players['A'].zones['library'][-3:])]
        self.assertEqual(list(reversed(refs)),actual);self.private_replay(session,private)

    def test_actual_ritual_counters_before_vigilance_and_expires_normally(self):
        session=self.session(748200);source=self.add(session.engine,'Silverflame Ritual',zone='hand')
        creature=self.add(session.engine,'Grizzly Bears');enemy=self.add(session.engine,'Grizzly Bears',seat='B',ref='ritual-enemy')
        self.prepare(session,mana={'W':3,'C':1});self.checkpoint(session);self.cast_paid(session,source,{'W':3,'C':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        card=session.state.cards[creature.object_id]
        self.assertEqual(1,card.counters.get('+1/+1',0));self.assertIn('vigilance',session.engine._combat_keywords(card))
        self.assertEqual(0,session.state.cards[enemy.object_id].counters.get('+1/+1',0))
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        self.assertNotIn('vigilance',session.engine._combat_keywords(card));self.assertEqual(1,card.counters['+1/+1'])

    def test_whole_spell_fizzles_after_target_departure_without_food_or_draw(self):
        session=self.session(748210);source=self.add(session.engine,'Turn into a Pumpkin',zone='hand')
        bounce=self.add(session.engine,'Boomerang',zone='hand');target=self.add(session.engine,'Mind Stone',seat='B')
        self.prepare(session,mana={'U':5,'C':1});self.checkpoint(session)
        self.cast_paid(session,source,{'U':3,'C':1},[target.ref]);self.priority(session,'A')
        self.cast_paid(session,bounce,{'U':2},[target.ref]);old=len(session.state.players['A'].zones['hand'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(old,len(session.state.players['A'].zones['hand']));self.assertFalse(self.foods(session,'A'))
        self.replay(session)

    def test_spell_copy_keeps_default_damage_and_has_no_original_paid_mana(self):
        session=self.session(748220);source=self.add(session.engine,'Slaying Fire',zone='hand')
        self.prepare(session,mana={'R':3});self.cast_paid(session,source,{'R':3},['B'])
        original=session.state.stack[-1]
        copied=session.engine._copy_stack_item(controller='A',target=original,targets=['B'],target_groups=original.context['target_groups'],reason='Paid result copy owner control')
        self.assertIsNone(copied.mana_spent_counts);self.checkpoint(session);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual(33,session.state.players['B'].life);self.replay(session)

    def test_controller_information_uses_current_then_lki_and_not_a_returned_incarnation(self):
        from quorune.target_controller_information import target_controller_reference,resolve_target_controller_information
        session=self.session(748230);source=self.add(session.engine,'Searing Barrage',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B');self.prepare(session,mana={'R':3,'C':2})
        self.cast_paid(session,source,{'R':3,'C':2},[target.ref]);item=session.state.stack[-1];reference=target_controller_reference()
        self.assertEqual('B',resolve_target_controller_information(session.engine,reference,item))
        session.engine.change_control(target.object_id,'C')
        self.assertEqual('C',resolve_target_controller_information(session.engine,reference,item))
        session.engine.move_card(target.object_id,'graveyard',log=False)
        self.assertEqual('C',resolve_target_controller_information(session.engine,reference,item))
        session.engine.move_card(target.object_id,'battlefield',controller='D',log=False)
        self.assertEqual('C',resolve_target_controller_information(session.engine,reference,item))

    def test_searing_uses_control_after_cast_and_not_its_targeting_time_snapshot(self):
        session=self.session(748240);source=self.add(session.engine,'Searing Barrage',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B');self.prepare(session,mana={'R':3,'C':2})
        self.cast_paid(session,source,{'R':3,'C':2},[target.ref]);session.engine.change_control(target.object_id,'D')
        self.checkpoint(session);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['D'].life);self.assertEqual(40,session.state.players['B'].life)
        self.replay(session)

    def test_fresh_target_departure_information_omission_is_killed(self):
        method='test_controller_information_uses_current_then_lki_and_not_a_returned_incarnation'
        with patch('quorune.target_controller_information.pin_target_controller_departures'):
            with self.assertRaisesRegex(ValueError,'last-known controller is unavailable'):
                getattr(type(self)(method),method)()

    def test_condition_reference_result_and_information_codecs_reject_wrong_shapes(self):
        from copy import deepcopy
        from quorune.spell_mana_conditions import ManaSpentCondition,mana_type_from_reference
        from quorune.resolution_conditions import validate_resolution_condition_instruction
        from quorune.target_controller_information import TargetControllerInformation,controller_reference_index
        condition=ManaSpentCondition('R',3)
        self.assertEqual(condition,ManaSpentCondition.from_dict(condition.to_dict()))
        for raw in ({**condition.to_dict(),'minimum':True},{**condition.to_dict(),'mana_type':'S'},
                {**condition.to_dict(),'schema_version':True},{**condition.to_dict(),'extra':1}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):ManaSpentCondition.from_dict(raw)
        with self.assertRaises(ValueError):mana_type_from_reference({**condition.reference(),'schema_version':True})
        session=self.session(748250);source=self.add(session.engine,'Slaying Fire',zone='hand')
        program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve']
        wrapper=deepcopy(program.effects[0]);validate_resolution_condition_instruction(wrapper)
        for raw in ({**wrapper,'schema_version':True},{**wrapper,'cast_fact':True},{**wrapper,'cast_fact':-1},
                {**wrapper,'cast_fact':ManaSpentCondition('U',3).reference()},
                {**wrapper,'cast_fact':{**condition.reference(),'schema_version':True}},
                {**wrapper,'effects':[wrapper]}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):validate_resolution_condition_instruction(raw)
        info=TargetControllerInformation('target',2,'C');self.assertEqual(info,TargetControllerInformation.from_dict(info.to_dict()))
        for raw in ({**info.to_dict(),'zone_change_counter':True},{**info.to_dict(),'schema_version':True},{**info.to_dict(),'extra':0}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):TargetControllerInformation.from_dict(raw)
        with self.assertRaises(ValueError):controller_reference_index({'kind':'target_controller_information','schema_version':1,'index':True})

    def test_verified_authority_withdrawals_and_stripped_runtime_dependencies_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        path=ROOT/'quorune/rules/capability-registry.json';baseline=json.loads(path.read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        known=set(discover_tests(ROOT).values())
        def verified(raw):
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Searing Barrage'),capability_registry=verified(baseline),capability_profile='commander_review').status)
        for cap in ('resolution.effect.paid_mana_condition','casting.payment.mana_unit_counts','resolution.effect.public_condition','resolution.target_controller_information'):
            raw=json.loads(path.read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn paid-result authority'])
            ir=compile_oracle_card(self.db.lookup('Searing Barrage'),capability_registry=verified(raw),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(748260);source=self.add(session.engine,'Searing Barrage',zone='hand')
        program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve']
        for cap in ('resolution.effect.paid_mana_condition','casting.payment.mana_unit_counts','resolution.target_controller_information'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
