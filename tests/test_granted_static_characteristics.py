"""CR613.7a's original Rune example and current recipient-owned static abilities."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_public_target_spell_costs as cast_actions
import test_ring_native_actions as turns
from test_qualified_zone_event_queries import query_record
from quorune.ability_fragments import StaticComponentSpec, canonical_ability_fragments, static_component_keys, ability_fragment_to_dict
from quorune.carddb import CardDatabase
from quorune.card_programs.runtime import _continuous_program_instances, _granted_characteristic_timestamp_order
from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler, fixed_power_toughness_anthem_handler
from quorune.continuous_effect_model import ContinuousEffect, ContinuousOperation, Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.granted_characteristic_shapes import granted_characteristic_handlers_are_closed, granted_characteristic_occurrences
from quorune.semantic_runtime.continuous_components import FixedQueryAbilityGrantHandler
from quorune.static_component_scope import GRANTED_STATIC_CHARACTERISTIC_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database


class GrantedStaticCharacteristicModelTests(unittest.TestCase):
    def test_marker_codec_preserves_legacy_and_requires_owning_effect_time(self):
        for old in (StaticComponentSpec('printed'), StaticComponentSpec('cost', 2, 'cast.cost.modify')):
            value = old.to_dict()
            self.assertNotIn('grant_timestamp', value)
            self.assertEqual(value, StaticComponentSpec.from_dict(value).to_dict())
        marker = StaticComponentSpec('child', 3, 'characteristics.evaluate')
        self.assertEqual(CAPABILITY, marker.capability_id)
        self.assertEqual(marker, StaticComponentSpec.from_dict(marker.to_dict()))
        for changes in ({'schema_version': 1}, {'schema_version': 2}, {'event': 'draw'},
                {'grant_timestamp': True}, {'grant_timestamp': -1}, {'unexpected': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                StaticComponentSpec.from_dict({**marker.to_dict(), **changes})
        with self.assertRaises(ValueError): granted_characteristic_occurrences((marker,), ('child',))
        self.assertEqual((), granted_characteristic_occurrences((marker,), ()))
        bound = replace(marker, grant_timestamp=3)
        self.assertEqual(2, len(canonical_ability_fragments((bound, bound))))
        self.assertEqual(('child',), static_component_keys((bound, bound)))
        self.assertEqual((('child', 3), ('child', 3)), granted_characteristic_occurrences((bound, bound), ('child',)))

    def test_relative_equipment_order_and_occurrences_have_distinct_effect_identities(self):
        def effect(key):
            return ContinuousEffect(key, 'equipment', Layer.ABILITY, '6', 9,
                (ContinuousOperation('add_ability', 'Flying'),))
        printed, first, second = effect('z-printed'), effect('a-grant-first'), effect('b-grant-second')
        ordered = _granted_characteristic_timestamp_order((first, printed, second),
            {first.effect_id: (3, 0, 'child'), second.effect_id: (5, 1, 'child')})
        by_id = {e.effect_id:e for e in ordered}
        self.assertEqual(('z-printed',), by_id[first.effect_id].depends_on)
        self.assertEqual(('a-grant-first',), by_id[second.effect_id].depends_on)
        self.assertEqual((), by_id[printed.effect_id].depends_on)
        child = SimpleNamespace(key='child', provenance={'granted_only':True}, ability_id='static:granted:front:n4',
            active_zone='battlefield', event='characteristics.evaluate', effects=(),
            handlers=(attached_fixed_characteristics_handler('Equipped creature has flying.')[1],))
        semantics = SimpleNamespace(get=lambda key:child if key=='child' else None)
        instances = _continuous_program_instances(semantics, object(), (child,), lambda source:(('child',3),('child',5)))
        self.assertEqual(((child,3,0),(child,5,1)), instances)
        legacy=replace_namespace(child,ability_id='static:front:n4:role:1',coverage=())
        self.assertEqual(((legacy,None,None),),_continuous_program_instances(semantics,object(),(legacy,),lambda source:()))
        from quorune.rules.keyword_runtime_requirements import keyword_runtime_dependencies
        self.assertNotIn(CAPABILITY,keyword_runtime_dependencies(legacy))
        for value in (None, replace_namespace(child, provenance={'granted_only':False}),
                replace_namespace(child, active_zone='hand'), replace_namespace(child,event='draw')):
            with self.assertRaises(ValueError):
                _continuous_program_instances(SimpleNamespace(get=lambda key:value), object(), (), lambda source:(('child',3),))

    def test_child_shapes_exclude_recursive_grants_and_unrepresented_characteristics(self):
        attached = attached_fixed_characteristics_handler('Equipped creature gets +1/+1 and has trample.')[1]
        anthem = fixed_power_toughness_anthem_handler('Creature tokens you control get +2/+2.')[1]
        self.assertTrue(granted_characteristic_handlers_are_closed((attached,)))
        self.assertTrue(granted_characteristic_handlers_are_closed((anthem,)))
        self.assertFalse(granted_characteristic_handlers_are_closed((attached,anthem)))
        for changes in ({'base_power':3,'base_toughness':3},{'remove_all_abilities':True},{'add_ability_fragments':[
                ability_fragment_to_dict(StaticComponentSpec('recursive',3,'characteristics.evaluate'))]}):
            candidate=deepcopy(attached);candidate['modifier'].update(changes)
            self.assertFalse(granted_characteristic_handlers_are_closed((candidate,)))


def replace_namespace(value, **changes):
    return SimpleNamespace(**{**vars(value),**changes})


class GrantedStaticCharacteristicCompilerTests(unittest.TestCase):
    def test_complete_grants_bind_closed_hidden_children_and_permanent_subjects(self):
        registry=load_default_capability_registry()
        clauses=('Commander creatures you own have "Creature tokens you control get +2/+2."',
            'As long as enchanted permanent is an Equipment, it has "Equipped creature has flying."')
        for text in clauses:
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            outer,child=ir.faces[0].nodes
            self.assertEqual('granted_static_ability',child.kind)
            self.assertIn(CAPABILITY,child.capability_dependencies)
            self.assertIn(CAPABILITY,outer.capability_dependencies)
            self.assertEqual('characteristics.evaluate',child.event)
            self.assertTrue(granted_characteristic_handlers_are_closed(child.handlers))
            if text.startswith('As long'):
                self.assertEqual([],outer.handlers[0]['target']['types_all'])
                from quorune.semantic_runtime.conditional_continuous import FixedPublicStateGrantedAbilityHandler,FixedPublicStateCharacteristicsHandler
                prebound=deepcopy(outer.handlers[0])
                prebound['modifier']['add_ability_fragments'][0]['value']['grant_timestamp']=9
                with self.assertRaises(ValueError):FixedPublicStateGrantedAbilityHandler().validate(prebound)
                legacy=deepcopy(outer.handlers[0])
                legacy.update(handler_id=FixedPublicStateCharacteristicsHandler().handler_id,schema_version=1,
                    modifier={'add_abilities':['Flying'],'power':0,'toughness':0})
                with self.assertRaises(ValueError):FixedPublicStateCharacteristicsHandler().validate(legacy)
                old_cost=deepcopy(outer.handlers[0])
                old_cost['modifier']['add_ability_fragments']=[ability_fragment_to_dict(StaticComponentSpec('cost',2,'cast.cost.modify'))]
                with self.assertRaises(ValueError):FixedPublicStateGrantedAbilityHandler().validate(old_cost)
            else:
                prebound=deepcopy(outer.handlers[0])
                prebound['modifier']['add_ability_fragments'][0]['value']['grant_timestamp']=9
                with self.assertRaises(ValueError):FixedQueryAbilityGrantHandler().validate(prebound)
        for text in ('Commander creatures you own have "Creatures you control are blue."',
            'Commander creatures you own have "Creature tokens you control get +X/+X."',
            'Commander creatures you own have "Creatures you control have flying."',
            clauses[1]+' Draw a card.', clauses[1].replace('Equipment','chosen kind')):
            with self.subTest(text=text): self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)

    def test_verified_grant_and_inner_owner_withdrawal_fail_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        text='Commander creatures you own have "Creature tokens you control get +2/+2."'
        for omitted in (CAPABILITY,'continuous.ability.fixed_query_grant','continuous.power_toughness.fixed_anthem','variant.monarch.designate'):
            value=deepcopy(raw)
            next(r for r in value['capabilities'] if r['id']==omitted).update(status='blocked',blockers=['static grant owner omitted'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertEqual(omitted=='variant.monarch.designate',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status=='exact',omitted)


class GrantedStaticCharacteristicNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay
    assert_closed=actions.JobSelectRuntimeTests.assert_closed
    priority=cast_actions.PublicTargetSpellCostNativeTests.priority
    cast_reserved=cast_actions.PublicTargetSpellCostNativeTests.cast_reserved
    action=cast_actions.PublicTargetSpellCostNativeTests.action

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'static-characteristic-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/granted-static-characteristic-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Granted static characteristics',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def stats(self,session,card):
        return tuple(session.engine._numeric_stat(card.object_id,stat) for stat in ('power','toughness'))

    def equip(self,session,equipment,body,*,reserve=None):
        self.priority(session,'A')
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+session.state.cards[equipment.object_id].ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'targets':[session.state.cards[body.object_id].ref],'pay':'auto','reserve':reserve or {}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))

    def cast_for(self,session,seat,card,targets=(),*,reserve=None):
        self.priority(session,seat)
        current=session.state.cards[card.object_id]
        offered=self.action(session,seat,current)
        result=session.act('pilot:'+seat,{'action_id':offered['id'],'targets':list(targets),
            'pay':'auto','reserve':reserve or {}})
        self.assertTrue(result.ok,result.summary)

    def test_each_original_rune_cast_draws_and_uses_its_equipment_receiver(self):
        cases=(('Rune of Sustenance','W','lifelink',(2,2)),('Rune of Flight','U','flying',(2,2)),
            ('Rune of Mortality','B','deathtouch',(2,2)),('Rune of Speed','R','haste',(3,2)),('Rune of Might','G','trample',(3,3)))
        for index,(name,color,keyword,expected) in enumerate(cases):
            with self.subTest(name=name):
                session=self.session(613_611_100+index)
                source=self.add(session.engine,name,zone='hand')
                equipment=self.add(session.engine,'Short Sword',zone='hand')
                body=self.add(session.engine,'Memnite')
                self.assert_closed(session,source);self.prepare(session,mana={'C':3,color:1});self.checkpoint(session)
                self.cast_reserved(session,equipment,reserve={color:1});self.assertIsNone(self.resolve(session))
                hand=len(session.state.players['A'].zones['hand'])
                self.cast(session,'A',source,[equipment.ref]);self.assertIsNone(self.resolve(session))
                self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
                self.assertNotIn(keyword,session.engine._combat_keywords(session.state.cards[body.object_id]))
                self.equip(session,equipment,body)
                self.assertEqual(expected,self.stats(session,body))
                self.assertIn(keyword,session.engine._combat_keywords(session.state.cards[body.object_id]))
                if name=='Rune of Might':self.replay(session,load=True)

    def test_original_might_grants_stack_move_and_disappear_independently(self):
        session=self.session(613_611_201)
        equipment=self.add(session.engine,'Short Sword',zone='hand')
        first=self.add(session.engine,'Rune of Might',zone='hand',ref='first-might-grant')
        second=self.add(session.engine,'Rune of Might',zone='hand',ref='second-might-grant')
        body=self.add(session.engine,'Memnite');other=self.add(session.engine,'Ornithopter')
        removal=self.add(session.engine,'Disenchant',seat='B',zone='hand',ref='remove-first-grant')
        destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand',ref='remove-equipment-grant')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        for index,name in enumerate((*('Plains',)*4,'Wastes','Wastes')):self.add(session.engine,name,seat='B',ref='grant-removal-mana-'+str(index))
        self.prepare(session,mana={'C':5,'G':2});self.checkpoint(session)
        self.cast_reserved(session,equipment,reserve={'G':2});self.assertIsNone(self.resolve(session))
        self.cast_reserved(session,first,[equipment.ref],reserve={'G':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',second,[equipment.ref]);self.assertIsNone(self.resolve(session))
        self.equip(session,equipment,body);self.assertEqual((4,4),self.stats(session,body))
        self.equip(session,equipment,other)
        self.assertEqual((1,1),self.stats(session,body));self.assertEqual((3,5),self.stats(session,other))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'cast:'+removal.ref,'targets':[first.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',removal,[first.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((2,4),self.stats(session,other))
        self.cast(session,'B',destroy,[equipment.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((0,2),self.stats(session,other))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_original_speed_on_vehicle_refreshes_creature_haste_and_mana_eligibility(self):
        session=self.session(613_611_206)
        vehicle=self.add(session.engine,"Cultivator's Caravan",zone='hand')
        rune=self.add(session.engine,'Rune of Speed',zone='hand')
        body=self.add(session.engine,'Hill Giant')
        shock=self.add(session.engine,'Shock',zone='hand')
        removal=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        untap=self.add(session.engine,'Twiddle',seat='B',zone='hand')
        self.assert_closed(session,rune);self.assert_closed(session,vehicle)
        session.state.players['B'].mana_pool.update({'C':1,'W':1,'U':1})
        self.prepare(session,mana={'C':4,'R':1});self.checkpoint(session)
        self.cast_reserved(session,vehicle,reserve={'R':1});self.assertIsNone(self.resolve(session))
        hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,'A',rune,[vehicle.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.assertNotIn('creature',session.engine._effective_type_parts(session.state.cards[vehicle.object_id])[0])
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[vehicle.object_id]) if a.crew_threshold==3)
        self.priority(session,'A')
        action_id='activate:'+session.state.cards[vehicle.object_id].ref+':'+ability.ability_id
        accepted=session.act('pilot:A',{'action_id':action_id,'cost_cards':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual((6,5),self.stats(session,vehicle))
        self.assertIn('haste',session.engine._combat_keywords(session.state.cards[vehicle.object_id]))
        self.assertIn(vehicle.ref,{source.ref for source in session.engine.available_mana_sources('A')})
        self.cast(session,'A',shock,['B']);self.assertIsNone(self.resolve(session))
        self.assertEqual(38,session.state.players['B'].life)
        self.assertTrue(session.state.cards[vehicle.object_id].tapped)
        self.cast_for(session,'B',removal,[rune.ref],reserve={'U':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'B',untap,[vehicle.ref])
        decision=self.resolve(session);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        session.packet('pilot:B',full=True)
        chosen=session.act('pilot:B',{'action_id':'choose','choice':'untap'})
        self.assertTrue(chosen.ok,chosen.summary);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[vehicle.object_id].tapped)
        self.assertEqual((5,5),self.stats(session,vehicle))
        self.assertNotIn('haste',session.engine._combat_keywords(session.state.cards[vehicle.object_id]))
        self.assertNotIn(vehicle.ref,{source.ref for source in session.engine.available_mana_sources('A')})
        self.replay(session,load=True)

    def test_original_flight_matches_pinned_hammer_timestamp_example_after_reattachment(self):
        session=self.session(613_611_202)
        equipment=self.add(session.engine,'Colossus Hammer',zone='hand')
        source=self.add(session.engine,'Rune of Flight',zone='hand')
        first=self.add(session.engine,'Goblin Piker');second=self.add(session.engine,'Ornithopter')
        removal=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        self.add(session.engine,'Plains',seat='B');self.add(session.engine,'Wastes',seat='B')
        self.prepare(session,mana={'C':18,'U':1});self.checkpoint(session)
        self.cast_reserved(session,equipment,reserve={'U':1});self.assertIsNone(self.resolve(session))
        self.equip(session,equipment,first,reserve={'U':1})
        self.cast(session,'A',source,[equipment.ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.equip(session,equipment,second)
        self.assertEqual((10,12),self.stats(session,second))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.cast(session,'B',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.replay(session,load=True)

    def test_actual_leader_uses_commander_controller_and_current_layer_six_grant_time(self):
        session=self.session(613_611_203)
        commander=next(card for card in session.state.cards.values() if card.is_commander and card.owner=='A')
        register_generated_programs(self.db,session.engine.semantics,(self.db.lookup(commander.printed_name),),
            trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True)
        session.engine.move_card(commander.object_id,'battlefield',log=False)
        source=self.add(session.engine,'Inspiring Leader')
        army=self.add(session.engine,'Raise the Alarm',zone='hand')
        blink=self.add(session.engine,'Flicker of Fate',zone='hand')
        removal=self.add(session.engine,'Dress Down',seat='B',zone='hand')
        control=self.add(session.engine,'Control Magic',seat='B',zone='hand')
        foreign=self.add(session.engine,'Raise the Alarm',seat='B',zone='hand',ref='foreign-army')
        restore=self.add(session.engine,'Disenchant',seat='B',zone='hand',ref='restore-commander-control')
        destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand',ref='remove-background-source')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,commander)
        # Keep payment-source enumeration out of this grant/controller witness.
        # The paired-Commander witness separately casts the giver and both commanders.
        session.state.players['A'].mana_pool.update({'C':2,'W':2})
        self.prepare(session,actor='B',active='B',mana={'C':6,'U':3,'W':3});self.checkpoint(session)
        self.cast_for(session,'A',army,reserve={'W':1});self.assertIsNone(self.resolve(session))
        own_tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        self.assertEqual(2,len(own_tokens));self.assertEqual([(3,3)]*2,[self.stats(session,c) for c in own_tokens])
        self.cast_for(session,'B',removal,reserve={'U':2,'W':3});self.assertIsNone(self.resolve(session))
        self.assertEqual([(1,1)]*2,[self.stats(session,c) for c in own_tokens])
        old_incarnation=session.state.cards[source.object_id].logical_object_id
        self.cast_for(session,'A',blink,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertNotEqual(old_incarnation,session.state.cards[source.object_id].logical_object_id)
        self.assertEqual([(3,3)]*2,[self.stats(session,c) for c in own_tokens])
        self.cast_for(session,'B',control,[session.state.cards[commander.object_id].ref],reserve={'W':3});self.assertIsNone(self.resolve(session))
        self.assertEqual(('A','B'),tuple(getattr(session.state.cards[commander.object_id],field) for field in ('owner','controller')))
        self.assertEqual([(1,1)]*2,[self.stats(session,c) for c in own_tokens])
        self.cast_for(session,'B',foreign,reserve={'W':2});self.assertIsNone(self.resolve(session))
        foreign_tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='B']
        self.assertEqual([(3,3)]*2,[self.stats(session,c) for c in foreign_tokens])
        self.cast_for(session,'B',restore,[control.ref],reserve={'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual([(3,3)]*2,[self.stats(session,c) for c in own_tokens])
        self.assertEqual([(1,1)]*2,[self.stats(session,c) for c in foreign_tokens])
        self.cast(session,'B',destroy,[session.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual([(1,1)]*2,[self.stats(session,c) for c in own_tokens])
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_two_actual_partner_commanders_each_supply_one_anthem(self):
        old_deck=self.deck
        try:
            self.deck=DeckDefinition('Two current commander sources',[
                DeckEntry('Rograkh, Son of Rohgahh',1,'commander'),DeckEntry('Prava of the Steel Legion',1,'commander'),
                DeckEntry('Generic Bound Plains',30)],['Rograkh, Son of Rohgahh','Prava of the Steel Legion'])
            session=self.session(613_611_205)
        finally:
            self.deck=old_deck
        commanders=[c for c in session.state.cards.values() if c.is_commander and c.owner=='A']
        register_generated_programs(self.db,session.engine.semantics,tuple(self.db.lookup(c.printed_name) for c in commanders),
            trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True)
        source=self.add(session.engine,'Inspiring Leader',zone='hand')
        army=self.add(session.engine,'Raise the Alarm',zone='hand')
        self.prepare(session,mana={'C':5,'W':3});self.checkpoint(session)
        for commander in commanders:
            self.assert_closed(session,commander)
            self.cast_reserved(session,commander,reserve={'W':2});self.assertIsNone(self.resolve(session))
        self.cast_reserved(session,source,reserve={'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',army);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        self.assertEqual(2,len(tokens))
        # Two independent +2/+2 anthems, plus Prava's printed own-turn +1/+4.
        self.assertEqual([(6,9)]*2,[self.stats(session,c) for c in tokens])
        turns.RingNativeActionTests.until(self,session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main' and s.state.priority_player=='B')
        self.assertEqual([(5,5)]*2,[self.stats(session,c) for c in tokens])
        self.replay(session,load=True)

    def test_actual_morph_commander_keeps_granted_anthem_without_printed_abilities(self):
        old_deck=self.deck
        try:
            self.deck=DeckDefinition('Face-down commander static source',[
                DeckEntry('Akroma, Angel of Fury',1,'commander'),DeckEntry('Wastes',30)],['Akroma, Angel of Fury'])
            session=self.session(613_611_207)
        finally:self.deck=old_deck
        commander=next(c for c in session.state.cards.values() if c.is_commander and c.owner=='A')
        register_generated_programs(self.db,session.engine.semantics,(self.db.lookup(commander.printed_name),),
            trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True)
        source=self.add(session.engine,'Inspiring Leader',zone='hand')
        army=self.add(session.engine,'Raise the Alarm',zone='hand')
        for index in range(2):self.add(session.engine,'Plains',ref='morph-grant-white-'+str(index))
        self.assert_closed(session,source);self.assert_closed(session,commander)
        self.prepare(session,mana={'C':6});self.checkpoint(session)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast-morph:'+commander.ref)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[commander.object_id].face_down)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',army);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        self.assertEqual(2,len(tokens));self.assertEqual([(3,3)]*2,[self.stats(session,c) for c in tokens])
        keywords=session.engine._combat_keywords(session.state.cards[commander.object_id])
        self.assertNotIn('flying',keywords);self.assertNotIn('trample',keywords)
        self.assertEqual((2,2),self.stats(session,commander))
        self.replay(session,load=True)

    def test_duplicate_collapse_mutant_is_killed_by_original_might(self):
        from quorune.rules import granted_characteristic_shapes as shapes
        original=shapes.granted_characteristic_occurrences
        def unique(values,keys):return tuple(dict(original(values,keys)).items())
        with patch.object(shapes,'granted_characteristic_occurrences',unique),self.assertRaises(AssertionError):
            self.test_original_might_grants_stack_move_and_disappear_independently()

    def test_reattachment_order_mutant_is_killed_by_original_hammer_flight(self):
        with patch('quorune.card_programs.runtime._granted_characteristic_timestamp_order',side_effect=lambda effects,order:effects),self.assertRaises(AssertionError):
            self.test_original_flight_matches_pinned_hammer_timestamp_example_after_reattachment()

    def test_runtime_binding_requires_characteristic_grant_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(613_611_204)
        for name in ('Rune of Flight','Inspiring Leader'):
            source=self.add(session.engine,name)
            checked=0
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if CAPABILITY not in program.capability_dependencies:continue
                checked+=1
                deps=tuple(cap for cap in program.capability_dependencies if cap!=CAPABILITY)
                altered=replace(program,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                binding=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(binding['strict'],name)
                self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,binding['blockers'])
            self.assertGreaterEqual(checked,2,'Both parent and independent hidden child must be checked: '+name)


if __name__=='__main__':unittest.main()
