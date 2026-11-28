from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.modal_templates import (
    FIXED_NONREPEATING_MODAL_CAPABILITY,
    FIXED_NONREPEATING_MODAL_MECHANIC,
)
from quorune.compiler.modal_program_closure import (
    is_closed_fixed_modal_program,
)
from quorune.deck import DeckDefinition, DeckEntry, DeckLoader
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.modal_selection import canonical_modes
from quorune.targets import mode_effects, target_plan
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-nonrepeating-modal-cards.json"
)


def current_capabilities() -> CapabilityRegistry:
    value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry = CapabilityRegistry(value)
    registry.mark_evidence_verified("0" * 64)
    return registry


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-nonrepeating-modal.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedNonrepeatingModalCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = current_capabilities()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, name: str, *, registry=None):
        return compile_oracle_card(
            self.db.lookup(name),
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_runtime_canonicalizes_closed_mode_sets_before_effects(self):
        schema = {
            "min_modes": 2,
            "max_modes": 2,
            "modes": {
                "mode_1": {
                    "groups": [{"id": "target_1", "min": 1, "max": 1}],
                    "effects": [{"op": "life", "player": "$target.0", "delta": 2}],
                },
                "mode_2": {"groups": [], "effects": [{"op": "draw", "count": 1}]},
                "mode_3": {
                    "groups": [{"id": "target_3", "min": 1, "max": 1}],
                    "effects": [{"op": "damage", "target": "$target.0", "amount": 2}],
                },
            },
        }

        selected_modes = canonical_modes(schema, ["mode_3", "mode_1"])
        plan = target_plan(schema, selected_modes)
        self.assertEqual(("mode_1", "mode_3"), plan.modes)
        self.assertEqual(
            ("target_1", "target_3"),
            tuple(group.group_id for group in plan.groups),
        )
        self.assertEqual(
            [
                {"op": "life", "player": "$target.0", "delta": 2},
                {"op": "damage", "target": "$target.1", "amount": 2},
            ],
            mode_effects(schema, selected_modes),
        )
        for invalid in (
            ["mode_1"],
            ["mode_1", "mode_2", "mode_3"],
            ["mode_1", "mode_1"],
            ["mode_1", "unknown"],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                canonical_modes(schema, invalid)

    def test_fixed_modal_programs_compile_across_shared_contexts(self):
        expected = {
            "Crush Contraband": ("spell_ability", 1, 2, 2),
            "Farewell": ("spell_ability", 1, 4, 4),
            "Kolaghan's Command": ("spell_ability", 2, 2, 4),
            "Dawnbringer Cleric": ("triggered_ability", 1, 1, 3),
            "Cankerbloom": ("activated_ability", 1, 1, 3),
            "Balm of Restoration": ("activated_ability", 1, 1, 2),
        }
        for name, (kind, minimum, maximum, mode_total) in expected.items():
            with self.subTest(name=name):
                ir = self.compile(name)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertTrue(
                    str(node.template_id).startswith(
                        "fixed-nonrepeating-modal-"
                    )
                )
                self.assertEqual((), node.effects)
                self.assertIn(
                    FIXED_NONREPEATING_MODAL_MECHANIC,
                    node.mechanics,
                )
                self.assertIn(
                    FIXED_NONREPEATING_MODAL_CAPABILITY,
                    node.capability_dependencies,
                )
                schema = node.target_schema
                self.assertEqual(minimum, schema["min_modes"])
                self.assertEqual(maximum, schema["max_modes"])
                self.assertEqual(mode_total, len(schema["modes"]))
                self.assertEqual(
                    node.text,
                    ir.faces[0].oracle_text[node.span.start : node.span.end],
                )
                group_ids = [
                    group["id"]
                    for definition in schema["modes"].values()
                    for group in definition["groups"]
                ]
                self.assertEqual(len(group_ids), len(set(group_ids)))
                self.assertTrue(
                    all(
                        definition["effects"]
                        and definition["mechanics"]
                        for definition in schema["modes"].values()
                    )
                )

    def test_modal_selection_grammar_and_shape_fail_closed(self):
        unsupported = (
            "Choose two. You may choose the same mode more than once.\n"
            "• Destroy target artifact.\n• You gain 2 life.",
            "Choose a mode at random —\n"
            "• Destroy target artifact.\n• You gain 2 life.",
            "Choose one. If this spell was kicked, choose any number instead —\n"
            "• Destroy target artifact.\n• You gain 2 life.",
            "Choose one or both —\n"
            "• Destroy target artifact.\n• You gain 2 life.\n"
            "Entwine {2}",
            "Escalate {2}\nChoose one or both —\n"
            "• Destroy target artifact.\n• You gain 2 life.",
        )
        base = self.db.lookup("Crush Contraband")
        for text in unsupported:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(base, oracle_text=text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        node = self.compile("Kolaghan's Command").faces[0].nodes[0]
        schema = copy.deepcopy(node.target_schema)
        mutations = []
        value = copy.deepcopy(schema)
        value["min_modes"] = 1
        mutations.append(value)
        value = copy.deepcopy(schema)
        value["max_modes"] = 3
        mutations.append(value)
        value = copy.deepcopy(schema)
        value["modes"]["mode_1"]["groups"][0]["id"] = "target_1"
        mutations.append(value)
        value = copy.deepcopy(schema)
        value["modes"]["mode_2"]["effects"] = []
        mutations.append(value)
        value = copy.deepcopy(schema)
        value["modes"]["mode_3"]["repeatable"] = True
        mutations.append(value)
        for target_schema in mutations:
            with self.subTest(target_schema=target_schema):
                self.assertEqual(
                    (),
                    capability_dependencies_for_node(
                        effects=(),
                        target_schema=target_schema,
                        mechanic_ids=node.mechanics,
                    ),
                )

    def test_modal_dependency_and_compiler_mutations_fail_closed(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == FIXED_NONREPEATING_MODAL_CAPABILITY
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused modal dependency"]
        blocked = CapabilityRegistry(value)
        blocked.mark_evidence_verified("0" * 64)
        self.assertNotEqual(
            "exact",
            self.compile("Farewell", registry=blocked).status,
        )

        def assert_exact() -> None:
            self.assertEqual("exact", self.compile("Farewell").status)
            self.assertEqual("exact", self.compile("Cankerbloom").status)

        assert_exact()
        with patch(
            "quorune.compiler.whole_modal_templates.fixed_nonrepeating_modal_template",
            return_value=None,
        ), patch(
            "quorune.compiler.modal_context_nodes."
            "fixed_nonrepeating_modal_template",
            return_value=None,
        ):
            self.assertNotEqual("exact", self.compile("Farewell").status)
            self.assertNotEqual("exact", self.compile("Cankerbloom").status)

    def test_modal_activation_closure_validates_typed_source_cost(self):
        programs = generated_programs(
            self.db,
            self.db.lookup("Balm of Restoration"),
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        program = next(value for value in programs if value.event == "activate")
        self.assertIn(
            "activation.source_zone_change.fixed",
            program.capability_dependencies,
        )
        self.assertTrue(is_closed_fixed_modal_program(program))

        missing_capability = copy.deepcopy(program)
        missing_capability.capability_dependencies = [
            value
            for value in program.capability_dependencies
            if value != "activation.source_zone_change.fixed"
        ]
        self.assertFalse(is_closed_fixed_modal_program(missing_capability))

        mismatched_descriptor = copy.deepcopy(program)
        mismatched_descriptor.handlers[-1]["ability"][
            "sacrifice_source"
        ] = False
        self.assertFalse(is_closed_fixed_modal_program(mismatched_descriptor))


class FixedNonrepeatingModalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session_with_replacement(
        self,
        *,
        original: str,
        replacement: str,
        seed: int,
    ):
        mishra = copy.deepcopy(self.mishra)
        next(
            entry
            for entry in mishra.entries
            if entry.board == "mainboard" and entry.name == original
        ).name = replacement
        session = make_session(
            self.db,
            mishra,
            copy.deepcopy(self.zimone),
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, *, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def prepare_priority(session, *, mana: dict[str, int]):
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update(mana)
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            pass_current(session)

    def assert_replays(self, session, label: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_choose_two_spell_commits_ordered_modes_targets_and_replays(self):
        session = self.session_with_replacement(
            original="Abrade",
            replacement="Kolaghan's Command",
            seed=119001,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Kolaghan's Command")
        artifact = self.card(engine, owner="B", name="Sol Ring")
        creature = self.card(engine, owner="B", name="Seedborn Muse")
        engine.move_card(source.object_id, "hand", controller="A", log=False)
        engine.move_card(artifact.object_id, "battlefield", controller="B", log=False)
        engine.move_card(creature.object_id, "battlefield", controller="B", log=False)
        self.prepare_priority(session, mana={"C": 1, "B": 1, "R": 1})

        action = next(
            row
            for row in session.packet("pilot:A", full=True)["decision"]["ctx"][
                "legal"
            ]["actions"]
            if row["id"] == f"cast:{source.ref}"
        )
        schema = action["target_schema"]
        self.assertEqual(2, schema["min_modes"])
        self.assertEqual(2, schema["max_modes"])
        self.assertEqual(
            ["mode_2", "mode_3", "mode_4"],
            schema["legal_modes"],
        )
        for seat in "BCD":
            self.assertNotIn(
                action["id"],
                str(session.packet(f"pilot:{seat}", full=True)),
            )

        before = authoritative_state_hash(engine.state)
        for modes, targets in (
            (["mode_3"], [artifact.ref]),
            (["mode_3", "mode_3"], [artifact.ref, artifact.ref]),
            (["mode_3", "mode_4"], [creature.ref, artifact.ref]),
            (["mode_2", "mode_3", "mode_4"], [artifact.ref, creature.ref]),
            (["mode_3", "unknown"], [artifact.ref, creature.ref]),
        ):
            rejected = session.act(
                "pilot:A",
                {
                    "action_id": action["id"],
                    "modes": modes,
                    "targets": targets,
                    "pay": "manual",
                    "payment": {"C": 1, "B": 1, "R": 1},
                },
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))

        source = engine.state.cards[source.object_id]
        artifact = engine.state.cards[artifact.object_id]
        creature = engine.state.cards[creature.object_id]
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_4", "mode_3"],
                "targets": [artifact.ref, creature.ref],
                "pay": "manual",
                "payment": {"C": 1, "B": 1, "R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(["mode_3", "mode_4"], engine.state.stack[-1].modes)
        self.pass_stack(session)
        self.assertEqual("graveyard", artifact.zone)
        self.assertEqual(2, creature.marked_damage)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "fixed-choose-two-spell")

    def test_modal_graveyard_return_replacement_resumes_then_damages_and_replays(
        self,
    ):
        session = self.session_with_replacement(
            original="Abrade",
            replacement="Kolaghan's Command",
            seed=119004,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Kolaghan's Command")
        commander = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.is_commander
        )
        creature = self.card(engine, owner="B", name="Seedborn Muse")
        engine.move_card(source.object_id, "hand", controller="A", log=False)
        engine.move_card(
            commander.object_id,
            "graveyard",
            controller="A",
            log=False,
        )
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        self.assertTrue(engine._stabilize())
        self.assertEqual(
            "state.commander_zone",
            engine.state.pending_decision.kind,
        )
        remained = session.act(
            "pilot:A",
            {"a": "choose", "choice": "remain"},
        )
        self.assertTrue(remained.ok, remained.summary)
        self.assertEqual("graveyard", commander.zone)
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.permissions.invalidate_current()
        self.prepare_priority(session, mana={"C": 1, "B": 1, "R": 1})

        actions = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertIn(f"cast:{source.ref}", [row["id"] for row in actions])
        action = next(
            row for row in actions if row["id"] == f"cast:{source.ref}"
        )
        self.assertIn("mode_1", action["target_schema"]["legal_modes"])
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_1", "mode_4"],
                "targets": [commander.ref, creature.ref],
                "pay": "manual",
                "payment": {"C": 1, "B": 1, "R": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)

        replacement_seen = False
        while engine.state.stack:
            decision = engine.state.pending_decision
            if decision is not None and decision.kind == "replacement.order":
                replacement_seen = True
                self.assertEqual(["pilot:A"], session.pending_principals())
                for seat in "BCD":
                    self.assertIsNone(
                        session.packet(f"pilot:{seat}", full=True)["decision"]
                    )
                projected = session.packet("pilot:A", full=True)["decision"]
                selected = next(
                    option
                    for option in projected["ctx"]["options"]
                    if not option.get("decline")
                )
                accepted = session.act(
                    "pilot:A",
                    {
                        "action_id": "choose",
                        "replacement": selected["id"],
                    },
                )
                self.assertTrue(accepted.ok, accepted.summary)
                continue
            pass_current(session)

        self.assertTrue(replacement_seen)
        self.assertEqual("command", commander.zone)
        self.assertNotIn(
            commander.object_id,
            engine.state.players["A"].zones["hand"],
        )
        self.assertEqual(2, creature.marked_damage)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "fixed-modal-return-replacement")

    def test_modal_activation_pays_cost_resolves_selected_mode_and_replays(self):
        session = self.session_with_replacement(
            original="Arcum Dagsson",
            replacement="Balm of Restoration",
            seed=119002,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Balm of Restoration")
        artifact = self.card(engine, owner="B", name="Sol Ring")
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        engine.move_card(artifact.object_id, "battlefield", controller="B", log=False)
        self.prepare_priority(session, mana={"C": 1})
        action_id = f"activate:{source.ref}:ab1"
        action = next(
            row
            for row in session.packet("pilot:A", full=True)["decision"]["ctx"][
                "legal"
            ]["actions"]
            if row["id"] == action_id
        )
        self.assertEqual(
            ["mode_1", "mode_2"],
            action["target_schema"]["legal_modes"],
        )
        self.assertEqual(
            [],
            action["target_schema"]["mode_schemas"]["mode_1"]["groups"],
        )

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "modes": ["mode_1"],
                "targets": [artifact.ref],
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        source = engine.state.cards[source.object_id]
        life_before = engine.state.players["A"].life
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "modes": ["mode_1"],
                "targets": [],
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", source.zone)
        self.pass_stack(session)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        self.assertEqual("battlefield", artifact.zone)
        self.assert_replays(session, "fixed-modal-activation")

    def test_modal_entry_trigger_selects_target_resolves_and_replays(self):
        session = self.session_with_replacement(
            original="Arcum Dagsson",
            replacement="Dawnbringer Cleric",
            seed=119003,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Dawnbringer Cleric")
        enchantment = self.card(engine, owner="B", name="Mystic Remora")
        engine.move_card(
            enchantment.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        engine._stabilize()

        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        decision = session.packet("pilot:A", full=True)["decision"]
        schema = decision["ctx"]["target_schema"]
        self.assertEqual(["mode_1", "mode_2"], schema["legal_modes"])
        self.assertIn(
            enchantment.ref,
            schema["mode_schemas"]["mode_2"]["groups"][0]["legal_refs"],
        )
        for seat in "BCD":
            self.assertIsNone(
                session.packet(f"pilot:{seat}", full=True)["decision"]
            )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "modes": ["mode_2"],
                "targets": [source.ref],
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        enchantment = engine.state.cards[enchantment.object_id]
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "modes": ["mode_2"],
                "targets": [enchantment.ref],
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)
        self.assertEqual("graveyard", enchantment.zone)
        self.assert_replays(session, "fixed-modal-entry-trigger")


class MeldFrontModalCompilerTests(unittest.TestCase):
    def test_meld_front_strict_admission_replaces_only_equivalent_reviewed_views(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.card_programs.modal_catalog_equivalence import shadowed_reviewed_meld_modal_keys
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry

        with tempfile.TemporaryDirectory() as directory, focused_database(directory) as db:
            card = db.lookup("The Mightstone and Weakstone", fuzzy=False)
            caps = load_default_capability_registry()
            profile = "commander_review"
            semantics = SemanticRegistry()
            prior = semantics.programs_for_oracle(card.oracle_id)
            self.assertEqual({"trigger:enter", "spell:front"}, {p.ability_id for p in prior})

            register_generated_programs(db, semantics, [card], capability_registry=caps,
                capability_profile=profile, promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True)
            current = semantics.programs_for_oracle(card.oracle_id)
            self.assertEqual({"trigger:front:n1", "ability:ab4"}, {p.ability_id for p in current})
            program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                capability_registry=caps, capability_profile=profile)
            result = bind_card_program_runtime(program, capability_registry=caps, profile=profile)
            self.assertTrue(result["strict_capability_ready"], result)
            self.assertFalse(result["compatibility_provenance"])

            legacy_trigger = next(p for p in prior if p.event == "permanent.enter.self")
            legacy_spell = next(p for p in prior if p.event == "resolve")

            def shadowed(prior_views=prior, new_views=current):
                return shadowed_reviewed_meld_modal_keys(card, new_views, prior_views,
                    capability_registry=caps, capability_profile=profile)

            self.assertEqual({p.key for p in prior}, shadowed())
            self.assertFalse(shadowed((legacy_trigger,)))
            self.assertFalse(shadowed((legacy_trigger, replace(legacy_spell, destination="graveyard"))))
            self.assertFalse(shadowed((legacy_spell, replace(legacy_trigger,
                provenance={**legacy_trigger.provenance, "source_rulings_hash": "0" * 64}))))
            bad_modes = copy.deepcopy(legacy_trigger.target_schema)
            bad_modes["modes"]["weaken"]["effects"][0]["power"] = -4
            self.assertFalse(shadowed((legacy_spell, replace(legacy_trigger, target_schema=bad_modes))))
            typed_trigger = next(p for p in current if p.event == "permanent.enter.self")
            dependencies = tuple(cap for cap in typed_trigger.capability_dependencies
                if cap != "continuous.resolution.fixed_characteristics_until_end_of_turn")
            incomplete = replace(typed_trigger, capability_dependencies=dependencies,
                capability_closure=caps.closure(dependencies, profile=profile).to_dict())
            self.assertFalse(shadowed(new_views=tuple(incomplete if p is typed_trigger else p for p in current)))


class MeldFrontModalNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "meld-front-modal.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/scryfall-exact-lists.json",
            ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Meld front", [DeckEntry("Sliver Hivelord", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Sliver Hivelord"])

    def choose_mode(self, session, mode, targets=()):
        command = {"action_id": "choose", "modes": [mode], "targets": list(targets)}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def activate_for(self, session, source, targets=()):
        self.priority(session, "A")
        packet = session.packet("pilot:A", full=True)
        action = next(row for row in packet["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(f"activate:{source.ref}:"))
        result = session.act("pilot:A", {"action_id": action["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_native_draw_is_the_only_mode_without_creatures_and_remains_private(self):
        session = self.session(951026)
        rock = self.add(session.engine, "The Mightstone and Weakstone", zone="hand")
        self.assert_closed(session, rock)
        self.prepare(session, mana={"C": 5})
        self.checkpoint(session)
        self.cast(session, "A", rock)
        self.resolve_top(session)

        packet = session.packet("pilot:A", full=True)
        self.assertEqual("semantic.target", packet["decision"]["kind"])
        schema = packet["decision"]["ctx"]["target_schema"]
        self.assertEqual(["mode_1"], schema["legal_modes"])
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        session = self.replay(session, load=True)

        original_hand = set(session.state.players["A"].zones["hand"])
        self.choose_mode(session, "mode_1")
        self.assertIsNone(self.resolve(session))
        drawn = set(session.state.players["A"].zones["hand"]) - original_hand
        self.assertEqual(2, len(drawn))
        for card in drawn:
            ref = session.state.cards[card].ref
            self.assertIn(ref, json.dumps(session.packet("pilot:A", full=True)))
            for seat in "BCD":
                self.assertNotIn(ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.assertEqual("battlefield", session.state.cards[rock.object_id].zone)
        self.replay(session, load=True)

    def test_native_weaken_ends_at_cleanup_and_invalid_target_never_draws(self):
        for case in ("expires", "stale", "lethal"):
            with self.subTest(case=case):
                session = self.session(951030 + ("expires", "stale", "lethal").index(case))
                rock = self.add(session.engine, "The Mightstone and Weakstone", zone="hand")
                target_name = "Prodigal Sorcerer" if case == "lethal" else "Colossal Dreadmaw"
                victim = self.add(session.engine, target_name, seat="B")
                sundial = self.add(session.engine, "Sundial of the Infinite")
                removal = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                self.add(session.engine, "Island", seat="B")
                self.assert_closed(session, rock)
                self.prepare(session, mana={"C": 6})
                self.checkpoint(session)
                self.cast_for(session, "A", rock, reserve={"C": 1})
                self.resolve_top(session)

                schema = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
                self.assertEqual(["mode_1", "mode_2"], schema["legal_modes"])
                self.assertEqual([victim.ref], schema["mode_schemas"]["mode_2"]["groups"][0]["legal_refs"])
                before_hand = len(session.state.players["A"].zones["hand"])
                self.choose_mode(session, "mode_2", [victim.ref])
                if case == "stale":
                    self.cast(session, "B", removal, [victim.ref])
                self.assertIsNone(self.resolve(session))
                body = session.state.cards[victim.object_id]
                self.assertEqual(before_hand, len(session.state.players["A"].zones["hand"]))
                if case == "expires":
                    self.assertEqual((1, 1), tuple(session.engine._numeric_stat(body.object_id, name)
                        for name in ("power", "toughness")))
                    self.activate_for(session, sundial)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual((6, 6), tuple(session.engine._numeric_stat(body.object_id, name)
                        for name in ("power", "toughness")))
                else:
                    self.assertEqual("hand" if case == "stale" else "graveyard", body.zone)
                self.replay(session, load=True)

    def test_native_warform_copies_the_modal_front_and_restricted_mana(self):
        session = self.session(951050)
        self.add(session.engine, "Mishra, Eminent One")
        rock = self.add(session.engine, "The Mightstone and Weakstone")
        self.assert_closed(session, rock)
        self.prepare(session, mana={})
        self.checkpoint(session)
        self.until(session, lambda game: game.state.pending_decision is not None
                   and game.state.pending_decision.kind == "semantic.target")
        target = session.act("pilot:A", {"action_id": "choose", "targets": [rock.ref]})
        self.assertTrue(target.ok, target.summary)
        self.assertEqual("semantic.target", self.resolve(session).kind)

        token, = (card for card in session.state.cards.values()
                  if card.is_token and session.engine.display_name(card.object_id) == "Mishra's Warform")
        schema = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual(["mode_1", "mode_2"], schema["legal_modes"])
        self.assertIn(token.ref, schema["mode_schemas"]["mode_2"]["groups"][0]["legal_refs"])
        self.choose_mode(session, "mode_1")
        self.assertIsNone(self.resolve(session))
        self.activate_for(session, token)
        self.assertEqual(2, session.state.players["A"].mana_pool["C"])
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_native_two_restricted_units_pay_equip_and_artifact_but_not_nonartifact_cast(self):
        session = self.session(951040)
        rock = self.add(session.engine, "The Mightstone and Weakstone", zone="hand")
        equipment = self.add(session.engine, "Skullclamp")
        creature = self.add(session.engine, "Colossal Dreadmaw")
        artifact = self.add(session.engine, "Sol Ring", zone="hand")
        nonartifact = self.add(session.engine, "Disenchant", zone="hand")
        self.assert_closed(session, rock)
        self.prepare(session, mana={"C": 5, "W": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", rock, reserve={"W": 1})
        self.resolve_top(session)
        self.choose_mode(session, "mode_1")
        self.assertIsNone(self.resolve(session))

        self.activate_for(session, rock)
        self.assertFalse(session.state.stack)
        self.assertEqual(2, session.state.players["A"].mana_pool["C"])
        self.priority(session, "A")
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": f"cast:{nonartifact.ref}", "targets": [rock.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        self.activate_for(session, equipment, [creature.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.players["A"].mana_pool["C"])
        self.cast_for(session, "A", artifact, reserve={"W": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0, session.state.players["A"].mana_pool["C"])
        self.assertEqual("battlefield", session.state.cards[artifact.object_id].zone)
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
