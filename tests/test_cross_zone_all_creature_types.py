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
from quorune.creature_subtypes import CREATURE_SUBTYPES
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cross_zone_characteristics import prospective_cross_zone_spell_type_line
from quorune.semantic_runtime.cross_zone_type_additions import (
    CROSS_ZONE_ALL_CREATURE_TYPES_CAPABILITY as CAPABILITY,
    CROSS_ZONE_TYPE_ADDITION_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Maskwood Nexus"
PROFILE = "commander_review"


class CrossZoneAllCreatureTypesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "all-creature-types.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "activation-result-views-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "shared-type-library-cards.json",
            fixtures / "external-entry-counter-cards.json",
            fixtures / "granted-static-characteristic-cards.json",
            fixtures / "recipient-control-cards.json",
            fixtures / "static-land-animation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cross-zone creature types", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_original_requires_runtime_authority_for_the_exact_all_subtype_descriptor(self):
        from quorune.compiler.cross_zone_type_addition_templates import cross_zone_type_addition_handler

        record = self.db.lookup(ORIGINAL, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = (item for item in semantics.programs_for_oracle(record.oracle_id)
                    if item.event == "characteristics.evaluate")
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertNotIn(CROSS_ZONE_TYPE_ADDITION_CAPABILITY, program.capability_dependencies)
        stripped = replace(program, coverage=[])
        self.assertTrue(bind_semantic_program_runtime(
            stripped, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        dependencies = tuple(item for item in stripped.capability_dependencies if item != CAPABILITY)
        closure = self.registry.closure(dependencies, profile=PROFILE).to_dict() if dependencies else None
        missing = replace(stripped, capability_dependencies=dependencies, capability_closure=closure)
        binding = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        text = record.oracle_text.splitlines()[0]
        for old, new in (
            ("creature spells you control", "creature spells your opponents control"),
            ("creature cards you own", "permanent cards you own"),
            ("every creature type", "every card type"),
        ):
            with self.subTest(variant=new):
                self.assertIsNone(cross_zone_type_addition_handler(text.replace(old, new)))
        old_report, _ = register_card(self.db, self.registry, "Biotransference")
        self.assertTrue(old_report["strict_capability_ready"], old_report["blockers"])

    def test_genuine_capability_withdrawal_rejects_the_new_variant_and_keeps_prior_scope(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        selected = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        selected.update(status="blocked", blockers=["Scoped all-creature-subtype withdrawal"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        rejected, _ = register_card(self.db, registry, ORIGINAL)
        self.assertFalse(rejected["strict_capability_ready"])
        retained, _ = register_card(self.db, registry, "Biotransference")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def _cast(self, game, card, payment, targets=()):
        self.priority(game, "A")
        current = game.state.cards[card.object_id]
        offer = self.action(game, "A", current)
        result = game.act("pilot:A", {
            "action_id": offer["id"], "targets": list(targets),
            "pay": "manual", "payment": payment,
        })
        self.assertTrue(result.ok, result.summary)

    def _subtypes(self, game, card):
        return game.engine._effective_type_parts(game.state.cards[card.object_id])[1]

    def _start(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        entrant = self.add(game.engine, "Grizzly Bears", zone="hand", ref="nexus-own-entrant")
        paragon = self.add(game.engine, "Bramblewood Paragon")
        for card in (source, entrant, paragon):
            self.assert_closed(game, card)
        return game, source, entrant

    def test_actual_original_updates_all_three_zones_and_survives_controller_handoff_and_replay(self):
        game, source, entrant = self._start(61412210)
        library = self.add(game.engine, "Grizzly Bears", zone="library", ref="nexus-own-library")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="nexus-foreign-hand")
        rival = self.add(game.engine, "Grizzly Bears", seat="B")
        land = self.add(game.engine, "Generic Bound Plains", zone="hand")
        donate = self.add(game.engine, "Donate", zone="hand")
        remove = self.add(game.engine, "Naturalize", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 11, "G": 3, "U": 1})
        self.checkpoint(game)
        self.assertEqual({"bear"}, self._subtypes(game, entrant))
        self._cast(game, source, {"C": 4})
        self.assertIsNone(self.resolve(game))
        for card in (entrant, library):
            self.assertEqual(CREATURE_SUBTYPES, self._subtypes(game, card))
        self.assertEqual({"plains"}, self._subtypes(game, land))
        self.assertEqual({"bear"}, self._subtypes(game, foreign))
        self.assertEqual({"bear"}, self._subtypes(game, rival))
        foreign_card = game.state.cards[foreign.object_id]
        prospective = prospective_cross_zone_spell_type_line(game.engine, foreign_card, "A", "Creature — Bear")
        self.assertEqual(CREATURE_SUBTYPES, game.engine._type_parts(prospective)[1])
        for viewer, card in (("B", entrant), ("C", entrant), ("D", library), ("A", foreign)):
            self.assertNotIn(card.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))

        self._cast(game, entrant, {"C": 1, "G": 1})
        self.assertEqual("stack", game.state.cards[entrant.object_id].zone)
        self.assertEqual(CREATURE_SUBTYPES, self._subtypes(game, entrant))
        self.assertIsNone(self.resolve(game))
        placed = game.state.cards[entrant.object_id]
        self.assertEqual(1, placed.counters["+1/+1"])
        self.assertEqual(CREATURE_SUBTYPES, self._subtypes(game, entrant))

        self.priority(game, "A")
        current = game.state.cards[source.object_id]
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (offer for offer in legal if offer["id"].startswith("activate:" + current.ref + ":"))
        accepted = game.act("pilot:A", {
            "action_id": activation["id"], "pay": "manual", "payment": {"C": 3},
        })
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        token, = (card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token)

        self._cast(game, donate, {"C": 2, "U": 1}, ["B", current.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        for card in (entrant, library):
            self.assertEqual({"bear"}, self._subtypes(game, card))
        for card in (foreign, rival):
            self.assertEqual(CREATURE_SUBTYPES, self._subtypes(game, card))
        game = self.replay(game, load=True)
        self._cast(game, remove, {"C": 1, "G": 1}, [game.state.cards[source.object_id].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual({"bear"}, self._subtypes(game, foreign))
        self.assertEqual(CREATURE_SUBTYPES, self._subtypes(game, token))
        self.replay(game, load=True)

    def test_omitting_prospective_subtypes_loses_the_actual_entry_counter(self):
        from contextlib import nullcontext

        for omit in (False, True):
            with self.subTest(omit=omit):
                game, source, _ = self._start(61412211 + int(omit))
                steel = self.add(game.engine, "Sculpting Steel", zone="hand")
                thopter = self.add(game.engine, "Ornithopter", seat="B")
                self.assert_closed(game, steel)
                game.state.config.semantic_policy = "trusted_only"
                self.prepare(game, mana={"C": 7})
                self.checkpoint(game)
                self._cast(game, source, {"C": 4})
                self.assertIsNone(self.resolve(game))
                selected = (patch("quorune.rules.cross_zone_characteristics.battlefield_entry_type_addition_effects",
                                  return_value=()) if omit else nullcontext())
                with selected:
                    self._cast(game, steel, {"C": 3})
                    self.assertEqual("replacement.order", self.resolve(game).kind)
                    options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                    offer, = (row["id"] for row in options if thopter.ref in row["id"] and not row["decline"])
                    result = game.act("pilot:A", {"action_id": "choose", "replacement": offer})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                self.assertEqual(0 if omit else 1, game.state.cards[steel.object_id].counters.get("+1/+1", 0))
                self.assertEqual(CREATURE_SUBTYPES, self._subtypes(game, steel))
                self.assertEqual({"thopter"}, self._subtypes(game, thopter))


if __name__ == "__main__":
    unittest.main()
