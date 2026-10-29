"""CR613: coupled removal/addition and distinct independently compiled quotes."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_restricted_mana_grants as native
import test_granted_static_characteristics as equipment_controls
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.composite_grants import COMPOSITE_GRANT_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database


TOXIC='All lands have "{T}: Add one mana of any color" and lose all other abilities.'
BLIGHT='Enchanted land loses all land types and abilities and has "{T}: Add {C}" and "{T}, Pay 1 life: Add one mana of any color."'
MULTI='Enchanted creature has "{T}: This creature deals 1 damage to any target" and "{T}: This creature deals 3 damage to target Werewolf creature."'


class CompositeQuotedGrantCompilerTests(unittest.TestCase):
    def test_descriptor_is_closed_and_emits_removal_before_replacement_at_one_timestamp(self):
        from quorune.semantic_runtime.composite_grants import CompositeAbilityGrantHandler
        from quorune.continuous_effect_model import ContinuousObjectIdentity,Layer
        registry=load_default_capability_registry()
        ir=compile_oracle_card(query_record(BLIGHT),capability_registry=registry,capability_profile='commander_review')
        descriptor=ir.faces[0].nodes[0].handlers[0];handler=CompositeAbilityGrantHandler()
        context=SimpleNamespace(source_object_id='giver',source_ref='giver-ref',source_controller='A',source_timestamp=7,
            component_id='component',attached_object=ContinuousObjectIdentity('land','land:1'))
        effects=handler.lower(descriptor,context)
        self.assertEqual((Layer.TYPE,Layer.ABILITY),tuple(effect.layer for effect in effects))
        self.assertEqual({7},{effect.timestamp for effect in effects})
        self.assertEqual(('remove_all_abilities','add_ability_fragment','add_ability_fragment'),tuple(op.op for op in effects[-1].operations))
        for mutate in (
            lambda value:value.update(schema_version=True),
            lambda value:value.update(unexpected=None),
            lambda value:value['target'].update(controller='A'),
            lambda value:value['modifier'].update(power=1),
            lambda value:value['modifier'].update(add_ability_fragments=[]),
            lambda value:value['modifier'].update(add_ability_fragments=[value['modifier']['add_ability_fragments'][0]]*2),
            lambda value:value['modifier']['type_operations'][0].update(values=['Forest']),
        ):
            changed=deepcopy(descriptor);mutate(changed)
            with self.assertRaises(ValueError):handler.validate(changed)
    def test_removed_star_definition_uses_zero_before_numeric_modifiers_and_keeps_early_boundary(self):
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.characteristic_fragments import QueryPowerToughnessDefinitionSpec,CharacteristicQuantitySpec,CharacteristicQuantityScope
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer
        from quorune.model import CardInstance
        from quorune.object_predicate import ObjectQuerySpec
        card=CardInstance('star-body','star-ref','fixture:star-body','Generic star body','A','A','battlefield')
        fragment=QueryPowerToughnessDefinitionSpec(quantity=CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,query=ObjectQuerySpec(zones=('battlefield',),types_all=('land',))),
            define_power=True,define_toughness=True,toughness_adjustment=1,schema_version=2)
        base={'name':'Generic star body','type_line':'Creature','power':'*','toughness':'1+*',
            'keywords':[],'ability_fragments':[ability_fragment_to_dict(fragment)],'activated_abilities':[]}
        remove=ContinuousEffect('remove','giver',Layer.ABILITY,'6',1,(ContinuousOperation('remove_all_abilities'),))
        active=evaluate_card_characteristics(card,base,query_count_resolver=lambda spec:4)
        self.assertEqual(('4','5'),(active['power'],active['toughness']))
        removed=evaluate_card_characteristics(card,base,runtime_effects=(remove,),query_count_resolver=lambda spec:4)
        self.assertEqual(('0','1'),(removed['power'],removed['toughness']))
        early=evaluate_card_characteristics(card,base,runtime_effects=(remove,),maximum_layer=Layer.COLOR)
        self.assertEqual(('*','1+*'),(early['power'],early['toughness']))
    def test_versioned_creature_land_addition_preserves_old_scope_and_requires_its_owner(self):
        from quorune.basic_type_addition_model import BasicTypeAdditionSpec,CREATURE_LAND_ADDITION_CAPABILITY
        old=BasicTypeAdditionSpec('controller_lands',('forest',))
        self.assertEqual({'scope':'controller_lands','basic_types':['forest']},old.to_dict())
        current=BasicTypeAdditionSpec('controller_nontoken_creatures',('forest',),2)
        self.assertEqual(current,BasicTypeAdditionSpec.from_dict(current.to_dict()))
        for changes in ({'schema_version':1},{'schema_version':True},{'scope':'all_creatures'},
                {'basic_types':['cave']},{'extra':None}):
            with self.assertRaises(ValueError):BasicTypeAdditionSpec.from_dict({**current.to_dict(),**changes})
        ir=compile_oracle_card(query_record('Nontoken creatures you control are Forest lands in addition to their other types.'),
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIn(CREATURE_LAND_ADDITION_CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
    def test_complete_children_have_distinct_consistent_identities_and_atomic_layer_plans(self):
        from quorune.compiler.generated_program_identity import generated_ability_id
        registry=load_default_capability_registry()
        for text,count,removal in ((TOXIC,1,True),(BLIGHT,2,True),(MULTI,2,False),
                ('Creatures you control have "{T}: Add {G}." and "{T}: Add {G}."',2,False)):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                parent,*children=ir.faces[0].nodes;self.assertEqual(count,len(children))
                modifier=parent.handlers[0]['modifier'];self.assertEqual(removal,modifier['remove_all_abilities'])
                fragments=modifier['add_ability_fragments'];self.assertEqual(count,len(fragments))
                keys=[f['value']['semantic_key'] for f in fragments];self.assertEqual(count,len(set(keys)))
                for slot,(child,fragment) in enumerate(zip(children,fragments),1):
                    ability_id=generated_ability_id(kind=child.kind,face_id='front',line=child.span.line,static_declaration=False,node_id=child.node_id)
                    self.assertEqual(fragment['value']['ability_id'],ability_id)
                    self.assertTrue(ability_id.endswith(':quote:'+str(slot)))
                self.assertIn(CAPABILITY,parent.capability_dependencies)
        from quorune.compiler.attached_granted_ability_nodes import attached_granted_program_ability_id
        self.assertEqual('ability:granted:front:n2',attached_granted_program_ability_id(kind='granted_mana_ability',face_id='front',line=2))

    def test_unknown_child_tails_and_unrepresented_settings_keep_the_whole_clause_residual(self):
        registry=load_default_capability_registry()
        for text in (BLIGHT.replace('Add {C}','Rewrite history'),MULTI+' Draw a card.',
                BLIGHT.replace('land types and abilities','colors and abilities'),
                'All Caves lose all land types and abilities and have "{T}: Add {C}."',
                TOXIC.replace('All lands','Cards in your library')):
            with self.subTest(text=text):self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)

    def test_verified_composite_and_inner_owner_withdrawal_fail_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        from quorune.basic_type_addition_model import CREATURE_LAND_ADDITION_CAPABILITY
        for owner in (CAPABILITY,'continuous.characteristics.fixed_public_setting','mana.activated.fixed_output',CREATURE_LAND_ADDITION_CAPABILITY,'variant.monarch.designate'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['composite authority withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            text='Nontoken creatures you control are Forest lands in addition to their other types.' if owner==CREATURE_LAND_ADDITION_CAPABILITY else TOXIC
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',owner)


class CompositeQuotedGrantNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.RestrictedManaGrantNativeTests.session
    add=native.RestrictedManaGrantNativeTests.add
    prepare=native.RestrictedManaGrantNativeTests.prepare
    cast=native.RestrictedManaGrantNativeTests.cast
    cast_for=native.RestrictedManaGrantNativeTests.cast_for
    action=native.RestrictedManaGrantNativeTests.action
    priority=native.RestrictedManaGrantNativeTests.priority
    checkpoint=native.RestrictedManaGrantNativeTests.checkpoint
    resolve=native.RestrictedManaGrantNativeTests.resolve
    replay=native.RestrictedManaGrantNativeTests.replay
    assert_closed=native.RestrictedManaGrantNativeTests.assert_closed
    activate_mana=native.RestrictedManaGrantNativeTests.activate_mana
    granted_action=native.RestrictedManaGrantNativeTests.granted_action
    equip=equipment_controls.GrantedStaticCharacteristicNativeTests.equip

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'composite-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/composite-quoted-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Composite grants',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_actual_toxicrene_replaces_intrinsic_mana_and_restores_it_after_departure(self):
        session=self.session(613616001)
        source=self.add(session.engine,'Toxicrene')
        land=self.add(session.engine,'Island');other=self.add(session.engine,'Sol Ring')
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.add(session.engine,'Island',seat='B',ref='opponent-blue-land')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        abilities=session.engine._activated_abilities(session.state.cards[land.object_id])
        self.assertEqual(1,len(abilities));self.assertTrue(abilities[0].ability_id.startswith('ability:granted:'))
        self.assertEqual({'island'},set(session.engine._type_parts(session.engine._effective_card_data(land)['type_line'])[1]))
        self.assertEqual(2,sum(next(a for a in session.engine._activated_abilities(other) if a.mana_ability).fixed_mana_outputs[0].bundle.values()))
        self.activate_mana(session,'A',land,'R');self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        self.cast(session,'B',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        restored=session.engine._activated_abilities(session.state.cards[land.object_id])
        self.assertEqual(1,len(restored));self.assertEqual('intrinsic_island',restored[0].ability_id)
        self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_blight_draws_preserves_basic_supertype_and_keeps_two_distinct_costs(self):
        session=self.session(613616010)
        aura=self.add(session.engine,'Lithoform Blight',zone='hand')
        land=self.add(session.engine,'Island',seat='B')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':1,'B':1});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,'A',aura,[land.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        current=session.state.cards[land.object_id]
        types,subtypes,supertypes=session.engine._type_parts(session.engine._effective_card_data(current)['type_line'])
        self.assertEqual({'land'},set(types));self.assertFalse(subtypes);self.assertIn('basic',supertypes)
        abilities=session.engine._activated_abilities(current);self.assertEqual(2,len(abilities))
        self.assertEqual({0,1},{a.life_payment for a in abilities});self.assertEqual(2,len({a.ability_id for a in abilities}))
        paid=next(a for a in abilities if a.life_payment==1)
        self.priority(session,'B');before=authoritative_state_hash(session.state);life=session.state.players['B'].life
        wrong={'action_id':'activate:'+current.ref+':'+next(a.ability_id for a in abilities if a.life_payment==0),'mana_output':{'G':1}}
        self.assertFalse(session.act('pilot:B',wrong).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'activate:'+current.ref+':'+paid.ability_id,'mana_output':{'G':1},'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(life-1,session.state.players['B'].life)
        self.assertEqual(1,session.state.players['B'].mana_pool['G']);self.replay(session,load=True)

    def test_runtime_composite_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.basic_type_addition_model import CREATURE_LAND_ADDITION_CAPABILITY
        session=self.session(613616020)
        for name,owner in (('Toxicrene',CAPABILITY),('Ashaya, Soul of the Wild',CREATURE_LAND_ADDITION_CAPABILITY)):
            source=self.add(session.engine,name);checked=0
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if owner not in program.capability_dependencies:continue
                checked+=1;deps=tuple(cap for cap in program.capability_dependencies if cap!=owner)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+owner,bound['blockers'])
            self.assertGreater(checked,0)

    def test_actual_ashaya_type_layer_continues_after_toxicrene_removes_its_abilities(self):
        session=self.session(613617001)
        ashaya=self.add(session.engine,'Ashaya, Soul of the Wild');ashaya.counters['+1/+1']=1
        body=self.add(session.engine,'Memnite');body.acquired_control_turn_count=-1
        token=self.add(session.engine,'Memnite',ref='token-body');token.is_token=True;token.object_kind='token'
        toxic=self.add(session.engine,'Toxicrene')
        self.add(session.engine,'Island')
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.add(session.engine,'Island',seat='B',ref='type-layer-opponent-land')
        opponent=self.add(session.engine,'Llanowar Elves',seat='B')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,ashaya);self.assert_closed(session,toxic)
        self.prepare(session);self.checkpoint(session)
        for card in (ashaya,body,toxic):
            data=session.engine._effective_card_data(session.state.cards[card.object_id])
            types,subtypes,_=session.engine._type_parts(data['type_line'])
            self.assertIn('land',types);self.assertIn('creature',types);self.assertIn('forest',subtypes)
            self.assertEqual(1,len(session.engine._activated_abilities(session.state.cards[card.object_id])))
        for card in (token,opponent):self.assertNotIn('land',session.engine._type_parts(session.engine._effective_card_data(card)['type_line'])[0])
        self.assertEqual((1,1),tuple(session.engine._numeric_stat(ashaya.object_id,stat) for stat in ('power','toughness')))
        self.activate_mana(session,'A',body,'R')
        self.cast(session,'B',removal,[toxic.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((4,4),tuple(session.engine._numeric_stat(ashaya.object_id,stat) for stat in ('power','toughness')))
        self.assertTrue(any(a.ability_id=='intrinsic_forest' for a in session.engine._activated_abilities(session.state.cards[body.object_id])))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_source_participation_uses_earlier_and_later_ability_removal(self):
        for earlier in (False,True):
            with self.subTest(removal_before_source=earlier):
                session=self.session(613618010+earlier)
                ashaya=self.add(session.engine,'Ashaya, Soul of the Wild');ashaya.counters['+1/+1']=1
                if earlier:self.add(session.engine,'Dress Down',seat='B')
                toxic=self.add(session.engine,'Toxicrene')
                if not earlier:self.add(session.engine,'Dress Down',seat='B')
                land=self.add(session.engine,'Island',seat='B')
                self.prepare(session,actor='B');self.checkpoint(session)
                abilities=session.engine._activated_abilities(session.state.cards[land.object_id])
                self.assertEqual(1,len(abilities))
                self.assertEqual(earlier,abilities[0].ability_id=='intrinsic_island')
                self.assertFalse(session.engine._activated_abilities(session.state.cards[toxic.object_id]))
                action=next(r for r in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+land.ref+':'))
                color='U' if earlier else 'R'
                result=session.act('pilot:B',{'action_id':action['id'],'mana_output':{color:1}});self.assertTrue(result.ok,result.summary)

    def test_actual_global_keyword_source_respects_participation_before_its_removal(self):
        for earlier in (False,True):
            with self.subTest(keyword_source_earlier=earlier):
                session=self.session(613618020+earlier)
                ashaya=self.add(session.engine,'Ashaya, Soul of the Wild');ashaya.counters['+1/+1']=1
                if earlier:self.add(session.engine,'Winged Sliver')
                self.add(session.engine,'Toxicrene')
                if not earlier:self.add(session.engine,'Winged Sliver')
                sliver=self.add(session.engine,'Generic Composite Sliver',seat='B')
                self.prepare(session);self.checkpoint(session)
                self.assertEqual(earlier,'flying' in session.engine._combat_keywords(session.state.cards[sliver.object_id]))

    def test_actual_quiver_distinct_children_use_receiver_damage_and_independent_target_rules(self):
        session=self.session(613617010)
        source=self.add(session.engine,"Wolfhunter's Quiver")
        body=self.add(session.engine,'Memnite');body.acquired_control_turn_count=-1
        wolf=self.add(session.engine,'Generic Composite Werewolf',seat='B')
        human=self.add(session.engine,'Llanowar Elves',seat='B')
        untap=self.add(session.engine,'Twiddle',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':5,'U':1});self.checkpoint(session)
        self.equip(session,source,body,reserve={'U':1})
        abilities=[a for a in session.engine._activated_abilities(session.state.cards[body.object_id]) if a.ability_id.startswith('ability:granted:')]
        self.assertEqual(2,len(abilities));first=next(a for a in abilities if a.ability_id.endswith(':quote:1'))
        second=next(a for a in abilities if a.ability_id.endswith(':quote:2'))
        result=session.act('pilot:A',{'action_id':'activate:'+body.ref+':'+first.ability_id,'targets':['B'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.assertEqual(39,session.state.players['B'].life)
        self.cast(session,'A',untap,[body.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.priority(session,'A');before=authoritative_state_hash(session.state)
        command={'action_id':'activate:'+body.ref+':'+second.ability_id,'targets':[human.ref],'pay':'auto'}
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        command['targets']=[wolf.ref];result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(3,session.state.cards[wolf.object_id].marked_damage)
        self.replay(session,load=True)

    def test_omitted_removal_mutant_is_killed_by_original_intrinsic_mana_catalog(self):
        import quorune.semantic_runtime.fixed_characteristic_settings as owner
        original=owner.fixed_characteristic_effects
        def mutant(node,context,*,common):return original(replace(node,remove_all_abilities=False),context,common=common)
        with patch.object(owner,'fixed_characteristic_effects',side_effect=mutant),self.assertRaises(AssertionError):
            self.test_actual_toxicrene_replaces_intrinsic_mana_and_restores_it_after_departure()

    def test_omitted_land_card_type_mutant_is_killed_by_original_ashaya(self):
        import quorune.semantic_runtime.basic_type_addition as owner
        original=owner.ScopedBasicTypeAdditionHandler.lower
        def mutant(handler,descriptor,context):
            return tuple(replace(effect,operations=tuple(op for op in effect.operations if op.field!='card_types'))
                for effect in original(handler,descriptor,context))
        with patch.object(owner.ScopedBasicTypeAdditionHandler,'lower',mutant),self.assertRaises(AssertionError):
            self.test_actual_ashaya_type_layer_continues_after_toxicrene_removes_its_abilities()
