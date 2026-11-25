"""The source's retained creature type qualifies two ordinary target slots."""

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
from quorune.compiler import entry_designation_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.designation_prevention import DESIGNATION_INFORMATION_FIELD, source_entry_designation_value
from quorune.entry_designations import CHOSEN_CREATURE_TYPE_TARGET_PREDICATE as PREDICATE, EntryDesignationKind
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.chosen_type_target_shapes import (
    CHOSEN_CREATURE_TYPE_TARGET_CAPABILITY as CAPABILITY, chosen_creature_type_target_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


BOON = "Kindred Boon"
RUBBLE = "From the Rubble"
DOOR = "Door of Destinies"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class LinkedCreatureTypeTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Plains", "Generic Bound Growth",
                "Disenchant", "Grizzly Bears",
            },
            "temporary-source-keyword-cards.json": {"Mistform Wall", "Universal Automaton"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Goblin Piker"},
            "chosen-type-event-cards.json": {DOOR},
        }
        parts = [FIXTURES / "linked-creature-type-target-cards.json"]
        for fixture, names in support.items():
            document = json.loads((FIXTURES / fixture).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] in ids]
            path = folder / fixture
            path.write_text(json.dumps(document), encoding="utf-8")
            parts.append(path)
        database = folder / "linked-creature-type-targets.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked Creature Target", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_originals_bind_complete_and_reject_missing_target_scope_authority(self):
        for name in (BOON, RUBBLE):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                            if CAPABILITY in p.capability_dependencies]
                self.assertEqual(PREDICATE, program.target_schema["predicate"])
                unlabelled = replace(program, coverage=[])
                self.assertTrue(bind_semantic_program_runtime(
                    unlabelled, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
                changed = replace(unlabelled, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])
                schema = dict(program.target_schema)
                for bad in ({"groups": [schema]}, {**schema, "count": True}, {**schema, "count": 2},
                            {**schema, "owner_relation": "opponent"}):
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        chosen_creature_type_target_runtime_dependencies(replace(program, target_schema=bad))

    def test_unowned_entry_choices_and_unsupported_target_results_do_not_admit(self):
        for name in (BOON, RUBBLE):
            card = self.db.lookup(name, fuzzy=False)
            source, remainder = card.oracle_text.split("\n", 1)
            for replacement in ("", source.replace("creature type", "color") + "\n", source + "\n" + source + "\n"):
                with self.subTest(name=name, source=replacement):
                    ir = compile_oracle_card(replace(card, oracle_text=replacement + remainder),
                                             capability_registry=self.registry, capability_profile=PROFILE)
                    self.assertNotEqual("exact", ir.status)
                    self.assertFalse(any(node.exact and (node.target_schema or {}).get("predicate") == PREDICATE
                                         for face in ir.faces for node in face.nodes))
        for body in ("Put a divinity counter on target creature of the chosen type.",
                     "Put two divinity counters on target creature you control of the chosen type.",
                     "Put a flying counter on target creature you control of the chosen type.",
                     "Return target creature card of the chosen type from your graveyard to your hand.",
                     "Return target creature card of the chosen type from an opponent's graveyard to the battlefield with a finality counter on it."):
            self.assertIsNone(entry_designation_templates.source_chosen_creature_type_target_template(body), body)

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:" + actor, command)
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose(self, game, command):
        self._reject(game, "D", command)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def _start(self, game, mana):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _cast_source(self, game, source, subtype="goblin"):
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        row, = [option for option in choices if option["id"].endswith(":" + subtype)]
        self._choose(game, {"action_id": "choose", "replacement": row["id"]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(subtype, game.state.cards[source.object_id].annotations["chosen_creature_type"])

    def _offers(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]

    def _activate(self, game, source, target, invalid=(), *, mana=None):
        row, = self._offers(game, source)
        command = {"action_id": row["id"], "targets": [target.ref], "pay": "auto"}
        if mana is not None:
            command.update(pay="manual", payment=mana)
        for rejected in invalid:
            self._reject(game, "A", {**command, "targets": [rejected.ref]})
        self._choose(game, command)
        return row

    def _wall_type(self, game, wall, subtype):
        offer, = self._offers(game, wall)
        self._choose(game, {"action_id": offer["id"], "pay": "manual", "payment": {"C": 1}})
        self.until(game, lambda s: s.state.pending_decision.kind == "semantic.choice")
        choice, = game.state.pending_decision.payload_by_actor["A"]["legal_actions"]
        self._choose(game, {"action_id": choice["id"], "creature_type": subtype})

    def _removal_fixture(self, game):
        spell = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        for i in range(2):
            self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"linked-type-removal-land-{i}")
        return spell

    def test_real_boon_uses_own_matching_creatures_and_keeps_the_link_after_source_departs(self):
        game = self.session(6070201)
        boon = self.add(game.engine, BOON, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker")
        changeling = self.add(game.engine, "Universal Automaton")
        other = self.add(game.engine, "Grizzly Bears")
        other.counters["divinity"] = 1
        kindred = self.add(game.engine, "Boggart Shenanigans")
        foreign = self.add(game.engine, "Goblin Piker", seat="C", ref="linked-foreign-goblin")
        removal = self._removal_fixture(game)
        self.assert_closed(game, boon)
        self._start(game, {"C": 4, "W": 4})
        self._cast_source(game, boon)
        self.assertIn("indestructible", game.engine._combat_keywords(other))
        row, = self._offers(game, boon)
        self.assertEqual({goblin.ref, changeling.ref}, set(row["target_schema"]["legal_refs"]))
        self._activate(game, boon, changeling, (other, kindred, foreign), mana={"C": 1, "W": 1})
        item = game.state.stack[-1]
        self.assertEqual("goblin", source_entry_designation_value(item, EntryDesignationKind.CREATURE_TYPE))
        self.assertNotIn("divinity", game.state.cards[changeling.object_id].counters)
        self.cast(game, "B", removal, [boon.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[boon.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[changeling.object_id].counters["divinity"])
        self.assertNotIn("indestructible", game.engine._combat_keywords(game.state.cards[changeling.object_id]))
        self.replay(game, load=True)

    def test_real_boon_rechecks_the_target_type_before_placing_its_counter(self):
        game = self.session(6070202)
        boon = self.add(game.engine, BOON, zone="hand")
        wall = self.add(game.engine, "Mistform Wall")
        self.assert_closed(game, boon)
        self._start(game, {"C": 6, "W": 4})
        self._cast_source(game, boon)
        self.assertEqual([], self._offers(game, boon))
        self._wall_type(game, wall, "Goblin")
        self.assertIsNone(self.resolve(game))
        self.assertIn("goblin", game.engine._effective_type_parts(game.state.cards[wall.object_id])[1])
        self._activate(game, boon, wall, mana={"C": 1, "W": 1})
        self._wall_type(game, wall, "Cat")
        self.assertIsNone(self.resolve(game))
        self.assertIn("cat", game.engine._effective_type_parts(game.state.cards[wall.object_id])[1])
        self.assertNotIn("divinity", game.state.cards[wall.object_id].counters)
        self.replay(game, load=True)

    def test_boon_without_an_entry_type_neither_offers_nor_accepts_activation(self):
        game = self.session(6070203)
        boon = self.add(game.engine, BOON)
        goblin = self.add(game.engine, "Goblin Piker")
        self.assert_closed(game, boon)
        self._start(game, {"C": 1, "W": 1})
        self.assertEqual([], self._offers(game, boon))
        ability, = game.engine._activated_abilities(boon)
        self._reject(game, "A", {"action_id": f"activate:{boon.ref}:{ability.ability_id}",
                                 "targets": [goblin.ref], "pay": "auto"})

    def test_real_rubble_target_choice_is_own_graveyard_only_and_replays_through_source_loss(self):
        game = self.session(6070204)
        rubble = self.add(game.engine, RUBBLE, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker", zone="graveyard")
        changeling = self.add(game.engine, "Universal Automaton", zone="graveyard")
        nonmatching = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        kindred = self.add(game.engine, "Boggart Shenanigans", zone="graveyard")
        foreign = self.add(game.engine, "Goblin Piker", seat="B", zone="graveyard", ref="rubble-foreign-goblin")
        secret = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="rubble-hidden")
        removal = self._removal_fixture(game)
        self.assert_closed(game, rubble)
        self._start(game, {"C": 4, "W": 2})
        self._cast_source(game, rubble)
        self.until(game, lambda s: s.state.pending_decision.kind == "semantic.target")
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual({goblin.ref, changeling.ref}, set(decision["ctx"]["target_schema"]["legal_refs"]))
        for actor in "BCD":
            self.assertIsNone(game.packet("pilot:" + actor, full=True)["decision"])
        self.assertNotIn(secret.ref, json.dumps(decision))
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "targets": [goblin.ref]}
        for denied in (nonmatching, kindred, foreign):
            self._reject(game, "A", {**command, "targets": [denied.ref]})
        self._choose(game, command)
        self.assertEqual("goblin", source_entry_designation_value(
            game.state.stack[-1], EntryDesignationKind.CREATURE_TYPE,
        ))
        self.cast(game, "B", removal, [rubble.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[goblin.object_id]
        self.assertEqual(("battlefield", "A", 1), (returned.zone, returned.controller, returned.counters["finality"]))
        self.assertEqual("graveyard", game.state.cards[changeling.object_id].zone)
        self.replay(game, load=True)

    def test_real_rubble_with_no_matching_card_cannot_announce_its_mandatory_target(self):
        game = self.session(6070205)
        rubble = self.add(game.engine, RUBBLE, zone="hand")
        self.add(game.engine, "Grizzly Bears", zone="graveyard")
        self.add(game.engine, "Goblin Piker", seat="B", zone="graveyard")
        self.assert_closed(game, rubble)
        self._start(game, {"C": 4, "W": 2})
        self._cast_source(game, rubble)
        self.until(game, lambda s: s.state.step == "end_step" and s.state.pending_decision.kind == "priority")
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_source_target_capture_is_incarnation_checked_and_omission_restores_only_its_residual(self):
        game = self.session(6070206)
        boon = self.add(game.engine, BOON, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker")
        self.assert_closed(game, boon)
        self._start(game, {"C": 3, "W": 3})
        self._cast_source(game, boon)
        self._activate(game, boon, goblin)
        item = deepcopy(game.state.stack[-1])
        item.context[DESIGNATION_INFORMATION_FIELD]["source_logical_object_id"] = "other"
        with self.assertRaises(ValueError):
            source_entry_designation_value(item, EntryDesignationKind.CREATURE_TYPE)
        with patch.object(entry_designation_templates, "source_chosen_creature_type_target_template", return_value=None):
            for name in (BOON, RUBBLE):
                original = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual(1, len(ir.material_residuals))
                self.assertEqual("zone.change", ir.faces[0].nodes[0].event)

    def test_genuine_chosen_type_target_withdrawal_keeps_older_entry_choices(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped linked type target authority withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (BOON, RUBBLE):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], (name, report))
        report, _ = register_card(self.db, registry, DOOR)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])


if __name__ == "__main__":
    unittest.main()
