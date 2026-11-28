"""Nonlegendary graveyard targets and actual fixed entry-counter placement."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

CARD = "Persist"
ORACLE = "Return target nonlegendary creature card from your graveyard to the battlefield with a -1/-1 counter on it."
PROFILE = "commander_review"
REQUIRED = ("zone.reanimate.fixed_target", "zone.reanimate.entry_counters", "counter.producer.effect_entry")


class NonlegendaryReanimationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "nonlegendary-reanimation.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "nonlegendary-reanimation-cards.json",
            "attacking-card-entry-cards.json", "counter-prohibition-cards.json",
            "state-qualified-zone-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Nonlegendary reanimation", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiles_the_target_exclusion_and_the_existing_entry_counter_instruction(self):
        record = replace(query_record(ORACLE), type_line="Sorcery", mana_cost="{1}{B}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(ORACLE, ORACLE[node.span.start:node.span.end])
        self.assertEqual(["legendary"], node.target_schema["supertypes_none"])
        self.assertEqual((["graveyard"], "you", ["creature"], 1), (
            node.target_schema["zones"], node.target_schema["owner_relation"],
            node.target_schema["types_any"], node.target_schema["count"],
        ))
        self.assertEqual({"counter_name": "-1/-1", "amount": 1}, node.effects[0]["entry_counter"])
        self.assertEqual(3, node.effects[0]["schema_version"])
        self.assertTrue(set(REQUIRED) <= set(node.capability_dependencies))
        for unsupported in ("nonmythic creature", "nonlegendary planeswalker"):
            with self.subTest(unsupported=unsupported):
                changed = replace(record, oracle_text=ORACLE.replace("nonlegendary creature", unsupported))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup(CARD, fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        for missing in REQUIRED:
            with self.subTest(missing=missing):
                dependencies = [value for value in program.capability_dependencies if value != missing]
                altered = replace(program, capability_dependencies=dependencies,
                                  capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
                self.assertFalse(bind_semantic_program_runtime(
                    altered, capability_registry=self.registry, profile=PROFILE)["strict"])

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)

    def test_cast_offers_only_eligible_own_cards_and_places_a_real_counter_during_entry(self):
        game = self.session(12264007)
        spell = self.add(game.engine, CARD, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="own-nonlegendary")
        legend = self.add(game.engine, "Generic Bound Commander", zone="graveyard", ref="own-legend")
        noncreature = self.add(game.engine, "Generic Bound Plains", zone="graveyard", ref="own-land")
        foreign = self.add(game.engine, "Grizzly Bears", zone="graveyard", seat="B", ref="foreign-nonlegendary")
        self.start(game, spell)
        action = self.action(game, "A", spell)
        self.assertEqual([body.ref], action["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        for illegal in (legend, noncreature, foreign):
            with self.subTest(card=illegal.ref):
                result = game.act("pilot:A", {"action_id": action["id"], "targets": [illegal.ref], "pay": "auto"})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [body.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        entered = game.state.cards[body.object_id]
        self.assertEqual(("battlefield", "A", False, 1), (
            entered.zone, entered.controller, entered.tapped, entered.counters.get("-1/-1"),
        ))
        self.assertEqual((1, 1), (game.engine._numeric_stat(entered.object_id, "power"),
                                  game.engine._numeric_stat(entered.object_id, "toughness")))
        self.replay(game, load=True)

    def test_counter_prohibition_skips_placement_without_preventing_the_return(self):
        game = self.session(12264008)
        spell = self.add(game.engine, CARD, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        prohibition = self.add(game.engine, "Solemnity", seat="C")
        self.start(game, spell, prohibition)
        self.cast_for(game, "A", spell, [body.ref])
        self.assertIsNone(self.resolve(game))
        entered = game.state.cards[body.object_id]
        self.assertEqual(("battlefield", "A", {}, False),
                         (entered.zone, entered.controller, entered.counters, entered.tapped))
        self.assertEqual((2, 2), (game.engine._numeric_stat(entered.object_id, "power"),
                                  game.engine._numeric_stat(entered.object_id, "toughness")))
        self.replay(game, load=True)

    def test_original_target_stays_illegal_after_a_real_graveyard_round_trip(self):
        game = self.session(12264009)
        spell = self.add(game.engine, CARD, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        exile = self.add(game.engine, "Cremate", seat="B", zone="hand")
        restore = self.add(game.engine, "Pull from Eternity", seat="B", zone="hand")
        self.add(game.engine, "Swamp", seat="B")
        self.add(game.engine, "Generic Bound Plains", seat="B")
        self.start(game, spell, exile, restore)

        original = body.logical_object_id
        self.cast_for(game, "A", spell, [body.ref])
        self.cast_for(game, "B", exile, [body.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[body.object_id].zone)
        self.cast_for(game, "B", restore, [body.ref])
        self.resolve_top(game)
        returned = game.state.cards[body.object_id]
        self.assertEqual("graveyard", returned.zone)
        self.assertNotEqual(original, returned.logical_object_id)
        before = returned.logical_object_id
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        target = game.state.cards[body.object_id]
        self.assertEqual(("graveyard", before, {}), (target.zone, target.logical_object_id, target.counters))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)
