"""Native Adventure cost offers and the exact public outcomes of a linked Mill."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.cast_cost_modifiers import ADVENTURE_PERMANENT_COST_CAPABILITY, PublicCastCostModifierV2Spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.committed_mill_cards import (
    MILL_ADVENTURES_TO_HAND_CAPABILITY,
    PUBLIC_MILLED_CARDS_CAPABILITY,
    MillAdventuresToHand,
)
from quorune.semantic_runtime.cast_costs import active_fixed_spell_cost_reductions
from scripts.build_test_database import build_fixture_database


BELUNA = "Beluna Grandsquall // Seek Thrills"
DRAGON = "Emerald Dragon // Dissonant Wave"
ALTAR = "Altar of Bhaal // Bone Offering"
PROFILE = "commander_review"


class CommittedMillAdventuresTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        database = folder / "committed-mill-adventures.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        pieces = [fixtures / "mill-committed-adventure-cards.json"]
        for name, supported in (
            ("ability-source-counter-cards", {DRAGON, ALTAR}),
            ("committed-milled-creature-tokens-cards",
             {"Grizzly Bears", "Isamaru, Hound of Konda", "Leyline of the Void", "Plains", "Rest in Peace"}),
        ):
            data = json.loads((fixtures / (name + ".json")).read_bytes())
            cards = [card for card in data["cards"] if card["name"] in supported]
            assert {card["name"] for card in cards} == supported
            ids = {card["oracle_id"] for card in cards}
            rulings = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / (name + ".json")
            part.write_text(json.dumps({"schema_version": 1, "cards": cards, "rulings": rulings}), encoding="utf-8")
            pieces.append(part)
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed Mill Adventures", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile_original(self, card=None):
        return compile_oracle_card(card or self.db.lookup(BELUNA), capability_registry=self.registry,
                                   capability_profile=PROFILE)

    def cast_named_face(self, game, card, face, *, seat="A"):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row.get("card") == card.ref and row.get("face") == face)
        command = {"action_id": offer["id"], "face": face, "pay": "auto"}
        if offer.get("cost_options"):
            command["cost_option"] = offer["cost_options"][0]["id"]
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D" if seat != "D" else "pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(face, game.state.stack[-1].chosen_face)

    def test_complete_original_requires_both_closed_results(self):
        ir = self.compile_original()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        cost, = (node for face in ir.faces for node in face.nodes
                 if ADVENTURE_PERMANENT_COST_CAPABILITY in node.capability_dependencies)
        descriptor = cost.handlers[0]["modifier"]
        self.assertEqual(3, descriptor["schema_version"])
        spec = PublicCastCostModifierV2Spec.from_dict(descriptor)
        self.assertEqual(1, spec.generic_reduction)
        with self.assertRaises(ValueError):
            PublicCastCostModifierV2Spec.from_dict({**descriptor, "mana_adjustment": {"GENERIC": -2}})
        result, = (node for face in ir.faces for node in face.nodes
                   if MILL_ADVENTURES_TO_HAND_CAPABILITY in node.capability_dependencies)
        self.assertIn(PUBLIC_MILLED_CARDS_CAPABILITY, result.capability_dependencies)
        self.assertEqual((MillAdventuresToHand().effect(),), result.effects)
        for bad in ({"schema_version": True, "kind": "all_adventure_to_hand"},
                    {"schema_version": 1, "kind": "all_adventure_to_hand", "target": "graveyard"}):
            with self.assertRaises(ValueError):
                MillAdventuresToHand.from_dict(bad)

    def test_permanent_face_pays_one_less_and_other_spell_faces_keep_their_cost(self):
        game = self.session(6859401)
        reducer = self.add(game.engine, BELUNA)
        dragon = self.add(game.engine, DRAGON, zone="hand")
        other_dragon = self.add(game.engine, DRAGON, seat="B", zone="hand", ref="opponents-adventure")
        bear = self.add(game.engine, "Grizzly Bears", zone="hand")
        for card in (reducer, dragon, other_dragon):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 4, "G": 2})
        self.checkpoint(game)
        dragon_type = "Creature — Dragon"
        discounts = lambda seat, card, **kwargs: active_fixed_spell_cost_reductions(game.engine, seat, card, **kwargs)
        self.assertEqual([1], [row.generic_reduction for row in discounts("A", dragon, cast_type_line=dragon_type)])
        self.assertFalse(discounts("A", dragon, cast_type_line="Instant — Adventure"))
        self.assertFalse(discounts("A", dragon, cast_type_line=dragon_type, face_down_method="morph"))
        self.assertFalse(discounts("B", other_dragon, cast_type_line=dragon_type))
        self.assertFalse(discounts("A", bear, cast_type_line="Creature — Bear"))
        self.cast_named_face(game, dragon, "Emerald Dragon")
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[dragon.object_id].zone)
        self.replay(game, load=True)

    def resolve_seek(self, game, caster, *, replacement):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 2, "G": 1, "U": 1, "R": 1})
        self.checkpoint(game)
        self.cast_named_face(game, caster, "Seek Thrills")
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        pending = self.resolve(game)
        if replacement:
            self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
            hidden = tuple(game.state.players["A"].zones["library"])
            for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                projected = json.dumps(game.packet(viewer, full=True))
                self.assertTrue(all(object_id not in projected for object_id in hidden))
            game = self.replay(game, load=True)
            for _ in range(8):
                if pending is None:
                    break
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                command = {"action_id": "choose", "replacement": options[0]["id"]}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                pending = self.resolve(game)
        self.assertIsNone(pending)
        return game

    def test_short_mill_returns_only_its_adventures_from_graveyard_or_public_exile(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                game = self.session(6859402 + int(replacement))
                for object_id in tuple(game.state.players["A"].zones["library"]):
                    game.engine.move_card(object_id, "hand", reason="Set a short library", log=False)
                caster = self.add(game.engine, BELUNA, zone="hand")
                outside = self.add(game.engine, ALTAR, zone="graveyard", ref="not-from-this-mill")
                plain = self.add(game.engine, "Grizzly Bears", zone="library")
                dragon = self.add(game.engine, DRAGON, zone="library")
                altar = self.add(game.engine, ALTAR, zone="library")
                for card in (plain, dragon, altar):
                    card.known_to = []
                    card.revealed_to = []
                self.assert_closed(game, caster)
                if replacement:
                    self.assert_closed(game, self.add(game.engine, "Rest in Peace", seat="C"))
                    self.assert_closed(game, self.add(game.engine, "Leyline of the Void", seat="D"))
                game = self.resolve_seek(game, caster, replacement=replacement)
                self.assertEqual("exile", game.state.cards[caster.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[outside.object_id].zone)
                expected_mill_zone = "exile" if replacement else "graveyard"
                self.assertEqual(expected_mill_zone, game.state.cards[plain.object_id].zone)
                self.assertEqual(("hand", "hand"), tuple(game.state.cards[card.object_id].zone for card in (dragon, altar)))
                mill, = (event for event in game.state.events if event.code == "card.mill")
                self.assertEqual(3, mill.details["count"])
                self.assertEqual([altar.ref, dragon.ref, plain.ref], mill.details["objects"])
                self.replay(game, load=True)

    def test_missing_rule_production_cannot_close_the_printed_spell(self):
        with patch("quorune.compiler.mill_templates.committed_mill_card_template", return_value=None):
            self.assertNotEqual("exact", self.compile_original().status)
        with patch("quorune.compiler.public_cast_cost_modifiers.public_cast_cost_modifier_v2_template", return_value=None):
            self.assertNotEqual("exact", self.compile_original().status)
        original = self.db.lookup(BELUNA)
        alternate = dict(original.faces[1])
        alternate["oracle_text"] = alternate["oracle_text"].replace("Mill seven cards.", "Mill six cards.")
        changed = replace(original, faces=(original.faces[0], alternate))
        self.assertNotEqual("exact", self.compile_original(changed).status)
