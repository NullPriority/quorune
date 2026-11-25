"""A public layer-7c modifier counts actual controlled back-up nonmodal permanents."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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
from quorune.rules.live_modifier_quantities import controlled_transformed_permanent_count
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Mutagen Connoisseur"
PROWLER = "Kessig Prowler // Sinuous Predator"
MODAL = "Bala Ged Recovery // Bala Ged Sanctuary"
CAPABILITY = "quantity_expression.controlled_transformed_permanents"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ControlledTransformedPermanentStaticCountTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "physical-transformed-static.sqlite3"
        build_fixture_database([basic, FIXTURES / "controlled-transformed-permanent-static-count-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Physical transformed count", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        program, = [item for item in programs if CAPABILITY in item.capability_dependencies]
        self.assertTrue(any("combat.attack.vigilance" in item.capability_dependencies for item in programs))
        self.assertTrue(any("combat.block.flying" in item.capability_dependencies for item in programs))
        return record, program

    def test_complete_original_keeps_flying_vigilance_and_live_transformed_power(self):
        card, program = self.program()
        descriptor, = program.handlers
        fragment = descriptor["fragment"]["value"]
        self.assertEqual((1, 0), (fragment["power"], fragment["toughness"]))
        self.assertEqual("controller_transformed_permanents", fragment["quantity"]["scope"])
        self.assertIsNone(fragment["quantity"]["query"])
        for text in (
            card.oracle_text.replace("+1/+0", "+1/+1"),
            card.oracle_text.replace("you control", "your opponents control"),
            card.oracle_text.replace("transformed permanent", "transformed creature"),
            card.oracle_text.replace("transformed permanent", "face-down permanent"),
        ):
            value = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                        capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in value.faces for node in face.nodes))

    def test_raw_live_count_rejects_wrong_multiplier_exclusion_and_missing_authority(self):
        _, program = self.program()
        descriptor, = program.handlers
        wrapper = descriptor["fragment"]
        value = wrapper["value"]
        for changed in (
            {**value, "power": 2},
            {**value, "quantity": {**value["quantity"], "exclude_source": True}},
            {**value, "quantity": {**value["quantity"], "multiplier": 2}},
        ):
            with self.assertRaises(ValueError):
                replace(program, handlers=({**descriptor, "fragment": {**wrapper, "value": changed}},))
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def test_physical_count_excludes_meld_components_phasing_and_face_down_status(self):
        transform = self.db.lookup(PROWLER, fuzzy=False)
        modal = self.db.lookup(MODAL, fuzzy=False)
        battle = SimpleNamespace(oracle_id="battle", layout="battle", faces=({"name": "Front"}, {"name": "Back"}))
        single = SimpleNamespace(oracle_id="single", layout="normal")

        def face(record, active, **kwargs):
            return SimpleNamespace(**{
                "oracle_id": record.oracle_id, "zone": "battlefield", "controller": "A", "is_token": False,
                "active_face": active, "phased_out": False, "face_down": False,
                "permanent_composition": None, **kwargs,
            })

        cards = {
            "t": face(transform, "Sinuous Predator"),
            "f": face(transform, "Kessig Prowler"),
            "m": face(modal, "Bala Ged Sanctuary"),
            "b": face(battle, "Back"),
            "p": face(transform, "Sinuous Predator", phased_out=True),
            "d": face(transform, "Sinuous Predator", face_down=True),
            "c": face(transform, "Sinuous Predator", permanent_composition=object()),
            "copy": face(single, "Sinuous Predator"),
        }
        player = SimpleNamespace(zones={"battlefield": list(cards)})
        records = {record.oracle_id: record for record in (transform, modal, battle, single)}
        host = SimpleNamespace(state=SimpleNamespace(players={"A": player}, cards=cards),
                               card_db=SimpleNamespace(by_oracle_id=records.get),
                               card_record=lambda card: transform if card is cards["copy"] else None)
        self.assertEqual(2, controlled_transformed_permanent_count(host, "A"))
        cards["f"].controller = "B"
        with self.assertRaisesRegex(ValueError, "contradictory battlefield membership"):
            controlled_transformed_permanent_count(host, "A")

    def activate_transform(self, game, card, *, seat="A", reserve=None):
        self.priority(game, seat)
        offers = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in offers if row["id"].startswith(f"activate:{card.ref}:")]
        result = game.act("pilot:" + seat, {"action_id": action["id"], "pay": "auto",
                                           "reserve": reserve or {}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_modal_land_other_controller_and_both_transforming_kinds_keep_live_power(self):
        game = self.session(701270701)
        source = self.add(game.engine, NAME)
        prowler = self.add(game.engine, PROWLER, ref="mutagen-own-prowler")
        other = self.add(game.engine, PROWLER, seat="B", ref="mutagen-other-prowler")
        for permanent in (prowler, other):
            permanent.active_face = "Kessig Prowler"
        modal = self.add(game.engine, MODAL, zone="hand")
        beast = self.add(game.engine, "Converter Beast", zone="hand")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="mutagen-private")
        for card in (source, prowler, other, beast):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "G": 2, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 4, "G": 1})
        self.checkpoint(game)

        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        land, = [row for row in offers if row["id"].startswith(f"play-land:{modal.ref}")]
        played = game.act("pilot:A", {"action_id": land["id"]})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("Bala Ged Sanctuary", game.state.cards[modal.object_id].active_face)
        self.activate_transform(game, other, seat="B")
        self.assertEqual("Sinuous Predator", game.state.cards[other.object_id].active_face)
        self.assertEqual(0, game.engine._numeric_stat(source.object_id, "power"))
        game = self.replay(game, load=True)

        self.activate_transform(game, prowler, reserve={"C": 5, "G": 1, "U": 2})
        self.assertEqual("Sinuous Predator", game.state.cards[prowler.object_id].active_face)
        self.assertEqual(1, game.engine._numeric_stat(source.object_id, "power"))
        self.cast_for(game, "A", beast, reserve={"C": 2, "U": 2})
        self.assertIsNone(self.resolve(game))
        token, = [card for card in game.state.cards.values()
                  if card.owner == "A" and card.is_token and card.zone == "battlefield"]
        self.assertEqual("Incubator Token", token.active_face)
        self.assertEqual(1, game.engine._numeric_stat(source.object_id, "power"))
        self.activate_transform(game, token, reserve={"U": 2})
        self.assertEqual("Phyrexian Token", game.state.cards[token.object_id].active_face)
        self.assertEqual(2, game.engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(5, game.engine._numeric_stat(source.object_id, "toughness"))

        self.cast_for(game, "A", bounce, [prowler.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[prowler.object_id].zone)
        self.assertEqual(1, game.engine._numeric_stat(source.object_id, "power"))
        self.assertTrue({"flying", "vigilance"}.issubset(game.engine._combat_keywords(game.state.cards[source.object_id])))
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_native_transformed_count_witness_kills_token_omission_mutation(self):
        from quorune.rules import live_modifier_quantities

        normal = live_modifier_quantities._is_transformed_permanent

        def omit_tokens(host, card):
            return not card.is_token and normal(host, card)

        with patch.object(live_modifier_quantities, "_is_transformed_permanent", side_effect=omit_tokens):
            with self.assertRaisesRegex(AssertionError, "2 != 1"):
                self.test_actual_modal_land_other_controller_and_both_transforming_kinds_keep_live_power()
