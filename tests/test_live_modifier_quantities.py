from __future__ import annotations

"""Pinned live layer-7c quantities retain their subject and information boundary."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls

from quorune.characteristic_fragments import (CharacteristicQuantitySpec,CharacteristicQuantityScope as Scope,
    CharacteristicQuantityReduction as Reduction,characteristic_quantity_capabilities)
from quorune.dynamic_characteristics import query_characteristic_count
from quorune.object_predicate import ObjectQuerySpec


class LiveModifierQuantityModelTests(unittest.TestCase):
    def test_current_value_models_are_closed_and_legacy_wire_is_unchanged(self):
        original=CharacteristicQuantitySpec(Scope.CONTROLLER_ZONE,ObjectQuerySpec(zones=('battlefield',),types_all=('land',)))
        self.assertEqual({'schema_version':1,'scope':'controller_zone','query':original.query.to_dict(),
            'counter_name':None,'exclude_source':False,'exclude_attached_object':False},original.to_dict())
        values=(CharacteristicQuantitySpec(Scope.CONTROLLER_LIFE_TOTAL,schema_version=4),
            CharacteristicQuantitySpec(Scope.OPPONENT_POISON_COUNTERS,schema_version=4),
            CharacteristicQuantitySpec(Scope.ATTACHED_OBJECT,schema_version=4,reduction=Reduction.DISTINCT_COLORS),
            CharacteristicQuantitySpec(Scope.ATTACHED_OBJECT,schema_version=4,reduction=Reduction.MAXIMUM_MANA_VALUE))
        for value in values:
            self.assertEqual(value,CharacteristicQuantitySpec.from_dict(value.to_dict()))
            self.assertTrue(characteristic_quantity_capabilities(value))
            for changes in ({'schema_version':1},{'schema_version':True},{'offset':1},{'exclude_source':True},
                    {'query':ObjectQuerySpec(zones=('hand',)).to_dict()}):
                with self.assertRaises(ValueError):
                    CharacteristicQuantitySpec.from_dict({**value.to_dict(),**changes})
        with self.assertRaises(ValueError):
            replace(original,schema_version=4)

    def test_current_life_has_the_pinned_negative_floor_and_poison_uses_active_opponents(self):
        players={seat:SimpleNamespace(life=life,poison=poison,in_game=True)
            for seat,life,poison in (('A',12,7),('B',40,2),('C',40,3),('D',40,5))}
        host=SimpleNamespace(state=SimpleNamespace(players=players));source=SimpleNamespace(controller='A')
        life=CharacteristicQuantitySpec(Scope.CONTROLLER_LIFE_TOTAL,schema_version=4)
        poison=CharacteristicQuantitySpec(Scope.OPPONENT_POISON_COUNTERS,schema_version=4)
        self.assertEqual(12,query_characteristic_count(host,source,life))
        players['A'].life=-4
        self.assertEqual(0,query_characteristic_count(host,source,life))
        self.assertEqual(10,query_characteristic_count(host,source,poison))
        players['D'].in_game=False
        self.assertEqual(5,query_characteristic_count(host,source,poison))
        source.controller='B'
        self.assertEqual(10,query_characteristic_count(host,source,poison))
        players['C'].poison=True
        with self.assertRaises(ValueError):
            query_characteristic_count(host,source,poison)

    def test_source_and_attached_compiler_quantities_preserve_their_references(self):
        from quorune.compiler.live_modifier_quantities import source_modifier_quantity,attached_object_quantity
        self.assertEqual(Scope.CONTROLLER_LIFE_TOTAL,source_modifier_quantity('your life total').scope)
        self.assertEqual(Scope.OPPONENT_POISON_COUNTERS,source_modifier_quantity('poison counter your opponents have').scope)
        union=source_modifier_quantity('Auras and Equipment attached to it')
        self.assertEqual((Scope.ATTACHED_TO_SOURCE,('aura','equipment')),(union.scope,union.query.subtypes_any))
        self.assertEqual(Reduction.DISTINCT_COLORS,attached_object_quantity('of its colors').reduction)
        self.assertEqual(Reduction.MAXIMUM_MANA_VALUE,attached_object_quantity('its mana value').reduction)
        for text in ('its color identity','its power','its controller\'s life total','colors of another permanent'):
            self.assertIsNone(attached_object_quantity(text))

    def test_public_quantified_anthem_keeps_keyword_and_proves_relative_exclusion(self):
        from quorune.compiler.live_modifier_quantities import quantified_public_modifier_handler
        from quorune.semantic_runtime.quantified_anthems import QuantifiedAnthemHandler
        for text,count,excluded in (
                ('All Sliver creatures get +1/+1 for each other Sliver on the battlefield.',1,True),
                ('Creatures you control get +0/+1 for each Gate you control and have vigilance.',2,False),
                ('All creatures get -X/-0, where X is the number of cards in your hand.',1,False)):
            compiled=quantified_public_modifier_handler(text)
            self.assertIsNotNone(compiled,text)
            self.assertEqual(count,len(compiled[1]))
            _base,_quantity,exclude=QuantifiedAnthemHandler().validate(compiled[1][0])
            self.assertEqual(excluded,exclude)
        self.assertIsNone(quantified_public_modifier_handler(
            'All Sliver creatures get +1/+1 for each other Elf on the battlefield.'))
        self.assertIsNone(quantified_public_modifier_handler(
            'All creatures get +1/+1 for each creature card in an opponent\'s hand.'))

    def test_attached_values_distinguish_empty_missing_and_current_layer_five_information(self):
        from quorune.model import CardInstance
        from quorune.continuous_effects import Layer
        source=CardInstance('source','S','fixture:source','Generic Source','A','A','battlefield')
        target=CardInstance('recipient','T','fixture:recipient','Generic Recipient','A','A','battlefield')
        data={'colors':['U','R'],'mana_value':3.0}
        def current(card,**kwargs):
            self.assertIs(card,target);self.assertEqual(Layer.COLOR,kwargs['maximum_layer'])
            return data
        host=SimpleNamespace(state=SimpleNamespace(players={'A':SimpleNamespace()},cards={source.object_id:source,target.object_id:target}),
            _effective_card_data=current)
        colors=CharacteristicQuantitySpec(Scope.ATTACHED_OBJECT,schema_version=4,reduction=Reduction.DISTINCT_COLORS)
        mana=replace(colors,reduction=Reduction.MAXIMUM_MANA_VALUE)
        self.assertEqual(0,query_characteristic_count(host,source,colors))
        source.attached_to=target.object_id;target.attachments=[source.object_id]
        self.assertEqual(2,query_characteristic_count(host,source,colors))
        self.assertEqual(3,query_characteristic_count(host,source,mana))
        data['colors']=[];data['mana_value']=0
        self.assertEqual(0,query_characteristic_count(host,source,colors));self.assertEqual(0,query_characteristic_count(host,source,mana))
        data['colors']=None
        with self.assertRaises(ValueError):query_characteristic_count(host,source,colors)
        data['mana_value']=True
        with self.assertRaises(ValueError):query_characteristic_count(host,source,mana)
        target.attachments=[]
        with self.assertRaises(ValueError):query_characteristic_count(host,source,colors)

    def test_other_count_uses_recipient_membership_even_when_source_is_not_a_sliver(self):
        from quorune.model import CardInstance
        from quorune.characteristic_evaluation import type_parts
        from quorune.continuous_effects import Layer
        from quorune.compiler.live_modifier_quantities import quantified_public_modifier_handler
        from quorune.semantic_runtime.quantified_anthems import QuantifiedAnthemHandler
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        source=CardInstance('source','S','fixture:source','Generic Artifact Source','A','A','battlefield')
        creatures=[CardInstance('c'+str(i),'C'+str(i),'fixture:sliver','Generic Sliver',seat,seat,'battlefield')
            for i,seat in enumerate(('A','B'))]
        noncreature=CardInstance('kindred','K','fixture:kindred','Generic Kindred Sliver','C','C','battlefield')
        cards={c.object_id:c for c in (source,*creatures,noncreature)}
        types={source.object_id:'Legendary Artifact',noncreature.object_id:'Kindred Enchantment — Sliver',
            **{c.object_id:'Artifact Creature — Sliver' for c in creatures}}
        def current(card,**kwargs):
            self.assertEqual(Layer.COLOR,kwargs['maximum_layer'])
            return {'name':card.printed_name,'type_line':types[card.object_id],'colors':[],'mana_value':0,'keywords':[]}
        players={seat:SimpleNamespace(in_game=True,zones={'battlefield':[c.object_id for c in cards.values() if c.controller==seat]})
            for seat in 'ABCD'}
        host=SimpleNamespace(state=SimpleNamespace(players=players,cards=cards),_effective_card_data=current,_type_parts=type_parts)
        descriptor=quantified_public_modifier_handler('All Sliver creatures get +1/+1 for each other Sliver on the battlefield.')[1][0]
        handler=QuantifiedAnthemHandler();_base,quantity,_exclude=handler.validate(descriptor)
        self.assertFalse(quantity.exclude_source)
        count=query_characteristic_count(host,source,quantity);self.assertEqual(3,count)
        context=ContinuousEffectSourceContext(source_object_id=source.object_id,source_ref=source.ref,
            source_controller='A',source_timestamp=0,component_id='generic-other-count',resolved_quantity=count)
        effect=handler.lower(descriptor,context)[0]
        self.assertEqual((2,2),effect.operations[0].value)
        self.assertEqual(('creature',),effect.applies.types_all)

    def test_global_quantities_reject_ambiguous_references_and_reserved_definition_values(self):
        from copy import deepcopy
        from quorune.compiler.live_modifier_quantities import quantified_public_modifier_handler
        from quorune.semantic_runtime.quantified_anthems import QuantifiedAnthemHandler
        from quorune.characteristic_fragments import QueryPowerToughnessDefinitionSpec
        descriptor=quantified_public_modifier_handler('All creatures get -X/-0, where X is the number of cards in your hand.')[1][0]
        for quantity in (CharacteristicQuantitySpec(Scope.ATTACHED_OBJECT,schema_version=4,reduction=Reduction.DISTINCT_COLORS),
                CharacteristicQuantitySpec(Scope.CONTROLLER_ZONE,ObjectQuerySpec(zones=('hand',),tapped=True))):
            changed=deepcopy(descriptor);changed['modifier']['quantity']=quantity.to_dict()
            with self.assertRaises(ValueError):QuantifiedAnthemHandler().validate(changed)
        self.assertEqual((),characteristic_quantity_capabilities({'scope':'attached_object','schema_version':1}))
        self.assertIsNone(quantified_public_modifier_handler('Equipped creature gets +1/+1 for each land you control.'))
        with self.assertRaises(ValueError):
            QueryPowerToughnessDefinitionSpec(CharacteristicQuantitySpec(Scope.CONTROLLER_LIFE_TOTAL,schema_version=4),True,True)

    def test_a_single_attachment_in_both_categories_is_counted_once(self):
        from quorune.model import CardInstance
        from quorune.characteristic_evaluation import type_parts
        from quorune.compiler.live_modifier_quantities import source_modifier_quantity
        source=CardInstance('source','S','fixture:source','Generic Recipient','A','A','battlefield')
        attached=CardInstance('attached','T','fixture:attached','Generic Combined Attachment','A','A','battlefield')
        source.attachments=[attached.object_id];attached.attached_to=source.object_id
        host=SimpleNamespace(state=SimpleNamespace(cards={source.object_id:source,attached.object_id:attached}),
            _effective_card_data=lambda *a,**k:{'name':'Generic Combined Attachment',
                'type_line':'Artifact Enchantment — Aura Equipment','colors':[],'mana_value':0,'keywords':[]},
            _type_parts=type_parts)
        self.assertEqual(1,query_characteristic_count(host,source,source_modifier_quantity('Auras and Equipment attached to it')))

    def test_fresh_recipient_membership_proof_omission_is_killed(self):
        method='test_public_quantified_anthem_keeps_keyword_and_proves_relative_exclusion'
        with patch('quorune.semantic_runtime.quantified_anthems.recipient_is_in_quantity_domain',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


NAMES=("Death's Shadow",'Civic Saber','Hedron Matrix','Blessing of the Nephilim','Mycosynth Fiend',
    'Hold the Gates','Sliver Legion','Champion of the Flame','Meishin, the Mind Cage')


class LiveModifierQuantityRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ProvokeTests.session
    add=controls.ProvokeTests.add
    prepare=controls.ProvokeTests.prepare
    checkpoint=controls.ProvokeTests.checkpoint
    resolve=controls.ProvokeTests.resolve
    replay=controls.ProvokeTests.replay
    binding=controls.ProvokeTests.binding
    private_replay=controls.ProvokeTests.private_replay
    priority=controls.ProvokeTests.priority
    advance=controls.ProvokeTests.advance
    actions=controls.ProvokeTests.actions
    attack=controls.ProvokeTests.attack

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'live-modifiers.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/live-modifier-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Live modifier quantities',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_complete_live_modifier_originals_have_normal_admission(self):
        for index,name in enumerate(NAMES):
            s=self.session(763010+index);c=self.add(s.engine,name,zone='hand')
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            for face in ir.faces:
                for node in face.nodes:self.assertEqual(node.text,face.oracle_text[node.span.start:node.span.end])
            bound=self.binding(s,c)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))

    def cast_row(self,s,c,*,seat='A',face=None,method=None):
        self.priority(s,seat)
        rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        return next((r for r in rows if r.get('card')==c.ref and r.get('kind')=='cast'
            and r.get('cast_method')==method and (face is None or r.get('face')==face)),None)

    def reject(self,s,command,*,principal='pilot:D'):
        before=authoritative_state_hash(s.state);result=s.act(principal,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def cast(self,s,c,payment,targets=(),*,seat='A',face=None,method=None,**choices):
        row=self.cast_row(s,c,seat=seat,face=face,method=method);self.assertIsNotNone(row,c.printed_name)
        command={'action_id':row['id'],'pay':'manual','payment':payment,'targets':list(targets),**choices}
        self.reject(s,command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def stats(self,s,c):
        return tuple(s.engine._numeric_stat(c.object_id,k) for k in ('power','toughness'))

    def equip(self,s,source,recipient,amount):
        self.priority(s,'A')
        abilities=[a for a in s.engine._activated_abilities(source) if a.builtin_semantic_key=='builtin:equip']
        self.assertEqual(1,len(abilities))
        action='activate:'+source.ref+':'+abilities[0].ability_id
        self.assertTrue(any(r['id']==action for r in self.actions(s)))
        command={'action_id':action,'targets':[recipient.ref],'pay':'manual','payment':{'C':amount}}
        self.reject(s,command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_shadow_cast_reads_current_life_and_damage_updates_the_modifier(self):
        s=self.session(763100);source=self.add(s.engine,"Death's Shadow",zone='hand')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='shadow-private-c')
        s.state.players['A'].life=12
        self.prepare(s,mana={'B':1,'R':1});self.checkpoint(s)
        self.assertEqual((13,13),self.stats(s,source))
        self.cast(s,source,{'B':1});self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,source))
        self.cast(s,bolt,{'R':1},['A']);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual(9,s.state.players['A'].life)
        self.assertEqual((4,4),self.stats(s,s.state.cards[source.object_id]))
        self.private_replay(s,private)

    def test_actual_blessing_uses_recipient_colors_and_turn_changes_the_live_value(self):
        s=self.session(763101);aura=self.add(s.engine,'Blessing of the Nephilim',zone='hand')
        turn=self.add(s.engine,'Turn // Burn',zone='hand')
        recipient=self.add(s.engine,'Fusion Elemental')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='blessing-private-c')
        self.prepare(s,mana={'W':1,'C':2,'U':1});self.checkpoint(s)
        self.cast(s,aura,{'W':1},[recipient.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((13,13),self.stats(s,recipient))
        self.cast(s,turn,{'C':2,'U':1},[recipient.ref],face='Turn')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((1,2),self.stats(s,s.state.cards[recipient.object_id]))
        self.advance(s,lambda session:session.state.active_player=='B' and session.state.phase=='precombat_main')
        self.assertEqual((13,13),self.stats(s,s.state.cards[recipient.object_id]))
        self.private_replay(s,private)

    def test_actual_meishin_cast_draw_and_hand_privacy_are_live(self):
        import json
        s=self.session(763102);source=self.add(s.engine,'Meishin, the Mind Cage',zone='hand')
        draw=self.add(s.engine,'Divination',zone='hand')
        own=self.add(s.engine,'Generic Bound Body');enemy=self.add(s.engine,'Fusion Elemental',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='meishin-private-c')
        self.prepare(s,mana={'C':6,'U':4});self.checkpoint(s)
        self.cast(s,source,{'C':4,'U':3});self.assertIsNone(self.resolve(s))
        count=len(s.state.players['A'].zones['hand'])
        self.assertEqual((2-count,6),self.stats(s,own));self.assertEqual((8-count,8),self.stats(s,enemy))
        self.cast(s,draw,{'C':2,'U':1});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        count2=len(s.state.players['A'].zones['hand']);self.assertEqual(count+1,count2)
        self.assertEqual((2-count2,6),self.stats(s,s.state.cards[own.object_id]))
        for seat in 'BCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            for oid in s.state.players['A'].zones['hand']:self.assertNotIn(s.state.cards[oid].ref,packet)
        self.private_replay(s,private)

    def test_fresh_current_value_and_global_producer_omissions_are_killed(self):
        import copy
        import quorune.rules.live_modifier_quantities as owner
        import quorune.characteristic_evaluation as layers
        from quorune.continuous_effects import Layer
        from quorune.ability_fragments import canonical_ability_fragments
        original=owner.current_modifier_quantity
        def wrong_recipient(host,source,quantity,**kwargs):
            if quantity.scope is Scope.ATTACHED_OBJECT:
                data=host._effective_card_data(source,maximum_layer=Layer.COLOR)
                return len(set(data['colors'])) if quantity.reduction is Reduction.DISTINCT_COLORS else int(data['mana_value'])
            return original(host,source,quantity,**kwargs)
        def own_poison(host,source,quantity,**kwargs):
            return host.state.players[source.controller].poison if quantity.scope is Scope.OPPONENT_POISON_COUNTERS else original(host,source,quantity,**kwargs)
        def before_abilities(state,effects,*,context):
            value=layers.evaluate_continuous_effects(copy.deepcopy(state),
                (effect for effect in effects if effect.layer<=Layer.COLOR),context=context)
            return canonical_ability_fragments(value.characteristics.get('ability_fragments',()))
        for path,value,method in (
                ('quorune.rules.live_modifier_quantities.current_modifier_quantity',lambda *a,**k:0,
                    'test_actual_shadow_cast_reads_current_life_and_damage_updates_the_modifier'),
                ('quorune.compiler.live_modifier_quantities.quantified_public_modifier_handler',lambda *a:None,
                    'test_actual_meishin_cast_draw_and_hand_privacy_are_live'),
                ('quorune.rules.live_modifier_quantities.current_modifier_quantity',wrong_recipient,
                    'test_actual_civic_saber_moves_between_five_color_red_and_colorless_recipients'),
                ('quorune.rules.live_modifier_quantities.current_modifier_quantity',own_poison,
                    'test_actual_mycosynth_counts_opponent_poison_and_elimination_removes_that_player'),
                ('quorune.characteristic_evaluation._intrinsic_quantity_fragments_after_abilities',before_abilities,
                    'test_actual_champion_counts_aura_equipment_union_and_retains_trample')):
            with patch(path,value):
                with self.assertRaises(AssertionError,msg=path):getattr(type(self)(method),method)()

    def test_actual_civic_saber_moves_between_five_color_red_and_colorless_recipients(self):
        s=self.session(763110);source=self.add(s.engine,'Civic Saber',zone='hand')
        turn=self.add(s.engine,'Turn // Burn',zone='hand')
        colored=self.add(s.engine,'Fusion Elemental');colorless=self.add(s.engine,'Sliver Construct')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='saber-private-c')
        self.prepare(s,mana={'C':5,'U':1});self.checkpoint(s)
        self.cast(s,source,{'C':1});self.assertIsNone(self.resolve(s))
        self.equip(s,source,colored,1);self.assertIsNone(self.resolve(s))
        self.assertEqual((13,8),self.stats(s,colored))
        self.cast(s,turn,{'C':2,'U':1},[colored.ref],face='Turn');self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,colored))
        self.equip(s,source,colorless,1);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0,1),self.stats(s,s.state.cards[colored.object_id]))
        self.assertEqual((2,2),self.stats(s,s.state.cards[colorless.object_id]))
        self.assertEqual(colorless.object_id,s.state.cards[source.object_id].attached_to)
        self.private_replay(s,private)

    def test_actual_hedron_uses_prototype_and_copied_token_mana_values(self):
        s=self.session(763111);source=self.add(s.engine,'Hedron Matrix',zone='hand')
        recipient=self.add(s.engine,'Blitz Automaton',zone='hand')
        copy_spell=self.add(s.engine,'Cackling Counterpart',zone='hand')
        self.prepare(s,mana={'C':15,'R':1,'U':2});self.checkpoint(s)
        self.cast(s,recipient,{'C':2,'R':1},method='prototype');self.assertIsNone(self.resolve(s))
        self.cast(s,source,{'C':4});self.assertIsNone(self.resolve(s))
        self.equip(s,source,recipient,4);self.assertIsNone(self.resolve(s))
        self.assertEqual((6,5),self.stats(s,recipient))
        self.assertIn('haste',s.engine._combat_keywords(recipient))
        before=set(s.state.cards)
        self.cast(s,copy_spell,{'C':1,'U':2},[recipient.ref]);self.assertIsNone(self.resolve(s))
        copies=[c for oid,c in s.state.cards.items() if oid not in before and c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(copies));copied=copies[0]
        self.assertEqual(3,s.engine._effective_card_data(copied)['mana_value'])
        self.assertEqual((3,2),self.stats(s,copied))
        self.equip(s,source,copied,4);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((3,2),self.stats(s,s.state.cards[recipient.object_id]))
        self.assertEqual((6,5),self.stats(s,s.state.cards[copied.object_id]))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.replay(s)

    def test_actual_champion_counts_aura_equipment_union_and_retains_trample(self):
        s=self.session(763112);champion=self.add(s.engine,'Champion of the Flame',zone='hand')
        aura=self.add(s.engine,'Blessing of the Nephilim',zone='hand')
        equipment=self.add(s.engine,'Civic Saber',zone='hand');humble=self.add(s.engine,'Humble',zone='hand')
        other=self.add(s.engine,'Sliver Construct')
        self.prepare(s,mana={'C':5,'R':1,'W':2});self.checkpoint(s)
        self.cast(s,champion,{'C':1,'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,champion));self.assertIn('trample',s.engine._combat_keywords(champion))
        self.cast(s,aura,{'W':1},[champion.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((4,4),self.stats(s,champion))
        self.cast(s,equipment,{'C':1});self.assertIsNone(self.resolve(s))
        self.equip(s,equipment,champion,1);self.assertIsNone(self.resolve(s))
        self.assertEqual((7,6),self.stats(s,champion))
        self.equip(s,equipment,other,1);self.assertIsNone(self.resolve(s))
        self.assertEqual((4,4),self.stats(s,champion));self.assertEqual((2,2),self.stats(s,other))
        self.cast(s,humble,{'C':1,'W':1},[champion.ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        current=s.state.cards[champion.object_id]
        self.assertEqual((1,2),self.stats(s,current));self.assertNotIn('trample',s.engine._combat_keywords(current))
        self.replay(s)

    def test_actual_mycosynth_counts_opponent_poison_and_elimination_removes_that_player(self):
        s=self.session(763113);source=self.add(s.engine,'Mycosynth Fiend',zone='hand')
        attacker=self.add(s.engine,'Plague Stinger');attacker.acquired_control_turn_count=-1
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='poison-private-c')
        for seat,amount in (('A',7),('B',9),('C',3),('D',5)):s.state.players[seat].poison=amount
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        self.cast(s,source,{'C':2,'G':1});self.assertIsNone(self.resolve(s))
        self.assertEqual((19,19),self.stats(s,source))
        self.attack(s,{attacker.ref:'B'})
        s=self.replay(s,load=True)
        self.advance(s,lambda session:session.state.phase=='postcombat_main')
        self.assertFalse(s.state.players['B'].in_game)
        self.assertEqual(10,s.state.players['B'].poison)
        self.assertEqual((10,10),self.stats(s,s.state.cards[source.object_id]))
        self.assertEqual((7,3,5),tuple(s.state.players[seat].poison for seat in ('A','C','D')))
        self.private_replay(s,private)

    def test_actual_hold_gates_keeps_vigilance_at_zero_and_tracks_live_gate_types(self):
        s=self.session(763114);source=self.add(s.engine,'Hold the Gates',zone='hand')
        gate=self.add(s.engine,'Azorius Guildgate',zone='hand')
        enemy_gate=self.add(s.engine,'Azorius Guildgate',seat='B',ref='enemy-gate')
        seas=self.add(s.engine,'Spreading Seas',zone='hand')
        own=self.add(s.engine,'Generic Bound Body');enemy=self.add(s.engine,'Fusion Elemental',seat='B')
        self.prepare(s,mana={'C':3,'W':1,'U':1});self.checkpoint(s)
        self.cast(s,source,{'C':2,'W':1});self.assertIsNone(self.resolve(s))
        self.assertEqual((2,6),self.stats(s,own));self.assertIn('vigilance',s.engine._combat_keywords(own))
        self.assertNotIn('vigilance',s.engine._combat_keywords(enemy))
        self.priority(s,'A')
        row=next(r for r in self.actions(s) if r.get('card')==gate.ref and r.get('kind')!='cast')
        command={'action_id':row['id']};self.reject(s,command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',gate.zone);self.assertTrue(gate.tapped)
        self.assertEqual((2,7),self.stats(s,own));self.assertEqual((8,8),self.stats(s,enemy))
        self.cast(s,seas,{'C':1,'U':1},[gate.ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        current=s.state.cards[own.object_id]
        self.assertEqual((2,6),self.stats(s,current));self.assertIn('vigilance',s.engine._combat_keywords(current))
        self.assertIn('island',s.engine._type_parts(s.engine._effective_card_data(s.state.cards[gate.object_id])['type_line'])[1])
        self.assertEqual('battlefield',s.state.cards[enemy_gate.object_id].zone)
        self.replay(s)

    def test_actual_sliver_legion_counts_all_controllers_and_ability_loss_removes_the_anthem(self):
        s=self.session(763115);source=self.add(s.engine,'Sliver Legion',zone='hand')
        small=self.add(s.engine,'Metallic Sliver');large=self.add(s.engine,'Sliver Construct',seat='B')
        other=self.add(s.engine,'Generic Bound Body',seat='C')
        humble=self.add(s.engine,'Humble',zone='hand')
        self.prepare(s,mana={'C':1,'W':2,'U':1,'B':1,'R':1,'G':1});self.checkpoint(s)
        self.cast(s,source,{'W':1,'U':1,'B':1,'R':1,'G':1});self.assertIsNone(self.resolve(s))
        self.assertEqual((9,9),self.stats(s,source));self.assertEqual((3,3),self.stats(s,small))
        self.assertEqual((4,4),self.stats(s,large));self.assertEqual((2,6),self.stats(s,other))
        self.cast(s,humble,{'C':1,'W':1},[source.ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0,1),self.stats(s,s.state.cards[source.object_id]))
        self.assertEqual((1,1),self.stats(s,s.state.cards[small.object_id]))
        self.assertEqual((2,2),self.stats(s,s.state.cards[large.object_id]))
        self.replay(s)

    def test_actual_legacy_kird_ape_quantity_is_removed_with_its_ability(self):
        s=self.session(763116);source=self.add(s.engine,'Kird Ape',zone='hand')
        self.add(s.engine,'Forest');humble=self.add(s.engine,'Humble',zone='hand')
        self.assertTrue(self.binding(s,source)['strict_capability_ready'])
        self.prepare(s,mana={'R':1,'C':1,'W':1});self.checkpoint(s)
        self.cast(s,source,{'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual((2,3),self.stats(s,source))
        self.cast(s,humble,{'C':1,'W':1},[source.ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0,1),self.stats(s,s.state.cards[source.object_id]))
        self.replay(s)

    def test_actual_shadow_at_thirteen_life_dies_and_other_zones_restore_printed_stats(self):
        s=self.session(763117);source=self.add(s.engine,"Death's Shadow",zone='hand')
        s.state.players['A'].life=13
        self.prepare(s,mana={'B':1});self.checkpoint(s)
        self.assertEqual((13,13),self.stats(s,source))
        self.cast(s,source,{'B':1});s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id]
        self.assertEqual('graveyard',current.zone);self.assertEqual((13,13),self.stats(s,current))
        self.assertEqual(13,s.state.players['A'].life)
        self.replay(s)

    def test_actual_hedron_does_not_treat_an_ordinary_token_as_its_equipment(self):
        s=self.session(763118);source=self.add(s.engine,'Hedron Matrix',zone='hand')
        create=self.add(s.engine,'Raise the Alarm',zone='hand')
        self.prepare(s,mana={'C':9,'W':1});self.checkpoint(s)
        self.cast(s,source,{'C':4});self.assertIsNone(self.resolve(s))
        self.cast(s,create,{'C':1,'W':1});self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(2,len(tokens));target=tokens[0]
        self.assertEqual(0,s.engine._effective_card_data(target)['mana_value'])
        self.equip(s,source,target,4);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,s.state.cards[target.object_id]))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.replay(s)

    def test_actual_mycosynth_control_change_rebinds_opponents_without_caching(self):
        s=self.session(763119);source=self.add(s.engine,'Mycosynth Fiend')
        control=self.add(s.engine,'Control Magic',seat='B',zone='hand')
        for seat,amount in (('A',7),('B',2),('C',3),('D',5)):s.state.players[seat].poison=amount
        self.prepare(s,actor='B',active='B',mana={'C':2,'U':2});self.checkpoint(s)
        self.assertEqual((12,12),self.stats(s,source))
        self.cast(s,control,{'C':2,'U':2},[source.ref],seat='B')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id]
        self.assertEqual('B',current.controller);self.assertEqual((17,17),self.stats(s,current))
        self.replay(s)

    def test_actual_meishin_control_change_uses_new_controllers_private_hand_count(self):
        import json
        s=self.session(763120);source=self.add(s.engine,'Meishin, the Mind Cage')
        control=self.add(s.engine,'Confiscate',seat='B',zone='hand')
        body=self.add(s.engine,'Generic Bound Body')
        for i in range(3):self.add(s.engine,'Generic Bound Growth',zone='hand',ref='meishin-a-'+str(i))
        self.prepare(s,actor='B',active='B',mana={'C':4,'U':2});self.checkpoint(s)
        old=len(s.state.players['A'].zones['hand']);self.assertEqual((2-old,6),self.stats(s,body))
        self.cast(s,control,{'C':4,'U':2},[source.ref],seat='B')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id];self.assertEqual('B',current.controller)
        new=len(s.state.players['B'].zones['hand']);self.assertNotEqual(old,new)
        self.assertEqual((2-new,6),self.stats(s,s.state.cards[body.object_id]))
        for seat in 'ACD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            for oid in s.state.players['B'].zones['hand']:self.assertNotIn(s.state.cards[oid].ref,packet)
        self.replay(s)

    def test_independent_runtime_minima_and_malformed_values_fail_closed(self):
        from copy import deepcopy
        from quorune.card_programs.binding import bind_semantic_program_runtime
        pairs=(("Death's Shadow",'quantity_expression.current_player_value'),
            ('Civic Saber','quantity_expression.attached_object_value'),
            ('Sliver Legion','continuous.power_toughness.live_quantity'))
        for index,(name,cap) in enumerate(pairs):
            s=self.session(763130+index);source=self.add(s.engine,name,zone='hand')
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
            deps=[c for c in program.capability_dependencies if c!=cap]
            stripped=replace(program,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
            changed=deepcopy(program.handlers)
            if name=="Death's Shadow":changed[0]['fragment']['value']['quantity']['schema_version']=True
            elif name=='Civic Saber':changed[0]['modifier']['quantity']['reduction']='object_count'
            else:changed[0]['modifier']['exclude_recipient']='yes'
            with self.assertRaises(ValueError):replace(program,handlers=changed)

    def test_canonically_verified_withdrawal_names_the_actual_quantity_owner(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        known=set(discover_tests(ROOT).values())
        for name,cap in (("Death's Shadow",'quantity_expression.current_player_value'),
                ('Civic Saber','quantity_expression.attached_object_value'),
                ('Sliver Legion','continuous.power_toughness.live_quantity')):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn live quantity owner'])
            registry=CapabilityRegistry(raw)
            index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
