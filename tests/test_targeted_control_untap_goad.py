"""One announced creature receives temporary control and a separate goad duration."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


BESMIRCH = "Besmirch"


class TargetedControlUntapGoadTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-control-untap-goad.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/targeted-control-untap-goad-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted control and goad", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def haste(self, game, card):
        data = game.engine._effective_card_data(self.current(game, card))
        return "haste" in {keyword.casefold() for keyword in data["keywords"]}

    def attackers(self, game, seat):
        self.until(game, lambda item: item.state.active_player == seat and item.state.pending_decision is not None
                   and item.state.pending_decision.kind == "combat.attackers")

    def submit_attack(self, game, seat, attackers, *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "attack", "attackers": attackers})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_acquired_haste_attacks_and_goad_survives_cleanup_and_return(self):
        game = self.playing(61127291)
        spell = self.add(game.engine, BESMIRCH, zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, spell)
        creature.tapped = True
        origin = (creature.logical_object_id, creature.zone_timestamp)
        self.prepare(game, actor="A", active="A", mana={"C": 1, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [creature.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = self.current(game, creature)
        self.assertEqual(("A", "B", False), (current.controller, current.owner, current.tapped))
        self.assertTrue(self.haste(game, creature))
        self.assertEqual(origin, (current.logical_object_id, current.zone_timestamp))

        self.attackers(game, "A")
        self.submit_attack(game, "A", {}, accepted=False)
        self.submit_attack(game, "A", {creature.ref: "C"})
        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual("B", self.current(game, creature).controller)
        self.assertFalse(self.haste(game, creature))
        self.assertFalse(self.current(game, creature).tapped)

        self.attackers(game, "B")
        self.submit_attack(game, "B", {}, accepted=False)
        self.submit_attack(game, "B", {creature.ref: "A"}, accepted=False)
        self.submit_attack(game, "B", {creature.ref: "C"})
        self.replay(game, load=True)

    def test_actual_departure_of_the_only_target_prevents_every_result(self):
        game = self.playing(61127292)
        spell = self.add(game.engine, BESMIRCH, zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        reply = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (spell, creature, reply):
            self.assert_closed(game, card)
        creature.tapped = True
        self.prepare(game, actor="A", active="A", mana={"C": 1, "R": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [creature.ref])
        self.cast_for(game, "B", reply, [creature.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, creature).zone)
        self.assertEqual("B", self.current(game, creature).controller)
        self.assertFalse(self.haste(game, creature))
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_full_original_requires_same_target_and_all_printed_results(self):
        original = self.db.lookup(BESMIRCH, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Besmirch")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        spell, = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(("gain_control", "grant_keyword_until_end_of_turn", "untap", "goad"),
                         tuple(effect["op"] for effect in spell.effects))
        self.assertEqual({"$target.0"}, {effect["card"] for effect in spell.effects})
        self.assertNotIn("controller_relation", spell.target_schema)
        for old, new in (("target creature", "target permanent"), ("Untap and goad", "Untap and tap"),
                         ("Until end of turn", "Until your next turn")):
            with self.subTest(new=new):
                modified = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    modified, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
