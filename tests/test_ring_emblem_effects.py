from dataclasses import replace
import unittest

import test_ring_temptation_state as ring_support
from quorune.continuous_effects import (
    CharacteristicState, ContinuousEffect, ContinuousOperation, Layer,
    evaluate_continuous_effects,
)
from quorune.control_effects import record_source_transition
from quorune.ring_effects import ring_legendary_effects, ring_power_block_allowed
from quorune.ring_model import RingObjectIdentity, RingStateError
from quorune.ring_state import (
    commit_ring_bearer_choice, current_ring_rule_views, prepare_ring_temptation,
)


class RingEmblemEffectValueTests(unittest.TestCase):
    def setUp(self):
        self.host = ring_support.RingOwnerHost()
        self.card = self.host.add_creature('C1')
        plan = prepare_ring_temptation(self.host,player='A',reason='external effect witness')
        commit_ring_bearer_choice(self.host,plan,self.card.ref)

    def identity(self, card=None):
        card = card or self.card
        return RingObjectIdentity(card.object_id,card.ref,card.logical_object_id)

    def evaluated(self, effects, *, identity=None, types=('Creature',)):
        identity = identity or self.identity()
        return evaluate_continuous_effects(
            CharacteristicState(name='Rule effect fixture',controller='A',
                                card_types=set(types),abilities=['Flying'],power=2,toughness=2),
            effects,context={'object_id':identity.object_id,
                             'logical_object_id':identity.logical_object_id,'zone':'battlefield'})

    def test_legendary_is_an_external_layer_four_effect_not_a_bearer_ability(self):
        views = current_ring_rule_views(self.host)
        effects = ring_legendary_effects(views)
        loss = ContinuousEffect('ability-loss','other-source',Layer.ABILITY,'6',99,
                                (ContinuousOperation('remove_all_abilities'),))
        result = self.evaluated((*effects,loss))
        self.assertIn('Legendary',result.characteristics['supertypes'])
        self.assertEqual([],result.characteristics['abilities'])
        self.assertFalse(ring_power_block_allowed(views,attacker=self.identity(),
                                                 attacker_power=2,blocker_power=3))
        self.assertEqual(views[0].state.emblem.object_id,effects[0].source_id)
        self.assertEqual(effects[0],ContinuousEffect.from_dict(effects[0].to_dict()))
        self.assertEqual((),ring_legendary_effects(views,maximum_layer=Layer.COPY))

    def test_greater_power_only_and_unknown_power_do_not_become_legal_by_default(self):
        views = current_ring_rule_views(self.host)
        for attacker,blocker,expected in ((2,1,True),(2,2,True),(2,3,False),(-2,-3,True),(-2,-1,False)):
            with self.subTest(attacker=attacker,blocker=blocker):
                self.assertEqual(expected,ring_power_block_allowed(views,attacker=self.identity(),
                    attacker_power=attacker,blocker_power=blocker))
        for value in (None,True,'2'):
            with self.subTest(value=value),self.assertRaises(RingStateError):
                ring_power_block_allowed(views,attacker=self.identity(),attacker_power=value,blocker_power=3)
        other = RingObjectIdentity('unrelated','C9','unrelated:0')
        self.assertTrue(ring_power_block_allowed(views,attacker=other,attacker_power=None,blocker_power=None))

    def test_new_bearer_changes_membership_without_retimestamping_the_emblem(self):
        old = ring_legendary_effects(current_ring_rule_views(self.host))[0]
        other = self.host.add_creature('C2')
        plan = prepare_ring_temptation(self.host,player='A',reason='new bearer')
        commit_ring_bearer_choice(self.host,plan,other.ref)
        current = ring_legendary_effects(current_ring_rule_views(self.host))
        self.assertEqual(old.timestamp,current[0].timestamp)
        self.assertEqual(old.effect_id,current[0].effect_id)
        self.assertNotIn('Legendary',self.evaluated(current).characteristics['supertypes'])
        self.assertIn('Legendary',self.evaluated(current,identity=self.identity(other)).characteristics['supertypes'])

    def test_type_loss_preserves_designation_and_reentry_or_control_return_does_not(self):
        self.host.types[self.card.object_id] = 'Artifact'
        effects = ring_legendary_effects(current_ring_rule_views(self.host))
        self.assertIn('Legendary',self.evaluated(effects,types=('Artifact',)).characteristics['supertypes'])
        self.card.controller = 'B'
        record_source_transition(self.host,self.card,control_changed=True)
        self.card.controller = 'A'
        record_source_transition(self.host,self.card,control_changed=True)
        self.assertEqual((),ring_legendary_effects(current_ring_rule_views(self.host)))
        newer = replace(self.identity(),logical_object_id='new-incarnation')
        self.assertNotIn('Legendary',self.evaluated(effects,identity=newer).characteristics['supertypes'])


