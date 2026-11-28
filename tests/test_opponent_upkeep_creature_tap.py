"""Exercise a scheduled opponent's creature and both Delirium timing checks."""

from dataclasses import replace
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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_player_targets import is_graveyard_type_opponent_upkeep_binding
from scripts.build_test_database import build_fixture_database


CARD = 'Topplegeist'
PROFILE = 'commander_review'


class OpponentUpkeepCreatureTapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'opponent-upkeep-tap.sqlite3'
        fixtures = ROOT / 'tests/fixtures'
        build_fixture_database([fixtures / name for name in (
            'bound-effect-program-cards.json', 'topplegeist-cards.json', 'all-player-turn-rule-cards.json',
            'state-qualified-zone-cards.json', 'enchanted-player-static-cards.json',
            'scheduled-player-land-untap-cards.json',
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = 'Generic Bound Commander'
        cls.deck = DeckDefinition('Opponent upkeep target', [
            DeckEntry(commander, 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], [commander])

    def begin(self, game, active='A', mana=None):
        game.state.config.semantic_policy = 'trusted_only'
        self.prepare(game, active=active, mana=mana)
        game.state.last_normal_turn_player = active
        self.checkpoint(game)

    def upkeep(self, game, seat):
        def at_upkeep(current):
            return current.state.active_player == seat and current.state.step == 'upkeep'

        for _ in range(len(game.state.players) + 1):
            self.until(game, lambda current: at_upkeep(current) or (
                current.state.pending_decision is not None and current.state.pending_decision.kind == 'cleanup.discard'
            ))
            if at_upkeep(game):
                return
            principal, = game.pending_principals()
            ctx = game.packet(principal, full=True)['decision']['ctx']
            cards = [row['id'] for row in ctx['hand'] if row['id'] != 'late-response'][:ctx['count']]
            outcome = game.act(principal, {'action_id': 'discard', 'cards': cards})
            self.assertTrue(outcome.ok, outcome.summary)
        self.fail(f"The expected upkeep for {seat} did not begin")

    def graveyard_cards(self, game, *, instant=True):
        self.add(game.engine, 'Dryad Arbor', zone='graveyard')
        if instant:
            self.add(game.engine, 'Unsummon', zone='graveyard', ref='graveyard-instant')
        return self.add(game.engine, 'Sol Ring', zone='graveyard')

    def test_complete_source_requires_the_exact_schedule_condition_and_creature_scope(self):
        card = self.db.lookup(CARD, fuzzy=False)
        settings = {'capability_registry': self.registry, 'capability_profile': PROFILE}
        compiled = compile_oracle_card(card, **settings)
        self.assertEqual('exact', compiled.status, compiled.material_residuals)
        node, = (item for item in compiled.faces[0].nodes if item.event == 'step.begin')
        original_line = card.oracle_text.splitlines()[2]
        self.assertEqual(original_line, card.oracle_text[node.span.start:node.span.end])
        self.assertTrue(is_graveyard_type_opponent_upkeep_binding(node.event, node.event_condition))
        self.assertEqual('$context.player', node.target_schema['controller_seat'])
        self.assertEqual(({'op': 'tap', 'card': '$target.0'},), tuple(node.effects))
        for new_line in (
            "At the beginning of each opponent's upkeep, tap target creature that player controls.",
            original_line.replace('target creature', 'target land'),
            original_line.replace("opponent's upkeep", "opponent's draw step"),
        ):
            changed = replace(card, oracle_text='\n'.join((*card.oracle_text.splitlines()[:2], new_line)))
            self.assertNotEqual('exact', compile_oracle_card(changed, **settings).status)
        game = self.session(60340501)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = [p for p in game.engine.semantics.programs_for_oracle(card.oracle_id) if p.event == 'step.begin']
        for changed in (replace(program, event_condition={'field': 'step', 'op': 'eq', 'value': 'upkeep'}),
                        replace(program, target_schema={**program.target_schema, 'count': True}),
                        replace(program, effects=[{'op': 'untap', 'card': '$target.0'}])):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                           profile=PROFILE)['strict'])
        with patch('quorune.compiler.event_player_target_results.is_graveyard_type_opponent_upkeep_binding',
                   return_value=False):
            self.assertNotEqual('exact', compile_oracle_card(card, **settings).status)

    def test_three_cards_supply_four_types_and_losing_one_stops_resolution(self):
        for remove_type in (False, True):
            with self.subTest(remove_type=remove_type):
                game = self.session(60340502 + remove_type)
                source = self.add(game.engine, CARD)
                target = self.add(game.engine, 'Grizzly Bears', seat='B', ref='upkeep-player-creature')
                other = self.add(game.engine, 'Grizzly Bears', seat='C', ref='other-player-creature')
                ring = self.graveyard_cards(game)
                if remove_type:
                    self.add(game.engine, 'Swamp', seat='D')
                    cremate = self.add(game.engine, 'Cremate', zone='hand', seat='D')
                self.assert_closed(game, source)
                self.begin(game)
                self.upkeep(game, 'B')
                pending = game.state.pending_decision
                self.assertEqual(('semantic.target', ['A']), (pending.kind, pending.actors))
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act('pilot:A', {'action_id': 'choose', 'targets': [other.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.target(game, 'A', [target.ref])
                trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
                self.assertEqual(('A', 'B', [target.ref]), (trigger.controller, trigger.context['player'], trigger.targets))
                if remove_type:
                    self.cast_for(game, 'D', cremate, [ring.ref])
                    self.resolve_top(game)
                    self.assertEqual('exile', game.state.cards[ring.object_id].zone)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertIs(game.state.cards[target.object_id].tapped, not remove_type)
                self.assertFalse(game.state.cards[other.object_id].tapped)
                self.replay(game)

    def test_a_missing_type_at_the_beginning_cannot_retroactively_trigger(self):
        game = self.session(60340504)
        source = self.add(game.engine, CARD)
        self.add(game.engine, 'Grizzly Bears', seat='B', ref='upkeep-creature')
        target = self.add(game.engine, 'Grizzly Bears', seat='C', ref='late-bounce-target')
        self.add(game.engine, 'Island')
        response = self.add(game.engine, 'Unsummon', zone='hand', ref='late-response')
        self.graveyard_cards(game, instant=False)
        self.assert_closed(game, source)
        self.begin(game)
        self.upkeep(game, 'B')
        self.assertEqual('priority', game.state.pending_decision.kind)
        self.assertEqual([], game.state.stack)
        self.cast_for(game, 'A', response, [target.ref])
        self.resolve_top(game)
        self.assertEqual('graveyard', game.state.cards[response.object_id].zone)
        self.assertEqual([], game.state.stack)
        self.replay(game)

    def test_own_upkeep_never_triggers_and_entry_tap_is_independent(self):
        game = self.session(60340505)
        source = self.add(game.engine, CARD)
        self.add(game.engine, 'Grizzly Bears', seat='A')
        self.graveyard_cards(game)
        self.assert_closed(game, source)
        self.begin(game, active='D')
        self.upkeep(game, 'A')
        self.assertEqual('priority', game.state.pending_decision.kind)
        self.assertEqual([], game.state.stack)
        self.replay(game)

        game = self.session(60340506)
        source = self.add(game.engine, CARD, zone='hand')
        target = self.add(game.engine, 'Grizzly Bears', seat='C')
        self.assert_closed(game, source)
        self.begin(game, mana={'W': 1})
        self.cast_for(game, 'A', source)
        self.resolve_top(game)
        self.target(game, 'A', [target.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[target.object_id].tapped)
        self.replay(game)
