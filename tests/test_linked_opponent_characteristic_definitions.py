"""Linked choices define current source statistics through the existing 7a owner."""

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
from quorune.characteristic_fragments import (
    CharacteristicQuantitySpec, CharacteristicQuantityScope, LINKED_OPPONENT_TAPPED_LANDS_QUERY,
    QueryPowerToughnessDefinitionSpec,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PALLIMUD = "Pallimud"
ORDER = "Lost Order of Jarkeld"
SPECTER = "Entropic Specter"


class LinkedOpponentCharacteristicDefinitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-parameter-definition.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-opponent-characteristic-definitions-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original opponent definitions", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_preserve_all_zone_definitions_and_independent_specter_discard(self):
        for name in (PALLIMUD, ORDER, SPECTER):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                programs = semantics.programs_for_oracle(card.oracle_id)
                definition, = [item for item in programs if item.active_zone == "all" and item.event == "continuous"]
                self.assertIsNone(definition.target_schema)
                typed, = definition.handlers
                self.assertEqual("query_power_toughness_definition", typed["fragment"]["kind"])
                if name == SPECTER:
                    discard, = [item for item in programs if item.event == "damage.dealt.self"]
                    effect, = discard.effects
                    self.assertEqual((["$context.target"], "discard", True),
                                     (effect["players"], effect["then"], effect["hidden"]))
                altered = replace(card, oracle_text=card.oracle_text.replace("choose an opponent", "choose a player"))
                self.assertNotEqual("exact", compile_oracle_card(altered, capability_registry=self.registry).status)

    def test_raw_defined_count_rejects_battlefield_only_scope_and_unowned_fields(self):
        card = self.db.lookup(PALLIMUD, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, PALLIMUD)
        definition, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "continuous"]
        handler, = definition.handlers
        value = handler["fragment"]["value"]
        self.assertEqual(
            CharacteristicQuantitySpec(CharacteristicQuantityScope.LINKED_OPPONENT_DEFINITION,
                                       query=LINKED_OPPONENT_TAPPED_LANDS_QUERY, schema_version=10),
            CharacteristicQuantitySpec.from_dict(value["quantity"]),
        )
        denied = bind_semantic_program_runtime(
            replace(definition, active_zone="battlefield"), capability_registry=self.registry,
            profile="commander_review",
        )
        self.assertFalse(denied["strict"], denied)
        with self.assertRaises(ValueError):
            QueryPowerToughnessDefinitionSpec.from_dict({**value, "define_toughness": True})
        with self.assertRaises(ValueError):
            CharacteristicQuantitySpec.from_dict({**value["quantity"], "offset": 1})

    def stats(self, game, source):
        return tuple(game.engine._numeric_stat(source.object_id, aspect) for aspect in ("power", "toughness"))

    def enter(self, game, source, seat):
        self.cast_for(game, "A", source)
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        self.assertNotEqual("battlefield", game.state.cards[source.object_id].zone)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choices = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in rows}
        self.assertEqual({"B", "C", "D"}, set(choices))
        command = {"action_id": "choose", "choices": {"replacement": choices[seat]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)

    def test_actual_pallimud_counts_only_the_chosen_players_current_tapped_lands(self):
        game = self.session(61340731)
        engine = game.engine
        source = self.add(engine, PALLIMUD, zone="hand")
        creature = self.add(engine, "Grizzly Bears")
        first = self.add(engine, "Island", seat="B", ref="pallimud-selected-land")
        other = self.add(engine, "Island", seat="C", ref="pallimud-unselected-land")
        other.tapped = True
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.assertEqual((0, 3), self.stats(game, source))
        self.enter(game, source, "B")
        self.assertEqual((0, 3), self.stats(game, source))
        self.cast_for(game, "B", bounce, [creature.ref])
        self.assertTrue(game.state.cards[first.object_id].tapped)
        self.assertEqual((1, 3), self.stats(game, source), "The original opponent's tapped land defines source power")
        self.resolve_top(game)
        self.assertEqual((1, 3), self.stats(game, source))
        self.replay(game, load=True)

    def test_actual_pallimud_witness_kills_defining_power_with_the_current_controllers_lands(self):
        with patch("quorune.rules.linked_entry_opponent.current_linked_entry_opponent",
                   side_effect=lambda host, source: source.controller):
            with self.assertRaisesRegex(AssertionError, "original opponent's tapped land defines"):
                self.test_actual_pallimud_counts_only_the_chosen_players_current_tapped_lands()

    def test_actual_lost_order_counts_one_plus_the_chosen_creatures_and_leaves_a_live_one_one(self):
        game = self.session(61340732)
        engine = game.engine
        source = self.add(engine, ORDER, zone="hand")
        body = self.add(engine, "Grizzly Bears", seat="C", ref="order-removed-creature")
        self.add(engine, "Grizzly Bears", seat="C", ref="order-other-creature")
        self.add(engine, "Ornithopter", seat="C")
        self.add(engine, "Sol Ring", seat="C")
        self.add(engine, "Grizzly Bears", seat="B", ref="order-wrong-seat")
        self.add(engine, "Island", seat="B")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 2})
        self.checkpoint(game)
        self.assertEqual((1, 1), self.stats(game, source))
        self.enter(game, source, "C")
        self.assertEqual((4, 4), self.stats(game, source))
        self.cast_for(game, "B", bounce, [body.ref])
        self.resolve_top(game)
        self.assertEqual((3, 3), self.stats(game, source))
        self.priority(game, "C")
        result = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("battlefield", (1, 1)), (game.state.cards[source.object_id].zone, self.stats(game, source)))
        self.replay(game, load=True)

    def test_actual_entropic_specter_uses_chosen_hand_and_keeps_flying_and_private_damage_discard(self):
        game = self.session(61340733)
        engine = game.engine
        source = self.add(engine, SPECTER, zone="hand")
        haste = self.add(engine, "Fervor")
        b_player = game.state.players["B"]
        for identity in tuple(b_player.zones["hand"])[3:]:
            engine.move_card(identity, "library", log=False)
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="specter-unrelated-secret")
        for card in (source, haste):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "B": 2})
        self.checkpoint(game)
        self.assertEqual((0, 0), self.stats(game, source))
        self.enter(game, source, "B")
        self.assertEqual((3, 3), self.stats(game, source))
        self.assertIn("flying", engine._combat_keywords(game.state.cards[source.object_id]))

        self.attack(game, [(source, "C")])
        self.until(game, lambda current: current.state.pending_decision.kind == "choice.apnap")
        self.assertEqual(["C"], game.state.pending_decision.actors)
        self.assertEqual(37, game.state.players["C"].life)
        self.assertEqual(3, len(game.state.players["B"].zones["hand"]))
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertIsNone(game.packet(viewer, full=True)["decision"])
        choice = game.packet("pilot:C", full=True)["decision"]["ctx"]
        self.assertEqual(1, choice["count"])
        ref = choice["options"][0]
        old_count = len(game.state.players["C"].zones["hand"])
        command = {"action_id": "choose", "cards": [ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:C", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(old_count - 1, len(game.state.players["C"].zones["hand"]))
        self.assertEqual((3, 3), self.stats(game, source))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)
