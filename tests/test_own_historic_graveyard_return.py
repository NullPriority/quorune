"""Historic target offers and the original paid Food activation."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_owner_zone_move_templates import _battlefield_owner_zone_target_schema
from quorune.deck import DeckDefinition, DeckEntry
from quorune.graveyard_target_unions import CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


SOURCE = "Samwise Gamgee"
SAGA = "Urza's Saga"
DOG = "Isamaru, Hound of Konda"
PROFILE = "commander_review"


class OwnHistoricGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixture_root = ROOT / "tests/fixtures"
        pieces = [fixture_root / "own-historic-graveyard-return-cards.json"]
        for name, supported in (
            ("committed-milled-creature-tokens-cards", {DOG, "Grizzly Bears", "Plains", "Tormod's Crypt"}),
            ("urza-saga-pinned-cards", {SAGA}),
            ("ability-source-counter-cards", {"Sol Ring"}),
            ("activation-public-state-cards", {"Raise the Alarm"}),
        ):
            data = json.loads((fixture_root / (name + ".json")).read_bytes())
            cards = [card for card in data["cards"] if card["name"] in supported]
            assert {card["name"] for card in cards} == supported
            ids = {card["oracle_id"] for card in cards}
            rulings = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            path = folder / (name + ".json")
            path.write_text(json.dumps({"schema_version": 1, "cards": cards, "rulings": rulings}), encoding="utf-8")
            pieces.append(path)
        db_path = folder / "historic-return.sqlite3"
        build_fixture_database(pieces, db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Historic return", [
            DeckEntry(DOG, 1, "commander"), DeckEntry("Plains", 30),
        ], [DOG])

    def ir(self):
        return compile_oracle_card(self.db.lookup(SOURCE), capability_registry=self.registry,
                                   capability_profile=PROFILE)

    def test_original_historic_target_uses_the_existing_three_typed_leaves(self):
        ir = self.ir()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (node for face in ir.faces for node in face.nodes
                 if CAPABILITY in node.capability_dependencies)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(["graveyard"], node.target_schema["zones"])
        self.assertEqual("you", node.target_schema["owner_relation"])
        self.assertEqual(1, node.target_schema["count"])
        artifact = {"types_all": ["artifact"], "subtypes_any": [], "supertypes_any": []}
        legendary = {"types_all": [], "subtypes_any": [], "supertypes_any": ["legendary"]}
        saga = {"types_all": [], "subtypes_any": ["saga"], "supertypes_any": []}
        self.assertEqual([legendary, saga, artifact], node.target_schema["characteristic_forms_any"])
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                          "types_none": ["land"], "characteristic_forms_any": [artifact, legendary, saga]},
                         _battlefield_owner_zone_target_schema("nonland historic permanent"))

    def test_removing_the_historic_parser_restores_the_material_residual(self):
        with patch("quorune.graveyard_target_unions.mixed_graveyard_target", return_value=None):
            result = self.ir()
        self.assertNotEqual("exact", result.status)
        self.assertTrue(any("target historic card" in row.text for row in result.material_residuals))

    def foods(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.controller == "A" and card.is_token
                and "food" in game.engine._effective_type_parts(card)[1]]

    def offer_for(self, game, seat, prefix):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row["id"].startswith(prefix))
        return offer

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_food_payment_and_historic_destinations_replay(self):
        for response in (False, True):
            with self.subTest(response=response):
                game = self.session(6831401 + int(response))
                source = self.add(game.engine, SOURCE)
                graveyard = {card: self.add(game.engine, card, zone="graveyard", ref=f"own-historic-{index}")
                             for index, card in enumerate(("Sol Ring", DOG, SAGA))}
                outside = self.add(game.engine, "Sol Ring", seat="B", zone="graveyard", ref="foreign-historic")
                nonhistoric = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="not-historic")
                creatures = [self.add(game.engine, "Grizzly Bears", zone="hand", ref=f"paid-food-source-{index}")
                             for index in range(3)]
                token_spell = self.add(game.engine, "Raise the Alarm", zone="hand")
                private = self.add(game.engine, "Grizzly Bears", zone="hand", seat="C", ref="spectator-hidden-unrelated")
                response_card = self.add(game.engine, "Tormod's Crypt", seat="B") if response else None
                game.state.config.semantic_policy = "trusted_only"
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 4, "G": 3, "W": 1})
                self.checkpoint(game)
                for expected, creature in enumerate(creatures, start=1):
                    self.cast(game, "A", creature, [])
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(expected, len(self.foods(game)))
                self.cast(game, "A", token_spell, [])
                self.assertIsNone(self.resolve(game))
                foods = self.foods(game)
                self.assertEqual(3, len(foods))
                offer = self.offer_for(game, "A", f"activate:{source.ref}:")
                self.assertEqual({card.ref for card in graveyard.values()}, set(offer["target_schema"]["legal_refs"]))
                price, = offer["cost_summary"]["choose_cost"]
                self.assertEqual(3, price["n"])
                self.assertEqual({card.ref for card in foods}, set(price["legal_refs"]))
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                command = {"action_id": offer["id"], "pay": "auto", "cost_cards": [card.ref for card in foods]}
                self.reject(game, "A", {**command, "targets": [outside.ref]})
                self.reject(game, "A", {**command, "targets": [nonhistoric.ref]})
                target = graveyard[SAGA]
                selected = {**command, "targets": [target.ref]}
                self.reject(game, "D", selected)
                self.reject(game, "A", {**selected, "cost_cards": command["cost_cards"][:2]})
                accepted = game.act("pilot:A", selected)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertFalse(self.foods(game))
                if response_card is not None:
                    crypt = self.offer_for(game, "B", f"activate:{response_card.ref}:")
                    accepted = game.act("pilot:B", {"action_id": crypt["id"], "targets": ["A"], "pay": "auto"})
                    self.assertTrue(accepted.ok, accepted.summary)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile" if response else "hand", game.state.cards[target.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[outside.object_id].zone)
                self.assertFalse(self.foods(game))
                self.replay(game, load=True)
