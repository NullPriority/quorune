"""Receiving-damage subscriptions observe one simultaneous recipient event."""
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest

from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ReceivedDamageNativeTests(actions.InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'received-damage.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/received-damage-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Received damage', [
            DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Isamaru, Hound of Konda'])

    def receiving_items(self, session, source):
        stacked = [item for item in session.state.stack if item.kind == 'triggered_ability'
                   and item.source_object_id == source.object_id]
        pending = [item for batch in session.state.pending_trigger_batches for item in batch.items
                   if item.get('source_object_id') == source.object_id]
        return [*stacked, *pending]

    def test_actual_ripjaw_multiple_blockers_create_one_receiving_trigger_and_one_private_draw(self):
        session = self.session(613654001)
        source = self.add(session.engine, 'Ripjaw Raptor')
        blockers = [self.add(session.engine, 'Grizzly Bears', seat='B', ref='received-blocker-' + str(i))
                    for i in range(2)]
        self.add(session.engine, 'Divination', zone='hand', ref='private-received-hand')
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.multiblock(session, source, blockers)
        self.assertEqual(4, session.state.cards[source.object_id].marked_damage)
        self.assertEqual(1, len(self.receiving_items(session, source)))
        hand = len(session.state.players['A'].zones['hand'])
        session = self.replay(session, load=True)
        self.settle(session)
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('private-received-hand', str(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def multiblock(self, session, source, blockers):
        self.attack(session, [(source, 'B')])
        self.until(session, lambda s: s.state.pending_decision is not None
                   and s.state.pending_decision.kind == 'combat.blockers')
        result = session.act('pilot:B', {
            'action_id': 'block', 'blocks': {card.ref: source.ref for card in blockers},
        })
        self.assertTrue(result.ok, result.summary)
        for _ in range(60):
            session.engine.pump()
            if self.receiving_items(session, source):
                break
            decision = session.state.pending_decision
            self.assertIsNotNone(decision)
            if decision.kind == 'priority':
                command = {'action_id': 'pass'}
            elif 'damage' in decision.kind:
                power = session.engine._numeric_stat(source.object_id, 'power')
                command = {'a': 'dmg', 'assignments': [
                    {'source': source.ref, 'target': blocker.ref, 'amount': amount}
                    for blocker, amount in zip(blockers, (2, power - 2), strict=True)
                ]}
            else:
                self.fail('Unexpected receiving-damage fixture decision: ' + decision.kind)
            before = authoritative_state_hash(session.state)
            principal = session.pending_principals()[0]
            foreign = next('pilot:' + seat for seat in 'ABCD'
                           if 'pilot:' + seat not in session.pending_principals())
            self.assertFalse(session.act(foreign, command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            result = session.act(principal, command)
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail('Combat did not produce a receiving occurrence')
        return session

    def test_actual_receiver_prevention_and_separate_noncombat_transactions_do_not_merge(self):
        session = self.session(613654010)
        source = self.add(session.engine, 'Ripjaw Raptor')
        salve = self.add(session.engine, 'Healing Salve', zone='hand')
        damage = [self.add(session.engine, name, zone='hand', ref='received-hit-' + str(i))
                  for i, name in enumerate(('Shock', 'Lightning Bolt', 'Shock'))]
        self.add(session.engine, 'Divination', zone='hand', ref='private-received-hand')
        self.assert_closed(session, source)
        self.prepare(session, mana={'W': 1, 'R': 3})
        self.checkpoint(session)
        offered = self.action(session, 'A', salve)
        result = session.act('pilot:A', {
            'action_id': offered['id'], 'modes': ['mode_2'], 'targets': [source.ref], 'pay': 'auto',
        })
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        for spell, marked, draws in zip(damage, (0, 2, 4), (0, 1, 1), strict=True):
            hand = len(session.state.players['A'].zones['hand'])
            self.cast(session, 'A', spell, [source.ref])
            self.resolve_top(session)
            self.assertEqual(draws, len(self.receiving_items(session, source)))
            self.assertEqual(marked, session.state.cards[source.object_id].marked_damage)
            session = self.replay(session, load=True)
            self.settle(session)
            self.assertEqual(hand - 1 + draws, len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('private-received-hand', str(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def test_actual_receiving_groups_keep_apnap_and_lethal_recipient_identity(self):
        session = self.session(613654020)
        source = self.add(session.engine, 'Ripjaw Raptor')
        first = self.add(session.engine, 'Grizzly Bears', seat='B')
        other_receiver = self.add(session.engine, 'Fungusaur', seat='B')
        self.assert_closed(session, source)
        self.assert_closed(session, other_receiver)
        self.prepare(session)
        self.checkpoint(session)
        self.multiblock(session, source, [first, other_receiver])
        self.assertEqual(1, len(self.receiving_items(session, source)))
        self.assertEqual(1, len(self.receiving_items(session, other_receiver)))
        self.assertEqual([source.object_id, other_receiver.object_id], [
            item.source_object_id for item in session.state.stack if item.kind == 'triggered_ability'
        ])
        self.assertEqual('graveyard', session.state.cards[other_receiver.object_id].zone)
        hand = len(session.state.players['A'].zones['hand'])
        session = self.replay(session, load=True)
        self.settle(session)
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.assertEqual(0, session.state.cards[other_receiver.object_id].counters.get('+1/+1', 0))
        self.replay(session, load=True)

    def test_actual_received_total_uses_lethal_sources_own_damage_identity(self):
        session = self.session(613654030)
        source = self.add(session.engine, 'Boros Reckoner')
        blockers = [self.add(session.engine, 'Grizzly Bears', seat='B', ref='reckoner-blocker-' + str(i))
                    for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.multiblock(session, source, blockers)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(self.receiving_items(session, source)))
        self.target(session, 'A', ['C'])
        session = self.replay(session, load=True)
        self.settle(session)
        self.assertEqual((36, 40), (session.state.players['C'].life, session.state.players['D'].life))
        self.replay(session, load=True)

    def test_legacy_receiving_packet_subscription_retains_original_two_packet_replay(self):
        import json
        from quorune.semantics import SemanticProgram
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(613654040)
        source = self.add(session.engine, 'Ripjaw Raptor')
        blockers = [self.add(session.engine, 'Grizzly Bears', seat='B', ref='legacy-blocker-' + str(i))
                    for i in range(2)]
        current = session.engine.semantics.programs_for_oracle(source.oracle_id)[0]
        # The published v653 descriptor shape, independently captured before the fix.
        dependencies = ('trigger.effect.fixed_event', 'trigger.event.normalized_damage',
                        'trigger.placement.apnap', 'zone.draw.library_to_hand')
        legacy = replace(current, event='damage.dealt',
            event_condition={'field': 'target', 'op': 'eq', 'value': '$source.ref'},
            effects=({'op': 'draw', 'player': '$controller', 'count': 1, 'private': True},),
            coverage=['generated_oracle_ir', 'triggered_ability', 'current_ability_fragment_required',
                      'cr-603-handling-triggered-abilities', 'fixed-typed-event-effect-trigger',
                      'trigger-event-normalized-damage', 'cr-121-drawing-a-card'],
            capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict(),
            provenance={**current.provenance, 'authored_by': 'oracle-ir-v653',
                        'template_id': 'fixed-typed-effect-source-dealt-damage-trigger-v1'})
        legacy = SemanticProgram.from_dict(json.loads(json.dumps(legacy.to_dict())))
        self.assertTrue(bind_semantic_program_runtime(legacy, capability_registry=self.registry,
                        profile='commander_review')['strict'])
        session.engine.semantics.put(legacy)
        self.assertTrue(session.engine.semantic_program_is_current_trusted(legacy))
        self.prepare(session)
        self.checkpoint(session)
        self.multiblock(session, source, blockers)
        self.assertEqual(2, len(self.receiving_items(session, source)))
        hand = len(session.state.players['A'].zones['hand'])
        session = self.replay(session, load=True)
        self.settle(session)
        self.assertEqual(hand + 2, len(session.state.players['A'].zones['hand']))
        self.replay(session, load=True)
