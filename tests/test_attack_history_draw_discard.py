"""The committed attacks of the resolving controller govern the private discard."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class AttackHistoryDrawDiscardTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attack-history-draw-discard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attack-history-draw-discard-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed attack history", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_two_draws_before_one_controller_discard_on_zero_history(self):
        admitted, semantics = register_card(self.db, self.registry, "Chart a Course")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Chart a Course", fuzzy=False)
        spell, = semantics.programs_for_oracle(card.oracle_id)
        draw, conditional = spell.effects
        self.assertEqual(("draw", "$controller", 2), (draw["op"], draw["player"], draw["count"]))
        condition = conditional["condition"]
        self.assertEqual(("history_count_at_most", 0), (condition["kind"], condition["amount"]))
        query = condition["history_query"]
        self.assertEqual(("creature_attacked", "controller"), (query["event"], query["relation"]))
        discard, = conditional["effects"]
        self.assertEqual(("choose_cards_apnap", ["$controller"], 1, True),
                         (discard["op"], discard["players"], discard["count"], discard["hidden"]))
        self.assertIsNone(spell.target_schema)
        for text in (card.oracle_text.replace("you attacked", "an opponent attacked"),
                     card.oracle_text.replace("this turn", "last turn")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def declare_attack(self, game, seat, body, opponent):
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:" + seat, {"action_id": "attack", "attackers": {body.ref: opponent}})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(any(event.actor == seat for event in game.engine._current_turn_history("creature_attacked")))

    def add_known_test_cards(self, game):
        self.add(game.engine, "Island", ref="chart-own-island")
        self.add(game.engine, "Plains", ref="chart-own-plains")
        return tuple(self.add(game.engine, "Plains", zone="library", ref=f"chart-private-drawn-{index}")
                     for index in range(2))

    def test_actual_attack_still_satisfies_chart_after_the_declared_creature_leaves(self):
        game = self.session(50840001)
        spell = self.add(game.engine, "Chart a Course", zone="hand")
        attacker = self.add(game.engine, "Grizzly Bears")
        attacker.acquired_control_turn_count = -1
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="chart-other-island")
        cards = self.add_known_test_cards(game)
        self.assert_closed(game, spell)
        self.assert_closed(game, response)
        self.prepare(game)
        self.checkpoint(game)
        self.declare_attack(game, "A", attacker, "B")
        self.cast_for(game, "B", response, [attacker.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.cast_for(game, "A", spell)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["hand", "hand"], [game.state.cards[card.object_id].zone for card in cards])
        self.replay(game, load=True)

    def test_actual_opponent_attack_does_not_skip_the_resolving_controllers_private_discard(self):
        game = self.session(50840002)
        spell = self.add(game.engine, "Chart a Course", zone="hand")
        orrery = self.add(game.engine, "Vedalken Orrery")
        attacker = self.add(game.engine, "Grizzly Bears", seat="B")
        attacker.acquired_control_turn_count = -1
        cards = self.add_known_test_cards(game)
        for card in (spell, orrery):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.declare_attack(game, "B", attacker, "C")
        self.cast_for(game, "A", spell)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(["hand", "hand"], [game.state.cards[card.object_id].zone for card in cards])
        owner = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(1, owner["count"])
        self.assertTrue({card.ref for card in cards} <= set(owner["options"]))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertTrue(all(card.ref not in json.dumps(game.packet(viewer, full=True)) for card in cards))
        before = authoritative_state_hash(game.state)
        choose = {"action_id": "choose", "cards": [cards[0].ref]}
        self.assertFalse(game.act("pilot:B", choose).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "cards": []}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", choose)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["graveyard", "hand"], [game.state.cards[card.object_id].zone for card in cards])
        self.assertNotIn(cards[1].ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)
