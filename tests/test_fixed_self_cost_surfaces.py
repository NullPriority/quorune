from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.compiler.cast_cost_modifier_templates import self_spell_cost_reduction_handler
from quorune.self_cast_reductions import SelfSpellCostReductionSpec,cast_reduction_multiplier
from quorune.semantic_runtime.cast_costs import SelfSpellCostReductionHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.continuous_effect_model import Layer,ContinuousOperation
from quorune.continuous_effect_state import (
    ResolutionEffectSource,ResolutionContinuousComponent,create_resolution_continuous_effect_components,
)
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree
import test_public_condition_surfaces as surfaces


CAP='casting.cost.self_public_reduction'
TEXT='This spell costs {1} less to cast if you control a Desert.'
CASES=(TEXT,'This spell costs {2} less to cast if there is a land card in your graveyard.',
    'This spell costs {8} less to cast if you have eight or more instant and/or sorcery cards in your graveyard.',
    'This spell costs {1} less to cast for each noncreature, nonland card in your graveyard.',
    "This spell costs {2}{U} less to cast if you've cast an instant or sorcery spell this turn.")


class FixedSelfCostSurfaceCompilerTests(unittest.TestCase):
    def test_complete_source_spans_reuse_typed_threshold_count_and_colored_descriptors(self):
        for text in CASES:
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes
                self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end))
                self.assertIn(CAP,node.capability_dependencies)
                descriptor,=node.handlers
                spec=SelfSpellCostReductionHandler().validate(descriptor)
                self.assertEqual(spec.to_dict(),SelfSpellCostReductionSpec.from_dict(spec.to_dict()).to_dict())
        term=self_spell_cost_reduction_handler(CASES[-1])[1]['reduction']['terms'][0]
        self.assertEqual({'GENERIC':2,'U':1},dict(term['reduction']))

    def test_current_graveyard_card_qualifiers_and_desert_land_type_are_preserved(self):
        for text in CASES[1:4]:
            term=SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(text)[1]).terms[0]
            for query in term.metric.queries:
                self.assertIs(query.query.token,False)
        metric=SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(TEXT)[1]).terms[0].metric
        self.assertEqual(('land',),metric.queries[0].query.types_all)
        self.assertEqual(('desert',),metric.queries[0].query.subtypes_all)
        spec=SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(CASES[3])[1])
        self.assertEqual(('creature','land'),spec.terms[0].metric.queries[0].query.excluded_types)

    def test_counted_graveyard_qualities_normalize_capture_whitespace_and_card_types(self):
        for quality,field,expected in (('Lesson','subtypes_all',('lesson',)),
                ('creature','types_all',('creature',)),
                ('instant and/or sorcery','types_any',('instant','sorcery'))):
            condition=fixed_public_state_condition('there are three or more '+quality+' cards in your graveyard',source_name='Fixture')
            self.assertIsNotNone(condition)
            self.assertEqual(expected,getattr(condition.quantity.query,field))
            self.assertIs(condition.quantity.query.token,False)

    def test_unsupported_costs_malformed_descriptors_and_omitted_capability_or_producer_fail_closed(self):
        for text in (TEXT.replace('{1}','{X}'),TEXT.replace('{1}','{U/P}'),
                TEXT.replace('you control a Desert','it targets a creature with power X or greater'),
                TEXT.replace('you control a Desert','you reveal a card'),
                CASES[3].replace('noncreature, nonland','noncreature or nonland')):
            with self.subTest(unsupported=text):
                self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),
                    capability_registry=load_default_capability_registry()).status)
        descriptor=self_spell_cost_reduction_handler(CASES[-1])[1]
        for field,value in (('schema_version',True),('unknown',1),('event','damage')):
            with self.subTest(field=field),self.assertRaises(SemanticNodeError):
                SelfSpellCostReductionHandler().validate({**descriptor,field:value})
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Self cost omission'])
        self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Creature'),
            capability_registry=CapabilityRegistry(raw)).status)
        with patch('quorune.compiler.cast_cost_modifier_templates._public_condition_metric',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Creature'),
                capability_registry=load_default_capability_registry()).status)


class FixedSelfCostSurfaceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=spree.SpreeModeCostRuntimeTests.session
    add=spree.SpreeModeCostRuntimeTests.add
    prepare=spree.SpreeModeCostRuntimeTests.prepare
    checkpoint=spree.SpreeModeCostRuntimeTests.checkpoint
    replay=spree.SpreeModeCostRuntimeTests.replay
    reject=spree.SpreeModeCostRuntimeTests.reject
    resolve=spree.SpreeModeCostRuntimeTests.resolve
    assert_closed=spree.SpreeModeCostRuntimeTests.assert_closed
    cast=surfaces.PublicConditionSurfaceRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'fixed-self-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-self-cost-surface-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed self cost surfaces',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def cost(self,session,card,actor='A'):
        rows=session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in rows if row['id']=='cast:'+card.ref)
        return next(option['requirements'] for option in offered['cost_options'] if option['id']=='normal')

    def offered(self,session,card,actor='A',prefix='cast:'):
        return any(row['id']==prefix+card.ref for row in
            session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions'])

    def test_all_five_original_cards_have_complete_trusted_bindings(self):
        session=self.session(60133001)
        for name in ('Cactarantula','Igneous Elemental','Octavia, Living Thesis','Serpent of the Pass','Stormwing Entity'):
            with self.subTest(name=name):self.assert_closed(session,self.add(session.engine,name,zone='hand'))

    def test_current_graveyard_card_queries_exclude_tokens_and_legacy_descriptors_still_decode(self):
        session=self.session(60133010)
        token=self.add(session.engine,'Island',zone='graveyard',ref='intermediate-token-card-negative')
        token.is_token=True
        specification=SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(CASES[1])[1])
        metric=specification.terms[0].metric
        before=authoritative_state_hash(session.state)
        self.assertEqual(0,cast_reduction_multiplier(session.engine,'A',metric))
        legacy=replace(metric,queries=tuple(replace(q,query=replace(q.query,token=None)) for q in metric.queries))
        self.assertEqual(1,cast_reduction_multiplier(session.engine,'A',legacy))
        legacy_spec=replace(specification,terms=tuple(replace(t,metric=legacy) for t in specification.terms))
        self.assertEqual(legacy_spec.to_dict(),SelfSpellCostReductionSpec.from_dict(legacy_spec.to_dict()).to_dict())
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_foreign_cast_history_does_not_enable_the_carrying_spells_colored_discount(self):
        session=self.session(60133011)
        spell=self.add(session.engine,'Stormwing Entity',zone='hand',ref='foreign-history-self-cost')
        prior=self.add(session.engine,'Giant Growth',seat='B',zone='hand',ref='actual-foreign-history-cast')
        body=self.add(session.engine,'Grizzly Bears',ref='foreign-history-target')
        self.prepare(session,actor='B',active='B',mana={'G':1});self.checkpoint(session)
        self.cast(session,prior,(body.ref,),actor='B');self.replay(session,load=True)
        metric=SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(CASES[4])[1]).terms[0].metric
        self.assertEqual(0,cast_reduction_multiplier(session.engine,'A',metric))
        self.assertEqual(1,cast_reduction_multiplier(session.engine,'B',metric))
        self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
        self.assertFalse(self.offered(session,spell));self.reject(session,'cast:'+spell.ref,pay='auto')
        self.replay(session,load=True)

    def test_native_self_reduction_omission_and_color_swap_are_killed(self):
        with patch.object(SelfSpellCostReductionHandler,'lower',return_value=()):
            with self.assertRaises((AssertionError,IndexError,StopIteration)):
                self.test_actual_cactarantula_uses_own_desert_and_reduced_price_then_preserves_reach()
        from quorune.compiler import cast_cost_modifier_templates as compiler
        original=compiler._reduction_pairs
        def swap_blue(token):
            values=original(token)
            if values is not None and token=='{2}{U}':return tuple((('W' if k=='U' else k),v) for k,v in values)
            return values
        with patch.object(compiler,'_reduction_pairs',side_effect=swap_blue):
            with self.assertRaises((AssertionError,StopIteration)):
                self.test_actual_stormwing_uses_real_cast_history_reduces_blue_and_completes_private_scry()

    def test_face_down_spell_does_not_receive_its_printed_self_reduction(self):
        session=self.session(60133008)
        spell=self.add(session.engine,'Generic Self Cost Morph',zone='hand',ref='intrinsic-discount-morph')
        self.assert_closed(session,spell)
        desert=self.add(session.engine,'Sunscorched Desert',ref='face-down-desert');desert.tapped=True
        self.prepare(session,mana={'C':2})
        self.assertFalse(self.offered(session,spell,prefix='cast-morph:'))
        self.reject(session,'cast-morph:'+spell.ref,pay='auto')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'cast-morph:'+spell.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[spell.object_id].face_down)
        self.replay(session,load=True)

    def test_selected_back_face_cannot_inherit_front_face_self_reduction(self):
        session=self.session(60133009)
        spell=self.add(session.engine,'Generic Cost Front // Generic Cost Back',zone='hand',ref='intrinsic-discount-faces')
        self.assert_closed(session,spell)
        desert=self.add(session.engine,'Sunscorched Desert',ref='other-face-desert');desert.tapped=True
        self.prepare(session,mana={'C':2,'U':1})
        def back_offer():return next((r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r['id'].endswith(':face:Generic Cost Back')),None)
        self.assertIsNone(back_offer())
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        action=back_offer();self.assertIsNotNone(action)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('Generic Cost Back',session.engine._effective_card_data(session.state.cards[spell.object_id])['name'])
        self.replay(session,load=True)

    def test_actual_cactarantula_uses_own_desert_and_reduced_price_then_preserves_reach(self):
        session=self.session(60133002)
        spell=self.add(session.engine,'Cactarantula',zone='hand',ref='actual-desert-reduction')
        self.assert_closed(session,spell)
        self.add(session.engine,'Sunscorched Desert',seat='B',ref='foreign-desert-negative')
        self.prepare(session,mana={'C':3,'G':2})
        self.assertFalse(self.offered(session,spell))
        self.reject(session,'cast:'+spell.ref,pay='auto')
        own=self.add(session.engine,'Sunscorched Desert',ref='own-desert-positive');own.tapped=True
        self.prepare(session,mana={'C':3,'G':2});self.checkpoint(session)
        self.assertEqual(3,self.cost(session,spell)['GENERIC'])
        self.cast(session,spell)
        current=session.state.cards[spell.object_id]
        self.assertEqual('battlefield',current.zone)
        self.assertIn('reach',session.engine._combat_keywords(current))
        self.assertEqual(6,session.engine._effective_card_data(current)['mana_value'])
        self.replay(session,load=True)

    def test_desert_sacrificed_for_mana_does_not_revoke_locked_self_reduction(self):
        session=self.session(60133003)
        spell=self.add(session.engine,'Cactarantula',zone='hand',ref='locked-desert-price')
        donor=self.add(session.engine,'Wild Cantor',ref='desert-mana-donor')
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-desert-cost-source'),targets=(donor,),components=(
                ResolutionContinuousComponent(Layer.TYPE,'4',(
                    ContinuousOperation('add_types',('land',),field='card_types'),
                    ContinuousOperation('add_types',('desert',),field='subtypes'),)),))
        self.prepare(session,mana={'C':3,'G':1});self.checkpoint(session)
        self.assertEqual(3,self.cost(session,spell)['GENERIC'])
        self.cast(session,spell)
        self.assertEqual('graveyard',session.state.cards[donor.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.replay(session,load=True)

    def test_serpent_combines_independent_lesson_timing_with_conjunctive_card_exclusions(self):
        session=self.session(60133004)
        spell=self.add(session.engine,'Serpent of the Pass',zone='hand',ref='actual-lesson-discount')
        self.assert_closed(session,spell)
        for name in ('Environmental Sciences','Expanded Anatomy','Memnite','Great Furnace'):
            self.add(session.engine,name,zone='graveyard')
        self.prepare(session,actor='A',active='B',mana={'C':5,'U':2})
        self.assertFalse(self.offered(session,spell));self.reject(session,'cast:'+spell.ref,pay='auto')
        self.add(session.engine,'Introduction to Prophecy',zone='graveyard')
        self.prepare(session,actor='A',active='B',mana={'C':1,'U':2})
        self.assertFalse(self.offered(session,spell))
        self.reject(session,'cast:'+spell.ref,pay='auto')
        self.prepare(session,actor='A',active='B',mana={'C':2,'U':2});self.checkpoint(session)
        self.assertEqual(2,self.cost(session,spell)['GENERIC'])
        self.cast(session,spell)
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.assertNotIn('flash',session.engine._combat_keywords(session.state.cards[spell.object_id]))
        self.replay(session,load=True)

    def test_actual_igneous_elemental_uses_own_land_card_then_resolves_optional_entry_damage(self):
        session=self.session(60133005)
        spell=self.add(session.engine,'Igneous Elemental',zone='hand',ref='actual-grave-land-reduction')
        self.assert_closed(session,spell)
        self.add(session.engine,'Island',seat='B',zone='graveyard',ref='foreign-grave-land')
        target=self.add(session.engine,'Wandering Ones',seat='B',ref='entry-damage-victim')
        self.prepare(session,mana={'C':2,'R':2})
        self.assertFalse(self.offered(session,spell));self.reject(session,'cast:'+spell.ref,pay='auto')
        self.add(session.engine,'Island',zone='graveyard',ref='own-grave-land')
        self.prepare(session,mana={'C':2,'R':2});self.checkpoint(session)
        self.assertEqual(2,self.cost(session,spell)['GENERIC'])
        result=session.act('pilot:A',{'action_id':'cast:'+spell.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session)
        self.assertEqual('semantic.target',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]})
        self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        packet=session.packet('pilot:A',full=True)['decision']
        self.assertTrue(packet['legal_actions'])
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.replay(session,load=True)

    def test_actual_octavia_requires_eight_own_matching_cards_and_keeps_ward_and_magecraft(self):
        session=self.session(60133006)
        spell=self.add(session.engine,'Octavia, Living Thesis',zone='hand',ref='actual-eight-card-reduction')
        growth=self.add(session.engine,'Giant Growth',zone='hand',ref='actual-octavia-magecraft-cast')
        body=self.add(session.engine,'Wandering Ones',ref='actual-magecraft-base-target')
        self.add(session.engine,'Forest',ref='actual-magecraft-payment')
        self.assert_closed(session,spell)
        for index in range(7):self.add(session.engine,'Shock',zone='graveyard',ref='own-seven-instant-'+str(index))
        self.add(session.engine,'Shock',seat='B',zone='graveyard',ref='opponent-eighth-negative')
        self.prepare(session,mana={'U':2})
        self.assertFalse(self.offered(session,spell));self.reject(session,'cast:'+spell.ref,pay='auto')
        self.add(session.engine,'Giant Growth',zone='graveyard',ref='own-eighth-sorcery-or-instant')
        self.prepare(session,mana={'U':2});self.checkpoint(session)
        self.assertEqual(0,self.cost(session,spell)['GENERIC'])
        self.cast(session,spell)
        self.assertIn('ward',session.engine._combat_keywords(session.state.cards[spell.object_id]))
        self.assertEqual(8,session.engine._numeric_stat(spell.object_id,'power'))
        self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'cast:'+growth.ref,'targets':[body.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','targets':[body.ref]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual((11,11),tuple(session.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
        self.replay(session,load=True)

    def test_actual_stormwing_uses_real_cast_history_reduces_blue_and_completes_private_scry(self):
        session=self.session(60133007)
        spell=self.add(session.engine,'Stormwing Entity',zone='hand',ref='actual-compound-reduction')
        prior=self.add(session.engine,'Unsummon',zone='hand',ref='actual-history-cast')
        target=self.add(session.engine,'Grizzly Bears',seat='B',ref='actual-history-target')
        growth=self.add(session.engine,'Giant Growth',zone='hand',ref='subsequent-prowess-spell')
        self.add(session.engine,'Forest',ref='subsequent-prowess-mana')
        self.assert_closed(session,spell)
        self.prepare(session,mana={'C':1,'U':2})
        self.assertFalse(self.offered(session,spell));self.reject(session,'cast:'+spell.ref,pay='auto')
        self.prepare(session,mana={'C':1,'U':2});self.checkpoint(session)
        self.cast(session,prior,(target.ref,))
        requirements=self.cost(session,spell)
        self.assertEqual((1,1),(requirements['GENERIC'],requirements['U']))
        result=session.act('pilot:A',{'action_id':'cast:'+spell.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        packet=session.packet('pilot:A',full=True)['decision']
        refs=packet['legal_actions'][0]['choice_schema']['legal_refs'];self.assertEqual(2,len(refs))
        for seat in 'BCD':
            foreign=session.packet('pilot:'+seat,full=True)
            self.assertIsNone(foreign['decision'])
            for ref in refs:self.assertNotIn(ref,json.dumps(foreign))
        session=self.replay(session,load=True)
        self.reject(session,'choose','B',cards={'top':refs,'bottom':[]})
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':[refs[1]],'bottom':[refs[0]]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.cast(session,growth,(spell.ref,))
        self.assertEqual((7,7),tuple(session.engine._numeric_stat(spell.object_id,k) for k in ('power','toughness')))
        self.replay(session,load=True)

    def test_actual_cactarantula_opponent_target_draw_is_private_loaded_and_replayed(self):
        session=self.session(60133012)
        source=self.add(session.engine,'Cactarantula',ref='actual-optional-target-draw')
        shock=self.add(session.engine,'Shock',seat='B',zone='hand',ref='opponent-targets-original')
        self.assert_closed(session,source)
        self.prepare(session,actor='B',active='B',mana={'R':1});self.checkpoint(session)
        result=session.act('pilot:B',{'action_id':'cast:'+shock.ref,'targets':[source.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,len(session.state.stack))
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['A'],list(decision.actors))
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        session=self.replay(session,load=True)
        before=len(session.state.players['A'].zones['hand'])
        self.reject(session,'choose','B',choice='draw')
        result=session.act('pilot:A',{'action_id':'choose','choice':'draw'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(before+1,len(session.state.players['A'].zones['hand']))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)
