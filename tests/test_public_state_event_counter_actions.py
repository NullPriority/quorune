from __future__ import annotations

import unittest

from quorune.continuous_effects import Layer
from quorune.object_predicate import ObjectQuerySpec
from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_public_target_counter_selection_observer_departure_and_replay(self):
        session = self.session(3640201)
        engine = session.engine
        observer = self.add(engine, 'Thran Vigil', zone='hand', ref='public-target-vigil')
        target = self.add(engine, 'Generic Bound Body', ref='public-target-eligible')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='public-target-foreign')
        graveyard_card = self.add(engine, 'Generic Bound Body', zone='graveyard', ref='public-target-returned-card')
        return_spell = self.add(engine, 'Raise Dead', zone='hand', ref='public-target-raise-dead')
        removal = self.add(engine, 'Disenchant', seat='B', zone='hand', ref='public-target-observer-removal')
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, observer, {'B': 4, 'C': 1})
        session.state.players['B'].mana_pool.update({'W': 1, 'C': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == return_spell.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [graveyard_card.ref], 'pay': 'auto'}).ok)
        decision = self.resolve(session)
        self.assertEqual('semantic.target', decision.kind)
        self.assertEqual('hand', session.state.cards[graveyard_card.object_id].zone)
        before = session.state.to_dict()
        for principal, choice in (('pilot:B', target.ref), ('pilot:A', foreign.ref)):
            self.assertFalse(session.act(principal, {'action_id': 'choose', 'targets': [choice]}).ok)
            self.assertEqual(before, session.state.to_dict())
        self.replay(session, load=True)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]}).ok)
        for _ in range(12):
            if session.state.priority_player == 'B': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == removal.ref)
        self.assertTrue(session.act('pilot:B', {'action_id': action['id'], 'targets': [observer.ref], 'pay': 'auto'}).ok)
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[observer.object_id].zone)
        self.assertEqual(1, session.state.cards[target.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_original_public_target_counter_reentered_target_fizzles_and_replays(self):
        session = self.session(3640202)
        engine = session.engine
        observer = self.add(engine, 'Thran Vigil', ref='public-target-blink-vigil')
        target = self.add(engine, 'Generic Bound Body', ref='public-target-blink-recipient')
        graveyard_card = self.add(engine, 'Generic Bound Body', zone='graveyard', ref='public-target-blink-returned')
        spell = self.add(engine, 'Raise Dead', zone='hand', ref='public-target-blink-return')
        blink = self.add(engine, 'Cloudshift', zone='hand', ref='public-target-native-blink')
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, spell, {'B': 1, 'W': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [graveyard_card.ref], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]}).ok)
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == blink.ref)
        old_incarnation = target.logical_object_id
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}).ok)
        self.replay(session, load=True)
        self.resolve(session)
        current = session.state.cards[target.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertNotEqual(old_incarnation, current.logical_object_id)
        self.assertEqual(0, current.counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[observer.object_id].counters.get('+1/+1', 0))
        self.replay(session, load=True)

    def test_original_public_target_counter_dual_entry_death_preserves_source_lki(self):
        session = self.session(3640203)
        engine = session.engine
        source = self.add(engine, 'Hunting Moa', zone='hand', ref='public-target-original-moa')
        own = self.add(engine, 'Generic Bound Body', ref='public-target-moa-own')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='public-target-moa-foreign')
        damage = self.add(engine, 'Lightning Bolt', zone='hand', ref='public-target-moa-damage')
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {'G': 3, 'C': 2, 'R': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [foreign.ref]}).ok)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[own.object_id].counters.get('+1/+1', 0))
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == damage.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [own.ref]}).ok)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[own.object_id].counters.get('+1/+1', 0))
        self.assertEqual(1, session.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        self.replay(session, load=True)

    def test_original_public_departure_source_counter_cast_pending_reentry_and_replay(self):
        session = self.session(3640101)
        engine = session.engine
        observer = self.add(engine, 'Ninth Bridge Patrol', zone='hand', ref='public-counter-observer')
        self.add(engine, 'Vedalken Orrery', ref='public-counter-flash-permission')
        owned = self.add(engine, 'Generic Bound Body', ref='public-counter-own-body')
        positive_body = self.add(engine, 'Generic Bound Body', ref='public-counter-positive-body')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='public-counter-foreign-body')
        first = self.add(engine, 'Unsummon', zone='hand', ref='public-counter-first-bounce')
        second = self.add(engine, 'Unsummon', zone='hand', ref='public-counter-source-bounce')
        last = self.add(engine, 'Unsummon', zone='hand', ref='public-counter-foreign-bounce')
        final = self.add(engine, 'Unsummon', zone='hand', ref='public-counter-positive-bounce')
        self.assert_whole_admission(observer.printed_name)
        offered = self.ready(session, observer, {'W': 2, 'C': 2, 'U': 8})
        self.checkpoint(session)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.resolve(session)

        def cast_bounce(card, target):
            for _ in range(12):
                if session.state.priority_player == 'A': break
                self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
            action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                          if a.get('card') == card.ref)
            accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
            self.assertTrue(accepted.ok, accepted.summary)

        cast_bounce(first, owned)
        for _ in range(16):
            if session.state.cards[owned.object_id].zone == 'hand': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('hand', session.state.cards[owned.object_id].zone)
        self.assertTrue(session.state.stack)
        self.assertEqual(0, session.state.cards[observer.object_id].counters.get('+1/+1', 0))
        self.replay(session, load=True)
        old_incarnation = session.state.cards[observer.object_id].logical_object_id
        cast_bounce(second, session.state.cards[observer.object_id])
        for _ in range(16):
            if session.state.cards[observer.object_id].zone == 'hand': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('hand', session.state.cards[observer.object_id].zone)
        for _ in range(12):
            if session.state.priority_player == 'A': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == observer.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        for _ in range(16):
            if session.state.cards[observer.object_id].zone == 'battlefield': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        current = session.state.cards[observer.object_id]
        self.assertNotEqual(old_incarnation, current.logical_object_id)
        self.resolve(session)
        self.assertEqual(0, current.counters.get('+1/+1', 0))
        cast_bounce(last, foreign)
        self.resolve(session)
        self.assertEqual(0, current.counters.get('+1/+1', 0))
        cast_bounce(final, session.state.cards[positive_body.object_id])
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[observer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[owned.object_id].counters.get('+1/+1', 0))
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_original_public_source_counter_controller_ordering_and_replay(self):
        session = self.session(3640102)
        engine = session.engine
        own = [self.add(engine, 'Starfield Mystic', ref=f'public-counter-mystic-{index}') for index in range(2)]
        foreign = self.add(engine, 'Starfield Mystic', seat='B', ref='public-counter-foreign-mystic')
        first = self.add(engine, 'Parallel Lives', ref='public-counter-own-enchantment')
        second = self.add(engine, 'Parallel Lives', seat='B', ref='public-counter-foreign-enchantment')
        spell = self.add(engine, 'Tranquility', zone='hand', ref='public-counter-simultaneous-destroy')
        self.assert_whole_admission('Starfield Mystic')
        action = self.ready(session, spell, {'G': 1, 'C': 2})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        for _ in range(16):
            if session.state.pending_decision and session.state.pending_decision.kind == 'trigger.order': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        pending = [item for batch in session.state.pending_trigger_batches for item in batch.items]
        own_pending = [item for item in pending if item.controller == 'A']
        self.assertEqual(3, len(pending))
        self.assertEqual(2, len(own_pending))
        self.assertEqual({card.object_id for card in own}, {item.source_object_id for item in own_pending})
        self.assertEqual('graveyard', first.zone)
        self.assertEqual('graveyard', second.zone)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id': 'order', 'triggers': [item.ref for item in own_pending]}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.replay(session, load=True)
        self.assertTrue(session.act('pilot:A', {'action_id': 'order', 'triggers': [item.ref for item in reversed(own_pending)]}).ok)
        self.assertEqual(['A', 'A', 'B'], [item.controller for item in session.state.stack])
        self.resolve(session)
        for card in own:self.assertEqual(1, session.state.cards[card.object_id].counters.get('+1/+1', 0))
        self.assertEqual(1, session.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        for card in own:self.assertEqual(1, session.state.cards[card.object_id].counters.get('+1/+1', 0))
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_public_source_counter_current_ability_diagnostic_stops_later_occurrences(self):
        from quorune.continuous_effects import (
            ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectDuration,
            ContinuousObjectIdentity, ContinuousOperation,
        )
        from quorune.continuous_effect_state import commit_continuous_effect
        session = self.session(3640103)
        engine = session.engine
        source = self.add(engine, 'Starfield Mystic', ref='public-counter-current-ability')
        first = self.add(engine, 'Parallel Lives', ref='public-counter-first-diagnostic')
        second = self.add(engine, 'Parallel Lives', ref='public-counter-second-diagnostic')
        spell = self.add(engine, 'Disenchant', zone='hand', ref='public-counter-diagnostic-destroy')
        later_spell = self.add(engine, 'Disenchant', zone='hand', ref='public-counter-later-diagnostic-destroy')
        action = self.ready(session, spell, {'W': 4, 'C': 2})
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [first.ref], 'pay': 'auto'}).ok)
        for _ in range(16):
            if session.state.cards[first.object_id].zone == 'graveyard': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual(1, len(session.state.stack))
        commit_continuous_effect(engine.state, ContinuousEffect(
            effect_id='fixture:remove-public-counter-observer-ability', source_id='fixture:source-counter-owner-diagnostic',
            layer=Layer.ABILITY, sublayer='6', timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation('remove_all_abilities'),), origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, applies=ObjectQuerySpec(zones=('battlefield',)),
            locked_objects=(ContinuousObjectIdentity(source.object_id, source.logical_object_id),),
        ))
        self.resolve(session)
        self.assertEqual(1, source.counters.get('+1/+1', 0))
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == later_spell.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [second.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual('graveyard', second.zone)
        self.assertFalse(session.state.stack)
        self.assertEqual(1, source.counters.get('+1/+1', 0))
