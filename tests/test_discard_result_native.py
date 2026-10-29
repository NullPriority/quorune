"""Actual cast, private replacement and composed Cipher discard-result witnesses."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cipher_fixture as fixtures
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DiscardResultNativeTests(fixtures.CipherFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'discard-results.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/discard-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Discard results', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def hands(self, s, sizes):
        for player in 'ABCD':
            for oid in tuple(s.state.players[player].zones['hand']):s.engine.move_card(oid, 'library', log=False)
            for index in range(sizes.get(player, 0)):
                self.add(s.engine, 'Divination', seat=player, zone='hand', ref=f'private-{player}-{index}')

    def test_actual_windfall_uses_greatest_committed_discard_and_apnap_draw_order(self):
        s = self.session(701090501)
        self.hands(s, {'A': 1, 'B': 3, 'C': 0, 'D': 2})
        source = self.add(s.engine, 'Windfall', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'U': 1})
        originals = {p: tuple(oid for oid in s.state.players[p].zones['hand'] if oid != source.object_id) for p in 'ABCD'}
        self.checkpoint(s)
        # A real old discard cannot inflate this instruction's greatest quantity.
        old = self.add(s.engine, 'Divination', seat='C', zone='hand', ref='private-old-discard')
        from quorune.semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
        from quorune.zone_trigger_events import ZoneTransitionKind
        s.engine.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(actor='A', object_refs=(old.ref,), expected_zones=('hand',), destination='graveyard', reason='Earlier discard', transition_kind=ZoneTransitionKind.DISCARD))
        self.checkpoint(s)
        self.cast(s, 'A', source)
        seen = []
        from quorune.drawing import transaction
        draw = transaction._record_draw
        def observe(*args, **kwargs):
            self.assertTrue(all(s.state.cards[oid].zone == 'graveyard' for ids in originals.values() for oid in ids))
            seen.append(args[1].resolution.player)
            return draw(*args, **kwargs)
        with patch.object(transaction, '_record_draw', side_effect=observe):self.settle(s)
        self.assertEqual([3] * 4, [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertEqual(list('AAABBBCCCDDD'), seen)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_tolarian_winds_counts_current_hand_excluding_spell_and_empty_hand(self):
        for size in (0, 2):
            with self.subTest(size=size):
                s = self.session(701090510 + size)
                self.hands(s, {'A': size})
                source = self.add(s.engine, 'Tolarian Winds', zone='hand')
                self.assert_closed(s, source)
                self.prepare(s, mana={'C': 1, 'U': 1})
                self.checkpoint(s)
                self.cast(s, 'A', source)
                self.settle(s)
                self.assertEqual(size, len(s.state.players['A'].zones['hand']))
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_windfall_redirected_discard_pending_privacy_rollback_and_replay(self):
        s = self.session(701090520)
        self.hands(s, {'B': 2})
        self.add(s.engine, 'Dauthi Voidwalker', ref='first-discard-redirect')
        self.add(s.engine, 'Dauthi Voidwalker', ref='second-discard-redirect')
        source = self.add(s.engine, 'Windfall', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'U': 1})
        self.checkpoint(s)
        self.cast(s, 'A', source)
        self.assertEqual('replacement.order', self.resolve(s).kind)
        self.assertEqual([0, 2, 0, 0], [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertNotIn('private-B-0', str(s.packet('pilot:C', full=True)))
        s = self.replay(s, load=True)
        while s.state.pending_decision.kind == 'replacement.order':
            principal = s.pending_principals()[0]
            packet = s.packet(principal, full=True)['decision']['ctx']
            command = {'action_id': 'choose', 'replacement': packet['options'][0]['id']}
            before = authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:D', command).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
            accepted = s.act(principal, command)
            self.assertTrue(accepted.ok, accepted.summary)
        self.settle(s)
        self.assertEqual([2] * 4, [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertTrue(all(next(c for c in s.state.cards.values() if c.ref == f'private-B-{index}').zone == 'exile' for index in range(2)))
        self.private(s)
        self.replay(s, load=True)

    def test_actual_whispering_madness_draws_before_encoding_and_repeats_combat_copy(self):
        s = self.session(701090530)
        self.hands(s, {'A': 1, 'B': 2})
        source = self.add(s.engine, 'Whispering Madness', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears')
        creature.acquired_control_turn_count = -1
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'U': 1, 'B': 1})
        self.checkpoint(s)
        self.cast(s, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual([2] * 4, [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.choose_as(s, 'A', cards=[creature.ref])
        self.settle(s)
        self.attack(s, [(creature, 'B')])
        self.until(s, lambda s: any(str(item.semantic_key).startswith('builtin:cipher:') for item in s.state.stack))
        self.assertEqual('selection.exile_cast', self.resolve(s).kind)
        s = self.replay(s, load=True)
        self.choose_as(s, 'A', choice='cast')
        self.settle(s)
        self.assertEqual([2] * 4, [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_retrace_and_flashback_keep_casting_discards_out_of_resolution_count(self):
        for index, (name, option, mana) in enumerate((('Decaying Time Loop', 'retrace', {'C': 3, 'R': 1}),
                                                    ('Shattered Perception', 'flashback', {'C': 5, 'R': 1}))):
            with self.subTest(card=name):
                s = self.session(701090540 + index)
                self.hands(s, {'A': 2})
                source = self.add(s.engine, name, zone='graveyard')
                land = self.add(s.engine, 'Island', zone='hand', ref='private-retrace-price') if option == 'retrace' else None
                self.assert_closed(s, source)
                self.prepare(s, mana=mana)
                self.checkpoint(s)
                offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == source.ref and a['id'].startswith('cast:'))
                command = {'action_id': offered['id'], 'cost_option': option, 'pay': 'auto'}
                if land is not None:command['discard_cards'] = [land.ref]
                before = authoritative_state_hash(s.state)
                self.assertFalse(s.act('pilot:D', command).ok)
                self.assertEqual(before, authoritative_state_hash(s.state))
                accepted = s.act('pilot:A', command)
                self.assertTrue(accepted.ok, accepted.summary)
                if land is not None:self.assertEqual('graveyard', s.state.cards[land.object_id].zone)
                s = self.replay(s, load=True)
                self.settle(s)
                self.assertEqual(2, len(s.state.players['A'].zones['hand']))
                self.assertEqual('graveyard' if option == 'retrace' else 'exile', s.state.cards[source.object_id].zone)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_soratami_return_cost_and_archivist_tap_cost_precede_discard_counts(self):
        for index, name in enumerate(('Soratami Seer', "Jace's Archivist")):
            with self.subTest(card=name):
                s = self.session(701090550 + index)
                self.hands(s, {'A': 1, 'B': 2})
                source = self.add(s.engine, name)
                source.acquired_control_turn_count = -1
                lands = [self.add(s.engine, 'Island', ref='returned-land-' + str(i)) for i in range(2)] if index == 0 else []
                self.assert_closed(s, source)
                self.prepare(s, mana={'C': 4} if index == 0 else {'U': 1})
                self.checkpoint(s)
                offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':'))
                command = {'action_id': offered['id'], 'pay': 'auto'}
                if lands:
                    command['cost_cards'] = [c.ref for c in lands]
                    bad = {**command, 'cost_cards': [lands[0].ref, lands[0].ref]}
                    before = authoritative_state_hash(s.state)
                    self.assertFalse(s.act('pilot:A', bad).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                accepted = s.act('pilot:A', command)
                self.assertTrue(accepted.ok, accepted.summary)
                if lands:self.assertTrue(all(s.state.cards[c.object_id].zone == 'hand' for c in lands))
                else:self.assertTrue(s.state.cards[source.object_id].tapped)
                self.settle(s)
                self.assertEqual([3, 2, 0, 0] if lands else [2] * 4, [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
                if lands:self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in lands))
                self.private(s)
                self.replay(s, load=True)

    def test_actual_incendiary_command_orders_damage_then_each_players_own_discard_draw(self):
        s = self.session(701090560)
        self.hands(s, {'A': 1, 'B': 3, 'C': 0, 'D': 2})
        source = self.add(s.engine, 'Incendiary Command', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 3, 'R': 2})
        self.checkpoint(s)
        offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == source.ref and a['id'].startswith('cast:'))
        accepted = s.act('pilot:A', {'action_id': offered['id'], 'modes': ['mode_4', 'mode_1'], 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.settle(s)
        self.assertEqual(36, s.state.players['B'].life)
        self.assertEqual([1, 3, 0, 2], [len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.private(s)
        self.replay(s, load=True)
