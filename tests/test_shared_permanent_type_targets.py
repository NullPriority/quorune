"""The printed target pairs compare current public card types at each boundary."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_forms import (
    SHARED_PERMANENT_TYPE_PAIR_CAPABILITY as CAPABILITY, SHARED_PERMANENT_TYPE_PAIR_FIELD, SharedPermanentTypeScope,
)
from quorune.targets import target_plan
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ROLE = "Role Reversal"
LEGER = "Legerdemain"
SWITCH = "Switcheroo"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class SharedPermanentTypeTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "permanent-control-exchange-cards.json": {ROLE, SWITCH},
            "ability-source-counter-cards.json": {"Song of the Dryads", "Sol Ring", "Twincast", "Vedalken Orrery"},
            "attached-destruction-set-cards.json": {"Fervor", "Forest", "Grizzly Bears", "Island"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Isamaru, Hound of Konda"},
            "entry-equipment-attachment-cards.json": {"Cloak and Dagger"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
        }
        paths = [FIXTURES / "shared-permanent-type-target-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, source
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            target = folder / source
            target.write_text(json.dumps(doc), encoding="utf-8")
            paths.append(target)
        database = folder / "shared-permanent-types.sqlite3"
        build_fixture_database(paths, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Shared permanent type targets", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_intact_originals_require_two_distinct_targets_and_their_exact_type_scope(self):
        for name, scope in ((ROLE, SharedPermanentTypeScope.PERMANENT),
                            (LEGER, SharedPermanentTypeScope.ARTIFACT_OR_CREATURE)):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, programs = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = programs.programs_for_oracle(card.oracle_id)
                schema = program.target_schema
                plan = target_plan(schema)
                self.assertEqual(scope, plan.shared_permanent_type_pair.scope)
                self.assertEqual(len(scope.types), 6 if name == ROLE else 2)
                self.assertTrue(plan.globally_distinct)
                for updates in ({"schema_version": True}, {"left": "second"}, {"scope": "kindred"}):
                    bad = deepcopy(schema)
                    bad[SHARED_PERMANENT_TYPE_PAIR_FIELD].update(updates)
                    with self.assertRaises(ValueError):
                        target_plan(bad)
                deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
                removed = replace(program, coverage=(), capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                rejected = bind_semantic_program_runtime(removed, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, rejected["blockers"])

    def _ready(self, game, cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _reject(self, game, command, *, actor="A"):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + actor, command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _accept(self, game, command, *, actor="A"):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _controllers(self, game, *cards):
        return tuple(game.state.cards[card.object_id].controller for card in cards)

    def test_role_offer_keeps_live_compatible_pairs_and_excludes_kindred_as_a_permanent_type(self):
        game = self.session(1104110)
        spell = self.add(game.engine, ROLE, zone="hand")
        cloak = self.add(game.engine, "Cloak and Dagger", seat="B")
        shenanigans = self.add(game.engine, "Boggart Shenanigans", seat="C")
        self._ready(game, (spell,), {"U": 2, "R": 1})
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(a["id"] == "cast:" + spell.ref for a in actions))
        self._reject(game, {"action_id": "cast:" + spell.ref, "targets": [cloak.ref, shenanigans.ref], "pay": "auto"})

        game = self.session(1104111)
        spell = self.add(game.engine, ROLE, zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        both = self.add(game.engine, "Universal Automaton", seat="C")
        creature = self.add(game.engine, "Grizzly Bears", seat="D")
        unrelated = self.add(game.engine, "Fervor")
        private = self.add(game.engine, "Forest", seat="D", zone="hand", ref="shared-type-private-hand")
        self._ready(game, (spell,), {"U": 2, "R": 1})
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        action = self.action(game, "A", spell)
        for group in action["target_schema"]["groups"]:
            self.assertEqual({artifact.ref, both.ref, creature.ref}, set(group["legal_refs"]))
            self.assertNotIn(unrelated.ref, group["legal_refs"])
        command = {"action_id": action["id"], "pay": "auto"}
        self._reject(game, {**command, "targets": [artifact.ref, creature.ref]})
        self._reject(game, {**command, "targets": [both.ref, both.ref]})
        self._reject(game, {**command, "targets": [artifact.ref, both.ref]}, actor="D")
        self._accept(game, {**command, "targets": [artifact.ref, both.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("C", "B", "D"), self._controllers(game, artifact, both, creature))
        self.replay(game, load=True)

    def test_legerdemain_shares_only_its_printed_two_types_and_exchanges_foreign_creatures(self):
        game = self.session(1104112)
        spell = self.add(game.engine, LEGER, zone="hand")
        both = self.add(game.engine, "Universal Automaton", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        enchantment = self.add(game.engine, "Fervor", seat="B", ref="leger-first-enchantment")
        other = self.add(game.engine, "Fervor", seat="C", ref="leger-second-enchantment")
        payment = self.db.lookup(LEGER, fuzzy=False).mana_cost
        self.assertEqual("{2}{U}{U}", payment)
        self._ready(game, (spell,), {"C": 2, "U": 2})
        action = self.action(game, "A", spell)
        command = {"action_id": action["id"], "pay": "auto"}
        self._reject(game, {**command, "targets": [enchantment.ref, other.ref]})
        self._accept(game, {**command, "targets": [both.ref, creature.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("C", "B"), self._controllers(game, both, creature))
        self.replay(game, load=True)

    def test_actual_response_breaks_the_pair_using_current_permanent_types(self):
        for card_name in (ROLE, LEGER):
            with self.subTest(card_name=card_name):
                game = self.session(1104113 if card_name == ROLE else 1104114)
                spell = self.add(game.engine, card_name, zone="hand")
                left = self.add(game.engine, "Sol Ring", seat="B")
                right = self.add(game.engine, "Universal Automaton", seat="C")
                orrery = self.add(game.engine, "Vedalken Orrery", seat="B")
                song = self.add(game.engine, "Song of the Dryads", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                self.add(game.engine, "Island", seat="B", ref="pair-response-first")
                self.add(game.engine, "Island", seat="B", ref="pair-response-second")
                mana = {"U": 2, "R": 1} if card_name == ROLE else {"C": 2, "U": 2}
                self._ready(game, (spell, song, orrery), mana)
                self.cast(game, "A", spell, [left.ref, right.ref])
                self.cast(game, "B", song, [right.ref])
                self.resolve_top(game)
                types, _, _ = game.engine._effective_type_parts(game.state.cards[right.object_id])
                self.assertEqual({"land"}, types)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(("B", "C"), self._controllers(game, left, right))
                illegal = [event.details["target"] for event in game.state.events
                           if event.code == "target.illegal" and event.details.get("reason") == "shared_permanent_type_changed"]
                self.assertCountEqual([left.ref, right.ref] if card_name == ROLE else [right.ref], illegal)
                self.replay(game, load=True)

    def test_actual_copy_rejects_a_broken_pair_and_may_select_a_different_shared_type(self):
        game = self.session(1104116)
        spell = self.add(game.engine, ROLE, zone="hand")
        copier = self.add(game.engine, "Twincast", seat="B", zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        both = self.add(game.engine, "Universal Automaton", seat="C")
        creature = self.add(game.engine, "Grizzly Bears", seat="D")
        self.add(game.engine, "Island", seat="B", ref="pair-copy-land-first")
        self.add(game.engine, "Island", seat="B", ref="pair-copy-land-second")
        self._ready(game, (spell, copier), {"U": 2, "R": 1})
        self.cast(game, "A", spell, [artifact.ref, both.ref])
        original, = game.state.stack
        self.cast(game, "B", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertIn(SHARED_PERMANENT_TYPE_PAIR_FIELD,
                      json.dumps(game.packet("pilot:B", full=True)["decision"]["ctx"]))
        decision = {"action_id": "choose"}
        self._reject(game, {**decision, "targets": [artifact.ref, creature.ref]}, actor="B")
        self._reject(game, {**decision, "targets": [creature.ref, both.ref]}, actor="D")
        game = self.replay(game, load=True)
        self._accept(game, {**decision, "targets": [creature.ref, both.ref]}, actor="B")
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual([creature.ref, both.ref], copied.targets)
        self.resolve_top(game)
        self.assertEqual(("B", "D", "C"), self._controllers(game, artifact, both, creature))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("D", "B", "C"), self._controllers(game, artifact, both, creature))
        self.replay(game, load=True)

    def test_removing_the_relation_fails_closed_for_both_actual_complete_programs(self):
        from quorune.compiler import fixed_control_templates

        original = fixed_control_templates.permanent_control_exchange_template
        def omit(text, **scope):
            compiled = original(text, **scope)
            if compiled is None or SHARED_PERMANENT_TYPE_PAIR_FIELD not in compiled[2]:
                return compiled
            template, effects, target, mechanics = compiled
            return template, effects, {k: v for k, v in target.items() if k != SHARED_PERMANENT_TYPE_PAIR_FIELD}, mechanics

        with patch.object(fixed_control_templates, "permanent_control_exchange_template", side_effect=omit), patch(
            "quorune.compiler.resolution_effect_templates.permanent_control_exchange_template", side_effect=omit,
        ):
            for name in (ROLE, LEGER):
                program, _ = register_card(self.db, self.registry, name)
                self.assertFalse(program["strict_capability_ready"], program)

    def test_genuine_type_pair_withdrawal_retains_the_existing_unqualified_exchange(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped exact shared-type target pair withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (ROLE, LEGER):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, SWITCH)
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
