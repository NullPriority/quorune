"""The paid equipped hexproof instruction uses the ordinary attachment and effect owners."""

from dataclasses import replace
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


ORIGINAL = "Ring of Evos Isle"


class EquippedTemporaryHexproofTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-temporary-hexproof.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-temporary-hexproof-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped hexproof", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_untargeted_hexproof_and_blue_upkeep_counter(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        rows = semantics.programs_for_oracle(card.oracle_id)
        upkeep, = (row for row in rows if row.event == "step.begin")
        grant, = (row for row in rows if row.event == "activate"
                  and any(effect["op"] == "apply_source_characteristics_until_end_of_turn" for effect in row.effects))
        self.assertIsNone(grant.target_schema)
        self.assertEqual(["Hexproof"], grant.effects[0]["keywords"])
        self.assertEqual({
            "kind": "source_attachment", "relation": "equipped_object",
            "required_card_type": "permanent", "schema_version": 2,
        }, grant.effects[0]["card"])
        self.assertIn("object.reference.source_attachment_information", grant.capability_dependencies)
        self.assertEqual(("U", 3), (upkeep.effects[0]["card"]["required_color"],
                                   upkeep.effects[0]["card"]["schema_version"]))
        for text in (
            card.oracle_text.replace("gains hexproof", "gains flying"),
            card.oracle_text.replace("until end of turn", "until your next turn"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status)

    def action_for(self, game, seat, source, *, targeted):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        matches = [row for row in actions
                   if row["id"].startswith(f"activate:{source.ref}:") and bool(row.get("target_schema")) == targeted]
        self.assertEqual(1, len(matches), matches)
        return matches[0]["id"]

    def prepare_ring(self, game, ring, creature=None):
        self.assert_closed(game, ring)
        self.prepare(game, mana={"C": 5 if creature is not None else 4})
        self.checkpoint(game)
        self.cast_for(game, "A", ring, reserve={"C": 3 if creature is not None else 2})
        self.resolve_top(game)
        if creature is not None:
            equip = self.action_for(game, "A", ring, targeted=True)
            accepted = game.act("pilot:A", {
                "action_id": equip, "targets": [creature.ref], "pay": "auto", "reserve": {"C": 2},
            })
            self.assertTrue(accepted.ok, accepted.summary)
            self.resolve_top(game)
            self.assertEqual(creature.object_id, game.state.cards[ring.object_id].attached_to)

    def activate(self, game, ring):
        offer = self.action_for(game, "A", ring, targeted=False)
        command = {"action_id": offer, "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(game.state.stack[-1].targets)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))

    def hexproof(self, game, creature):
        data = game.engine._effective_card_data(game.state.cards[creature.object_id])
        return "hexproof" in {item.casefold() for item in data["keywords"]}

    def test_actual_unattached_activation_is_available_and_pays_without_granting(self):
        game = self.session(70211031)
        ring = self.add(game.engine, ORIGINAL, zone="hand")
        creature = self.add(game.engine, "Merfolk of the Pearl Trident")
        self.prepare_ring(game, ring)
        self.activate(game, ring)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.hexproof(game, creature))
        self.assertIsNone(game.state.cards[ring.object_id].attached_to)
        self.replay(game, load=True)

    def test_actual_hexproof_does_not_transfer_after_reattachment_and_only_blocks_opponents(self):
        game = self.session(70211032)
        ring = self.add(game.engine, ORIGINAL, zone="hand")
        creature = self.add(game.engine, "Merfolk of the Pearl Trident", ref="hexproof-original-creature")
        other = self.add(game.engine, "Runeclaw Bear", seat="B", ref="hexproof-new-attached-creature")
        theft = self.add(game.engine, "Magnetic Theft", seat="B", zone="hand")
        opposing = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="opposing-bounce")
        friendly = self.add(game.engine, "Unsummon", zone="hand", ref="friendly-bounce")
        self.add(game.engine, "Island", ref="friendly-bounce-island")
        self.add(game.engine, "Island", seat="B", ref="opposing-bounce-island")
        self.add(game.engine, "Mountain", seat="B")
        for card in (theft, opposing, friendly):
            self.assert_closed(game, card)
        self.prepare_ring(game, ring, creature)
        self.activate(game, ring)
        self.resolve_top(game)
        self.assertTrue(self.hexproof(game, creature))
        self.priority(game, "B")
        result = game.act("pilot:B", {
            "action_id": f"cast:{theft.ref}", "pay": "auto",
            "targets": {"target_0": [ring.ref], "target_1": [other.ref]},
        })
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(other.object_id, game.state.cards[ring.object_id].attached_to)
        self.assertTrue(self.hexproof(game, creature))
        self.assertFalse(self.hexproof(game, other))
        self.priority(game, "B")
        before = authoritative_state_hash(game.state)
        illegal = game.act("pilot:B", {"action_id": f"cast:{opposing.ref}", "targets": [creature.ref], "pay": "auto"})
        self.assertFalse(illegal.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", friendly, [creature.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_actual_paid_activation_uses_last_attachment_after_source_departs(self):
        game = self.session(70211033)
        ring = self.add(game.engine, ORIGINAL, zone="hand")
        creature = self.add(game.engine, "Merfolk of the Pearl Trident")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Wastes", seat="B")
        self.assert_closed(game, removal)
        self.prepare_ring(game, ring, creature)
        self.activate(game, ring)
        self.cast_for(game, "B", removal, [ring.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[ring.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertTrue(self.hexproof(game, creature))
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertFalse(self.hexproof(game, creature))
        self.replay(game, load=True)
