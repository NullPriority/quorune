"""Private scheduled draws from the original source's named counters."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.scheduled_player_shapes import UPKEEP_SOURCE_COUNTER_DRAW_CAPABILITY, upkeep_source_counter_draw_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = 'commander_review'
CARD = 'Walking Archive'


class UpkeepSourceCounterDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'upkeep-counter-draw.sqlite3'
        fixtures = ROOT / 'tests/fixtures'
        build_fixture_database([fixtures / 'upkeep-source-counter-draw-cards.json',
                                fixtures / 'upkeep-source-counter-sacrifice-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = 'Isamaru, Hound of Konda'
        cls.deck = DeckDefinition('Upkeep source counter draw', [DeckEntry(commander, 1, 'commander'),
                                                                DeckEntry('Plains', 30)], [commander])

    def pending_upkeep(self, seed, *, destroy=False):
        game = self.session(seed)
        source = self.add(game.engine, CARD, zone='hand')
        for index, name in enumerate(('Plains', 'Plains', 'Island', 'Island')):
            self.add(game.engine, name, ref=f'archive-mana-{index}')
        spell = self.add(game.engine, 'Disenchant', seat='B', zone='hand') if destroy else None
        if spell is not None:
            for index in range(2):
                self.add(game.engine, 'Plains', seat='B', ref=f'archive-disenchant-mana-{index}')
        self.assert_closed(game, source)
        game.state.config.semantic_policy = 'trusted_only'
        self.prepare(game, mana={'C': 3})
        self.checkpoint(game)
        self.cast(game, 'A', source)
        self.resolve_top(game)
        original = game.state.cards[source.object_id]
        self.assertEqual(1, original.counters['+1/+1'])
        incarnation = original.logical_object_id

        self.until(game, lambda session: session.state.active_player == 'B' and session.state.step == 'upkeep'
                   and any(item.source_object_id == source.object_id for item in session.state.stack))
        self.assertEqual(1, len(game.state.stack))
        self.priority(game, 'A')
        view = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activate, = (row for row in view if row['id'].startswith(f'activate:{source.ref}:'))
        result = game.act('pilot:A', {'action_id': activate['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[source.object_id].counters['+1/+1'])
        if spell is not None:
            self.cast(game, 'B', spell, [source.ref])
            self.resolve_top(game)
            departed = game.state.cards[source.object_id]
            self.assertEqual('graveyard', departed.zone)
            self.assertNotEqual(incarnation, departed.logical_object_id)
        return game

    def check_draw(self, game):
        hands = {seat: set(player.zones['hand']) for seat, player in game.state.players.items()}
        self.resolve_top(game)
        self.assertEqual('upkeep', game.state.step)
        drawn = set(game.state.players['B'].zones['hand']) - hands['B']
        self.assertEqual(2, len(drawn), 'The upkeep participant draws the two counters at resolution')
        for seat in 'ACD':
            self.assertEqual(hands[seat], set(game.state.players[seat].zones['hand']))
        private_refs = {game.state.cards[card].ref for card in drawn}
        for principal in ('pilot:A', 'pilot:C', 'pilot:D', 'spectator'):
            view = json.dumps(game.packet(principal, full=True))
            self.assertTrue(all(card_ref not in view for card_ref in private_refs))
        self.replay(game, load=True)

    def test_native_live_count_draws_for_the_upkeep_seat_and_replays(self):
        self.check_draw(self.replay(self.pending_upkeep(50310071), load=True))

    def test_native_departed_incarnation_draws_from_last_known_count_and_replays(self):
        self.check_draw(self.replay(self.pending_upkeep(50310072, destroy=True), load=True))

    def test_native_mutant_using_trigger_time_count_is_killed(self):
        game = self.pending_upkeep(50310073)
        with patch('quorune.scalar_effect_amounts.resolve_scalar_effect_amount', return_value=1):
            with self.assertRaisesRegex(AssertionError, 'The upkeep participant draws the two counters'):
                self.check_draw(game)

    def test_exact_upkeep_draw_requires_original_recipient_and_named_counter_authority(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        trigger, = (node for face in ir.faces for node in face.nodes if node.event == 'step.begin')
        self.assertEqual((upkeep_source_counter_draw_effect(),), tuple(trigger.effects))
        self.assertIsNone(trigger.target_schema)
        admission, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admission['strict_capability_ready'], admission['blockers'])
        cap = UPKEEP_SOURCE_COUNTER_DRAW_CAPABILITY
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies)
        retained = tuple(dependency for dependency in program.capability_dependencies if dependency != cap)
        missing = replace(program, capability_dependencies=retained,
                          capability_closure=self.registry.closure(retained, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)['strict'])
        effect, = program.effects
        for altered in ({**effect, 'player': '$controller'},
                        {**effect, 'count': {**effect['count'], 'counter_name': 'soot'}}):
            changed = replace(program, effects=(altered,))
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)['strict'])
