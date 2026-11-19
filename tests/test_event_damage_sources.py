"""CR608.2h/120: dealer information differs from the source owning a trigger."""
from types import SimpleNamespace
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.event_damage_source_model import (EventDamageSourceReference, EventDamageSourceInformation,
    EVENT_DAMAGE_SOURCE_CAPABILITY,EVENT_DAMAGE_SOURCE_CONTEXT)
from quorune.rules.event_damage_sources import (event_damage_source_node_capabilities,event_damage_source_runtime_dependencies,
    bind_event_damage_source,event_damage_departure_updates)
from quorune.event_counter_reference import event_object_identity
from quorune.damage_source import DamageSourceSnapshot
from quorune.model import CardInstance
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

ANY_TARGET={'zones':['player','battlefield'],'categories':['player','permanent'],'predicate':'damageable','count':1}
NAMES=['Extra Arms',"Hawkeye's Bow",'Aether Charge','Pain for All','Blazing Sunsteel']


class EventDamageSourceModelTests(unittest.TestCase):
    def test_closed_event_dealer_reference_requires_exact_version_and_single_proved_event(self):
        value=EventDamageSourceReference('creature.enter').to_dict()
        self.assertEqual(value,EventDamageSourceReference.from_dict(value).to_dict())
        for change in [{'schema_version':True},{'schema_version':2},{'event':'spell.cast'},{'kind':'attachment'},{'reference':'arbitrary'}]:
            with self.assertRaises(ValueError):EventDamageSourceReference.from_dict({**value,**change})

    def test_dealer_dependency_is_derived_from_closed_payload_and_matches_its_program_event(self):
        effect={'op':'damage','source':EventDamageSourceReference('creature.enter').to_dict(),'target':'$target.0','amount':2}
        self.assertIn(EVENT_DAMAGE_SOURCE_CAPABILITY,event_damage_source_node_capabilities(effects=(effect,),target_schema=ANY_TARGET,mechanic_ids=('cr-120-damage','cr-115-targets')))
        program=SimpleNamespace(effects=(effect,),active_zone='battlefield',event='creature.enter',event_condition={},target_schema=ANY_TARGET)
        self.assertIn(EVENT_DAMAGE_SOURCE_CAPABILITY,event_damage_source_runtime_dependencies(program))
        program.event='permanent.tap'
        with self.assertRaises(ValueError):event_damage_source_runtime_dependencies(program)

    def test_dealer_effect_rejects_nonpositive_boolean_and_unrepresented_result_fields(self):
        effect={'op':'damage','source':EventDamageSourceReference('permanent.tap').to_dict(),'target':'$target.0','amount':1}
        for change in [{'amount':True},{'amount':0},{'amount':-1},{'extra':'unrepresented'}]:
            self.assertFalse(event_damage_source_node_capabilities(effects=({**effect,**change},),target_schema=ANY_TARGET,mechanic_ids=('cr-120-damage',)))

    def test_single_received_event_identity_uses_the_recipient_not_an_outgoing_source(self):
        context={'card':'recipient-ref','target_kind':'permanent','target_logical_object_id':'recipient@2','source_logical_object_id':'other@8'}
        self.assertEqual({'reference':'recipient-ref','logical_object_id':'recipient@2'},event_object_identity('damage.received',context))
        context['target_kind']='player'
        with self.assertRaises(ValueError):event_object_identity('damage.received',context)

    def test_existing_self_source_binding_keeps_its_previous_compiler_owner(self):
        from quorune.compiler.event_damage_source_templates import event_damage_source_template
        binding=SimpleNamespace(event=SimpleNamespace(value='damage.received'),active_zone='battlefield',
            explicit_capabilities=(),variant='source_dealt_damage')
        with patch('quorune.compiler.event_damage_source_templates.trigger_scalar_effect_amount_template',side_effect=AssertionError('Self binding owner was replaced')):
            self.assertIsNone(event_damage_source_template(binding,'it deals that much damage to any target.',card_name='Generic Creature',effect_template=lambda *a,**kw:None))

    def received_fixture(self):
        card=CardInstance('dealer','dealer-ref','dealer-oracle','Generic Dealer','C','B','battlefield',zone_change_counter=1)
        snapshot=DamageSourceSnapshot(card.ref,card.object_id,card.logical_object_id,'B','C',zone='battlefield',
            types=('creature',),colors=('G',),keywords=('lifelink',))
        info=EventDamageSourceInformation('damage.received',snapshot,event_id='transaction-1',received_amount=3)
        context={'event':'damage.received','event_id':'transaction-1','amount':3,'card':card.ref,
            'target_kind':'permanent','target_object_id':card.object_id,'target_logical_object_id':card.logical_object_id,
            EVENT_DAMAGE_SOURCE_CONTEXT:info.to_dict()}
        effect={'op':'damage','source':EventDamageSourceReference('damage.received').to_dict(),'target':'$target.0','amount':1}
        item=SimpleNamespace(ref='ability-frame',source_object_id='equipment-source',controller='A',context=context)
        host=SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card}))
        return host,card,snapshot,info,effect,item

    def test_received_information_seals_amount_event_and_incarnation_without_changing_ability_source(self):
        host,card,snapshot,info,effect,item=self.received_fixture()
        self.assertEqual(info,EventDamageSourceInformation.from_dict(info.to_dict()))
        for change in [{'amount':4},{'amount':True},{'event_id':'other-transaction'},
                {'target_logical_object_id':'dealer@2'}]:
            altered=SimpleNamespace(context={**item.context,**change})
            with self.assertRaises(ValueError):bind_event_damage_source(host,effect,altered)
        for change in [{'schema_version':True},{'received_amount':0},{'event_id':''}]:
            with self.assertRaises(ValueError):EventDamageSourceInformation.from_dict({**info.to_dict(),**change})

    def test_current_control_then_immediate_departure_lki_does_not_rebind_returned_dealer(self):
        host,card,snapshot,info,effect,item=self.received_fixture()
        def current(_host,source,**_kw):
            return replace(snapshot,controller=source.controller,logical_object_id=source.logical_object_id)
        with patch('quorune.rules.stack_damage_sources._public_source_snapshot',side_effect=current):
            card.controller='D';bound=bind_event_damage_source(host,effect,item)
            self.assertEqual('D',bound['source_snapshot']['controller']);self.assertEqual('A',item.controller)
            self.assertEqual('equipment-source',item.source_object_id)
            updates=event_damage_departure_updates(host,[{'ref':item.ref,'context':item.context}],{(card.object_id,card.logical_object_id):card})
            item.context[EVENT_DAMAGE_SOURCE_CONTEXT]=updates[item.ref]
            card.zone_change_counter+=1;card.controller='B'
            bound=bind_event_damage_source(host,effect,item)
            self.assertEqual('D',bound['source_snapshot']['controller'])
            self.assertEqual(snapshot.logical_object_id,bound['source_snapshot']['logical_object_id'])

    def test_missing_immediate_departure_refresh_mutant_is_killed_by_last_controller(self):
        from quorune.rules import event_damage_sources as owner
        def witness():
            host,card,snapshot,info,effect,item=self.received_fixture();card.controller='D'
            with patch('quorune.rules.stack_damage_sources._public_source_snapshot',
                    side_effect=lambda _h,c:replace(snapshot,controller=c.controller)):
                updates=owner.event_damage_departure_updates(host,[{'ref':item.ref,'context':item.context}],
                    {(card.object_id,card.logical_object_id):card})
            if item.ref in updates:item.context[EVENT_DAMAGE_SOURCE_CONTEXT]=updates[item.ref]
            card.zone_change_counter+=1;card.controller='B'
            self.assertEqual('D',owner.bind_event_damage_source(host,effect,item)['source_snapshot']['controller'])
        witness()
        with patch.object(owner,'event_damage_departure_updates',return_value={}),self.assertRaises(AssertionError):witness()


class EventDamageSourceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'event-damage-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/event-damage-source-cards.json'],path);cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_all_originals_have_complete_normal_admission_and_source_spans(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        semantics=SemanticRegistry();cards=[self.db.lookup(n,fuzzy=False) for n in NAMES]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes:self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],(card.name,bound['blockers']));self.assertEqual('capability_closed',bound['trust_basis'])

    def test_unproved_dealers_wrong_quantities_and_multiple_subscriptions_remain_residual(self):
        from test_qualified_zone_event_queries import query_record
        for text in ['Whenever you draw a card, it deals 2 damage to any target.',
                'Whenever a creature attacks, it deals that much damage to any target.',
                'Whenever enchanted creature attacks or dies, it deals 2 damage to any target.',
                'Whenever enchanted creature attacks, it deals 2 damage to any target and draw a card.']:
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=self.registry,capability_profile='commander_review').status)

    def test_existing_self_reflectors_keep_old_source_references_without_new_dealer_authority(self):
        from quorune.carddb import CardDatabase
        from quorune.compiler.ir_model import OracleNode
        from test_qualified_zone_event_queries import query_record
        record=query_record('Whenever this creature is dealt damage, it deals that much damage to any target.')
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertTrue(all(EVENT_DAMAGE_SOURCE_CAPABILITY not in n.capability_dependencies for f in ir.faces for n in f.nodes))

    def test_verified_event_dealer_withdrawal_blocks_new_originals_and_keeps_self_reflection(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_qualified_zone_event_queries import query_record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==EVENT_DAMAGE_SOURCE_CAPABILITY).update(status='blocked',blockers=['sealed dealer source owner withdrawn'])
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)
        old=query_record('Whenever this creature is dealt damage, it deals that much damage to any target.')
        self.assertEqual('exact',compile_oracle_card(old,capability_registry=registry,capability_profile='commander_review').status)


class EventDamageSourceNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'event-damage-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/event-damage-source-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Event damage dealers',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def lives(self,s):return tuple(s.state.players[p].life for p in 'ABCD')

    def equip(self,s,source,creature):
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[source.object_id])
        command={'action_id':'activate:'+source.ref+':'+ability.ability_id,'targets':[creature.ref],'pay':'auto'}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(creature.object_id,s.state.cards[source.object_id].attached_to)

    def test_actual_extra_arms_uses_ability_source_for_targets_and_white_dealer_for_protection(self):
        s=self.session(1116400);source=self.add(s.engine,'Extra Arms',zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        protected_source=self.add(s.engine,'Silver Knight',seat='B');protected_dealer=self.add(s.engine,'Black Knight',seat='B')
        self.add(s.engine,'Fervor');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-extra-protection-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':4,'R':1});self.checkpoint(s)
        self.cast(s,'A',source,[dealer.ref]);self.assertIsNone(self.resolve(s));self.attack(s,[(dealer,'D')])
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':[protected_source.ref]});self.target(s,'A',[protected_dealer.ref])
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[protected_dealer.object_id].zone)
        self.assertEqual(0,s.state.cards[protected_dealer.object_id].marked_damage);self.assertEqual((40,40,40,40),self.lives(s));self.finish(s,private)

    def test_actual_extra_arms_keeps_last_dealer_controller_after_control_change_blink_and_aura_loss(self):
        s=self.session(1116401);source=self.add(s.engine,'Extra Arms',zone='hand');dealer=self.add(s.engine,"Healer's Hawk",seat='C')
        s.engine.change_control(dealer.object_id,'A');donate=self.add(s.engine,'Donate',zone='hand');blink=self.add(s.engine,'Momentary Blink',seat='B',zone='hand')
        self.add(s.engine,'Fervor');self.add(s.engine,'Vedalken Orrery');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-extra-lki-C')
        for i in range(3):self.add(s.engine,'Island',ref='extra-response-island-'+str(i))
        for i in range(2):self.add(s.engine,'Generic Bound Plains',seat='B',ref='blink-response-plains-'+str(i))
        self.assert_closed(s,source);self.prepare(s,mana={'C':6,'R':1,'U':1});s.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[dealer.ref],reserve={'U':1});self.assertIsNone(self.resolve(s));self.attack(s,[(dealer,'D')]);self.target(s,'A',['D'])
        old=s.state.cards[dealer.object_id].logical_object_id;self.cast(s,'A',donate,['B',dealer.ref]);self.resolve_top(s)
        self.cast(s,'B',blink,[dealer.ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[dealer.object_id].logical_object_id);self.assertEqual('C',s.state.cards[dealer.object_id].controller)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((40,42,40,38),self.lives(s));self.finish(s,private)

    def test_actual_bow_keeps_old_tapped_dealer_after_equipment_moves_and_uses_current_controller(self):
        s=self.session(1116410);source=self.add(s.engine,"Hawkeye's Bow",zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        other=self.add(s.engine,'Grizzly Bears');donate=self.add(s.engine,'Donate',zone='hand');move=self.add(s.engine,'Brass Squire')
        self.add(s.engine,'Fervor');self.add(s.engine,'Vedalken Orrery');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-bow-current-C')
        for i in range(3):self.add(s.engine,'Island',ref='bow-response-island-'+str(i))
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'R':2,'U':1});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'R':1,'U':1});self.assertIsNone(self.resolve(s));self.equip(s,source,dealer)
        self.assertEqual(2,s.engine._numeric_stat(dealer.object_id,'power'));self.assertIn('reach',s.engine._combat_keywords(s.state.cards[dealer.object_id]))
        self.attack(s,[(dealer,'D')]);self.cast(s,'A',donate,['B',dealer.ref]);self.resolve_top(s)
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[move.object_id])
        result=s.act('pilot:A',{'action_id':'activate:'+move.ref+':'+ability.ability_id,'targets':[source.ref,other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.resolve_top(s);self.assertEqual(other.object_id,s.state.cards[source.object_id].attached_to)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual((40,42,39,39),self.lives(s));self.finish(s,private)

    def test_actual_aether_charge_has_dead_zero_power_beast_deal_fixed_damage_with_last_known_lifelink(self):
        s=self.session(1116420);source=self.add(s.engine,'Aether Charge',zone='hand');dealer=self.add(s.engine,'Krakilin',zone='hand')
        self.add(s.engine,'Whip of Erebos');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-aether-zero-C')
        self.assert_closed(s,source);self.assert_closed(s,dealer);self.prepare(s,mana={'C':4,'R':1,'G':2});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'G':2});self.assertIsNone(self.resolve(s));self.priority(s,'A');offer=self.action(s,'A',dealer)
        result=s.act('pilot:A',{'action_id':offer['id'],'x':0,'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve_top(s)
        self.target(s,'A',['B']);self.assertEqual('graveyard',s.state.cards[dealer.object_id].zone);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.replay(s,load=True);command={'action_id':'choose','choice':'apply'};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual((44,36,40,40),self.lives(s));self.finish(s,private)

    def test_actual_aether_optional_decline_keeps_announced_own_planeswalker_and_rejects_self_player(self):
        s=self.session(1116421);source=self.add(s.engine,'Aether Charge',zone='hand');dealer=self.add(s.engine,'Leatherback Baloth',zone='hand')
        walker=self.add(s.engine,'Jace Beleren');walker.counters['loyalty']=3
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-aether-decline-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':4,'R':1,'G':3});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'G':3});self.assertIsNone(self.resolve(s));self.cast(s,'A',dealer);self.resolve_top(s)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':['A']});self.target(s,'A',[walker.ref]);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[walker.object_id].counters['loyalty']);self.assertEqual((40,40,40,40),self.lives(s));self.finish(s,private)

    def pain(self,seed,*,dealer_name="Healer's Hawk"):
        s=self.session(seed);source=self.add(s.engine,'Pain for All',zone='hand');dealer=self.add(s.engine,dealer_name)
        shock=self.add(s.engine,'Shock',seat='B',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-pain-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'R':1,'W':2});s.state.players['B'].mana_pool.update({'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[dealer.ref],reserve={'W':2});self.resolve_top(s);self.target(s,'A',['B']);self.assertIsNone(self.resolve(s))
        return s,source,dealer,shock,private

    def test_actual_pain_lethal_received_damage_reflects_committed_amount_after_dealer_and_aura_die(self):
        s,source,dealer,shock,private=self.pain(1116430);before=self.lives(s)
        self.cast(s,'B',shock,[dealer.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[dealer.object_id].zone);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((before[0]+6,before[1]-2,before[2]-2,before[3]-2),self.lives(s));self.finish(s,private)

    def test_actual_pain_uses_received_amount_after_prevention_and_full_prevention_does_not_trigger(self):
        s=self.session(1116431);source=self.add(s.engine,'Pain for All',zone='hand');dealer=self.add(s.engine,'Vampire Nighthawk')
        healer=self.add(s.engine,'Samite Healer');shield=self.add(s.engine,'Mending Hands',zone='hand');self.add(s.engine,'Fervor')
        first=self.add(s.engine,'Shock',seat='B',zone='hand',ref='first-pain-shock');second=self.add(s.engine,'Shock',seat='B',zone='hand',ref='second-pain-shock')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-pain-prevention-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'R':1,'W':1});s.state.players['B'].mana_pool.update({'R':2});self.checkpoint(s)
        self.cast_for(s,'A',source,[dealer.ref],reserve={'W':1});self.resolve_top(s);self.target(s,'A',['B']);self.assertIsNone(self.resolve(s));before=self.lives(s)
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[healer.object_id])
        result=s.act('pilot:A',{'action_id':'activate:'+healer.ref+':'+ability.ability_id,'targets':[dealer.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.cast(s,'B',first,[dealer.ref]);self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[dealer.object_id].marked_damage)
        self.assertEqual((before[0]+3,before[1]-1,before[2]-1,before[3]-1),self.lives(s))
        before=self.lives(s);self.cast(s,'A',shield,[dealer.ref]);self.assertIsNone(self.resolve(s));self.cast(s,'B',second,[dealer.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(before,self.lives(s));self.assertEqual(1,s.state.cards[dealer.object_id].marked_damage);self.finish(s,private)

    def test_actual_sunsteel_target_protection_uses_equipment_and_damage_prevention_uses_dead_creature(self):
        s=self.session(1116440);source=self.add(s.engine,'Blazing Sunsteel',zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        illegal=self.add(s.engine,'Silver Knight',seat='B');target=self.add(s.engine,'Black Knight',seat='B');shock=self.add(s.engine,'Shock',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-sunsteel-protection-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':5,'R':1});s.state.players['B'].mana_pool.update({'R':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.equip(s,source,dealer);self.assertEqual(4,s.engine._numeric_stat(dealer.object_id,'power'))
        self.cast(s,'B',shock,[dealer.ref]);self.resolve_top(s)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':[illegal.ref]});self.target(s,'A',[target.ref]);self.assertEqual('graveyard',s.state.cards[dealer.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
        self.assertEqual(0,s.state.cards[target.object_id].marked_damage);self.assertEqual((40,40,40,40),self.lives(s));self.finish(s,private)

    def test_actual_sunsteel_source_departure_keeps_dealer_reflection_and_lifelink(self):
        s=self.session(1116441);source=self.add(s.engine,'Blazing Sunsteel',zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        shock=self.add(s.engine,'Shock',seat='B',zone='hand');destroy=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-sunsteel-departure-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':5,'R':1});s.state.players['B'].mana_pool.update({'R':1,'C':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.equip(s,source,dealer)
        self.cast(s,'B',shock,[dealer.ref]);self.resolve_top(s);self.target(s,'A',['D'])
        self.cast(s,'B',destroy,[source.ref]);self.resolve_top(s);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual((42,40,40,38),self.lives(s));self.finish(s,private)

    def test_referenced_dealer_authority_is_required_independently_of_payload_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(1116450);source=self.add(s.engine,'Extra Arms');self.assert_closed(s,source)
        programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if EVENT_DAMAGE_SOURCE_CAPABILITY in p.capability_dependencies];self.assertTrue(programs)
        for program in programs:
            deps=tuple(c for c in program.capability_dependencies if c!=EVENT_DAMAGE_SOURCE_CAPABILITY)
            changed=replace(program,capability_dependencies=deps,coverage=(),capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+EVENT_DAMAGE_SOURCE_CAPABILITY,bound['blockers'])

    def test_post_received_departure_fault_rolls_back_damage_trigger_and_zone_information_then_retries(self):
        from quorune.rules import event_damage_sources as owner
        from quorune.errors import GameRuleError
        s,source,dealer,shock,private=self.pain(1116451);before=self.lives(s);self.cast(s,'B',shock,[dealer.ref])
        for _ in range(3):
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        seat=s.pending_principals()[0].split(':',1)[1]
        with patch.object(owner,'event_damage_departure_updates',side_effect=GameRuleError('Injected dealer departure failure')):
            self.reject(s,seat,{'action_id':'pass'})
        self.assertEqual('battlefield',s.state.cards[dealer.object_id].zone);self.assertEqual(0,s.state.cards[dealer.object_id].marked_damage)
        self.assertEqual(before,self.lives(s));result=s.act('pilot:'+seat,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((before[0]+6,before[1]-2,before[2]-2,before[3]-2),self.lives(s));self.finish(s,private)