class RingEmblemEngineAdapterTests(unittest.TestCase):
    def test_static_legendary_omission_mutant_is_killed(self):
        from unittest.mock import patch
        self.test_current_characteristics_and_block_adapter_apply_the_external_rule()
        with patch('quorune.characteristic_evaluation_host.ring_legendary_effects', return_value=()), self.assertRaises(AssertionError):
            self.test_current_characteristics_and_block_adapter_apply_the_external_rule()

    def test_bearer_control_continuity_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.ring_model import RingBearerSnapshot
        self.test_real_control_away_and_back_does_not_restore_the_emblem_effect()
        with patch.object(RingBearerSnapshot, 'control_is_continuous', return_value=True), self.assertRaises(AssertionError):
            self.test_real_control_away_and_back_does_not_restore_the_emblem_effect()

    def test_attack_draw_amount_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.ring_triggers import RingEmblemTrigger
        original = RingEmblemTrigger.effects
        def mutated(trigger):
            return tuple({**effect, 'count': 2} if effect.get('op') == 'draw' else effect
                         for effect in original(trigger))
        self.test_internal_attack_trigger_copy_draws_and_discards_for_copy_controller()
        with patch.object(RingEmblemTrigger, 'effects', mutated), self.assertRaises(AssertionError):
            self.test_internal_attack_trigger_copy_draws_and_discards_for_copy_controller()

    def test_blocker_sacrifice_omission_mutant_is_killed(self):
        from unittest.mock import patch
        self.test_internal_blocked_trigger_routes_through_delayed_and_sacrifice_primitives()
        with patch('quorune.ring_delays.resolve_ring_blocker_sacrifice', return_value=None), self.assertRaises(AssertionError):
            self.test_internal_blocked_trigger_routes_through_delayed_and_sacrifice_primitives()

    def test_combat_life_amount_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.ring_triggers import RingEmblemTrigger
        original = RingEmblemTrigger.effects
        def mutated(trigger):
            return tuple({**effect, 'amount': 4} if effect.get('op') == 'lose_life_each_opponent' else effect
                         for effect in original(trigger))
        self.test_internal_damage_trigger_copy_life_loss_uses_copy_opponents()
        with patch.object(RingEmblemTrigger, 'effects', mutated), self.assertRaises(AssertionError):
            self.test_internal_damage_trigger_copy_life_loss_uses_copy_opponents()

    """Actual engine adapters with an internal owner call, not native card admission."""
    @classmethod
    def setUpClass(cls):
        from common import load_assets
        cls.db,cls.first,cls.second = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def engine(self, seed):
        from common import keep_all,make_session
        session = make_session(self.db,self.first,self.second,players=2,seed=seed)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        self.session = session
        return engine

    def token(self, engine, controller, power):
        refs = engine.create_token(controller,name='Ring adapter creature',characteristics={
            'type_line':'Creature — Soldier','power':str(power),'toughness':'3',
            'colors':[],'keywords':[],'oracle_text':''})
        return next(card for card in engine.state.cards.values() if card.ref==refs[0])

    def test_current_characteristics_and_block_adapter_apply_the_external_rule(self):
        engine = self.engine(70154001)
        bearer = self.token(engine,'A',2)
        larger = self.token(engine,'B',3)
        equal = self.token(engine,'B',2)
        plan = prepare_ring_temptation(engine,player='A',reason='engine adapter witness')
        commit_ring_bearer_choice(engine,plan,bearer.ref)
        data = engine._effective_card_data(bearer)
        self.assertIn('legendary',engine._type_parts(data['type_line'])[2])
        self.assertNotIn('skulk',engine._combat_keywords(bearer))
        self.assertEqual((False,'external_greater_power_restriction'),engine._can_block(bearer,larger))
        self.assertTrue(engine._can_block(bearer,equal)[0])
        copies = engine.create_token('A',name='Copied creature',copy_of=bearer.ref)
        copied = next(card for card in engine.state.cards.values() if card.ref==copies[0])
        self.assertNotIn('legendary',engine._type_parts(engine._effective_card_data(copied)['type_line'])[2])

    def test_real_control_away_and_back_does_not_restore_the_emblem_effect(self):
        engine = self.engine(70154002)
        bearer = self.token(engine,'A',2)
        blocker = self.token(engine,'B',3)
        plan = prepare_ring_temptation(engine,player='A',reason='real control witness')
        commit_ring_bearer_choice(engine,plan,bearer.ref)
        engine.change_control(bearer.object_id,'B',reason='control leaves Ring player')
        engine.change_control(bearer.object_id,'A',reason='control returns')
        self.assertNotIn('legendary',engine._type_parts(engine._effective_card_data(bearer)['type_line'])[2])
        self.assertTrue(engine._can_block(bearer,blocker)[0])

    def test_internal_attack_trigger_copy_draws_and_discards_for_copy_controller(self):
        from quorune.ring_triggers import RingCombatOccurrence,RingTriggerKind,materialize_ring_trigger,ring_trigger_specs
        engine = self.engine(70154003)
        bearer = self.token(engine,'A',2)
        for _ in range(2):
            plan=prepare_ring_temptation(engine,player='A',reason='attack trigger fixture')
            commit_ring_bearer_choice(engine,plan,bearer.ref)
        identity=RingObjectIdentity(bearer.object_id,bearer.ref,bearer.logical_object_id)
        event=RingCombatOccurrence(RingTriggerKind.ATTACK,identity,'A')
        spec=ring_trigger_specs(current_ring_rule_views(engine),event)[0]
        item=materialize_ring_trigger(engine,spec)
        item.controller='B'
        before={seat:len(engine.state.players[seat].zones['hand']) for seat in ('A','B')}
        engine.state.stack.append(item)
        engine._prepare_stack_resolution()
        self.assertEqual('choice.apnap',engine.state.pending_decision.kind)
        self.assertEqual(['B'],engine.state.pending_decision.actors)
        self.assertEqual(before['A'],len(engine.state.players['A'].zones['hand']))
        self.assertEqual(before['B']+1,len(engine.state.players['B'].zones['hand']))
        self.assertIsNone(self.session.packet('pilot:A',full=True)['decision'])
        ref=engine.state.cards[engine.state.players['B'].zones['hand'][0]].ref
        result=self.session.act('pilot:B',{'action_id':'choose','cards':[ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(before['B'],len(engine.state.players['B'].zones['hand']))

    def test_internal_damage_trigger_copy_life_loss_uses_copy_opponents(self):
        from quorune.ring_triggers import RingCombatOccurrence,RingTriggerKind,materialize_ring_trigger,ring_trigger_specs
        engine = self.engine(70154004)
        bearer = self.token(engine,'A',2)
        for _ in range(4):
            plan=prepare_ring_temptation(engine,player='A',reason='damage trigger fixture')
            commit_ring_bearer_choice(engine,plan,bearer.ref)
        identity=RingObjectIdentity(bearer.object_id,bearer.ref,bearer.logical_object_id)
        event=RingCombatOccurrence(RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER,identity,'A',
                                   damaged_player='B',dealt_amount=1,combat=True)
        item=materialize_ring_trigger(engine,ring_trigger_specs(current_ring_rule_views(engine),event)[0])
        item.controller='B'
        before={seat:engine.state.players[seat].life for seat in ('A','B')}
        engine.state.stack.append(item)
        engine._prepare_stack_resolution()
        self.assertEqual(before['A']-3,engine.state.players['A'].life)
        self.assertEqual(before['B'],engine.state.players['B'].life)

    def test_blocker_delay_uses_current_controller_and_original_incarnation(self):
        from quorune.ring_delays import resolve_ring_blocker_sacrifice,schedule_ring_blocker_sacrifice
        from quorune.delayed_triggers import materialize_delayed_trigger
        from quorune.target_source import stack_targeting_source
        engine=self.engine(70154005)
        bearer=self.token(engine,'A',2)
        blocker=self.token(engine,'B',2)
        plan=prepare_ring_temptation(engine,player='A',reason='delay owner witness')
        commit_ring_bearer_choice(engine,plan,bearer.ref)
        source=current_ring_rule_views(engine)[0].state.emblem
        identity=RingObjectIdentity(blocker.object_id,blocker.ref,blocker.logical_object_id)
        delayed=schedule_ring_blocker_sacrifice(engine,controller='B',emblem=source,
            emblem_owner='A',blocker=identity,reason='copied Ring trigger')
        self.assertEqual([blocker.object_id],delayed.referred_object_ids)
        item=materialize_delayed_trigger(delayed,ref='S-RING',stack_id='ring-delay',visibility=engine.seats)
        self.assertEqual('B',item.controller)
        self.assertEqual(source.object_id,item.source_object_id)
        self.assertEqual('A',stack_targeting_source(engine,item).source_controller)
        engine.change_control(blocker.object_id,'A',reason='blocker changes controller')
        self.assertEqual(('A',blocker.ref),resolve_ring_blocker_sacrifice(engine,
            blocker=identity,reason='current controller sacrifices'))
        self.assertEqual('graveyard',blocker.zone)
        self.assertIn(blocker.object_id,engine.state.players['B'].zones['graveyard'])

    def _designate(self, engine, bearer, count):
        for _ in range(count):
            plan = prepare_ring_temptation(engine, player='A', reason='sealed occurrence witness')
            commit_ring_bearer_choice(engine, plan, bearer.ref)

    def _declare_attack(self, engine, bearer):
        from quorune.attack_transition_engine_adapter import commit_engine_attack_declaration
        from quorune.model import CombatState
        engine.state.active_player = 'A'
        engine.state.combat = CombatState()
        commit_engine_attack_declaration(engine, controller='A', chosen=[(
            bearer, {'target': 'B', 'kind': 'player', 'defending_player': 'B'})])

    def test_attack_transition_discovers_the_command_zone_emblem(self):
        from quorune.attack_transition_engine_adapter import attack_transition_stack_items
        engine = self.engine(70154009)
        bearer = self.token(engine, 'A', 2)
        self._designate(engine, bearer, 2)
        emblem = current_ring_rule_views(engine)[0].state.emblem
        self._declare_attack(engine, bearer)
        items = [item for item in attack_transition_stack_items(engine)
                 if item.context.get('ring_trigger_kind') == 'attack']
        self.assertEqual(1, len(items))
        self.assertEqual(emblem.object_id, items[0].source_object_id)
        self.assertNotEqual(bearer.object_id, items[0].source_object_id)
        self.assertEqual('A', items[0].controller)
        self.assertEqual(bearer.logical_object_id, items[0].context['ring_bearer_logical_object_id'])

    def test_block_transition_discovers_each_distinct_blocker(self):
        from quorune.block_transition_engine_adapter import (
            commit_engine_block_declaration, enqueue_block_transition_triggers)
        from quorune.model import StackItem
        engine = self.engine(70154010)
        bearer = self.token(engine, 'A', 2)
        blockers = [self.token(engine, 'B', power) for power in (1, 2)]
        self._designate(engine, bearer, 3)
        emblem = current_ring_rule_views(engine)[0].state.emblem
        self._declare_attack(engine, bearer)
        commit_engine_block_declaration(engine, controller='B',
                                        chosen=[(blocker, bearer) for blocker in blockers])
        enqueue_block_transition_triggers(engine)
        self.assertEqual([], engine.state.stack)
        pending = [StackItem.from_dict(item.to_dict())
                   for batch in engine.state.pending_trigger_batches
                   for group in batch.groups for item in group.items]
        items = [item for item in pending
                 if item.context.get('ring_trigger_kind') == 'blocked_by_creature']
        self.assertEqual(2, len(items))
        self.assertEqual({emblem.object_id}, {item.source_object_id for item in items})
        self.assertEqual({blocker.object_id for blocker in blockers},
                         {item.referred_object_ids[0] for item in items})

    def _pending_ring_blockers(self, engine):
        from quorune.model import StackItem
        pending = [StackItem.from_dict(item.to_dict())
                   for batch in engine.state.pending_trigger_batches
                   for group in batch.groups for item in group.items]
        return [item for item in pending
                if item.context.get('ring_trigger_kind') == 'blocked_by_creature']

    def test_put_blocking_bypasses_declaration_restrictions_and_emits_each_new_relation(self):
        from unittest.mock import patch
        from quorune.block_transition_engine_adapter import commit_engine_put_blocking
        from quorune.combat_relationship_state import BlockDeclarationAssignment
        engine = self.engine(70154012)
        bearer = self.token(engine, 'A', 2)
        first, second = (self.token(engine, 'B', power) for power in (9, 2))
        self._designate(engine, bearer, 3)
        self._declare_attack(engine, bearer)
        engine.state.phase, engine.state.step = 'combat', 'declare_blockers'
        self.assertFalse(engine._can_block(bearer, first)[0])
        first.tapped = True
        assignment = BlockDeclarationAssignment(first.object_id, bearer.object_id)
        with patch.object(engine, '_dispatch_semantic_event', wraps=engine._dispatch_semantic_event) as events:
            self.assertEqual((assignment,), commit_engine_put_blocking(engine,
                controller='B', assignments=(assignment,)))
            self.assertEqual((), commit_engine_put_blocking(engine,
                controller='B', assignments=(assignment,)))
            commit_engine_put_blocking(engine, controller='B', assignments=(
                BlockDeclarationAssignment(second.object_id, bearer.object_id),))
        kinds = [call.args[0] for call in events.call_args_list]
        self.assertNotIn('creature.blocks', kinds)
        self.assertEqual(1, kinds.count('creature.becomes_blocked'))
        self.assertEqual(2, kinds.count('creature.becomes_blocked_by'))
        self.assertEqual([first.object_id, second.object_id], engine.state.combat.blockers[bearer.object_id])
        self.assertTrue(first.tapped)
        items = self._pending_ring_blockers(engine)
        self.assertEqual(2, len(items))
        self.assertEqual({first.object_id, second.object_id},
                         {item.referred_object_ids[0] for item in items})

    def test_put_blocking_wrong_defender_or_departed_attacker_keeps_entry_without_blocking(self):
        from quorune.block_transition_engine_adapter import commit_engine_put_blocking
        from quorune.combat_relationship_state import BlockDeclarationAssignment, remove_combat_relationships
        from quorune.errors import StateInvariantError
        engine = self.engine(70154013)
        bearer = self.token(engine, 'A', 2)
        wrong_defender = self.token(engine, 'A', 2)
        entrant = self.token(engine, 'B', 2)
        self._designate(engine, bearer, 3)
        self._declare_attack(engine, bearer)
        self.assertEqual((), commit_engine_put_blocking(engine, controller='A', assignments=(
            BlockDeclarationAssignment(wrong_defender.object_id, bearer.object_id),)))
        with self.assertRaises(StateInvariantError):
            commit_engine_put_blocking(engine, controller='B', assignments=(
                BlockDeclarationAssignment(entrant.object_id, bearer.object_id),
                BlockDeclarationAssignment('missing-entering-object', bearer.object_id)))
        self.assertEqual({}, engine.state.combat.blockers)
        self.assertIsNone(entrant.blocking)
        remove_combat_relationships(engine.state.combat, bearer.object_id)
        self.assertEqual((), commit_engine_put_blocking(engine, controller='B', assignments=(
            BlockDeclarationAssignment(entrant.object_id, bearer.object_id),)))
        for card in (wrong_defender, entrant):
            self.assertEqual('battlefield', card.zone)
            self.assertIsNone(card.blocking)
        self.assertEqual([], engine.state.pending_trigger_batches)

    def test_damage_pipeline_keeps_the_trigger_after_bearer_control_changes(self):
        from quorune.damage import damage_proposal, resolve_damage_batch
        engine = self.engine(70154011)
        bearer = self.token(engine, 'A', 2)
        self._designate(engine, bearer, 4)
        ordinary = damage_proposal(engine, proposal_id='ring-noncombat-damage', actor='A',
            source_ref=bearer.ref, target='B', amount=1, combat=False, reason='noncombat control')
        self.assertEqual((), resolve_damage_batch(engine, (ordinary,)).ring_trigger_items)
        self.assertEqual([], engine.state.pending_trigger_batches)
        before = engine.state.players['B'].life
        proposal = damage_proposal(engine, proposal_id='ring-combat-damage', actor='A',
            source_ref=bearer.ref, target='B', amount=2, combat=True, reason='Ring occurrence')
        result = resolve_damage_batch(engine, (proposal,))
        self.assertEqual(1, len(result.ring_trigger_items))
        self.assertEqual(before - 2, engine.state.players['B'].life)
        engine.change_control(bearer.object_id, 'B', reason='bearer control after damage')
        self.assertIsNone(current_ring_rule_views(engine)[0].state.bearer)
        engine._prepare_stack_resolution()
        self.assertEqual(before - 5, engine.state.players['B'].life)

    def test_skipped_end_combat_and_source_lki_do_not_erase_waiting_delay(self):
        from quorune.ring_delays import resolve_ring_blocker_sacrifice,schedule_ring_blocker_sacrifice
        from quorune.delayed_triggers import materialize_delayed_trigger
        from quorune.target_source import stack_targeting_source
        from quorune.trigger_processing import TriggerProcessingOwner
        engine=self.engine(70154006)
        # Tokens cannot return after leaving the battlefield (CR 111.8).
        blocker=next(card for card in engine.state.cards.values()
            if card.owner=='B' and card.zone=='library'
            and 'creature' in engine.card_record(card).type_line.casefold())
        engine.move_card(blocker.object_id,'battlefield',controller='B',log=False)
        plan=prepare_ring_temptation(engine,player='A',reason='delayed source LKI')
        source=plan.state.emblem
        identity=RingObjectIdentity(blocker.object_id,blocker.ref,blocker.logical_object_id)
        engine.state.players['A'].zones['command'].remove(source.object_id)
        engine.state.cards.pop(source.object_id)
        delayed=schedule_ring_blocker_sacrifice(engine,controller='B',emblem=source,
            emblem_owner='A',blocker=identity,reason='source left before copied ability resolved')
        self.assertIsNone(delayed.expires_turn_sequence)
        engine.state.turn_sequence+=1
        owner=TriggerProcessingOwner(engine)
        self.assertFalse(owner.trigger_matches(delayed,'step.begin',{'phase':'ending','step':'end_step'}))
        self.assertTrue(owner.trigger_matches(delayed,'step.begin',{'phase':'combat','step':'end_combat'}))
        item=materialize_delayed_trigger(delayed,ref='S-LKI',stack_id='ring-delay-lki',visibility=engine.seats)
        self.assertEqual('A',stack_targeting_source(engine,item).source_controller)
        engine.move_card(blocker.object_id,'exile',log=False)
        engine.move_card(blocker.object_id,'battlefield',controller='B',log=False)
        self.assertIsNone(resolve_ring_blocker_sacrifice(engine,blocker=identity,reason='old blocker only'))
        self.assertEqual('battlefield',blocker.zone)

    def test_internal_blocked_trigger_routes_through_delayed_and_sacrifice_primitives(self):
        from quorune.ring_triggers import RingCombatOccurrence,RingTriggerKind,materialize_ring_trigger,ring_trigger_specs
        from quorune.delayed_triggers import materialize_delayed_trigger
        engine=self.engine(70154007)
        bearer=self.token(engine,'A',2)
        blocker=next(card for card in engine.state.cards.values()
            if card.owner=='B' and card.zone=='library'
            and 'creature' in engine.card_record(card).type_line.casefold())
        engine.move_card(blocker.object_id,'battlefield',controller='B',log=False)
        for _ in range(3):
            plan=prepare_ring_temptation(engine,player='A',reason='routed blocked trigger')
            commit_ring_bearer_choice(engine,plan,bearer.ref)
        actor=RingObjectIdentity(bearer.object_id,bearer.ref,bearer.logical_object_id)
        blocked_by=RingObjectIdentity(blocker.object_id,blocker.ref,blocker.logical_object_id)
        event=RingCombatOccurrence(RingTriggerKind.BLOCKED_BY_CREATURE,actor,'A',blocker=blocked_by)
        trigger=materialize_ring_trigger(engine,ring_trigger_specs(current_ring_rule_views(engine),event)[0])
        trigger.controller='B'
        engine.state.stack.append(trigger)
        engine._prepare_stack_resolution()
        delayed=engine.state.delayed_triggers[-1]
        self.assertEqual('B',delayed.controller)
        self.assertIsNone(delayed.expires_turn_sequence)
        engine.change_control(blocker.object_id,'A',reason='current controller at end combat')
        ref=engine._next_ref('S')
        item=materialize_delayed_trigger(delayed,ref=ref,
            stack_id=engine._stable_runtime_id('stack',ref),visibility=engine.seats)
        engine.state.stack.append(item)
        engine._prepare_stack_resolution()
        self.assertEqual('graveyard',blocker.zone)
        self.assertIn(blocker.object_id,engine.state.players['B'].zones['graveyard'])
