"""Witness current rules names through a live land-count characteristic."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import (
    CharacteristicFragmentError, CharacteristicQuantityScope, DISTINCT_PUBLIC_NAMES_CAPABILITY,
    characteristic_quantity_capabilities,
)
from quorune.compiler.query_characteristic_templates import query_characteristic_value
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.distinct_public_names import distinct_public_name_count
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class DistinctLandNamesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "distinct-land-names.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/distinct-land-names-cards.json", path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Land name witnesses", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def play_land(self, game, land, seat="A"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions if row.get("card") == land.ref and row.get("kind") != "cast")
        result = game.act("pilot:" + seat, {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)

    def zombies(self, game):
        return sum(game.state.cards[object_id].printed_name == "Zombie Token"
                   for object_id in game.state.players["A"].zones["battlefield"])

    def test_actual_amalgam_counts_unique_controlled_names_in_hand_and_in_play(self):
        game = self.session(2010021)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Awakened Amalgam", zone="hand")
        self.add(game.engine, "Plains", ref="first-plains")
        self.add(game.engine, "Plains", ref="second-plains")
        self.add(game.engine, "Island")
        self.add(game.engine, "Forest", seat="B")
        mountain = self.add(game.engine, "Mountain", zone="hand")
        donate = self.add(game.engine, "Donate", zone="hand")
        for card in (source, mountain, donate):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "U": 1})
        self.checkpoint(game)

        def stats(session):
            return tuple(session.engine._numeric_stat(source.object_id, stat) for stat in ("power", "toughness"))

        self.assertEqual((2, 2), stats(game))
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), stats(game))
        self.play_land(game, mountain)
        self.assertEqual((3, 3), stats(game))
        self.cast_for(game, "A", donate, ["B", game.state.cards[mountain.object_id].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), stats(game))
        self.replay(game, load=True)

    def test_actual_field_counts_its_own_entry_and_rechecks_seven_controlled_names(self):
        for index, mode in enumerate(("self", "duplicate", "recheck", "foreign")):
            with self.subTest(mode=mode):
                game = self.session(2010040 + index)
                game.state.config.semantic_policy = "trusted_only"
                field = self.add(game.engine, "Field of the Dead", zone="hand" if mode == "self" else "battlefield")
                lands = [self.add(game.engine, name) for name in ("Plains", "Island", "Swamp", "Mountain", "Forest")]
                land = field
                if mode in ("self", "foreign"):
                    self.add(game.engine, "Wastes")
                    if mode == "foreign":
                        land = self.add(game.engine, "Wastes", seat="B", zone="hand", ref="foreign-wastes")
                else:
                    land = self.add(game.engine, "Plains" if mode == "duplicate" else "Wastes", zone="hand", ref="entering-land")
                if mode == "recheck":
                    answer = self.add(game.engine, "Beast Within", seat="B", zone="hand")
                    self.assert_closed(game, answer)
                    game.state.players["B"].mana_pool.update({"C": 2, "G": 1})
                self.assert_closed(game, field)
                seat = "B" if mode == "foreign" else "A"
                self.prepare(game, actor=seat, active=seat)
                self.checkpoint(game)
                self.play_land(game, land, seat)
                self.assertEqual(1 if mode in ("self", "recheck") else 0, self.trigger_count(game, field))
                if mode == "self":
                    self.assertTrue(game.state.cards[field.object_id].tapped)
                if mode == "recheck":
                    self.cast_for(game, "B", answer, [lands[1].ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[lands[1].object_id].zone)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(1 if mode == "self" else 0, self.zombies(game))
                self.replay(game, load=True)

    def test_actual_field_places_both_triggers_after_a_group_of_distinct_lands_enters(self):
        game = self.session(2010050)
        game.state.config.semantic_policy = "trusted_only"
        field = self.add(game.engine, "Field of the Dead")
        reclaim = self.add(game.engine, "Splendid Reclamation", zone="hand")
        for name in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            self.add(game.engine, name)
        for name in ("Wastes", "Terramorphic Expanse"):
            self.add(game.engine, name, zone="graveyard")
        for card in (field, reclaim):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", reclaim)
        ordering = self.resolve(game)
        self.assertIsNotNone(ordering)
        self.assertEqual("trigger.order", ordering.kind)
        refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(refs))
        result = game.act("pilot:A", {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, self.trigger_count(game, field))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, self.zombies(game))
        self.replay(game, load=True)

    def test_shared_names_do_not_count_multiple_objects_or_an_unnamed_object(self):
        self.assertEqual(3, distinct_public_name_count([
            (), ("Plains",), ("Plains",), ("Island", "Room"), ("Island",), ("Forest",),
        ]))
        self.assertEqual(2, distinct_public_name_count([("A", "B"), ("B", "C"), ("C", "D")]))
        self.assertEqual(1, distinct_public_name_count([("A", "B"), ("B", "C"), ("C", "A")]))

    def test_distinct_names_query_refuses_a_different_scope_and_its_withdrawal_is_residual(self):
        quantity = query_characteristic_value("the number of differently named lands you control", source_name="Awakened Amalgam")
        self.assertIn(DISTINCT_PUBLIC_NAMES_CAPABILITY, characteristic_quantity_capabilities(quantity))
        with self.assertRaises(CharacteristicFragmentError):
            replace(quantity, scope=CharacteristicQuantityScope.OPPONENT_ZONES)
        source = self.db.lookup("Awakened Amalgam", fuzzy=False)
        document = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in document["capabilities"] if row["id"] == DISTINCT_PUBLIC_NAMES_CAPABILITY).update(
            status="blocked", blockers=["Withdraw the current public name reduction"],
        )
        registry = CapabilityRegistry(document)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=document, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(
            source, capability_registry=registry, capability_profile="commander_review",
        ).status)
        with patch("quorune.compiler.query_characteristic_templates._unadjusted_characteristic_value", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                source, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
