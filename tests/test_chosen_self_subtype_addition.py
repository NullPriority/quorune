"""Linked self creature types have a prospective entry and a live layer-four result."""

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
from quorune.card_programs.reviewed_overlay import shadowed_reviewed_self_chosen_subtype_keys
from quorune.carddb import CardDatabase
from quorune.compiler import entry_designation_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import (
    SELF_CHOSEN_SUBTYPE_CAPABILITY as CAPABILITY,
    SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID as STATIC_HANDLER,
    SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID as ENTRY_HANDLER,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


AUTOMATON = "Adaptive Automaton"
THRONE = "Roaming Throne"
MIMIC = "Metallic Mimic"
PARAGON = "Bramblewood Paragon"
CHIEF = "Beetleback Chief"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenSelfSubtypeAdditionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Plains", "Grizzly Bears", "Unsummon",
            },
            "entry-designation-cards.json": {AUTOMATON, MIMIC},
            "external-entry-counter-cards.json": {PARAGON},
            "creature-subtype-addition-cards.json": {"Goblin Piker"},
            "scryfall-exact-lists.json": {THRONE, "Island"},
        }
        inputs = [FIXTURES / "chosen-self-subtype-addition-cards.json"]
        for name, names in selected.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [c for c in document["cards"] if c["name"] in names]
            assert {c["name"] for c in document["cards"]} == names
            ids = {c["oracle_id"] for c in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", []) if r["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(document), encoding="utf-8")
            inputs.append(path)
        path = folder / "chosen-self-subtype-addition.sqlite3"
        build_fixture_database(inputs, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked Self Type", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_both_originals_bind_the_exact_prospective_and_live_type_owners(self):
        for name in (AUTOMATON, THRONE):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantic = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                if name == THRONE:
                    originals = SemanticRegistry().programs_for_oracle(card.oracle_id)
                    legacy, = [p for p in originals if p.ability_id == "spell:front"]
                    self.assertIsNone(semantic.get(legacy.key))
                    current = semantic.programs_for_oracle(card.oracle_id)
                    args = dict(capability_registry=self.registry, capability_profile=PROFILE, oracle_ir=ir)
                    self.assertEqual({legacy.key}, shadowed_reviewed_self_chosen_subtype_keys(
                        card, current, (legacy,), **args,
                    ))
                    changed = replace(legacy, effects=[{"op": "choose_creature_type", "apply_as_subtype": False}])
                    self.assertEqual(set(), shadowed_reviewed_self_chosen_subtype_keys(
                        card, current, (changed,), **args,
                    ))
                    incomplete = tuple(p for p in current if p.event != "characteristics.evaluate")
                    self.assertEqual(set(), shadowed_reviewed_self_chosen_subtype_keys(
                        card, incomplete, (legacy,), **args,
                    ))
                owned = tuple(p for p in semantic.programs_for_oracle(card.oracle_id)
                              if CAPABILITY in p.capability_dependencies)
                self.assertEqual({ENTRY_HANDLER, STATIC_HANDLER},
                                 {h["handler_id"] for p in owned for h in p.handlers})
                self.assertEqual({"zone.change", "characteristics.evaluate"}, {p.event for p in owned})
                for program in owned:
                    unlabelled = replace(program, coverage=[])
                    self.assertTrue(bind_semantic_program_runtime(
                        unlabelled, capability_registry=self.registry, profile=PROFILE,
                    )["strict"])
                    deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
                    missing = replace(unlabelled, capability_dependencies=deps,
                                      capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                    report = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
                    self.assertFalse(report["strict"])
                    self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, report["blockers"])

    def test_unrepresented_sibling_and_unlinked_or_duplicate_choices_still_fail_closed(self):
        mimic = self.db.lookup(MIMIC, fuzzy=False)
        with patch("quorune.compiler.external_entry_counters._chosen_subtype_external_counter_handler",
                   return_value=None):
            admitted, _ = register_card(self.db, self.registry, MIMIC)
            self.assertFalse(admitted["strict_capability_ready"])
            ir = compile_oracle_card(mimic, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(any("additional +1/+1 counter" in s.text for s in ir.material_residuals))
        auto = self.db.lookup(AUTOMATON, fuzzy=False)
        first, remainder = auto.oracle_text.split("\n", 1)
        for prefix in ("", first + "\n" + first + "\n", first.replace("creature type", "color") + "\n"):
            with self.subTest(prefix=prefix):
                other = compile_oracle_card(replace(auto, oracle_text=prefix + remainder),
                                            capability_registry=self.registry, capability_profile=PROFILE)
                self.assertFalse(any(h["handler_id"] == ENTRY_HANDLER
                                     for f in other.faces for n in f.nodes for h in n.handlers))
                self.assertNotEqual("exact", other.status)

    def _start(self, game, mana):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _choose(self, game, command, *, actor="A"):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _cast_entry(self, game, source, mana, subtype):
        self.priority(game, "A")
        row = self.action(game, "A", source)
        self._choose(game, {"action_id": row["id"], "pay": "manual", "payment": mana})
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("stack", game.state.cards[source.object_id].zone)
        self.assertFalse(game.state.cards[source.object_id].counters)
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        found, = [row["id"] for row in schema if ENTRY_HANDLER in row["id"] and row["id"].endswith(":" + subtype)]
        self._choose(game, {"action_id": "choose", "replacement": found})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(subtype, game.state.cards[source.object_id].annotations["chosen_creature_type"])

    def _types(self, game, card):
        return game.engine._effective_type_parts(game.state.cards[card.object_id])[1]

    def test_paid_automaton_adds_before_an_external_warrior_counter_and_rechooses_after_bounce(self):
        game = self.session(61412501)
        auto = self.add(game.engine, AUTOMATON, zone="hand")
        paragon = self.add(game.engine, PARAGON)
        goblin = self.add(game.engine, "Goblin Piker")
        bear = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Goblin Piker", seat="C", ref="automaton-foreign-warrior")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        for card in (auto, paragon):
            self.assert_closed(game, card)
        self._start(game, {"C": 6})
        self._cast_entry(game, auto, {"C": 3}, "warrior")
        current = game.state.cards[auto.object_id]
        first_incarnation = current.logical_object_id
        self.assertEqual({"construct", "warrior"}, self._types(game, auto))
        self.assertEqual(1, current.counters["+1/+1"])
        self.assertEqual(3, game.engine._numeric_stat(auto.object_id, "power"))
        self.assertEqual(3, game.engine._numeric_stat(goblin.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(bear.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(foreign.object_id, "power"))

        self.cast(game, "B", bounce, [auto.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[auto.object_id].zone)
        self.assertEqual({"construct"}, self._types(game, auto))
        self._cast_entry(game, auto, {"C": 3}, "bear")
        current = game.state.cards[auto.object_id]
        self.assertNotEqual(first_incarnation, current.logical_object_id)
        self.assertEqual({"bear", "construct"}, self._types(game, auto))
        self.assertNotIn("+1/+1", current.counters)
        self.assertEqual(2, game.engine._numeric_stat(auto.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(goblin.object_id, "power"))
        self.assertEqual(3, game.engine._numeric_stat(bear.object_id, "power"))
        self.replay(game, load=True)

    def test_paid_throne_retains_printed_type_and_its_independent_trigger_envelope(self):
        game = self.session(61412502)
        throne = self.add(game.engine, THRONE, zone="hand")
        chief = self.add(game.engine, CHIEF, zone="hand")
        for card in (throne, chief):
            self.assert_closed(game, card)
        self._start(game, {"C": 6, "R": 2})
        self._cast_entry(game, throne, {"C": 4}, "goblin")
        self.assertEqual({"golem", "goblin"}, self._types(game, throne))
        self.priority(game, "A")
        row = self.action(game, "A", chief)
        self._choose(game, {"action_id": row["id"], "pay": "manual", "payment": {"C": 2, "R": 2}})
        self.resolve_top(game)
        pending = game.state.pending_decision
        if pending.kind == "trigger.order":
            group = next(g for g in game.state.pending_trigger_batches[0].groups if g.controller == "A")
            self._choose(game, {"action_id": "order", "triggers": [item.ref for item in group.items]})
        self.assertEqual(2, self.trigger_count(game, chief))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        tokens = [c for c in game.state.cards.values() if c.is_token and c.zone == "battlefield"]
        self.assertEqual(4, len(tokens))
        self.assertTrue(all("goblin" in self._types(game, token) for token in tokens))
        self.replay(game, load=True)

    def test_omitting_the_link_keeps_the_plain_entry_owner_fail_closed(self):
        with patch.object(entry_designation_templates, "_compose_self_chosen_subtype_entry", side_effect=lambda face: face):
            for name in (AUTOMATON, THRONE):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertTrue(any(s.kind == "entry_designation" for s in ir.material_residuals))
                self.assertNotEqual("exact", ir.status)
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"])

    def test_genuine_self_type_withdrawal_preserves_external_warrior_entry(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped chosen self type authority withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (AUTOMATON, THRONE):
            rejected, _ = register_card(self.db, registry, name)
            self.assertFalse(rejected["strict_capability_ready"])
        retained, _ = register_card(self.db, registry, PARAGON)
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
