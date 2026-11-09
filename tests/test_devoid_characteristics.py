from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.aura import SimpleEnchantSpec
from quorune.card_programs import (
    bind_card_program_runtime,
    compile_card_program,
)
from quorune.card_programs.commands import runtime_component_status
from quorune.carddb import CardDatabase
from quorune.characteristic_evaluation import evaluate_card_characteristics
from quorune.characteristic_fragments import (
    ColorlessCharacteristicDefinitionSpec,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import object_matches_query, object_query_result
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime.ability_fragments import (
    default_ability_fragment_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


CAPABILITY_ID = "continuous.characteristics.devoid"
HANDLER_ID = "ability.static.colorless-characteristic-definition.v1"
TEMPLATE_ID = "devoid-colorless-characteristic-definition-v1"
REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
DEVOID_FRAGMENT = ability_fragment_to_dict(
    ColorlessCharacteristicDefinitionSpec()
)


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "devoid-characteristics.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "devoid-characteristics-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


def colored_devoid_base() -> dict[str, object]:
    return {
        "name": "Devoid Test Drone",
        "mana_cost": "{2}{U}",
        "mana_value": 3,
        "type_line": "Creature — Eldrazi Drone",
        "oracle_text": "Devoid (This card has no color.)",
        "executable_oracle_text": "Devoid (This card has no color.)",
        "power": "3",
        "toughness": "3",
        "loyalty": None,
        "defense": None,
        "keywords": ["Devoid"],
        "colors": ["U"],
        "produced_mana": [],
        "ability_fragments": [DEVOID_FRAGMENT],
        "activated_abilities": [],
    }


def devoid_card(*, zone: str = "battlefield") -> CardInstance:
    return CardInstance(
        object_id="devoid-test-object",
        ref="DEVOID-TEST",
        oracle_id="00000000-0000-4000-8000-000012100001",
        printed_name="Devoid Test Drone",
        owner="A",
        controller="A",
        zone=zone,
        known_to=["A", "B", "C", "D"],
        revealed_to=["A", "B", "C", "D"],
    )


class DevoidCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Devoid Test Drone")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_ordinary_devoid_compiles_to_all_zone_characteristic_definition(self):
        ir = compile_oracle_card(
            self.record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == TEMPLATE_ID
        ]
        self.assertEqual("exact", ir.status)
        self.assertEqual(1, len(nodes))
        node = nodes[0]
        self.assertEqual("all", node.active_zone)
        self.assertEqual("continuous", node.event)
        self.assertEqual((CAPABILITY_ID,), node.capability_dependencies)
        self.assertEqual(HANDLER_ID, node.handlers[0]["handler_id"])
        self.assertEqual(DEVOID_FRAGMENT, node.handlers[0]["fragment"])
        self.assertEqual(1, node.span.line)
        self.assertTrue(
            self.record.oracle_text[node.span.start : node.span.end].startswith(
                "Devoid"
            )
        )

        program = compile_card_program(
            self.db,
            self.record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        ability = next(
            ability
            for ability in program.abilities
            if ability.provenance.get("template_id") == TEMPLATE_ID
        )
        self.assertEqual("all", ability.active_zone)
        self.assertEqual((), program.residuals)

        components = [
            row
            for row in runtime_component_status("commander_review")[
                "runtime_components"
            ]
            if row["family"]
            == "ability.static.colorless_characteristic_definition"
        ]
        self.assertEqual(1, len(components))
        self.assertEqual(HANDLER_ID, components[0]["handler_id"])
        self.assertEqual(
            [CAPABILITY_ID],
            components[0]["capability_dependencies"],
        )
        self.assertTrue(components[0]["capability_closure"]["trusted"])

    def test_nonordinary_devoid_wording_remains_source_spanned_residual(self):
        record = replace(
            self.record,
            oracle_text="Devoid 2",
            keywords=("Devoid",),
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)
        self.assertFalse(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for node in ir.faces[0].nodes
                for descriptor in node.handlers
            )
        )
        self.assertTrue(
            any(
                record.oracle_text[residual.span.start : residual.span.end]
                == "Devoid 2"
                for residual in ir.material_residuals
            )
        )

    def test_devoid_dependency_and_compiler_mutations_fail_closed(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in raw["capabilities"]
            if row["id"] == CAPABILITY_ID
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.record,
            capability_registry=CapabilityRegistry(raw),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                CAPABILITY_ID in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )

        from quorune.compiler.characteristic_definition_nodes import _FAMILIES

        empty_lowering = type(
            "EmptyLowering",
            (),
            {"handlers": (), "residual_kind": None},
        )()
        mutated_family = replace(
            _FAMILIES["devoid"],
            lowerer=lambda material, mechanics: empty_lowering,
        )
        with patch.dict(
            _FAMILIES,
            {"devoid": mutated_family},
        ):
            mutated = compile_oracle_card(
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)
        self.assertTrue(
            any(
                HANDLER_ID in residual.blockers
                for residual in mutated.material_residuals
            )
        )
        self.assertFalse(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for node in mutated.faces[0].nodes
                for descriptor in node.handlers
            )
        )

    def test_devoid_descriptor_and_prose_only_objects_fail_closed_without_mutation(self):
        ir = compile_oracle_card(
            self.record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        descriptor = copy.deepcopy(
            next(
                node.handlers[0]
                for node in ir.faces[0].nodes
                if node.template_id == TEMPLATE_ID
            )
        )
        registry = default_ability_fragment_registry()
        registry.validate(descriptor)
        malformed = []
        for path, value in (
            (("unknown",), True),
            (("schema_version",), True),
            (("event",), "characteristics.evaluate"),
            (("fragment", "kind"), "dynamic_power_toughness"),
            (("fragment", "value", "schema_version"), 2),
        ):
            candidate = copy.deepcopy(descriptor)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            malformed.append(candidate)

        card = devoid_card()
        base = colored_devoid_base()
        base["ability_fragments"] = []
        before = copy.deepcopy(base)
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(candidate)
                self.assertEqual(before, base)
        result = evaluate_card_characteristics(card, base)
        self.assertEqual(["U"], result["colors"])
        self.assertEqual(before, base)


class DevoidRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
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
        cls.record = cls.db.lookup("Devoid Test Drone")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        registration = register_generated_programs(
            self.db,
            session.engine.semantics,
            (self.record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(1, registration["runtime_handlers_promoted"])
        return session

    def test_devoid_is_colorless_in_every_zone_and_preserves_color_identity(self):
        session = self.session(121_100_001)
        engine = session.engine
        card = devoid_card()
        for zone in (
            "library",
            "hand",
            "graveyard",
            "exile",
            "command",
            "stack",
            "battlefield",
        ):
            with self.subTest(zone=zone):
                card.zone = zone
                current = engine._effective_card_data(card)
                self.assertEqual([], current["colors"])
                self.assertIn("Devoid", current["keywords"])
                self.assertIn(DEVOID_FRAGMENT, current["ability_fragments"])

        self.assertEqual(("U",), self.record.color_identity)
        engine.state.commander_oracle_ids["A"] = [self.record.oracle_id]
        self.assertEqual({"U"}, engine._commander_identity("A"))

    def test_devoid_orders_before_layer_five_color_addition_and_after_copy_values(self):
        card = devoid_card()
        add_red = ContinuousEffect(
            effect_id="add-red",
            source_id="paint-source",
            layer=Layer.COLOR,
            sublayer="5",
            timestamp=10,
            operations=(ContinuousOperation("add_colors", ["R"]),),
            duration=ContinuousEffectDuration.ZONE_OBJECT,
        )
        remove_abilities = ContinuousEffect(
            effect_id="remove-abilities",
            source_id="ability-source",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=11,
            operations=(ContinuousOperation("remove_all_abilities"),),
            duration=ContinuousEffectDuration.ZONE_OBJECT,
        )
        current = evaluate_card_characteristics(
            card,
            colored_devoid_base(),
            runtime_effects=(add_red, remove_abilities),
        )
        self.assertEqual(["R"], current["colors"])
        self.assertEqual([], current["keywords"])

        copied = devoid_card()
        copied.annotations["copy_overrides"] = {
            "colors": ["G"],
            "keywords": ["Devoid"],
            "ability_fragments": [DEVOID_FRAGMENT],
        }
        nondevoid_base = colored_devoid_base()
        nondevoid_base["keywords"] = []
        nondevoid_base["ability_fragments"] = []
        self.assertEqual(
            [],
            evaluate_card_characteristics(copied, nondevoid_base)["colors"],
        )

        copied.annotations["copy_overrides"] = {
            "colors": ["G"],
            "keywords": ["Devoid"],
            "ability_fragments": [],
        }
        self.assertEqual(
            ["G"],
            evaluate_card_characteristics(
                copied, colored_devoid_base()
            )["colors"],
        )

    def test_devoid_feeds_current_colorless_and_colored_predicates(self):
        session = self.session(121_100_002)
        engine = session.engine
        card = devoid_card()
        current = engine._effective_card_data(card)
        row = object_query_result(
            card,
            current,
            type_parts=engine._type_parts(current["type_line"]),
            known_to_actor=True,
            attached_to_ref=None,
        )
        self.assertTrue(
            object_matches_query(row, ObjectQuerySpec(colorless=True))
        )
        self.assertFalse(
            object_matches_query(row, ObjectQuerySpec(colors_any=("U",)))
        )

    def test_devoid_runtime_mutation_is_killed(self):
        session = self.session(121_100_003)
        card = devoid_card()
        original = ContinuousOperation

        def keep_blue(operation: str, value=None, field=None):
            if operation == "remove_all_colors":
                return original("add_colors", ["U"])
            return original(operation, value, field)

        with patch(
            "quorune.characteristic_evaluation.ContinuousOperation",
            side_effect=keep_blue,
        ):
            current = session.engine._effective_card_data(card)
        self.assertEqual(["U"], current["colors"])

    def test_devoid_and_compiled_aura_attachment_compose(self):
        session = self.session(121_100_005)
        engine = session.engine
        record = self.db.lookup("Visions of Brutality")
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(
            registration["runtime_handlers_promoted"],
            2,
        )
        self.assertTrue(program.residuals)

        target_ref = engine.create_token(
            "A",
            name="Devoid Aura Target",
            characteristics={
                "type_line": "Token Creature — Bear",
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
            reason="Devoid and Aura interaction assurance",
        )[0]
        target = engine._resolve_object(
            "A", target_ref, zones={"battlefield"}
        )
        aura = CardInstance(
            object_id="devoid-visions-of-brutality",
            ref="VISIONS-OF-BRUTALITY",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="hand",
            known_to=["A"],
        )
        engine.state.cards[aura.object_id] = aura
        engine.state.players["A"].zones["hand"].append(aura.object_id)
        self.assertEqual([], engine._effective_card_data(aura)["colors"])

        moved = engine.move_card(
            aura.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=target.ref,
            reason="Devoid and Aura interaction assurance",
        )
        self.assertIs(aura, moved)
        self.assertEqual(target.object_id, aura.attached_to)
        self.assertEqual([], engine._effective_card_data(aura)["colors"])
        self.assertTrue(
            any(
                isinstance(fragment, SimpleEnchantSpec)
                for fragment in engine._effective_ability_fragments(aura)
            )
        )
        self.assertTrue(
            engine._attachment_is_legal(aura, subtypes={"aura"})
        )

    def test_devoid_executes_while_replacement_siblings_fail_closed(self):
        session = self.session(121_100_006)
        engine = session.engine
        printed = compile_oracle_card(
            self.db.lookup("Void Shatter"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", printed.status, printed.material_residuals)
        record = self.db.lookup("Devoid Test Conditional Counterspell")
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        blockers = {
            blocker
            for residual in program.residuals
            for blocker in residual["blockers"]
        }
        self.assertGreaterEqual(
            blockers,
            {
                "replacement applicability",
                "self-replacement and prevention ordering",
            },
        )
        before = copy.deepcopy(engine.state.to_dict())
        binding = bind_card_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])
        self.assertIn("trust_basis:unresolved", binding["blockers"])
        self.assertEqual(before, engine.state.to_dict())

        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(1, registration["runtime_handlers_promoted"])
        runtime_programs = tuple(
            engine.semantics.runtime_handler_programs_for_oracle(
                record.oracle_id,
                active_zone="all",
                event="continuous",
            )
        )
        self.assertTrue(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for runtime_program in runtime_programs
                if engine.semantic_program_is_current_trusted(runtime_program)
                for descriptor in runtime_program.handlers
            )
        )
        self.assertFalse(
            any(
                "replacement" in runtime_program.event
                and engine.semantic_program_is_current_trusted(
                    runtime_program
                )
                for runtime_program in engine.semantics.programs_for_oracle(
                    record.oracle_id
                )
            )
        )

        card = CardInstance(
            object_id="devoid-conditional-counterspell",
            ref="DEVOID-CONDITIONAL-COUNTER",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="stack",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        self.assertEqual([], engine._effective_card_data(card)["colors"])

    def test_devoid_projection_and_replay_are_identity_safe(self):
        session = self.session(121_100_004)
        engine = session.engine
        card = devoid_card()
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        self.assertEqual([], engine._effective_card_data(card)["colors"])

        projected = session.projector._snapshot("pilot:B")
        public_card = next(
            value
            for value in projected["players"]["A"]["bf"]
            if value["id"] == card.ref
        )
        self.assertEqual("Devoid Test Drone", public_card["n"])
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn(card.object_id, rendered)
        self.assertNotIn("ability_fragments", rendered)

        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_DEVOID_CHARACTERISTICS",
                "reason": "Verify the typed all-zone characteristic checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "devoid-characteristics"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
