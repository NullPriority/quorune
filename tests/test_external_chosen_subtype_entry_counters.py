"""An external fixed +1/+1 entry counter reads its source's retained type."""

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
from quorune.compiler.external_entry_counters import external_entry_counter_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.external_entry_counter_model import (
    EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_CAPABILITY as CAPABILITY,
    EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_HANDLER as HANDLER,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MIMIC = "Metallic Mimic"
XENO = "Xenograft"
AUTO = "Adaptive Automaton"
RESTORE = "Brilliant Restoration"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ExternalChosenSubtypeEntryCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains", "Grizzly Bears"},
            "entry-designation-cards.json": {MIMIC, AUTO},
            "controlled-chosen-subtype-addition-cards.json": {XENO},
            "chosen-type-entry-counter-cards.json": {RESTORE},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "creature-subtype-addition-cards.json": {"Goblin Piker"},
        }
        parts = []
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {r["name"] for r in doc["cards"]} == names
            ids = {r["oracle_id"] for r in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", []) if row["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(doc), encoding="utf-8")
            parts.append(path)
        db_path = folder / "external-chosen-entry.sqlite3"
        build_fixture_database(parts, db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("External Chosen Counter", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_original_binds_self_addition_and_exact_external_counter_with_required_authority(self):
        card = self.db.lookup(MIMIC, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"zone.change", "characteristics.evaluate"}, {n.event for f in ir.faces for n in f.nodes})
        report, semantics = register_card(self.db, self.registry, MIMIC)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                    if CAPABILITY in p.capability_dependencies]
        self.assertEqual(HANDLER, program.handlers[0]["handler_id"])
        unlabelled = replace(program, coverage=[])
        self.assertTrue(bind_semantic_program_runtime(
            unlabelled, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
        omitted = replace(unlabelled, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        rejected = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(rejected["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, rejected["blockers"])
        body = card.oracle_text.splitlines()[-1]
        for text in (body.replace("other creature", "creature"), body.replace("you control", "an opponent controls"),
                     body.replace("+1/+1", "time"), body.replace("an additional", "two additional"),
                     body + " It gains haste."):
            self.assertIsNone(external_entry_counter_handler(text, source_name=MIMIC), text)

    def _start(self, game, mana):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _command(self, game, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def _pay(self, game, card, payment):
        self.priority(game, "A")
        row = self.action(game, "A", card)
        self._command(game, {"action_id": row["id"], "pay": "manual", "payment": payment})

    def _choose_goblin(self, game):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choice, = (row["id"] for row in options if row["id"].endswith(":goblin"))
        self._command(game, {"action_id": "choose", "replacement": choice})
        self.assertIsNone(self.resolve(game))

    def _counter(self, game, card):
        return game.state.cards[card.object_id].counters.get("+1/+1", 0)

    def test_paid_mimic_counters_match_the_prospective_creature_type(self):
        game = self.session(61412600)
        mimic = self.add(game.engine, MIMIC, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", zone="hand", ref="mimic-nonmatching")
        second = self.add(game.engine, "Grizzly Bears", zone="hand", ref="mimic-prospective-match")
        xeno = self.add(game.engine, XENO, zone="hand")
        for card in (mimic, goblin, xeno):
            self.assert_closed(game, card)
        self._start(game, {"C": 9, "U": 1, "R": 1, "G": 2})
        self._pay(game, mimic, {"C": 2})
        self._choose_goblin(game)
        self.assertEqual(0, self._counter(game, mimic))
        self.assertEqual({"shapeshifter", "goblin"}, game.engine._effective_type_parts(game.state.cards[mimic.object_id])[1])
        self._pay(game, goblin, {"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self._counter(game, goblin))
        self._pay(game, bear, {"C": 1, "G": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self._counter(game, bear))
        self._pay(game, xeno, {"C": 4, "U": 1})
        self._choose_goblin(game)
        self._pay(game, second, {"C": 1, "G": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self._counter(game, second))
        self.assertEqual(0, self._counter(game, bear))
        self.replay(game, load=True)

    def test_actual_simultaneous_restore_waits_to_apply_mimic_until_later_entry(self):
        game = self.session(61412601)
        restore = self.add(game.engine, RESTORE, zone="hand")
        mimic = self.add(game.engine, MIMIC, zone="graveyard")
        same = self.add(game.engine, "Universal Automaton", zone="graveyard", ref="mimic-same-entry")
        later = self.add(game.engine, "Universal Automaton", zone="hand", ref="mimic-later-entry")
        for card in (restore, mimic, same):
            self.assert_closed(game, card)
        self._start(game, {"C": 4, "W": 4})
        self._pay(game, restore, {"C": 3, "W": 4})
        pending = self.resolve(game)
        self.assertEqual("replacement.order", pending.kind)
        self.assertEqual("graveyard", game.state.cards[same.object_id].zone)
        game = self.replay(game, load=True)
        self._choose_goblin(game)
        self.assertEqual("battlefield", game.state.cards[same.object_id].zone)
        self.assertEqual(0, self._counter(game, same))
        self.assertEqual(0, self._counter(game, mimic))
        self._pay(game, later, {"C": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self._counter(game, later))
        self.replay(game, load=True)

    def test_actual_source_without_an_entry_choice_produces_no_counter(self):
        game = self.session(61412602)
        mimic = self.add(game.engine, MIMIC)
        goblin = self.add(game.engine, "Goblin Piker", zone="hand")
        self.assert_closed(game, mimic)
        self._start(game, {"C": 1, "R": 1})
        self._pay(game, goblin, {"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self._counter(game, goblin))
        self.replay(game, load=True)

    def test_omitting_the_linked_counter_restores_the_actual_mimic_residual(self):
        with patch("quorune.compiler.external_entry_counters._chosen_subtype_external_counter_handler", return_value=None):
            card = self.db.lookup(MIMIC, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(any("additional +1/+1 counter" in r.text for r in ir.material_residuals))
            report, _ = register_card(self.db, self.registry, MIMIC)
            self.assertFalse(report["strict_capability_ready"])

    def test_genuine_counter_withdrawal_retains_the_independent_selected_subtype_sources(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped external chosen subtype counter withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        retired, _ = register_card(self.db, registry, MIMIC)
        self.assertFalse(retired["strict_capability_ready"])
        for name in (AUTO, XENO):
            retained, _ = register_card(self.db, registry, name)
            self.assertTrue(retained["strict_capability_ready"], (name, retained["blockers"]))


if __name__ == "__main__":
    unittest.main()
