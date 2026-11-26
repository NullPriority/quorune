"""Native tests for the controlled-land subtype pair on the original Urza lands."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_quantity_mana_abilities import (
    PUBLIC_QUANTITY_MANA_HANDLER_ID, PublicQuantityActivatedManaAbilitySpec,
)
from quorune.public_quantity_mana_model import CONTROLLED_LAND_SUBTYPE_PAIR_MANA_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

PROFILE = "commander_review"


class ControlledLandSubtypePairManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "controlled-land-subtype-pair-mana.sqlite3"
        fixture = ROOT / "tests/fixtures/controlled-land-subtype-pair-mana-cards.json"
        build_fixture_database([fixture], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Native Urza mana", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, *cards, active="A", mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=active, active=active, mana=mana)
        self.checkpoint(game)

    def mana_offer(self, game, card):
        self.priority(game, "A")
        source = game.state.cards[card.object_id]
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions
                  if row["id"].startswith(f"activate:{source.ref}:") and row.get("mana_ability") is True)
        return offer

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate_mana(self, game, card, amount, *, check_rejections=False):
        offer = self.mana_offer(game, card)
        command = {"action_id": offer["id"]}
        before_c = game.state.players["A"].mana_pool.get("C", 0)
        before_total = sum(game.state.players["A"].mana_pool.values())
        before_stack = tuple(item.ref for item in game.state.stack)
        if check_rejections:
            self.reject(game, "B", command)
            self.reject(game, "A", {**command, "mana_output": {"C": amount - 1}})
            self.reject(game, "A", {**command, "mana_output": {"C": amount + 1}})
            self.assertFalse(game.state.cards[card.object_id].tapped)
            self.assertEqual(before_c, game.state.players["A"].mana_pool.get("C", 0))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[card.object_id].tapped)
        self.assertEqual(before_c + amount, game.state.players["A"].mana_pool["C"])
        self.assertEqual(before_total + amount, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual(before_stack, tuple(item.ref for item in game.state.stack))
        self.assertEqual("priority", game.state.pending_decision.kind)

    def test_actual_complete_trio_produces_mandatory_two_two_three_and_rejects_allocations(self):
        game = self.session(50829101)
        mine = self.add(game.engine, "Urza's Mine")
        plant = self.add(game.engine, "Urza's Power Plant")
        tower = self.add(game.engine, "Urza's Tower")
        self.ready(game, mine, plant, tower)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.activate_mana(game, mine, 2)
        self.activate_mana(game, plant, 2)
        game = self.replay(game, load=True)
        self.activate_mana(game, tower, 3, check_rejections=True)
        self.assertEqual(7, game.state.players["A"].mana_pool["C"])
        self.assertEqual(0, sum(game.state.players["B"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_missing_foreign_phased_factory_or_changed_helper_yields_one(self):
        for index, layout in enumerate(("missing", "foreign", "factory", "phased", "island")):
            with self.subTest(layout=layout):
                game = self.session(50829102 + index)
                tower = self.add(game.engine, "Urza's Tower")
                mine = self.add(game.engine, "Urza's Mine")
                cards = [tower, mine]
                plant = None
                if layout in {"foreign", "phased", "island"}:
                    plant = self.add(game.engine, "Urza's Power Plant",
                                     seat="B" if layout == "foreign" else "A")
                    cards.append(plant)
                if layout == "factory":
                    cards.append(self.add(game.engine, "Urza's Factory"))
                answer = None
                if layout == "phased":
                    answer = self.add(game.engine, "Reality Ripple", seat="B", zone="hand")
                elif layout == "island":
                    answer = self.add(game.engine, "Spreading Seas", seat="B", zone="hand")
                if answer is not None:
                    cards.append(answer)
                self.ready(game, *cards, active="B" if answer else "A",
                           mana={"C": 1, "U": 1} if answer else None)
                if answer is not None:
                    self.cast(game, "B", answer, [plant.ref])
                    self.assertIsNone(self.resolve(game))
                    changed = game.state.cards[plant.object_id]
                    self.assertEqual("battlefield", changed.zone)
                    self.assertEqual("Urza's Power Plant", changed.printed_name)
                    if layout == "phased":
                        self.assertTrue(changed.phased_out)
                    else:
                        live = game.engine._effective_card_data(changed)
                        self.assertEqual({"island"}, game.engine._type_parts(live["type_line"])[1])
                if layout == "foreign":
                    self.assertEqual("B", game.state.cards[plant.object_id].controller)
                self.activate_mana(game, tower, 1)
                self.replay(game, load=True)

    def test_actual_automatic_cast_uses_enhanced_tower_while_both_helpers_are_tapped(self):
        game = self.session(50829108)
        mine = self.add(game.engine, "Urza's Mine")
        plant = self.add(game.engine, "Urza's Power Plant")
        tower = self.add(game.engine, "Urza's Tower")
        artifact = self.add(game.engine, "Worn Powerstone", zone="hand")
        mine.tapped = True
        plant.tapped = True
        self.ready(game, mine, plant, tower, artifact)
        self.assertFalse(game.state.cards[tower.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "A", artifact)
        self.assertTrue(game.state.cards[tower.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        spell, = game.state.stack
        self.assertEqual(("spell", artifact.object_id), (spell.kind, spell.card_object_id))
        self.assertEqual("priority", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
        self.assertTrue(game.state.cards[artifact.object_id].tapped)
        self.replay(game, load=True)

    def test_all_three_originals_require_their_exact_typed_price_and_lands(self):
        cap = CONTROLLED_LAND_SUBTYPE_PAIR_MANA_CAPABILITY
        game = self.session(50829109)
        for source, required, amount in (
            (self.add(game.engine, "Urza's Mine"), ("power-plant", "tower"), 2),
            (self.add(game.engine, "Urza's Power Plant"), ("mine", "tower"), 2),
            (self.add(game.engine, "Urza's Tower"), ("mine", "power-plant"), 3),
        ):
            with self.subTest(card=source.printed_name):
                self.assert_closed(game, source)
                program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                            if cap in program.capability_dependencies)
                handler, = (node for node in program.handlers if node["handler_id"] == PUBLIC_QUANTITY_MANA_HANDLER_ID)
                spec = PublicQuantityActivatedManaAbilitySpec.from_dict(handler["ability"])
                self.assertEqual(required, spec.output.amount.land_subtypes)
                self.assertEqual(amount, spec.output.amount.enhanced_amount)
                self.assertEqual(("C",), spec.output.colors)
                self.assertTrue(spec.costs.tap_source)
                self.assertFalse(any(spec.costs.mana_cost.values()))
                remaining = tuple(key for key in program.capability_dependencies if key != cap)
                altered = replace(program, capability_dependencies=remaining,
                                  capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
                missing = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(missing["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", missing["blockers"])
        card = self.db.lookup("Urza's Mine", fuzzy=False)
        for needle, substitute in (
            ("{T}:", "{1}, {T}:"),
            ("an Urza's Tower", "an Urza's Power-Plant"),
            ("and an", "or an"),
            ("add {C}{C} instead", "add {C}{G} instead"),
        ):
            with self.subTest(change=substitute):
                changed = replace(card, oracle_text=card.oracle_text.replace(needle, substitute))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
