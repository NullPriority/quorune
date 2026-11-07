from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT,keep_all,pass_current
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import GameConfig
from quorune.object_predicate import ObjectQuerySpec,ObjectQueryError
from quorune.object_query import ObjectQueryResult,object_matches_query
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.semantic_runtime import default_continuous_effect_component_registry,SemanticNodeError
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class CommanderCharacteristicQueryTests(unittest.TestCase):
    def test_attacked_player_life_condition_is_closed_and_required(self):
        from types import SimpleNamespace
        from quorune.rules.attacked_player_life import (
            ATTACKED_PLAYER_LIFE_CAPABILITY, ATTACKED_PLAYER_LIFE_COVERAGE,
            attacked_player_life_runtime_dependencies,
        )

        text = ('Whenever this creature attacks a player, if no opponent has more life than that player, '
                'this creature can\'t be blocked this turn.')
        registry = load_default_capability_registry()
        ir = compile_oracle_card(query_record(text), capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIn(ATTACKED_PLAYER_LIFE_CAPABILITY, node.capability_dependencies)
        program = dict(event=node.event, active_zone=node.active_zone, event_condition=node.event_condition,
                       coverage=node.runtime_coverage)
        self.assertEqual((ATTACKED_PLAYER_LIFE_CAPABILITY,),
                         attacked_player_life_runtime_dependencies(SimpleNamespace(**program)))
        for change in ({'coverage': (ATTACKED_PLAYER_LIFE_COVERAGE,)}, {'event': 'creature.dies'},
                       {'event_condition': {'any': node.event_condition['all']}}):
            with self.assertRaises(ValueError):
                attacked_player_life_runtime_dependencies(SimpleNamespace(**{**program, **change}))
        unsupported = text.replace('no opponent has more life', 'no player has more life')
        self.assertNotEqual('exact', compile_oracle_card(query_record(unsupported), capability_registry=registry).status)
        value = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(item for item in value['capabilities'] if item['id'] == ATTACKED_PLAYER_LIFE_CAPABILITY)
        row.update(status='blocked', blockers=['Attacked-player life comparison withdrawn'])
        blocked = CapabilityRegistry(value)
        blocked.mark_evidence_verified('0' * 64)
        self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=blocked).status)

    def test_attacked_player_life_rejects_permanent_and_forged_attack_facts(self):
        from types import SimpleNamespace
        from quorune.attack_transition_model import (
            AttackRecipient, AttackRecipientKind, AttackTransitionAssignment,
            AttackTransitionEvent, AttackTransitionParticipant,
        )
        from quorune.rules.attacked_player_life import attacked_player_life_holds

        attacker = AttackTransitionParticipant('attacker', 'attacker@1', 'attacker-ref', 'A', True)
        defender = AttackRecipient(AttackRecipientKind.PLANESWALKER, 'planeswalker-ref', 'B', 'walker@1')
        transition = AttackTransitionEvent.create(
            turn_sequence=1, priority_epoch=2, active_player='A', participants=(attacker,),
            assignments=(AttackTransitionAssignment(attacker.object_id, defender),),
        )
        host = SimpleNamespace(active_seats=('A', 'B', 'C'), state=SimpleNamespace(
            players={seat: SimpleNamespace(life=life) for seat, life in zip('ABC', (50, 40, 35))},
        ))
        context = {'attack_transition': transition.to_dict(), 'event_id': transition.transition_id,
                   'card': attacker.reference, 'controller': 'A', 'defender': defender.reference, 'defending_player': 'B'}
        self.assertFalse(attacked_player_life_holds(host, context, source_object_id='attacker', controller='A'))
        with self.assertRaises(ValueError):
            attacked_player_life_holds(host, {**context, 'defender': 'B'}, source_object_id='attacker', controller='A')

    def test_designation_query_serializes_additively_and_matches_physical_status(self):
        legacy=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)).to_dict()
        self.assertNotIn('commander',legacy);self.assertEqual(legacy,ObjectQuerySpec.from_dict(legacy).to_dict())
        predicate=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),commander=True)
        self.assertIs(True,predicate.to_dict()['commander']);self.assertEqual(predicate,predicate.from_dict(predicate.to_dict()))
        row=ObjectQueryResult(object_id='one',ref='one',printed_name='Generic',owner='A',controller='B',zone='battlefield',types=('creature',),commander=True)
        self.assertTrue(object_matches_query(row,predicate))
        self.assertFalse(object_matches_query(replace(row,commander=False),predicate))
        self.assertFalse(object_matches_query(replace(row,types=('artifact',)),predicate))
        self.assertFalse(object_matches_query(replace(row,zone='command'),predicate))
        for value in (1,'commander',[]):
            with self.assertRaises(ObjectQueryError):ObjectQuerySpec(commander=value)
        with self.assertRaises(ObjectQueryError):ObjectQuerySpec(_serialization_version=True)

    def test_owner_and_controller_shells_retain_distinct_relations_and_original_span(self):
        for relation,expected in (('own','source_owner'),('control','source_controller')):
            text='Commander creatures you '+relation+' get +2/+2 and have indestructible.'
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];condition=node.handlers[0]['condition']
            self.assertEqual(expected,condition['target_controller']);self.assertIs(True,condition['predicate']['commander'])
            self.assertEqual(['creature'],condition['predicate']['types_all']);self.assertEqual((0,len(text)),(node.span.start,node.span.end))
            altered={**dict(node.handlers[0]),'condition':{**dict(condition),'target_controller':'source_owner','predicate':ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)).to_dict()}}
            with self.assertRaises(SemanticNodeError):default_continuous_effect_component_registry().validate(altered)
            for changes in ({'commander':False},{'types_all':['artifact']}):
                forged={**dict(node.handlers[0]),'condition':{**dict(condition),'predicate':{**dict(condition['predicate']),**changes}}}
                with self.assertRaises(SemanticNodeError):default_continuous_effect_component_registry().validate(forged)

    def test_commander_designation_dependency_omission_rejects_grants(self):
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(r for r in value['capabilities'] if r['id']=='state_query.commander.designation');row.update(status='blocked',blockers=['designation owner unavailable'])
        registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
        for text in ('Commander creatures you control get +2/+2 and have indestructible.',
                     'Commander creatures you own have "{2}: Draw a card."'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)

    def test_designation_is_not_admitted_into_unrepresented_quantity_or_cost_shapes(self):
        from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope,CharacteristicFragmentError
        from quorune.compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        predicate=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),commander=True)
        with self.assertRaises(CharacteristicFragmentError):CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,query=predicate)
        self.assertFalse(fixed_resolution_characteristic_query_is_closed(predicate,target_schema=None))


class CommanderCharacteristicGrantRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'commander-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/commander-characteristic-grants.json',
                                ROOT/'tests/fixtures/scheduled-half-life-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Commander grant',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def session(self,seed):
        decks={seat:DeckDefinition('Commander grant '+seat,[DeckEntry('Generic Grant Commander '+seat,1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Grant Commander '+seat]) for seat in 'ABCD'}
        s=CommanderSession.create(self.db,decks,first_player='A',seed=seed,
            config=GameConfig(seed=seed,auto_pass_empty_priority=False));keep_all(s)
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        names=('Bastion Protector','Bloodsworn Steward','Clan Crafter','Agent of the Iron Throne','Candlekeep Sage','Flaming Fist','Far Traveler','Unsummon','Ornithopter','Act of Treason')
        register_generated_programs(self.db,e.semantics,[self.db.lookup(name) for name in names],trust_level='trusted',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return s

    def commander(self,e,seat):
        return next(c for c in e.state.cards.values() if c.is_commander and c.owner==seat)

    def _prepare_life_attack(self, seed, background_name, *, other_life=40, response_name=None):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.model import CombatState

        s = self.session(seed)
        e = s.engine
        background = self.add(e, background_name)
        response = self.add(e, response_name, seat='D', zone='hand', ref='life-attack-private-response') if response_name else None
        records = [self.db.lookup(background_name)]
        if response_name is not None:
            records.append(self.db.lookup(response_name))
        register_generated_programs(
            self.db, e.semantics, records, trust_level='provisional', capability_registry=self.registry,
            capability_profile='commander_review', promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        program = compile_best_available_card_program(self.db, records[0], semantic_registry=e.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        self.assertEqual('A', background.controller)
        attacker = self.commander(e, 'A')
        e.move_card(attacker.object_id, 'battlefield', controller='B', log=False)
        attacker.acquired_control_turn_count = -1
        for seat, life in zip('ABCD', (30, 60, 40, other_life)):
            s.state.players[seat].life = life
        s.state.active_player = 'B'
        s.state.phase = 'combat'
        s.state.step = 'declare_attackers'
        s.state.phase_index = 5
        s.state.combat = CombatState()
        return s, attacker, response

    def _declare_life_attack(self, s, attacker):
        s.engine._issue_attackers()
        self.checkpoint(s)
        command = {'action_id': 'attack', 'attackers': {attacker.ref: 'C'}}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        accepted = s.act('pilot:B', command)
        self.assertTrue(accepted.ok, accepted.summary)

    def _cast_life_attack_response(self, s, response, *, targets=()):
        self.assertIn(response.ref, json.dumps(s.packet('pilot:D', full=True)))
        self.assertNotIn(response.ref, json.dumps(s.packet('pilot:B', full=True)))
        for _ in range(3):
            if 'pilot:D' in s.pending_principals():
                break
            pass_current(s)
        self.assertIn('pilot:D', s.pending_principals())
        options = s.packet('pilot:D', full=True)['decision']['ctx']['legal']['actions']
        action = next(option for option in options if option.get('card') == response.ref)
        accepted = s.act('pilot:D', {'action_id': action['id'], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)

    def _agent_life_attack(self, seed, *, other_life, respond, depart=False):
        s, attacker, response = self._prepare_life_attack(
            seed, 'Agent of the Shadow Thieves', other_life=other_life,
            response_name='Revitalize' if respond else None,
        )
        if respond:
            s.state.players['D'].mana_pool.update({'C': 1, 'W': 1})
        self._declare_life_attack(s, attacker)
        e = s.engine
        if other_life > 40:
            self.assertFalse(s.state.stack)
        else:
            item, = s.state.stack
            self.assertEqual(('B', attacker.object_id), (item.controller, item.source_object_id))
            self.assertTrue(e.semantic_program_is_current_trusted(e.semantics.get(item.semantic_key)))
        if response is not None:
            self._cast_life_attack_response(s, response)
        if depart:
            pass_current(s)
            self.assertIn('pilot:C', s.pending_principals())
            accepted = s.act('pilot:C', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertNotIn('C', e.active_seats)
            self.assertEqual(40, s.state.players['C'].life)
        self.resolve(s)
        result = s.state.cards[attacker.object_id]
        expected = other_life <= 40 and not respond
        self.assertEqual(int(expected), result.counters.get('+1/+1', 0))
        self.assertEqual(expected, {'deathtouch', 'indestructible'} <= set(e._combat_keywords(result)))
        if respond:
            self.assertEqual(other_life + 3, s.state.players['D'].life)
        return s

    def test_actual_agent_uses_borrowed_attackers_opponents_and_rechecks_life(self):
        cases = ((40, False, False), (41, False, False), (40, True, False), (39, False, True))
        for index, (other_life, respond, depart) in enumerate(cases):
            with self.subTest(other_life=other_life, respond=respond, depart=depart):
                session = self._agent_life_attack(314007 + index, other_life=other_life, respond=respond, depart=depart)
                self.replay(session, load=True)

    def test_attacked_player_life_recheck_omission_mutant_is_killed(self):
        with patch('quorune.engine.semantic_intervening_condition_matches', return_value=True):
            with self.assertRaises(AssertionError):
                self._agent_life_attack(314011, other_life=40, respond=True)

    def test_actual_sailor_makes_the_attacking_commander_unblockable(self):
        s, attacker, _ = self._prepare_life_attack(314012, 'Sword Coast Sailor')
        self.add(s.engine, 'Generic Bound Body', seat='C')
        self._declare_life_attack(s, attacker)
        item, = s.state.stack
        self.assertEqual(attacker.object_id, item.source_object_id)
        self.resolve(s)
        for _ in range(8):
            if s.state.combat.blockers_declared:
                break
            pass_current(s)
        self.assertTrue(s.state.combat.blockers_declared)
        self.assertFalse(s.state.combat.blockers)
        block = next(event for event in reversed(s.state.events) if event.code == 'combat.block')
        self.assertTrue(block.details['automatic'])
        self.replay(s, load=True)

    def test_actual_hardy_uses_the_original_attackers_power_after_departure(self):
        s, attacker, response = self._prepare_life_attack(314013, 'Hardy Outlander', response_name='Unsummon')
        e = s.engine
        target = self.add(e, 'Generic Bound Body', seat='B')
        s.state.players['D'].mana_pool['U'] = 1
        attacker_power = e._numeric_stat(attacker.object_id, 'power')
        before_stats = tuple(e._numeric_stat(target.object_id, stat) for stat in ('power', 'toughness'))
        self._declare_life_attack(s, attacker)
        self.assertEqual('semantic.target', s.state.pending_decision.kind)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B', {'action_id': 'choose', 'targets': [attacker.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        accepted = s.act('pilot:B', {'action_id': 'choose', 'targets': [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self._cast_life_attack_response(s, response, targets=[attacker.ref])
        self.resolve(s)
        while s.state.pending_decision is not None and s.state.pending_decision.kind == 'replacement.order':
            principal = s.pending_principals()[0]
            options = s.packet(principal, full=True)['decision']['ctx']['options']
            decline = next(option['id'] for option in options if option['id'].startswith('decline:'))
            accepted = s.act(principal, {'action_id': 'choose', 'replacement': decline})
            self.assertTrue(accepted.ok, accepted.summary)
            self.resolve(s)
        self.assertEqual('hand', s.state.cards[attacker.object_id].zone)
        self.assertEqual(tuple(stat + attacker_power for stat in before_stats),
                         tuple(e._numeric_stat(target.object_id, stat) for stat in ('power', 'toughness')))
        self.replay(s, load=True)

    def test_actual_bastion_applies_only_to_current_controlled_designated_creatures(self):
        s=self.session(314001);e=s.engine
        own=self.commander(e,'A');foreign=self.commander(e,'B');e.move_card(own.object_id,'battlefield',log=False);e.move_card(foreign.object_id,'battlefield',log=False)
        steal=self.add(e,'Act of Treason',zone='hand');source=self.add(e,'Bastion Protector',zone='hand')
        copy_ref=e.create_token('C',name='Generic Bound Commander',copy_of=own.ref)[0]
        copy=e._resolve_object('C',copy_ref,zones={'battlefield'})
        self.assertFalse(copy.is_commander);self.assertIsNone(copy.commander_designation_id)
        action=self.ready(s,steal,{'R':1,'W':1,'C':4});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[foreign.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        for card in (own,foreign):self.assertIn('indestructible',e._combat_keywords(card))
        self.assertNotIn('indestructible',e._combat_keywords(copy));self.assertNotIn('indestructible',e._combat_keywords(source))
        self.assertEqual('B',s.state.cards[foreign.object_id].owner);self.assertEqual('A',s.state.cards[foreign.object_id].controller);self.replay(s,load=True)

    def test_copy_of_actual_commander_controlled_by_grant_player_remains_undesignated(self):
        s=self.session(314005);e=s.engine;commander=self.commander(e,'A');e.move_card(commander.object_id,'battlefield',log=False)
        copy_ref=e.create_token('B',name=commander.printed_name,copy_of=commander.ref)[0];copy=e._resolve_object('B',copy_ref,zones={'battlefield'})
        source=self.add(e,'Bastion Protector',zone='hand');bounce=self.add(e,'Unsummon',zone='hand');steal=self.add(e,'Act of Treason',zone='hand')
        action=self.ready(s,source,{'W':1,'U':2,'R':1,'C':6});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[commander.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        while s.state.pending_decision is not None and s.state.pending_decision.kind=='replacement.order':
            principal=s.pending_principals()[0];ctx=s.packet(principal,full=True)['decision']['ctx'];choice=next(o['id'] for o in ctx['options'] if o['id'].startswith('decline:'))
            accepted=s.act(principal,{'action_id':'choose','replacement':choice});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==steal.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[copy.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertFalse(s.state.cards[copy.object_id].is_commander)
        self.assertNotIn('indestructible',e._combat_keywords(copy))
        self.replay(s)

    def test_actual_clan_crafter_owned_borrowed_commander_activates_for_current_controller(self):
        s=self.session(314002);e=s.engine
        own=self.commander(e,'A');e.move_card(own.object_id,'battlefield',log=False)
        source=self.add(e,'Clan Crafter',zone='hand');steal=self.add(e,'Act of Treason',seat='B',zone='hand')
        artifact=self.add(e,'Ornithopter',seat='B');action=self.ready(s,source,{'U':1,'C':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        e.permissions.invalidate_current();s.state.pending_decision=None;e.state.active_player='B';e.state.phase='precombat_main';e.state.step='main';e.state.players['B'].mana_pool.update({'R':1,'C':4});e._grant_priority('B');e.pump();self.checkpoint(s)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==steal.ref)
        accepted=s.act('pilot:B',{'action_id':action['id'],'targets':[own.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+own.ref+':') and 'granted' in a['id'])
        hand=len(s.state.players['B'].zones['hand']);before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:A',{'action_id':action['id'],'cost_objects':[artifact.ref],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:B',{'action_id':action['id'],'cost_objects':[artifact.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('graveyard',s.state.cards[artifact.object_id].zone);self.assertEqual(1,s.state.cards[own.object_id].counters['+1/+1'])
        self.assertEqual(hand+1,len(s.state.players['B'].zones['hand']));self.replay(s,load=True)

    def test_real_partner_commanders_both_receive_bastion_without_designating_copies(self):
        names=('Thrasios, Triton Hero','Tymna the Weaver')
        deck=DeckDefinition('Paired commander grant',[*(DeckEntry(n,1,'commander') for n in names),DeckEntry('Generic Bound Plains',30)],list(names))
        s=CommanderSession.create(self.db,{seat:deck for seat in 'ABCD'},first_player='A',seed=314003,
            config=GameConfig(seed=314003,auto_pass_empty_priority=False));keep_all(s);e=s.engine
        commanders=[c for c in s.state.cards.values() if c.owner=='A' and c.is_commander]
        register_generated_programs(self.db,e.semantics,[self.db.lookup('Bastion Protector')],trust_level='trusted',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_effect_programs=True)
        self.assertEqual(2,len(commanders));self.assertEqual(2,len({c.commander_designation_id for c in commanders}))
        for card in commanders:e.move_card(card.object_id,'battlefield',log=False)
        source=self.add(e,'Bastion Protector',zone='hand');action=self.ready(s,source,{'W':1,'C':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertTrue(all('indestructible' in e._combat_keywords(c) for c in commanders));self.replay(s,load=True)

    def test_actual_source_bounce_removes_commander_grants_and_replays(self):
        s=self.session(314004);e=s.engine;commander=self.commander(e,'A');e.move_card(commander.object_id,'battlefield',log=False)
        source=self.add(e,'Bastion Protector',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
        action=self.ready(s,source,{'W':1,'U':2,'C':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertIn('indestructible',e._combat_keywords(commander))
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertNotIn('indestructible',e._combat_keywords(commander));self.replay(s)

    def test_designation_query_omission_mutant_is_killed_by_actual_bastion(self):
        original=object_matches_query
        def ignore(row,predicate):return original(row,replace(predicate,commander=None))
        with patch('quorune.continuous_effects.object_matches_query',side_effect=ignore):
            with self.assertRaises(AssertionError):self.test_actual_bastion_applies_only_to_current_controlled_designated_creatures()


if __name__=='__main__':unittest.main()
