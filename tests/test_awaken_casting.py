"""Awaken keeps its price, paid-only target and lasting layered result distinct."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from quorune.awaken_model import FixedManaAwakenSpec
from quorune.cleave_model import FixedManaCleaveSpec
from quorune.overload_model import FixedManaOverloadSpec
from quorune.spell_text_variants import SpellTextVariantKind
from quorune.continuous_effect_model import ContinuousEffectDuration,Layer
from quorune.resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION,FixedResolutionCharacteristicsSpec,IndefiniteTargetLandAnimationSpec,
    INDEFINITE_TARGET_LAND_ANIMATION_VERSION,INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC,
    INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY,fixed_resolution_characteristic_instruction,
)
from quorune.rules.resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities
from quorune.awaken_model import AWAKEN_PAID_MECHANIC,AWAKEN_CAPABILITY
from quorune.spell_text_variants import spell_body_fingerprint
from quorune.target_clause_binding import scope_clause_effect
from quorune.rules.awaken_shapes import ordinary_awaken_body,awaken_node_capabilities,awaken_result_effects,awaken_land_target_schema


class AwakenValueTests(unittest.TestCase):
    def settings(self):
        return FixedResolutionCharacteristicsSpec(card_types=('Creature',),creature_subtypes=('Elemental',),
            retain_types=True,retain_creature_subtypes=True,keywords=('Haste',),base_power=0,base_toughness=0)

    def test_price_and_target_flag_are_closed_without_changing_existing_paid_variant_codecs(self):
        mana={'GENERIC':4,**{color:int(color=='W') for color in 'WUBRGC'}}
        spec=FixedManaAwakenSpec('front:awaken:line:2',mana,4,True,'a'*64,'b'*64,('cr-121-drawing-a-card',))
        self.assertEqual(spec,FixedManaAwakenSpec.from_dict(spec.to_dict()))
        mana['GENERIC']=99;self.assertEqual(4,spec.requirements['GENERIC'])
        for change in ({'count':True},{'count':0},{'count':-1},{'normal_targeted':1},
                       {'normal_targeted':None},{'schema_version':True},{'schema_version':2},
                       {'awakened_fingerprint':'a'*64},{'normal_mechanics':[]},{'normal_mechanics':True},{'extra':0}):
            with self.subTest(change=change),self.assertRaises((TypeError,ValueError)):
                FixedManaAwakenSpec.from_dict({**spec.to_dict(),**change})
        for kind,cls,field in ((SpellTextVariantKind.CLEAVE,FixedManaCleaveSpec,'cleaved_fingerprint'),
                               (SpellTextVariantKind.OVERLOAD,FixedManaOverloadSpec,'overloaded_fingerprint')):
            old=cls('front:legacy:line:2',dict(spec.requirements),'a'*64,'b'*64)
            self.assertEqual(field,kind.paid_fingerprint_field)
            self.assertEqual({'schema_version':1,'instance_id':'front:legacy:line:2','requirements':dict(spec.requirements),
                'normal_fingerprint':'a'*64,field:'b'*64},old.to_dict())

    def test_lasting_targeted_land_form_uses_existing_retaining_layers_and_closed_target_instructions(self):
        spec=IndefiniteTargetLandAnimationSpec(self.settings())
        self.assertEqual(spec,IndefiniteTargetLandAnimationSpec.from_dict(spec.to_dict()))
        self.assertIs(ContinuousEffectDuration.ZONE_OBJECT,spec.duration)
        layers=spec.layer_operations();self.assertEqual([Layer.TYPE,Layer.ABILITY,Layer.POWER_TOUGHNESS],[row[0] for row in layers])
        self.assertTrue(all(operation.op=='add_types' for operation in layers[0][2]))
        self.assertEqual(('Creature',),layers[0][2][0].value);self.assertEqual(('Elemental',),layers[0][2][1].value)
        self.assertEqual('Haste',layers[1][2][0].value);self.assertEqual((0,0),layers[2][2][0].value)
        effect={'op':CHARACTERISTIC_OPERATION,'schema_version':INDEFINITE_TARGET_LAND_ANIMATION_VERSION,
                'card':'$target.0','characteristics':spec.to_dict()}
        self.assertEqual((spec,'$target.0'),fixed_resolution_characteristic_instruction(effect))
        self.assertEqual((spec,'$target_group.clause_1.0'),fixed_resolution_characteristic_instruction({**effect,'card':'$target_group.clause_1.0'}))
        self.assertEqual((spec,'bound-land'),fixed_resolution_characteristic_instruction({**effect,'card':'bound-land'},resolved=True))
        for change in ({'schema_version':True},{'card':'$source.zone_object'},{'card':'$target_group.bad'},
                       {'extra':0},{'characteristics':{**spec.to_dict(),'duration':'until_end_of_turn'}}):
            with self.subTest(change=change),self.assertRaises(ValueError):fixed_resolution_characteristic_instruction({**effect,**change})
        for change in ({'retain_types':False,'retain_creature_subtypes':False},{'retain_creature_subtypes':False},
                       {'remove_all_abilities':True},{'colors':('G',)},{'card_types':None,'retain_types':False,'retain_creature_subtypes':False}):
            with self.subTest(change=change),self.assertRaises(ValueError):IndefiniteTargetLandAnimationSpec(replace(self.settings(),**change))

    def test_targeted_land_shape_requires_the_controller_target_and_its_distinct_capability(self):
        spec=IndefiniteTargetLandAnimationSpec(self.settings())
        effect={'op':CHARACTERISTIC_OPERATION,'schema_version':INDEFINITE_TARGET_LAND_ANIMATION_VERSION,
                'card':'$target.0','characteristics':spec.to_dict()}
        schema={'zones':['battlefield'],'categories':['permanent'],'count':1,'types_any':['land'],'controller_relation':'you'}
        mechanics=('cr-611-continuous-effects','cr-115-targets',INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC)
        owned=fixed_resolution_characteristic_capabilities(effects=(effect,),target_schema=schema,mechanic_ids=mechanics)
        self.assertIn(INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY,owned);self.assertIn('target.revalidate_resolution',owned)
        for missing in mechanics:
            self.assertFalse(fixed_resolution_characteristic_capabilities(effects=(effect,),target_schema=schema,
                mechanic_ids=tuple(value for value in mechanics if value!=missing)))
        for change in ({'controller_relation':'any'},{'types_any':['creature']},{'count':2}):
            self.assertFalse(fixed_resolution_characteristic_capabilities(effects=(effect,),target_schema={**schema,**change},mechanic_ids=mechanics))

    def paid_body(self,targeted):
        from quorune.oracle_ir import _reviewed_effect_template
        from quorune.compiler.program_composition import _composed_target_schema
        text='Counter target spell.' if targeted else 'Draw two cards.'
        template,body,schema,mechanics=_reviewed_effect_template(text,card_name='Awaken Binding Witness',source_is_permanent=False)
        self.assertIsNotNone(template);self.assertTrue(body)
        paid_schema=_composed_target_schema([schema,awaken_land_target_schema()]) if targeted else awaken_land_target_schema()
        paid=tuple(scope_clause_effect(effect,0) for effect in body) if targeted else tuple(body)
        paid+=awaken_result_effects(1,'$target_group.clause_1.0' if targeted else '$target.0')
        mana={'GENERIC':4,**{color:int(color=='U') for color in 'WUBRGC'}}
        spec=FixedManaAwakenSpec('front:awaken:line:2',mana,1,targeted,
            spell_body_fingerprint(body,schema),spell_body_fingerprint(paid,paid_schema),tuple(mechanics))
        return spec,body,schema,paid,paid_schema,mechanics

    def test_paid_body_preserves_the_original_target_role_and_infers_both_land_owners_without_a_producer_marker(self):
        from quorune.rules.capabilities import capability_dependencies_for_node
        for targeted in (False,True):
            spec,body,schema,paid,paid_schema,mechanics=self.paid_body(targeted)
            self.assertEqual((tuple(body),schema),ordinary_awaken_body(spec,paid,paid_schema))
            if targeted:
                self.assertEqual(['stack'],paid_schema['groups'][0]['zones'])
                self.assertEqual(['battlefield'],paid_schema['groups'][1]['zones'])
                self.assertEqual('$target_group.clause_0.0',paid[0]['stack'])
                self.assertEqual('$target_group.clause_1.0',paid[-1]['card'])
            required=capability_dependencies_for_node(effects=paid,target_schema=paid_schema,
                mechanic_ids=(*mechanics,AWAKEN_PAID_MECHANIC),cost_schema={'awaken':spec.to_dict()})
            self.assertTrue({AWAKEN_CAPABILITY,INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY,
                'counter.producer.fixed_effect','target.revalidate_resolution'}<=set(required))
            without_marker=capability_dependencies_for_node(effects=paid,target_schema=paid_schema,
                mechanic_ids=mechanics,cost_schema={'awaken':spec.to_dict()})
            self.assertEqual(required,without_marker)

    def test_paid_body_correlations_reject_recomputed_fingerprints_for_wrong_order_count_or_target_roles(self):
        from copy import deepcopy
        for targeted in (False,True):
            spec,body,schema,paid,paid_schema,mechanics=self.paid_body(targeted)
            mutations=[]
            wrong_order=list(deepcopy(paid));wrong_order[-2:]=reversed(wrong_order[-2:]);mutations.append((wrong_order,paid_schema))
            boolean_count=list(deepcopy(paid));boolean_count[-2]['amount']=True;mutations.append((boolean_count,paid_schema))
            changed_count=list(deepcopy(paid));changed_count[-2]['amount']=2;mutations.append((changed_count,paid_schema))
            changed_schema=deepcopy(paid_schema)
            if targeted:
                changed_schema['groups'][1]['min']=True
                escaped=list(deepcopy(paid));escaped[0]['stack']='$target_group.clause_1.0';mutations.append((escaped,paid_schema))
            else:changed_schema['count']=True
            mutations.append((paid,changed_schema));mutations.append((paid[:-1],paid_schema))
            for effects,targets in mutations:
                forged=replace(spec,awakened_fingerprint=spell_body_fingerprint(effects,targets))
                with self.assertRaises(ValueError):ordinary_awaken_body(forged,effects,targets)
                self.assertFalse(awaken_node_capabilities(effects=effects,target_schema=targets,
                    mechanics=(*mechanics,AWAKEN_PAID_MECHANIC),cost_schema={'awaken':forged.to_dict()}))

    def test_payload_minimum_and_validator_omission_detect_a_missing_paid_animation(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        spec,body,schema,paid,paid_schema,mechanics=self.paid_body(False)
        required=typed_result_amount_runtime_dependencies(paid[-1])
        self.assertIn(INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY,required)
        missing=paid[:-1];forged=replace(spec,awakened_fingerprint=spell_body_fingerprint(missing,paid_schema))
        def witness():self.assertFalse(awaken_node_capabilities(effects=missing,target_schema=paid_schema,
            mechanics=(*mechanics,AWAKEN_PAID_MECHANIC),cost_schema={'awaken':forged.to_dict()}))
        witness()
        with patch('quorune.rules.awaken_shapes.ordinary_awaken_body',return_value=(body,schema)):
            with self.assertRaises(AssertionError):witness()


from pathlib import Path
import json,tempfile
from common import ROOT,packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class AwakenNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'awaken.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/awaken-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Awaken',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def coastal(self,mana):
        s=self.session(1006400+sum(mana.values()));source=self.add(s.engine,'Coastal Discovery',zone='hand')
        land=self.add(s.engine,'Forest',ref='awaken-own-land')
        foreign=self.add(s.engine,'Forest',seat='B',ref='awaken-foreign-land')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='awaken-private-C')
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana=mana);self.checkpoint(s);return s,source,land,foreign,private

    def scene(self,name,*,land_name='Forest',before_ready=None):
        s=self.session(1006500+len(name));source=self.add(s.engine,name,zone='hand')
        land=self.add(s.engine,land_name,ref='awaken-scene-land')
        body=self.add(s.engine,'Grizzly Bears',seat='B',ref='awaken-scene-body');body.tapped=name=='Sheer Drop'
        other=self.add(s.engine,'Wind Drake',seat='C',ref='awaken-scene-other')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='awaken-private-C')
        if before_ready is not None:before_ready(s,source,land,body,other)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana={'C':20,**{color:10 for color in 'WUBRG'}});self.checkpoint(s)
        return s,source,land,body,other,private

    def cast_awaken(self,s,source,targets,*,paid=True):
        self.priority(s,'A')
        row=self.action(s,'A',s.state.cards[source.object_id]);option='awaken' if paid else next(
            item['id'] for item in row['cost_options'] if item['id']!='awaken')
        command={'action_id':row['id'],'cost_option':option,'targets':list(targets),'pay':'auto'}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        return next(item for item in s.state.stack if item.card_object_id==source.object_id)

    def test_actual_ordinary_and_paid_routes_keep_distinct_prices_targets_and_layered_land_results(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                s,source,land,foreign,private=self.coastal({'C':5,'U':1});before=set(s.state.players['A'].zones['hand'])
                row=self.action(s,'A',source);self.assertIn('awaken',[option['id'] for option in row['cost_options']])
                item=self.cast_awaken(s,source,[land.ref] if paid else [],paid=paid)
                self.assertEqual(6 if paid else 4,item.context['mana_spent_total'])
                self.assertEqual(paid,str(item.semantic_key).endswith(':awaken'))
                self.assertEqual(4,s.engine._effective_card_data(s.state.cards[source.object_id])['mana_value'])
                self.assertIsNone(self.resolve(s))
                drawn=set(s.state.players['A'].zones['hand'])-(before-{source.object_id});self.assertEqual(2,len(drawn))
                for viewer in 'BCD':
                    packet=json.dumps(packet_without_decision_capability(s.packet('pilot:'+viewer,full=True)))
                    for oid in drawn:self.assertNotIn(s.state.cards[oid].ref,packet)
                data=s.engine._effective_card_data(s.state.cards[land.object_id]);types,subtypes,_=s.engine._type_parts(data['type_line'])
                self.assertIn('land',types);self.assertIn('forest',subtypes);self.assertEqual([],list(data['colors']))
                self.assertEqual(paid,'creature' in types)
                if paid:
                    self.assertIn('elemental',subtypes);self.assertIn('haste',{word.casefold() for word in data['keywords']})
                    self.assertEqual(4,s.state.cards[land.object_id].counters['+1/+1'])
                    self.assertEqual((4,4),tuple(s.engine._numeric_stat(land.object_id,stat) for stat in ('power','toughness')))
                    self.assertFalse(s.state.cards[land.object_id].tapped)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_underpayment_foreign_land_and_paid_price_omission_cannot_admit_free_awaken(self):
        from copy import deepcopy
        from quorune.rules.casting.proposal import build_cast_offer
        from quorune.rules.casting import fixed_text_variant_costs as owner
        s,source,land,foreign,private=self.coastal({'C':3,'U':1})
        def witness():
            built=build_cast_offer(s.engine,'A',s.state.cards[source.object_id])
            self.assertIsNotNone(built.offer)
            self.assertNotIn('awaken',[option.option_id for option in built.cost_options])
        witness();self.reject(s,'A',{'action_id':'cast:'+source.ref,'cost_option':'awaken','targets':[land.ref],'pay':'auto'})
        self.finish(s,private)
        mutation_scene,mutation_source,mutation_land,mutation_foreign,mutation_private=self.coastal({'C':3,'U':1})
        def mutated_witness():
            built=build_cast_offer(mutation_scene.engine,'A',mutation_scene.state.cards[mutation_source.object_id])
            self.assertNotIn('awaken',[option.option_id for option in built.cost_options])
        original=owner.fixed_variant_cost_options
        def omitted(*args,**kwargs):
            result=original(*args,**kwargs)
            if kwargs['kind'] is SpellTextVariantKind.AWAKEN and result:
                result=deepcopy(result)
                for option in result:option['requirements']={key:0 for key in option['requirements']}
            return result
        with patch.object(owner,'fixed_variant_cost_options',side_effect=omitted):
            with self.assertRaises(AssertionError):mutated_witness()
        s,source,land,foreign,private=self.coastal({'C':5,'U':1})
        self.reject(s,'A',{'action_id':'cast:'+source.ref,'cost_option':'awaken','targets':[foreign.ref],'pay':'auto'})
        self.cast_awaken(s,source,[land.ref]);self.assertIsNone(self.resolve(s));self.finish(s,private)

    def test_actual_original_bodies_keep_their_own_target_roles_before_counters_and_animation(self):
        counts={"Mire's Malice":3,'Earthen Arms':4,'Clutch of Currents':3,'Ruinous Path':4,
                'Sheer Drop':3,'Rush of Ice':3,'Roil Spout':4}
        for name,count in counts.items():
            with self.subTest(name=name):
                s,source,land,body,other,private=self.scene(name)
                target='B' if name=="Mire's Malice" else land.ref if name=='Earthen Arms' else body.ref
                before=set(s.state.players['B'].zones['hand']);self.cast_awaken(s,source,[target,land.ref])
                decision=self.resolve(s)
                if name=="Mire's Malice":
                    self.assertIsNotNone(decision);self.assertEqual('choice.apnap',decision.kind)
                    cards=[s.state.cards[oid].ref for oid in list(before)[:2]]
                    self.reject(s,'A',{'action_id':'choose','cards':cards})
                    result=s.act('pilot:B',{'action_id':'choose','cards':cards});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s));self.assertEqual(len(before)-2,len(s.state.players['B'].zones['hand']))
                else:self.assertIsNone(decision)
                if name in ('Ruinous Path','Sheer Drop'):self.assertIn(body.object_id,s.state.players['B'].zones['graveyard'])
                if name=='Clutch of Currents':self.assertIn(body.object_id,s.state.players['B'].zones['hand'])
                if name=='Roil Spout':self.assertEqual(body.object_id,s.state.players['B'].zones['library'][-1])
                if name=='Rush of Ice':self.assertTrue(s.state.cards[body.object_id].tapped)
                expected=count+2 if name=='Earthen Arms' else count
                self.assertEqual(expected,s.state.cards[land.object_id].counters['+1/+1'])
                self.assertEqual((expected,expected),tuple(s.engine._numeric_stat(land.object_id,stat) for stat in ('power','toughness')))
                self.finish(s,private)

    def test_actual_nontargeted_mass_bodies_resolve_before_the_land_becomes_a_creature(self):
        for name,count in (('Rising Miasma',3),('Boiling Earth',4),('Planar Outburst',4)):
            with self.subTest(name=name):
                s,source,land,body,other,private=self.scene(name)
                self.cast_awaken(s,source,[land.ref]);self.assertIsNone(self.resolve(s))
                if name=='Boiling Earth':
                    self.assertEqual(1,s.state.cards[body.object_id].marked_damage)
                    self.assertEqual(1,s.state.cards[other.object_id].marked_damage)
                else:
                    self.assertIn(body.object_id,s.state.players['B'].zones['graveyard'])
                    self.assertIn(other.object_id,s.state.players['C'].zones['graveyard'])
                self.assertEqual('battlefield',s.state.cards[land.object_id].zone)
                self.assertEqual((count,count),tuple(s.engine._numeric_stat(land.object_id,stat) for stat in ('power','toughness')))
                self.finish(s,private)

    def test_actual_mixed_spell_and_land_targets_resolve_each_legal_role_and_reject_a_returned_incarnation(self):
        for invalid in ('none','spell','land','both'):
            with self.subTest(invalid=invalid):
                extras={}
                def prepare(s,source,land,body,other):
                    s.state.players['B'].mana_pool.update({'U':1});s.state.players['D'].mana_pool.update({'U':2})
                    extras['original']=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='awaken-original-spell')
                    extras['answer']=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='awaken-other-counter')
                    extras['blink']=self.add(s.engine,'Cloudshift',zone='hand',ref='awaken-land-blink')
                s,source,land,body,other,private=self.scene('Scatter to the Winds',land_name='Dryad Arbor',before_ready=prepare)
                original=extras['original'];answer=extras['answer'];blink=extras['blink']
                self.cast(s,'B',original);spell=next(item for item in s.state.stack if item.card_object_id==original.object_id)
                self.cast_awaken(s,source,[spell.ref,land.ref]);identity=s.state.cards[land.object_id].logical_object_id
                if invalid in ('spell','both'):
                    self.cast(s,'D',answer,[spell.ref]);self.resolve_top(s)
                if invalid in ('land','both'):
                    self.cast(s,'A',blink,[s.state.cards[land.object_id].ref]);self.resolve_top(s)
                    self.assertNotEqual(identity,s.state.cards[land.object_id].logical_object_id)
                self.assertIsNone(self.resolve(s));current=s.state.cards[land.object_id]
                if invalid in ('none','spell'):
                    self.assertEqual(3,current.counters['+1/+1']);self.assertEqual(3,s.engine._numeric_stat(land.object_id,'power'))
                else:
                    self.assertFalse(current.counters);self.assertEqual(1,s.engine._numeric_stat(land.object_id,'power'))
                self.assertIn(original.object_id,s.state.players['B'].zones['graveyard']);self.finish(s,private)

    def test_actual_counter_replacement_and_prohibition_do_not_turn_animation_into_an_earthbend_return(self):
        for provider,count,destination in (('Doubling Season',8,'battlefield'),('Solemnity',0,'graveyard')):
            with self.subTest(provider=provider):
                def prepare(s,source,land,body,other):
                    replacement=self.add(s.engine,provider);self.assert_closed(s,replacement)
                s,source,land,body,other,private=self.scene('Coastal Discovery',before_ready=prepare)
                self.cast_awaken(s,source,[land.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual(destination,s.state.cards[land.object_id].zone)
                self.assertFalse(any('earthbend' in str(trigger.to_dict()).casefold() for trigger in s.state.delayed_triggers))
                if count:
                    self.assertEqual(count,s.state.cards[land.object_id].counters['+1/+1'])
                    self.assertEqual(count,s.engine._numeric_stat(land.object_id,'power'))
                self.finish(s,private)

    def test_actual_animation_retains_old_subtypes_and_abilities_across_cleanup_then_ends_on_blink(self):
        extras={}
        def prepare(s,source,land,body,other):
            extras['growth']=self.add(s.engine,'Giant Growth',zone='hand',ref='awaken-layer-growth')
            extras['blink']=self.add(s.engine,'Cloudshift',zone='hand',ref='awaken-lasting-blink')
            self.add(s.engine,'Plains',ref='awaken-next-turn-white')
            removable=[oid for oid in s.state.players['A'].zones['hand'] if oid!=source.object_id
                       and s.state.cards[oid].printed_name=='Generic Bound Plains']
            for oid in removable[:5]:s.engine.move_card(oid,'library',position='bottom',log=False)
        s,source,land,body,other,private=self.scene('Coastal Discovery',land_name='Dryad Arbor',before_ready=prepare)
        self.cast_awaken(s,source,[land.ref]);self.assertIsNone(self.resolve(s))
        data=s.engine._effective_card_data(s.state.cards[land.object_id]);self.assertEqual(['G'],list(data['colors']))
        self.assertTrue({'forest','dryad','elemental'}<=set(s.engine._type_parts(data['type_line'])[1]))
        self.cast(s,'A',extras['growth'],[s.state.cards[land.object_id].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(7,s.engine._numeric_stat(land.object_id,'power'))
        self.until(s,lambda current:current.state.active_player=='B' and current.state.phase=='precombat_main'
                   and current.state.pending_decision is not None and current.state.pending_decision.kind=='priority')
        self.assertEqual(4,s.engine._numeric_stat(land.object_id,'power'));identity=s.state.cards[land.object_id].logical_object_id
        s=self.replay(s,load=True);self.cast(s,'A',s.state.cards[extras['blink'].object_id],[s.state.cards[land.object_id].ref])
        self.assertIsNone(self.resolve(s));current=s.state.cards[land.object_id]
        self.assertNotEqual(identity,current.logical_object_id);self.assertFalse(current.counters)
        self.assertEqual(1,s.engine._numeric_stat(land.object_id,'power'))
        self.assertNotIn('elemental',s.engine._type_parts(s.engine._effective_card_data(current)['type_line'])[1]);self.finish(s,private)

    def test_actual_cost_reduction_is_applied_to_awaken_and_the_target_land_can_pay_its_own_price(self):
        s=self.session(1006700);source=self.add(s.engine,'Earthen Arms',zone='hand')
        lands=[self.add(s.engine,'Forest',ref='awaken-paying-land-'+str(index)) for index in range(6)]
        self.add(s.engine,'Goblin Electromancer');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='awaken-private-C')
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source);self.prepare(s);self.checkpoint(s)
        item=self.cast_awaken(s,source,[lands[0].ref,lands[0].ref]);self.assertEqual(6,item.context['mana_spent_total'])
        self.assertTrue(all(s.state.cards[land.object_id].tapped for land in lands));self.assertIsNone(self.resolve(s))
        self.assertEqual(6,s.engine._numeric_stat(lands[0].object_id,'power'));self.assertTrue(s.state.cards[lands[0].object_id].tapped)
        self.finish(s,private)

    def test_actual_opponents_spell_copy_retains_awaken_and_can_retarget_both_roles_without_paying_again(self):
        extras={}
        def prepare(s,source,land,body,other):
            s.state.players['B'].mana_pool.update({'U':1,'R':2})
            extras['original']=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='awaken-copy-original')
            extras['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand',ref='awaken-copy-producer')
            extras['land']=self.add(s.engine,'Forest',seat='B',ref='awaken-copy-land')
        s,source,land,body,other,private=self.scene('Scatter to the Winds',before_ready=prepare)
        self.cast(s,'B',extras['original']);first=next(item for item in s.state.stack if item.card_object_id==extras['original'].object_id)
        self.cast_awaken(s,source,[first.ref,land.ref]);original=next(item for item in s.state.stack if item.card_object_id==source.object_id)
        self.cast(s,'B',extras['copy'],[original.ref]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        command={'action_id':'choose','targets':[original.ref,extras['land'].ref]};self.reject(s,'D',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        copied=next(item for item in s.state.stack if item.kind=='spell_copy')
        self.assertEqual(original.semantic_key,copied.semantic_key);self.assertEqual('B',copied.controller)
        self.assertIsNone(self.resolve(s));self.assertEqual(0,sum(s.state.players['B'].mana_pool.values()))
        self.assertEqual(3,s.state.cards[extras['land'].object_id].counters['+1/+1'])
        self.assertFalse(s.state.cards[land.object_id].counters);self.assertIn(source.object_id,s.state.players['A'].zones['graveyard'])
        self.finish(s,private)
