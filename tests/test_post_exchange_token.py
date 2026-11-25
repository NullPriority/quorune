from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules import post_exchange_token as token
from quorune.rules.player_life_exchange import PlayerLifeExchangeSpec, player_life_exchange_runtime_dependencies
from scripts.build_test_database import build_fixture_database


class PostExchangeTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "post-exchange-token.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/post-exchange-token-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Life gap token", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def board(self, seed, *, life=(10, 23)):
        game = self.session(seed)
        spell = self.add(game.engine, "Profane Transfusion", zone="hand")
        self.assert_closed(game, spell)
        for seat, total in zip("BC", life, strict=True):
            game.state.players[seat].life = total
        return game, spell

    def cast_exchange(self, game, spell):
        self.prepare(game, mana={"B": 9})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B", "C"])

    def lives(self, game):
        return tuple(game.state.players[seat].life for seat in "BC")

    def tokens(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.controller == "A"]

    def replacement(self, game, seat):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("replacement.order", [seat]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        option = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"][0]["id"]
        before = authoritative_state_hash(game.state)
        response = {"action_id": "choose", "replacement": option}
        self.assertFalse(game.act("pilot:D", response).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:" + seat, response)
        self.assertTrue(accepted.ok, accepted.summary)
        return game

    def test_actual_replacements_are_sequential_and_replay_never_repeats_exchange(self):
        game, spell = self.board(701120901)
        for index in range(2):
            life = self.add(game.engine, "Boon Reflection", seat="B", ref=f"life-exchange-double-{index}")
            count = self.add(game.engine, "Anointed Procession", ref=f"exchange-token-double-{index}")
            self.assert_closed(game, life)
            self.assert_closed(game, count)
        self.cast_exchange(game, spell)
        self.assertEqual((10, 23), self.lives(game))
        game = self.replacement(game, "B")
        self.assertEqual((62, 10), self.lives(game))
        self.assertFalse(self.tokens(game))
        game = self.replacement(game, "A")
        self.assertIsNone(self.resolve(game))
        self.assertEqual((62, 10), self.lives(game))
        created = self.tokens(game)
        self.assertEqual(4, len(created))
        for card in created:
            data = game.engine._effective_card_data(card)
            self.assertEqual(("52", "52"), (data["power"], data["toughness"]), "size uses post-exchange totals")
            self.assertEqual("Horror Token", data["name"])
            self.assertEqual([], data["colors"])
        self.replay(game, load=True)

    def test_actual_prohibited_exchange_still_creates_a_token_from_both_live_totals(self):
        game, spell = self.board(701120902)
        prohibition = self.add(game.engine, "Giant Cindermaw", seat="D")
        self.assert_closed(game, prohibition)
        self.cast_exchange(game, spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((10, 23), self.lives(game))
        horror, = self.tokens(game)
        data = game.engine._effective_card_data(horror)
        self.assertEqual(("13", "13"), (data["power"], data["toughness"]))
        self.replay(game, load=True)

    def test_actual_departed_target_provides_neither_exchange_nor_token_size(self):
        game, spell = self.board(701120903)
        self.cast_exchange(game, spell)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(23, game.state.players["C"].life)
        self.assertFalse(self.tokens(game))
        self.assertFalse(any(event.code=="token.create" for event in game.state.events))
        self.replay(game, load=True)

    def test_actual_equal_totals_create_zero_size_before_state_based_actions(self):
        game, spell = self.board(701120904, life=(23, 23))
        self.cast_exchange(game, spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((23, 23), self.lives(game))
        self.assertFalse(self.tokens(game))
        entry, = [event for event in game.state.events if event.code == "token.create"
                  and event.details.get("base_name") == "Horror Token"]
        self.assertEqual(1, entry.details["base_quantity"])
        self.replay(game, load=True)

    def test_scope_rejects_removed_exchange_or_stale_token_definition(self):
        spec = PlayerLifeExchangeSpec("two_targets")
        token_body = token.post_exchange_token_instruction()
        valid = dict(event="resolve", active_zone="stack",
                     effects=(spec.to_instruction(), token_body),
                     target_schema=spec.to_target_schema(), handlers=(), event_condition=None)
        self.assertIn(token.POST_EXCHANGE_TOKEN_CAPABILITY,
                      player_life_exchange_runtime_dependencies(SimpleNamespace(**valid)))
        for changes in (
            {"effects": (token_body,)},
            {"effects": (spec.to_instruction(), {**token_body, "quantity": 2})},
            {"target_schema": {**spec.to_target_schema(), "count": 1}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                player_life_exchange_runtime_dependencies(SimpleNamespace(**{**valid, **changes}))

    def test_pre_exchange_measurement_mutation_is_detected_by_actual_token_size(self):
        real = token.materialize_post_exchange_token

        def use_old_total(controller, players, current_life):
            return real(controller, players, {"B": 10, "C": 23})

        with patch.object(token, "materialize_post_exchange_token", side_effect=use_old_total):
            with self.assertRaisesRegex(AssertionError, "size uses post-exchange totals"):
                self.test_actual_replacements_are_sequential_and_replay_never_repeats_exchange()
