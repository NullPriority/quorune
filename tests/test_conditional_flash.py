from __future__ import annotations

from pathlib import Path
import json
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.conditional_flash import (
    ConditionalFlashPermissionHandler, ConditionalFlashKeywordHandler,
)
from quorune.cast_timing import CastTimingPermission
from quorune.compiled_cast_timing import compiled_cast_timing_permissions
from quorune.compiled_cast_timing import prospective_conditional_flash_spell_data
from quorune.record import authoritative_state_hash
from quorune.mana import ManaMode, ManaSource, auto_plan_payment
from quorune.util import normalize_mana_bundle
from quorune.continuous_effect_model import Layer, ContinuousOperation
from quorune.continuous_effect_state import (
    ResolutionEffectSource, ResolutionContinuousComponent, create_resolution_continuous_effect_components,
)
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as spree
import test_public_condition_surfaces as surfaces


TEXT='As long as you control a green or blue permanent, you may cast this spell as though it had flash.'
CAP='timing.cast.conditional_self_flash'


class ConditionalFlashCompilerTests(unittest.TestCase):
    def test_permission_and_real_keyword_nodes_preserve_whole_source_spans(self):
        for text,expected in ((TEXT,1),('This creature has flash as long as you control a Desert.',3)):
            ir=compile_oracle_card(record(text,'Creature'),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual(expected,len(ir.faces[0].nodes))
            for node in ir.faces[0].nodes:
                self.assertEqual(text,node.text)
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
                self.assertIn(CAP,node.capability_dependencies)
            if expected==3:
                self.assertEqual({'playable','battlefield','stack'},{n.active_zone for n in ir.faces[0].nodes})

    def test_malformed_and_unrepresented_timing_variants_remain_closed(self):
        ir=compile_oracle_card(record(TEXT,'Creature'),capability_registry=load_default_capability_registry())
        descriptor,=ir.faces[0].nodes[0].handlers
        handler=ConditionalFlashPermissionHandler()
        for field,value in (('schema_version',True),('unknown',True),('event','damage')):
            bad={**descriptor,field:value}
            with self.subTest(field=field),self.assertRaises(SemanticNodeError):handler.validate(bad)
        with self.assertRaises(SemanticNodeError):handler.lower(descriptor,None)
        for text in (TEXT.replace('you control a green or blue permanent','you pay {2} more'),
                     TEXT.replace('you control a green or blue permanent','it targets a commander'),
                     TEXT.replace('you control a green or blue permanent','the chosen card in your hand is blue')):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),
                capability_registry=load_default_capability_registry()).status)
        self.assertEqual({'schema_version':1,'timing':'instant','scope':'this_face'},CastTimingPermission().to_dict())

    def test_same_line_programs_stay_distinct_and_legacy_tap_plan_shape_is_preserved(self):
        from types import SimpleNamespace
        from quorune.oracle_ir import generated_programs
        row=record('This creature has flash as long as you control a Desert.','Creature')
        programs=generated_programs(SimpleNamespace(rulings=lambda _:()),row,trust_level='trusted',
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        contributions=[p for p in programs if any(h.get('handler_id') in {
            ConditionalFlashPermissionHandler().handler_id,ConditionalFlashKeywordHandler().handler_id} for h in p.handlers)]
        self.assertEqual(3,len(contributions))
        self.assertEqual(3,len({p.key for p in contributions}))
        normal=ManaMode(normalize_mana_bundle({'R':1}))
        self.assertEqual({'m':{'R':1}},normal.to_compact())
        old=auto_plan_payment({'R':1},[ManaSource('source','S','Old tap source',(normal,))])
        self.assertNotIn('tap_source',old.activations[0])
        sacrifice=replace(normal,tap_source=False,side_effects=({'op':'sacrifice_source'},))
        current=auto_plan_payment({'R':1},[ManaSource('source','S','Nontap source',(sacrifice,))])
        self.assertIs(current.activations[0]['tap_source'],False)

    def test_producer_and_each_timing_dependency_omission_keep_complete_source_closed(self):
        with patch('quorune.compiler.conditional_flash_nodes.conditional_self_flash_nodes',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Creature'),
                capability_registry=load_default_capability_registry()).status)
        for text,missing in ((TEXT,CAP),(TEXT,'timing.cast.printed_flash'),
                (TEXT,'continuous.characteristics.fixed_public_state'),
                ('If you control a creature with power 4 or greater, you may cast this spell as though it had flash.',
                 'condition.creature.fixed_numeric_exists')):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            row=next(row for row in raw['capabilities'] if row['id']==missing)
            row.update(status='blocked',blockers=['Conditional timing dependency omission witness'])
            with self.subTest(missing=missing):
                self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),
                    capability_registry=CapabilityRegistry(raw)).status)


class ConditionalFlashRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject
    cast = surfaces.PublicConditionSurfaceRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'conditional-flash.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/conditional-flash-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditional Flash', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_yeti_off_turn_cast_uses_current_green_permanent(self):
        session=self.session(60131001)
        source=self.add(session.engine,'Hungering Yeti',zone='hand',ref='actual-conditional-yeti')
        self.add(session.engine,'Grizzly Bears',ref='own-green-permanent')
        self.assert_closed(session,source)
        self.prepare(session,actor='A',active='B',mana={'C':4,'R':1})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(row['id']=='cast:'+source.ref for row in actions))
        self.checkpoint(session)
        self.cast(session,source)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def offers(self,session,actor='A'):
        return session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']

    def capture_cast(self,session,card,targets=()):
        captured=[];original=session.engine._dispatch_semantic_event
        def observed(event,context=None,**values):
            if event=='spell.cast' and context and context.get('card')==card.ref:captured.append(dict(context))
            return original(event,context,**values)
        with patch.object(session.engine,'_dispatch_semantic_event',side_effect=observed):
            result=session.act('pilot:A',{'action_id':'cast:'+card.ref,'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len(captured))
        return captured[0]

    def test_all_four_originals_use_current_conditions_and_keep_real_keyword_distinct(self):
        for name,control,mana in (
            ('Dragon Grip','Wandering Ones',{'C':2,'R':1}),
            ('Hungering Yeti','Grizzly Bears',{'C':4,'R':1}),
            ('Illusion Spinners','Generic Faerie Presence',{'C':4,'U':1}),
            ('Colossal Rattlewurm','Sunscorched Desert',{'C':2,'G':2})):
            with self.subTest(name=name):
                session=self.session(60131002)
                source=self.add(session.engine,name,zone='hand',ref='conditional-original')
                self.assert_closed(session,source)
                self.add(session.engine,control,seat='B',ref='foreign-qualifier-negative')
                self.prepare(session,actor='A',active='B',mana=mana)
                self.assertFalse(any(row['id']=='cast:'+source.ref for row in self.offers(session)))
                own=self.add(session.engine,control,ref='own-qualifier-positive')
                if name=='Dragon Grip':own.counters['+1/+1']=3
                self.prepare(session,actor='A',active='B',mana=mana)
                self.assertTrue(any(row['id']=='cast:'+source.ref for row in self.offers(session)))
                self.checkpoint(session)
                self.reject(session,'cast:'+source.ref,'C',targets=[own.ref] if name=='Dragon Grip' else [],pay='auto')
                event=self.capture_cast(session,source,(own.ref,) if name=='Dragon Grip' else ())
                self.assertEqual(name=='Colossal Rattlewurm','flash' in event['keywords'])
                session=self.replay(session,load=True)
                self.assertIsNone(self.resolve(session))
                current=session.state.cards[source.object_id]
                self.assertEqual('battlefield',current.zone)
                self.assertEqual(name=='Colossal Rattlewurm','flash' in session.engine._combat_keywords(current))
                if name=='Dragon Grip':
                    self.assertEqual(own.object_id,current.attached_to)
                    self.assertIn('first strike',session.engine._combat_keywords(session.state.cards[own.object_id]))
                if name=='Illusion Spinners':
                    self.assertIn('hexproof',session.engine._combat_keywords(current))
                    self.assertIn('flying',session.engine._combat_keywords(current))
                self.replay(session,load=True)

    def test_condition_loss_during_actual_mana_payment_does_not_cancel_as_though_casts(self):
        for name,mana in (('Hungering Yeti',{'C':4}),('Dragon Grip',{'C':2})):
            with self.subTest(name=name):
                session=self.session(60131003)
                source=self.add(session.engine,name,zone='hand',ref='cast-before-source-sacrifice')
                donor=self.add(session.engine,'Wild Cantor',ref='sole-qualifying-mana-creature')
                donor.counters['+1/+1']=3
                target=self.add(session.engine,'Wandering Ones',ref='small-aura-recipient') if name=='Dragon Grip' else None
                for card in (source,donor):self.assert_closed(session,card)
                self.prepare(session,actor='A',active='B',mana=mana);self.checkpoint(session)
                event=self.capture_cast(session,source,(target.ref,) if target else ())
                self.assertEqual('graveyard',session.state.cards[donor.object_id].zone)
                self.assertNotIn('flash',event['keywords'])
                session=self.replay(session,load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                self.replay(session,load=True)

    def test_real_flash_reduces_locked_cost_then_can_end_during_mana_payment_before_cast_event(self):
        session=self.session(60131004)
        source=self.add(session.engine,'Colossal Rattlewurm',zone='hand',ref='real-flash-cost-spell')
        donor=self.add(session.engine,'Wild Cantor',ref='sole-desert-mana-source')
        reducer=self.add(session.engine,'Generic Flash Cost Reduction',ref='flash-qualified-cost-reducer')
        for card in (source,donor,reducer):self.assert_closed(session,card)
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-desert-mana-boundary'),targets=(donor,),components=(
                ResolutionContinuousComponent(Layer.TYPE,'4',(
                    ContinuousOperation('add_types',('land',),field='card_types'),
                    ContinuousOperation('add_types',('desert',),field='subtypes'),)),))
        self.prepare(session,actor='A',active='B',mana={'C':1,'G':1});self.checkpoint(session)
        event=self.capture_cast(session,source)
        self.assertEqual('graveyard',session.state.cards[donor.object_id].zone)
        self.assertNotIn('flash',event['keywords'])
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertNotIn('flash',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.replay(session,load=True)

    def test_as_though_permission_does_not_receive_flash_cost_reduction(self):
        session=self.session(60131005)
        source=self.add(session.engine,'Hungering Yeti',zone='hand',ref='permission-only-cost-spell')
        self.add(session.engine,'Grizzly Bears',ref='green-timing-qualifier')
        reducer=self.add(session.engine,'Generic Flash Cost Reduction',ref='does-not-reduce-as-though')
        self.assert_closed(session,reducer)
        self.prepare(session,actor='A',active='B',mana={'C':3,'R':1})
        self.assertFalse(any(row['id']=='cast:'+source.ref for row in self.offers(session)))
        self.reject(session,'cast:'+source.ref,pay='auto')

    def test_caster_relative_permissions_and_proposed_spell_views_preserve_foreign_owner_and_state(self):
        session=self.session(60131006)
        yeti=self.add(session.engine,'Hungering Yeti',zone='hand',ref='foreign-owner-yeti')
        wurm=self.add(session.engine,'Colossal Rattlewurm',zone='hand',ref='foreign-owner-real-flash')
        self.add(session.engine,'Grizzly Bears',seat='B',ref='caster-green')
        self.add(session.engine,'Sunscorched Desert',seat='B',ref='caster-desert')
        before=authoritative_state_hash(session.state)
        self.assertEqual((),compiled_cast_timing_permissions(session.engine,yeti,actor='A'))
        self.assertEqual(1,len(compiled_cast_timing_permissions(session.engine,yeti,actor='B')))
        self.assertNotIn('flash',{v.lower() for v in prospective_conditional_flash_spell_data(session.engine,wurm,'A')['keywords']})
        self.assertIn('flash',{v.lower() for v in prospective_conditional_flash_spell_data(session.engine,wurm,'B')['keywords']})
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual(('A','A'),(yeti.owner,wurm.owner))

    def test_losing_condition_after_offer_rejects_before_any_payment_and_keeps_private_hand(self):
        session=self.session(60131007)
        source=self.add(session.engine,'Hungering Yeti',zone='hand',ref='stale-conditional-offer')
        control=self.add(session.engine,'Grizzly Bears',ref='lost-green-control')
        self.prepare(session,actor='A',active='B',mana={'C':4,'R':1})
        self.assertTrue(any(row['id']=='cast:'+source.ref for row in self.offers(session)))
        from quorune.control_effects import change_control
        change_control(session.engine,control.object_id,'B',reason='Stale conditional timing source')
        self.reject(session,'cast:'+source.ref,pay='auto')
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        for actor in 'BCD':self.assertNotIn(source.ref,json.dumps(session.packet('pilot:'+actor,full=True)))

    def test_selected_face_and_face_down_method_do_not_inherit_other_intrinsic_flash(self):
        session=self.session(60131008)
        source=self.add(session.engine,'Generic Conditional Front // Generic Conditional Back',zone='hand',ref='conditional-faced-card')
        self.assert_closed(session,source)
        self.add(session.engine,'Sunscorched Desert',ref='face-timing-desert')
        self.prepare(session,actor='A',active='B',mana={'C':2,'U':1,'G':1})
        actions=self.offers(session)
        self.assertFalse(any(row['id']=='cast:'+source.ref for row in actions))
        back=next(row for row in actions if row['id'].endswith(':face:Generic Conditional Back'))
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':back['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertIn('flash',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.replay(session,load=True)
        other=self.session(60131009)
        morph=self.add(other.engine,'Generic Conditional Flash Morph',zone='hand',ref='conditional-face-down-card')
        self.assert_closed(other,morph)
        morph_desert=self.add(other.engine,'Sunscorched Desert',ref='morph-desert')
        self.add(other.engine,'Generic Flash Cost Reduction',ref='face-down-cost-negative')
        self.prepare(other,actor='A',active='B',mana={'C':3,'G':1})
        self.assertFalse(any(row['id']=='cast-morph:'+morph.ref for row in self.offers(other)))
        other.state.players['A'].mana_pool.update({key:0 for key in other.state.players['A'].mana_pool})
        morph_desert.tapped=True
        self.prepare(other,mana={'C':2})
        self.assertFalse(any(row['id']=='cast-morph:'+morph.ref for row in self.offers(other)))
        self.prepare(other,mana={'C':3});self.checkpoint(other)
        result=other.act('pilot:A',{'action_id':'cast-morph:'+morph.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertNotIn('flash',{v.lower() for v in other.engine._effective_card_data(other.state.cards[morph.object_id])['keywords']})
        self.assertIsNone(self.resolve(other))
        self.assertTrue(other.state.cards[morph.object_id].face_down)
        self.replay(other,load=True)

    def test_real_battlefield_flash_uses_current_control_and_ability_removal(self):
        session=self.session(60131010)
        source=self.add(session.engine,'Colossal Rattlewurm',ref='live-real-flash-source')
        self.assertNotIn('flash',session.engine._combat_keywords(source))
        desert=self.add(session.engine,'Sunscorched Desert',ref='live-real-flash-desert')
        self.assertIn('flash',session.engine._combat_keywords(source))
        from quorune.control_effects import change_control
        change_control(session.engine,source.object_id,'B',reason='Current actual-Flash controller')
        self.assertNotIn('flash',session.engine._combat_keywords(source))
        change_control(session.engine,desert.object_id,'B',reason='Move current Desert condition')
        self.assertIn('flash',session.engine._combat_keywords(source))
        self.add(session.engine,'Humility',ref='remove-conditional-flash-generator')
        self.assertNotIn('flash',session.engine._combat_keywords(source))

    def test_actual_copy_keeps_conditional_keyword_after_original_returns(self):
        session=self.session(60131011)
        source=self.add(session.engine,'Colossal Rattlewurm',ref='copy-real-flash-source')
        self.add(session.engine,'Sunscorched Desert',ref='copy-real-flash-desert')
        spell=self.add(session.engine,'Generic Entry One Copy',zone='hand',ref='copy-real-flash-spell')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='return-real-flash-original')
        for card in (source,spell,bounce):self.assert_closed(session,card)
        self.prepare(session,mana={'W':1,'U':1});self.checkpoint(session)
        self.cast(session,spell,(source.ref,))
        token,=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.cast(session,bounce,(source.ref,))
        self.assertIn('flash',session.engine._combat_keywords(session.state.cards[token.object_id]))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[token.object_id]))
        self.replay(session,load=True)

    def test_tapped_newly_controlled_nontap_self_sacrifice_source_can_pay_once(self):
        session=self.session(60131012)
        source=self.add(session.engine,'Hungering Yeti',zone='hand',ref='tapped-nontap-cast')
        donor=self.add(session.engine,'Wild Cantor',ref='tapped-sick-nontap-source')
        donor.tapped=True;donor.acquired_control_turn_count=session.state.players['A'].turns_begun
        self.prepare(session,actor='A',active='B',mana={'C':4});self.checkpoint(session)
        event=self.capture_cast(session,source)
        self.assertNotIn('flash',event['keywords'])
        self.assertEqual('graveyard',session.state.cards[donor.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.replay(session,load=True)

    def test_manual_nontap_flag_cannot_bypass_an_intrinsic_land_tap_cost(self):
        session=self.session(60131013)
        island=self.add(session.engine,'Island',ref='manual-plan-tap-cost')
        body=self.add(session.engine,'Grizzly Bears',ref='manual-plan-target')
        spell=self.add(session.engine,'Unsummon',zone='hand',ref='manual-plan-cast')
        self.prepare(session);self.checkpoint(session)
        self.reject(session,'cast:'+spell.ref,targets=[body.ref],pay='manual',
            mana=[{'source':island.ref,'bundle':{'U':1},'tap_source':False}],payment={'U':1})
        self.assertFalse(session.state.cards[island.object_id].tapped)
        self.cast(session,spell,(body.ref,))
        self.assertTrue(session.state.cards[island.object_id].tapped)
        self.replay(session,load=True)

    def test_omitted_timing_and_actual_keyword_contributions_fail_native_controls(self):
        with patch('quorune.rules.casting.proposal.compiled_cast_timing_permissions',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_yeti_off_turn_cast_uses_current_green_permanent()
        original=ConditionalFlashKeywordHandler.lower
        def omit_keyword(handler,descriptor,context):
            handler.validate(descriptor)
            return ()
        with patch.object(ConditionalFlashKeywordHandler,'lower',new=omit_keyword):
            with self.assertRaises(AssertionError):self.test_caster_relative_permissions_and_proposed_spell_views_preserve_foreign_owner_and_state()

    def test_actual_copied_nontap_mana_source_uses_the_same_payment_eligibility(self):
        session=self.session(60131014)
        original=self.add(session.engine,'Wild Cantor',ref='original-copied-mana-source')
        copy=self.add(session.engine,'Generic Entry One Copy',zone='hand',ref='copy-nontap-mana-source')
        bounce=self.add(session.engine,'Unsummon',zone='hand',ref='return-original-mana-source')
        spell=self.add(session.engine,'Hungering Yeti',zone='hand',ref='pay-with-copied-mana')
        self.prepare(session,mana={'W':1,'U':1,'C':4});self.checkpoint(session)
        self.cast(session,copy,(original.ref,))
        token,=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.cast(session,bounce,(original.ref,))
        self.cast(session,spell)
        self.assertNotIn(token.object_id,session.state.players['A'].zones['battlefield'])
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.replay(session,load=True)


if __name__=='__main__':
    unittest.main()
