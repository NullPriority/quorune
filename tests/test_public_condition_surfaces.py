from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.engine import TURN_STEPS
from quorune.haste import summoning_sickness_prohibits_attack
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.compiler.public_condition_surfaces import fixed_condition_subject_extension
from quorune.oracle_ir import compile_oracle_card
from quorune.continuous_conditions import FixedPublicStateConditionSpec
from quorune.object_predicate import ObjectQuerySpec
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as spree


class PublicConditionSurfaceCompilerTests(unittest.TestCase):
    def test_permanent_card_type_intersections_and_passive_history_boolean_terms(self):
        for phrase, types in (('a land creature', ('creature','land')),
                              ('an artifact creature', ('artifact','creature')),
                              ('an enchantment creature', ('creature','enchantment'))):
            with self.subTest(phrase=phrase):
                condition = fixed_public_state_condition('you control '+phrase, source_name='Surface Observer')
                self.assertIsNotNone(condition)
                self.assertEqual(types, condition.quantity.query.types_all)
                self.assertEqual((), condition.quantity.query.types_any)
                self.assertEqual(condition.to_dict(), FixedPublicStateConditionSpec.from_dict(condition.to_dict()).to_dict())
        condition = fixed_public_state_condition(
            'you control a land creature or a land entered the battlefield under your control this turn',
            source_name='Surface Observer')
        self.assertIsNotNone(condition)
        self.assertEqual('any', condition.kind.value)
        self.assertEqual(('creature','land'), condition.terms[0].quantity.query.types_all)
        self.assertEqual('permanent_entered', condition.terms[1].history_query.event)
        self.assertEqual(('land',), condition.terms[1].history_query.types_all)
        ordinary_union = fixed_public_state_condition(
            'you control an artifact or an enchantment', source_name='Surface Observer')
        self.assertIsNotNone(ordinary_union)
        self.assertEqual('any', ordinary_union.kind.value)
        self.assertEqual((('artifact',),('enchantment',)), tuple(
            term.quantity.query.types_all for term in ordinary_union.terms))
        for text in ('creature creature','instant creature','land permanent','land mystery'):
            self.assertIsNone(fixed_condition_subject_extension(text))
        self.assertIsNone(fixed_public_state_condition(
            'you control a land creature or an unrepresented event happened this turn',source_name='Surface Observer'))

    def test_aliases_reuse_exact_existing_typed_conditions_and_legacy_codecs(self):
        pairs = (("there's a Lesson card in your graveyard", 'there is a Lesson card in your graveyard'),
            ('there’s a Lesson card in your graveyard', 'there is a Lesson card in your graveyard'),
            ('this Aura entered this turn', 'this enchantment entered this turn'),
            ('you had another creature enter the battlefield under your control this turn',
                'another creature entered the battlefield under your control this turn'))
        for source, canonical in pairs:
            with self.subTest(source=source):
                actual = fixed_public_state_condition(source, source_name='Surface Observer')
                expected = fixed_public_state_condition(canonical, source_name='Surface Observer')
                self.assertIsNotNone(actual)
                self.assertEqual(expected.to_dict(), actual.to_dict())
                self.assertEqual(actual.to_dict(), FixedPublicStateConditionSpec.from_dict(actual.to_dict()).to_dict())

    def test_colorless_nontoken_and_nonbasic_queries_preserve_every_qualifier(self):
        cases = (('you control another colorless creature', 'colorless', True),
            ('an opponent controls a nontoken white permanent', 'token', False),
            ('an opponent controls a nonbasic land', 'excluded_supertypes', ('basic',)))
        for text, field, expected in cases:
            with self.subTest(text=text):
                condition = fixed_public_state_condition(text, source_name='Surface Observer')
                self.assertIsNotNone(condition)
                self.assertEqual(expected, getattr(condition.quantity.query, field))
                if field == 'token':
                    self.assertEqual(('W',), condition.quantity.query.colors_all)
                    self.assertEqual((), condition.quantity.query.types_all)
                if field == 'colorless':
                    self.assertTrue(condition.quantity.exclude_source)
                    self.assertEqual(('creature',), condition.quantity.query.types_all)
                if field == 'excluded_supertypes':self.assertEqual(('land',),condition.quantity.query.types_all)
        for text in ('nontoken white and blue permanent','nonbasic creature','colorless cards in your hand'):
            self.assertIsNone(fixed_condition_subject_extension(text))

    def test_source_spans_unsupported_neighbors_and_surface_omissions(self):
        text="This creature gets +1/+2 as long as an opponent controls a nonbasic land."
        ir=compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(text,node.text)
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        method='test_nonbasic_source_keeps_its_exact_basic_supertype_exclusion'
        # The generic quantity owner retains the complete qualifier when the
        # narrower surface extension is absent.
        with patch('quorune.compiler.public_condition_surfaces.fixed_condition_subject_extension',return_value=None):
            getattr(type(self)(method),method)()
        original=ObjectQuerySpec.to_dict
        def omitted(query,*args,**kwargs):
            payload=original(query,*args,**kwargs)
            if 'basic' in query.excluded_supertypes:
                payload={**payload,'excluded_supertypes':[]}
            return payload
        with patch.object(ObjectQuerySpec,'to_dict',new=omitted):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        for bad in (text.replace('nonbasic land','nonbasic creature'),
                    text.replace('nonbasic land','stickered permanent'),
                    text.replace('+1/+2','+X/+X')):
            self.assertNotEqual('exact',compile_oracle_card(record(bad,'Creature'),
                capability_registry=load_default_capability_registry()).status)

    def test_nonbasic_source_keeps_its_exact_basic_supertype_exclusion(self):
        text='This creature gets +1/+2 as long as an opponent controls a nonbasic land.'
        ir=compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        queries=[]
        def visit(value):
            if isinstance(value,Mapping):
                if tuple(value.get('types_all',()))==('land',) and 'excluded_supertypes' in value:
                    queries.append(value)
                for child in value.values():visit(child)
            elif isinstance(value,(list,tuple)):
                for child in value:visit(child)
        for face in ir.faces:
            for node in face.nodes:visit(node.handlers)
        self.assertTrue(queries)
        self.assertTrue(all(tuple(query['excluded_supertypes'])==('basic',) for query in queries))


class PublicConditionSurfaceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'public-condition-surfaces.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/public-condition-surface-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public condition surfaces', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast(self, session, card, targets=(), actor='A'):
        result=session.act('pilot:'+actor,{'action_id':'cast:'+card.ref,'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def stats(self, session, card):
        current=session.state.cards[card.object_id]
        return tuple(session.engine._effective_card_data(current)[key] for key in ('power','toughness'))

    def produce_mana(self, session, land, actor='A'):
        actions=session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions
            if row.get('action')=='activate' and row.get('source')==land.ref)
        result=session.act('pilot:'+actor,{'action_id':action['id']})
        self.assertTrue(result.ok,result.summary)

    def test_actual_wrestlers_distinguish_one_land_creature_from_split_types_and_land_history(self):
        session = self.session(61135108)
        source = self.add(session.engine, 'Earth Rumble Wrestlers')
        self.assert_closed(session, source)
        from quorune.card_programs import bind_semantic_program_runtime
        conditional = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
                           if program.handlers)
        history_capability = 'condition.history.fixed_public_query'
        self.assertIn(history_capability, conditional.capability_dependencies)
        dependencies = tuple(cap for cap in conditional.capability_dependencies if cap != history_capability)
        stripped = replace(conditional, coverage=[], capability_dependencies=dependencies,
                           capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+history_capability, binding['blockers'])
        self.add(session.engine, 'Generic Bound Body', ref='creature-only-negative')
        self.add(session.engine, 'Forest', ref='land-only-negative')
        foreign = self.add(session.engine, 'Dryad Arbor', seat='B', ref='foreign-land-creature-negative')
        self.assertEqual(('3','4'), self.stats(session, source))
        self.assertNotIn('trample', session.engine._combat_keywords(source))
        living_land = self.add(session.engine, 'Dryad Arbor', ref='own-land-creature-positive')
        self.assert_closed(session, living_land)
        self.assertEqual(('4','4'), self.stats(session, source))
        self.assertIn('trample', session.engine._combat_keywords(source))
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        land = self.add(session.engine, 'Forest', zone='hand', ref='actual-history-land')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='condition-private-negative')
        self.prepare(session, mana={'U':2})
        self.checkpoint(session)
        self.reject(session, 'cast:'+bounce.ref, 'B', targets=[living_land.ref], pay='auto')
        self.cast(session, bounce, (living_land.ref,))
        self.assertEqual('hand', session.state.cards[living_land.object_id].zone)
        self.assertEqual(('3','4'), self.stats(session, source))
        self.assertNotIn('trample', session.engine._combat_keywords(session.state.cards[source.object_id]))
        session = self.replay(session, load=True)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(row for row in actions if row['id']=='play-land:'+land.ref)
        self.reject(session, offered['id'], 'D')
        result = session.act('pilot:A', {'action_id':offered['id']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
        self.assertEqual(('4','4'), self.stats(session, source))
        self.assertIn('trample', session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertEqual('battlefield', session.state.cards[foreign.object_id].zone)
        turn = session.state.turn_sequence
        for _ in range(64):
            if session.state.turn_sequence != turn:
                break
            decision = session.state.pending_decision
            principal = session.pending_principals()[0]
            if decision.kind == 'priority': command={'action_id':'pass'}
            elif decision.kind == 'combat.attackers': command={'action_id':'attack','attackers':{}}
            elif decision.kind == 'combat.blockers': command={'action_id':'block','blocks':{}}
            elif decision.kind == 'cleanup.discard':
                ctx=session.packet(principal,full=True)['decision']['ctx']
                command={'a':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
            else: self.fail('Unexpected public-condition decision '+decision.kind)
            result=session.act(principal,command)
            self.assertTrue(result.ok,result.summary)
        self.assertGreater(session.state.turn_sequence, turn)
        self.assertEqual(('3','4'), self.stats(session, source))
        self.assertNotIn('trample', session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertIn('reach', session.engine._combat_keywords(session.state.cards[source.object_id]))
        for actor in 'ABD': self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+actor,full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C',full=True)))
        self.replay(session, load=True)

    def test_actual_skyshroud_elite_distinguishes_basic_supertype_from_basic_land_types(self):
        session = self.session(61135101)
        source = self.add(session.engine, 'Skyshroud Elite', ref='actual-nonbasic-observer')
        self.assert_closed(session, source)
        self.add(session.engine, 'Tundra', ref='own-nonbasic-control')
        self.add(session.engine, 'Wastes', seat='B', ref='basic-no-land-subtypes')
        self.assertEqual(('1','1'),tuple(session.engine._effective_card_data(source)[k]
            for k in ('power','toughness')))
        dual=self.add(session.engine, 'Tundra', seat='B', ref='nonbasic-with-basic-types')
        self.assertEqual(('2','3'),tuple(session.engine._effective_card_data(source)[k]
            for k in ('power','toughness')))
        spell=self.add(session.engine,'Boomerang',zone='hand',ref='return-opponent-nonbasic')
        self.assert_closed(session,spell)
        self.prepare(session,mana={'U':2});self.checkpoint(session)
        self.reject(session,'cast:'+spell.ref,'C',targets=[dual.ref],pay='auto')
        self.cast(session,spell,(dual.ref,))
        self.assertEqual('hand',session.state.cards[dual.object_id].zone)
        self.assertEqual(('1','1'),self.stats(session,source))
        self.replay(session,load=True)

    def test_actual_beasts_excludes_white_tokens_and_own_permanents_after_native_bounce(self):
        session=self.session(61135102)
        source=self.add(session.engine,'Beasts of Bogardan',ref='nontoken-white-observer')
        self.assert_closed(session,source)
        self.add(session.engine,'Elite Vanguard',ref='own-white-negative')
        session.engine.create_token('B',name='White token negative',characteristics={
            'type_line':'Token Creature — Soldier','colors':['W'],'power':'1','toughness':'1','keywords':[]})
        self.assertEqual(('3','3'),self.stats(session,source))
        other=self.add(session.engine,'Elite Vanguard',seat='C',ref='foreign-white-positive')
        self.assertEqual(('4','4'),self.stats(session,source))
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='return-nontoken-white')
        self.prepare(session,mana={'U':1});self.checkpoint(session)
        self.cast(session,bounce,(other.ref,))
        self.assertEqual(('3','3'),self.stats(session,source))
        self.assertIn('protection',str(session.engine._effective_card_data(session.state.cards[source.object_id])['ability_fragments']).lower())
        self.replay(session,load=True)

    def test_actual_lesson_conditions_use_own_public_graveyard_and_native_exile_with_private_draw(self):
        session=self.session(61135103)
        aang=self.add(session.engine,'Aang, A Lot to Learn',ref='lesson-vigilance-observer')
        flyer=self.add(session.engine,'First-Time Flyer',ref='lesson-stats-observer')
        for source in (aang,flyer):self.assert_closed(session,source)
        self.add(session.engine,'Elemental Summoning',seat='B',zone='graveyard',ref='foreign-lesson-negative')
        own=self.add(session.engine,'Elemental Summoning',zone='hand',ref='own-lesson-location')
        self.add(session.engine,'Wandering Ones',zone='graveyard',ref='nonlesson-negative')
        self.assertNotIn('vigilance',session.engine._combat_keywords(aang))
        self.assertEqual(('1','2'),self.stats(session,flyer))
        session.engine.move_card(own.object_id,'graveyard',log=False)
        self.assertIn('vigilance',session.engine._combat_keywords(aang))
        self.assertEqual(('2','3'),self.stats(session,flyer))
        spell=self.add(session.engine,'Cremate',seat='B',zone='hand',ref='exile-own-lesson')
        self.assert_closed(session,spell)
        self.prepare(session,actor='B',active='A',mana={'B':1});self.checkpoint(session)
        before=set(session.state.players['B'].zones['hand'])
        self.reject(session,'cast:'+spell.ref,'D',targets=[own.ref],pay='auto')
        self.cast(session,spell,(own.ref,),actor='B')
        drawn,=set(session.state.players['B'].zones['hand'])-before
        ref=session.state.cards[drawn].ref
        self.assertIn(ref,json.dumps(session.packet('pilot:B',full=True)))
        for actor in 'ACD':self.assertNotIn(ref,json.dumps(session.packet('pilot:'+actor,full=True)))
        self.assertNotIn('vigilance',session.engine._combat_keywords(session.state.cards[aang.object_id]))
        self.assertEqual(('1','2'),self.stats(session,flyer))
        self.replay(session,load=True)

    def test_actual_other_entry_conditions_ignore_self_and_retain_departed_entry_through_reload(self):
        session=self.session(61135104)
        stag=self.add(session.engine,'Saddled Rimestag',zone='hand',ref='another-entry-stag')
        elk=self.add(session.engine,'Bellowing Elk',zone='hand',ref='another-entry-elk')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='return-earlier-creature')
        island=self.add(session.engine,'Island',ref='entry-history-response-mana')
        for source in (stag,elk,bounce):self.assert_closed(session,source)
        self.prepare(session,mana={'C':4,'G':2,'U':1});self.checkpoint(session)
        self.cast(session,stag)
        self.assertEqual(('2','2'),self.stats(session,stag))
        session=self.replay(session,load=True)
        self.cast(session,elk)
        self.assertEqual(('4','4'),self.stats(session,stag))
        self.assertTrue({'trample','indestructible'}<=session.engine._combat_keywords(session.state.cards[elk.object_id]))
        self.produce_mana(session,island)
        self.cast(session,bounce,(stag.ref,))
        self.assertEqual('hand',session.state.cards[stag.object_id].zone)
        self.assertTrue({'trample','indestructible'}<=session.engine._combat_keywords(session.state.cards[elk.object_id]))
        self.replay(session,load=True)

    def test_old_source_incarnation_counts_as_another_after_native_return_and_recast(self):
        session=self.session(61135105)
        stag=self.add(session.engine,'Saddled Rimestag',zone='hand',ref='return-recast-stag')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='return-own-incarnation')
        self.prepare(session,mana={'C':2,'G':2,'U':1});self.checkpoint(session)
        self.cast(session,stag)
        original=session.state.cards[stag.object_id].logical_object_id
        self.assertEqual(('2','2'),self.stats(session,stag))
        self.cast(session,bounce,(stag.ref,))
        self.cast(session,stag)
        self.assertNotEqual(original,session.state.cards[stag.object_id].logical_object_id)
        self.assertEqual(('4','4'),self.stats(session,stag))
        self.replay(session,load=True)

    def test_actual_aggressor_uses_another_current_colorless_creature_for_native_haste_attack(self):
        session=self.session(61135106)
        source=self.add(session.engine,'Eldrazi Aggressor',zone='hand',ref='actual-colorless-haste')
        self.add(session.engine,'Wastes',ref='colorless-noncreature-negative')
        self.add(session.engine,'Ornithopter',seat='B',ref='foreign-colorless-negative')
        support=self.add(session.engine,'Ornithopter',zone='hand',ref='own-colorless-support')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='remove-haste-after-declaration')
        island=self.add(session.engine,'Island',ref='combat-response-mana')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'R':1,'U':1});self.checkpoint(session)
        self.cast(session,source)
        current=session.state.cards[source.object_id]
        self.assertEqual([],session.engine._effective_card_data(current)['colors'])
        self.assertNotIn('haste',session.engine._combat_keywords(current))
        self.assertTrue(summoning_sickness_prohibits_attack(session.engine,current))
        self.cast(session,support)
        self.assertIn('haste',session.engine._combat_keywords(current))
        for _ in range(24):
            if session.state.pending_decision.kind=='combat.attackers':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual('combat.attackers',session.state.pending_decision.kind)
        self.reject(session,'attack','B',attackers={source.ref:'B'})
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.produce_mana(session,island)
        self.cast(session,bounce,(support.ref,))
        current=session.state.cards[source.object_id]
        self.assertNotIn('haste',session.engine._combat_keywords(current))
        self.assertEqual('B',current.attacking)
        self.replay(session,load=True)

    def test_actual_rescue_reads_aura_entry_turn_and_expires_before_native_shock(self):
        session=self.session(61135107)
        body=self.add(session.engine,'Wandering Ones',ref='old-enchanted-creature')
        source=self.add(session.engine,"Shardmage's Rescue",zone='hand',ref='new-aura-entry-source')
        shock=self.add(session.engine,'Shock',seat='B',zone='hand',ref='test-entry-turn-hexproof')
        mountain=self.add(session.engine,'Mountain',seat='B',ref='next-turn-shock-mana')
        self.assert_closed(session,source)
        self.prepare(session,mana={'W':1})
        e=session.engine;e.state.players['B'].mana_pool['R']=1
        e.state.phase_index=TURN_STEPS.index(('ending','end_step'));e.state.phase='ending';e.state.step='end_step'
        self.checkpoint(session)
        self.cast(session,source,(body.ref,))
        self.assertIn('hexproof',session.engine._combat_keywords(session.state.cards[body.object_id]))
        turn=session.state.turn_sequence
        result=session.act('pilot:A',{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.reject(session,'cast:'+shock.ref,'B',targets=[body.ref],pay='auto')
        for _ in range(12):
            if session.state.turn_sequence!=turn:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertGreater(session.state.turn_sequence,turn)
        self.assertNotIn('hexproof',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertEqual(('2','2'),self.stats(session,body))
        self.produce_mana(session,mountain,actor='B')
        self.cast(session,shock,(body.ref,),actor='B')
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_dropping_basic_supertype_exclusion_is_killed_by_the_actual_native_control(self):
        original=ObjectQuerySpec.to_dict
        def remove_exclusion(query,*args,**kwargs):
            payload=original(query,*args,**kwargs)
            if 'basic' in query.excluded_supertypes:
                payload={**payload,'excluded_supertypes':[]}
            return payload
        method='test_actual_skyshroud_elite_distinguishes_basic_supertype_from_basic_land_types'
        with patch.object(ObjectQuerySpec,'to_dict',new=remove_exclusion):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()


if __name__ == '__main__':
    unittest.main()
