"""Controlled chosen subtypes apply to the live battlefield and prospective entry."""

from copy import deepcopy
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
from quorune.compiler.cross_zone_type_addition_templates import cross_zone_type_addition_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.cross_zone_type_additions import (
    CONTROLLED_CHOSEN_CREATURE_SUBTYPE_CAPABILITY as CAPABILITY,
    CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER as HANDLER,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


XENO = "Xenograft"
ARCANE = "Arcane Adaptation"
PARAGON = "Bramblewood Paragon"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ControlledChosenSubtypeAdditionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains", "Grizzly Bears"},
            "activation-result-views-cards.json": {"Ornithopter"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans"},
            "external-entry-counter-cards.json": {PARAGON},
            "recipient-control-cards.json": {"Donate"},
            "static-land-animation-cards.json": {"Sculpting Steel"},
        }
        paths = [FIXTURES / "controlled-chosen-subtype-addition-cards.json"]
        for name, names in selected.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(document), encoding="utf-8")
            paths.append(path)
        path = folder / "controlled-chosen-subtype.sqlite3"
        build_fixture_database(paths, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled Chosen Type", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_original_admits_only_the_exact_battlefield_descriptor(self):
        record = self.db.lookup(XENO, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, XENO)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        p, = [program for program in semantics.programs_for_oracle(record.oracle_id)
               if CAPABILITY in program.capability_dependencies]
        self.assertEqual("characteristics.evaluate", p.event)
        self.assertEqual(HANDLER, p.handlers[0]["handler_id"])
        unlabelled = replace(p, coverage=[])
        self.assertTrue(bind_semantic_program_runtime(
            unlabelled, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        deps = tuple(c for c in p.capability_dependencies if c != CAPABILITY)
        changed = replace(unlabelled, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])
        original = record.oracle_text.splitlines()[-1]
        for text in (original.replace("creature you control", "creature spell you control"),
                     original.replace("creature you control", "creature your opponents control"),
                     original.replace("in addition to its other types", "and loses all other creature types")):
            self.assertIsNone(cross_zone_type_addition_handler(text), text)

    def _start(self, game, mana):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _actor_action(self, game, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _paid(self, game, source, payment, targets=()):
        self.priority(game, "A")
        row = self.action(game, "A", source)
        self._actor_action(game, {"action_id": row["id"], "pay": "manual", "payment": payment, "targets": list(targets)})

    def _designate(self, game, source):
        self._paid(game, source, {"C": 4, "U": 1})
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        target, = [row["id"] for row in choices if row["id"].endswith(":warrior")]
        self._actor_action(game, {"action_id": "choose", "replacement": target})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("warrior", game.state.cards[source.object_id].annotations["chosen_creature_type"])

    def _types(self, game, card):
        return game.engine._effective_type_parts(game.state.cards[card.object_id])[1]

    def _paid_entry(self, seed):
        game = self.session(seed)
        xeno = self.add(game.engine, XENO, zone="hand")
        paragon = self.add(game.engine, PARAGON)
        own = self.add(game.engine, "Grizzly Bears", ref="xeno-own-bear")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="xeno-other-bear")
        entrant = self.add(game.engine, "Grizzly Bears", zone="hand", ref="xeno-entering-bear")
        kindred = self.add(game.engine, "Boggart Shenanigans")
        gift = self.add(game.engine, "Donate", zone="hand")
        for card in (xeno, paragon, entrant, gift):
            self.assert_closed(game, card)
        self._start(game, {"C": 7, "U": 2, "G": 1})
        self._designate(game, xeno)
        self.assertEqual({"bear", "warrior"}, self._types(game, own))
        self.assertEqual({"bear"}, self._types(game, foreign))
        self.assertEqual({"goblin"}, self._types(game, kindred))
        self.assertEqual({"bear"}, self._types(game, entrant))
        self._paid(game, entrant, {"C": 1, "G": 1})
        self.assertEqual("stack", game.state.cards[entrant.object_id].zone)
        self.assertEqual({"bear"}, self._types(game, entrant))
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"bear", "warrior"}, self._types(game, entrant))
        return game, xeno, own, foreign, entrant, gift

    def test_paid_xenograft_changes_actual_entry_and_tracks_its_new_controller(self):
        game, xeno, own, foreign, entrant, gift = self._paid_entry(61412070)
        self.assertEqual(1, game.state.cards[entrant.object_id].counters["+1/+1"])
        self._paid(game, gift, {"C": 2, "U": 1}, ["B", xeno.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "B"), (game.state.cards[xeno.object_id].owner, game.state.cards[xeno.object_id].controller))
        self.assertEqual({"bear"}, self._types(game, own))
        self.assertEqual({"bear"}, self._types(game, entrant))
        self.assertEqual({"bear", "warrior"}, self._types(game, foreign))
        self.assertEqual(1, game.state.cards[entrant.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_artifact_copy_sees_the_controlled_entry_subtype_before_counters(self):
        game = self.session(61412071)
        xeno = self.add(game.engine, XENO, zone="hand")
        paragon = self.add(game.engine, PARAGON)
        steel = self.add(game.engine, "Sculpting Steel", zone="hand")
        thopter = self.add(game.engine, "Ornithopter", seat="B")
        for card in (xeno, paragon, steel, thopter):
            self.assert_closed(game, card)
        self._start(game, {"C": 7, "U": 1})
        self._designate(game, xeno)
        self._paid(game, steel, {"C": 3})
        pending = self.resolve(game)
        self.assertEqual("replacement.order", pending.kind)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        selected, = [row["id"] for row in choices if thopter.ref in row["id"] and not row["decline"]]
        game = self.replay(game, load=True)
        self._actor_action(game, {"action_id": "choose", "replacement": selected})
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"thopter", "warrior"}, self._types(game, steel))
        self.assertEqual({"thopter"}, self._types(game, thopter))
        self.assertEqual(1, game.state.cards[steel.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_prospective_omission_loses_the_counter_and_retains_the_live_subtype(self):
        with patch("quorune.rules.cross_zone_characteristics.battlefield_entry_type_addition_effects", return_value=()):
            game, _xeno, _own, _foreign, entrant, _gift = self._paid_entry(61412072)
        self.assertNotIn("+1/+1", game.state.cards[entrant.object_id].counters)
        self.assertEqual({"bear", "warrior"}, self._types(game, entrant))

    def test_genuine_controlled_subtype_withdrawal_keeps_the_distinct_cross_zone_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(r for r in raw["capabilities"] if r["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped controlled battlefield subtype withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, XENO)
        self.assertFalse(rejected["strict_capability_ready"])
        for name in (ARCANE, PARAGON):
            retained, _ = register_card(self.db, registry, name)
            self.assertTrue(retained["strict_capability_ready"], (name, retained["blockers"]))


if __name__ == "__main__":
    unittest.main()
