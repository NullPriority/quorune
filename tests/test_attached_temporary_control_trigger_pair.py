"""Both source triggers use the original enchanted permanent without retargeting."""

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
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FUGUE = "Frenzied Fugue"


class AttachedTemporaryControlTriggerPairTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-temporary-control-trigger-pair.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attached-temporary-control-trigger-pair-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attached control and both triggers", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def haste(self, game, card):
        data = game.engine._effective_card_data(self.current(game, card))
        return "haste" in {word.casefold() for word in data["keywords"]}

    def trigger(self, game, source):
        item, = game.state.stack
        self.assertEqual(("triggered_ability", source.object_id, "A"),
                         (item.kind, item.source_object_id, item.controller))
        self.assertEqual([], list(item.targets))
        return item

    def tap_ring(self, game, ring):
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (option for option in actions if option["id"].startswith(f"activate:{ring.ref}:"))
        paid = game.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(self.current(game, ring).tapped)

    def scene(self, seed, *, reply=None):
        game = self.playing(seed)
        aura = self.add(game.engine, FUGUE, zone="hand")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        answer = self.add(game.engine, reply, seat="B", zone="hand") if reply else None
        for card in (aura, ring, answer):
            if card is not None:
                self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 3, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", aura, [ring.ref])
        self.resolve_top(game)
        self.assertEqual(ring.object_id, self.current(game, aura).attached_to)
        self.assertEqual("B", self.current(game, ring).controller)
        self.trigger(game, aura)
        return game, aura, ring, answer

    def assert_acquired(self, game, ring, identity):
        current = self.current(game, ring)
        self.assertEqual(("A", "B", False), (current.controller, current.owner, current.tapped))
        self.assertEqual(identity, (current.logical_object_id, current.zone_timestamp))
        self.assertTrue(self.haste(game, ring))

    def test_actual_entry_and_own_upkeep_resolve_independently_on_the_same_attachment(self):
        game, aura, ring, _ = self.scene(61127293)
        identity = (ring.logical_object_id, ring.zone_timestamp)
        entry_key = self.trigger(game, aura).semantic_key
        self.tap_ring(game, ring)
        self.assertIsNone(self.resolve(game))
        self.assert_acquired(game, ring, identity)

        for seat in ("B", "C", "D", "A"):
            turn = game.state.turn_sequence
            self.until(game, lambda item: item.state.turn_sequence > turn and item.state.active_player == seat)
            self.assertEqual("B", self.current(game, ring).controller)
            self.assertFalse(self.haste(game, ring))
            if seat != "A":
                self.assertEqual([], game.state.stack)
        self.assertEqual("upkeep", game.state.step)
        self.assertEqual(entry_key, self.trigger(game, aura).semantic_key)
        self.tap_ring(game, ring)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assert_acquired(game, ring, identity)
        self.replay(game, load=True)

    def test_actual_source_departure_retains_the_original_attached_permanent(self):
        game, aura, ring, answer = self.scene(61127294, reply="Disenchant")
        identity = (ring.logical_object_id, ring.zone_timestamp)
        self.tap_ring(game, ring)
        self.cast_for(game, "B", answer, [aura.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.trigger(game, aura)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assert_acquired(game, ring, identity)
        self.replay(game, load=True)

    def test_actual_attachment_departure_cannot_apply_to_the_old_incarnation(self):
        game, aura, ring, answer = self.scene(61127295, reply="Boomerang")
        self.cast_for(game, "B", answer, [ring.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, ring).zone)
        self.assertEqual("B", self.current(game, ring).controller)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.assertFalse(self.haste(game, ring))
        self.replay(game, load=True)

    def test_complete_source_keeps_enchant_and_two_untargeted_trigger_subscriptions(self):
        original = self.db.lookup(FUGUE, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Frenzied Fugue")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        enchant, = (node for face in compiled.faces for node in face.nodes
                    if "attachment.aura.simple_object" in node.capability_dependencies)
        programs = semantics.programs_for_oracle(original.oracle_id)
        trigger, = (program for program in programs if program.ability_id.startswith("trigger:"))
        self.assertEqual(["permanent"], list(enchant.target_schema["categories"]))
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(("permanent.enter", "step.begin"),
                         tuple(row["event"] for row in trigger.event_condition["event_subscriptions"]))
        for old, new in (("at the beginning of your upkeep", "at the beginning of your end step"),
                         ("gain control of enchanted permanent", "gain control of equipped permanent")):
            with self.subTest(new=new):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
