"""CR105.2/611.2c/611.3: current negative colors and locked resolution sets."""
from __future__ import annotations

from dataclasses import replace
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.object_predicate import ObjectQueryError,ObjectQuerySpec
from quorune.object_query import ObjectQueryResult,object_matches_query
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.oracle_ir import compile_oracle_card
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_scalar_mill_wording as controls


class PublicNegativeColorModelTests(unittest.TestCase):
    def test_withdrawn_negative_color_query_capability_keeps_original_residual(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='state_query.permanent.negative_color').update(
            status='blocked',blockers=['withdrawn current negative-color authority'])
        ir=compile_oracle_card(record('Nonwhite creatures get -1/-1.','Enchantment'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_negative_color_matches_all_color_subsets_and_preserves_legacy_shapes(self):
        row=ObjectQueryResult(object_id='object',ref='C1',printed_name='Generic',owner='A',
            controller='B',zone='battlefield',types=('creature',),subtypes=(),supertypes=(),
            colors=(),keywords=())
        for color in 'WUBRG':
            query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),colors_none=(color,))
            self.assertEqual(query,ObjectQuerySpec.from_dict(query.to_dict()))
            for size in range(6):
                for colors in itertools.combinations('WUBRG',size):
                    self.assertEqual(color not in colors,object_matches_query(replace(row,colors=colors),query))
        for version in range(1,9):
            raw=ObjectQuerySpec(_serialization_version=version).to_dict()
            self.assertNotIn('colors_none',raw)
            self.assertEqual(raw,ObjectQuerySpec.from_dict(raw).to_dict())

    def test_negative_color_rejects_unknown_conflicting_and_unowned_domains(self):
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),colors_none=('W',))
        for changes in ({'colors_none':('C',)},{'colors_none':('W','W')},
                {'colors_all':('W',)},{'zones':('hand',)},{'zones':('library',)},
                {'_serialization_version':True},{'_serialization_version':10}):
            with self.assertRaises(ObjectQueryError):replace(query,**changes)
        with self.assertRaises(ObjectQueryError):ObjectQuerySpec.from_dict({**query.to_dict(),'future':True})
        legacy=ObjectQuerySpec(zones=('battlefield',)).to_dict()
        with self.assertRaises(ObjectQueryError):ObjectQuerySpec.from_dict({**legacy,'colors_none':['W']})

    def test_public_grammar_keeps_color_controller_and_nearby_boundaries(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        from quorune.compiler.continuous_templates import fixed_query_keyword_grant_handler
        for name,color in (('white','W'),('blue','U'),('black','B'),('red','R'),('green','G')):
            relation,query,other=fixed_characteristic_battlefield_query_subject('Other non'+name+' creatures your opponents control')
            self.assertEqual('source_opponents',relation);self.assertTrue(other)
            self.assertEqual((color,),query.colors_none)
            self.assertIsNotNone(fixed_query_keyword_grant_handler('Non'+name+' creatures have vigilance.'))
        for text in ('Nonwhite or blue creatures','Noncolorless creatures','Nonwhite creatures of your choice',
                'Creatures without flying','Creatures you control with exactly two counters on it'):
            self.assertIsNone(fixed_characteristic_battlefield_query_subject(text),text)

    def test_specialized_consumers_reject_unowned_negative_color_fields(self):
        from quorune.activation_condition_model import _closed_public_query
        from quorune.activation_cost_modifiers import FixedActivationCostModifierSpec
        from quorune.fixed_damage_set_model import PermanentDamageGroup
        from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope
        from quorune.object_predicate import validate_chosen_damage_source_predicate
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),colors_none=('W',))
        self.assertFalse(_closed_public_query(query))
        for constructor in (
                lambda value:FixedActivationCostModifierSpec(-1,predicate=value),
                lambda value:PermanentDamageGroup(value),
                lambda value:CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,query=value),
                lambda value:validate_chosen_damage_source_predicate(replace(value,known_to_actor=True))):
            constructor(replace(query,colors_none=()))
            with self.assertRaises(ValueError):constructor(query)


class PublicNegativeColorRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ScalarMillWordingRuntimeTests.session
    add=controls.ScalarMillWordingRuntimeTests.add
    prepare=controls.ScalarMillWordingRuntimeTests.prepare
    checkpoint=controls.ScalarMillWordingRuntimeTests.checkpoint
    cast=controls.ScalarMillWordingRuntimeTests.cast
    resolve=controls.ScalarMillWordingRuntimeTests.resolve
    replay=controls.ScalarMillWordingRuntimeTests.replay
    until=controls.ScalarMillWordingRuntimeTests.until
    binding=controls.ScalarMillWordingRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'negative-color.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-negative-color-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Negative color',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def board(self,session,*,colorless_seat='C'):
        return [self.add(session.engine,'Generic Negative '+color,seat=seat)
            for color,seat in (('White','A'),('White Blue','B'),('Colorless',colorless_seat),('Green','D'))]

    def stats(self,session,cards):
        return [(session.engine._numeric_stat(c.object_id,'power'),
            session.engine._numeric_stat(c.object_id,'toughness')) for c in cards]

    def private_replay(self,session,private):
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def test_original_crovax_cast_applies_negative_color_and_keeps_white_sibling(self):
        session=self.session(72001001);cards=self.board(session)
        source=self.add(session.engine,'Crovax, Ascendant Hero',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':2,'C':4});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a.get('card')==source.ref)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',source);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([(5,5),(5,5),(3,3),(3,3)],self.stats(session,cards))
        self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
        self.private_replay(session,private)

    def test_original_holy_light_locks_nonwhite_incarnations_at_actual_resolution(self):
        session=self.session(72001002);cards=self.board(session)
        source=self.add(session.engine,'Holy Light',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([(4,4),(4,4),(3,3),(3,3)],self.stats(session,cards))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_omitting_negative_color_match_is_killed_by_fresh_native_original(self):
        method='test_original_crovax_cast_applies_negative_color_and_keeps_white_sibling'
        from quorune.object_query import object_matches_query as original
        def ignore(row,spec):return original(row,replace(spec,colors_none=()))
        with patch('quorune.continuous_effects.object_matches_query',side_effect=ignore):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_executable_negative_color_query_rejects_undeclared_capability(self):
        session=self.session(72001090);source=self.add(session.engine,'Crovax, Ascendant Hero')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(h.get('condition',{}).get('predicate',{}).get('colors_none') for h in p.handlers))
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict'])
        cap='state_query.permanent.negative_color';dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
        missing=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_static_original_rechecks_color_and_source_departure_after_actual_casts(self):
        session=self.session(72001003);cards=self.board(session)
        source=self.add(session.engine,'Crovax, Ascendant Hero')
        blue=self.add(session.engine,'Generic Negative Blue Creatures',zone='hand')
        removal=self.add(session.engine,'Unsummon',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':2});self.checkpoint(session)
        for card in (source,blue,removal):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.assertEqual([(5,5),(5,5),(3,3),(3,3)],self.stats(session,cards))
        self.cast(session,'A',blue);self.assertIsNone(self.resolve(session))
        self.assertEqual([(3,3)]*4,self.stats(session,cards))
        self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',removal,[source.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual([(4,4)]*4,self.stats(session,cards))
        self.private_replay(session,private)

    def test_resolution_set_ignores_later_color_and_entrants_and_returned_incarnation(self):
        session=self.session(72001004);cards=self.board(session,colorless_seat='A')
        spell=self.add(session.engine,'Holy Light',zone='hand')
        blue=self.add(session.engine,'Generic Negative Blue Creatures',zone='hand')
        late=self.add(session.engine,'Generic Negative Green',zone='hand',ref='late-green-body')
        removal=self.add(session.engine,'Unsummon',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':1,'U':2,'C':10});self.checkpoint(session)
        for card in (spell,blue,late,removal):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.cast(session,'A',spell);self.assertIsNone(self.resolve(session))
        self.assertEqual([(4,4),(4,4),(3,3),(3,3)],self.stats(session,cards))
        self.cast(session,'A',blue);self.assertIsNone(self.resolve(session))
        self.assertEqual([(4,4),(4,4),(3,3),(3,3)],self.stats(session,cards))
        self.cast(session,'A',late);self.assertIsNone(self.resolve(session))
        self.assertEqual([(4,4)],self.stats(session,[late]))
        affected=cards[2]
        old_incarnation=affected.logical_object_id
        self.cast(session,'A',removal,[affected.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[affected.object_id].zone)
        self.assertEqual(4,session.engine._numeric_stat(affected.object_id,'power'))
        returned=session.state.cards[affected.object_id]
        self.assertNotEqual(old_incarnation,returned.logical_object_id)
        self.cast(session,'A',returned);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[affected.object_id].zone)
        session=self.replay(session,load=True)
        self.assertEqual([(4,4),(4,4),(4,4),(3,3)],self.stats(session,cards))
        self.private_replay(session,private)

    def test_original_evincar_keeps_black_multicolor_sibling_and_penalizes_colorless(self):
        session=self.session(72001005);cards=self.board(session)
        black=self.add(session.engine,'Generic Negative Black',seat='B')
        multi=self.add(session.engine,'Generic Negative Black Green',seat='D')
        source=self.add(session.engine,'Ascendant Evincar',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':2,'C':4});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertEqual([(3,3)]*4,self.stats(session,cards))
        self.assertEqual([(5,5),(5,5)],self.stats(session,[black,multi]))
        self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
        self.private_replay(session,private)

    def test_original_festergloom_excludes_black_and_multicolored_black_creatures(self):
        session=self.session(72001006);cards=self.board(session)
        black=self.add(session.engine,'Generic Negative Black',seat='B')
        multi=self.add(session.engine,'Generic Negative Black Green',seat='D')
        source=self.add(session.engine,'Festergloom',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertEqual([(3,3)]*4,self.stats(session,cards))
        self.assertEqual([(4,4),(4,4)],self.stats(session,[black,multi]))
        self.private_replay(session,private)

    def test_original_battle_frenzy_preserves_both_controller_scoped_results(self):
        session=self.session(72001007)
        green=self.add(session.engine,'Generic Negative Green')
        white=self.add(session.engine,'Generic Negative White')
        enemy=self.add(session.engine,'Generic Negative Green',seat='B',ref='enemy-green')
        source=self.add(session.engine,'Battle Frenzy',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertEqual([(5,5),(5,4),(4,4)],self.stats(session,[green,white,enemy]))
        self.private_replay(session,private)

    def test_original_gloomdrifter_entry_uses_threshold_before_creating_locked_set(self):
        for count in (6,7):
            session=self.session(72001010+count);cards=self.board(session)
            black=self.add(session.engine,'Generic Negative Black',seat='B')
            source=self.add(session.engine,'Gloomdrifter',zone='hand')
            for index in range(count):self.add(session.engine,'Generic Bound Growth',zone='graveyard',ref='threshold-'+str(index))
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'B':1,'C':3});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
            self.assertEqual([(4,4) if count==6 else (2,2)]*4,self.stats(session,cards))
            self.assertEqual([(4,4)],self.stats(session,[black]))
            self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
            self.private_replay(session,private)
