from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.card_programs import bind_card_program_runtime, compile_card_program
from quorune.card_programs.commands import runtime_component_status
from quorune.carddb import CardDatabase, CardRecord
from quorune.characteristic_evaluation import (
    evaluate_card_characteristics,
    type_parts,
)
from quorune.characteristic_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from quorune.creature_subtypes import CREATURE_SUBTYPES
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.morph import MORPH_FACE_DOWN_ANNOTATION
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import object_matches_query, object_query_result
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.ability_fragments import (
    default_ability_fragment_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import _card_payload, build_fixture_database


CAPABILITY_ID = "continuous.characteristics.changeling"
HANDLER_ID = "ability.static.all-creature-types-characteristic-definition.v1"
TEMPLATE_ID = "changeling-all-creature-types-characteristic-definition-v1"
CHANGELING_FRAGMENT = ability_fragment_to_dict(
    AllCreatureTypesCharacteristicDefinitionSpec()
)


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "changeling-characteristics.sqlite3"
    boundary = CardRecord(
        oracle_id="fixture:changeling-compound-entry-boundary",
        name="Generic Changeling Compound Entry Boundary",
        mana_cost="{3}", mana_value=3,
        type_line="Artifact Creature — Shapeshifter",
        oracle_text="Changeling\nAs this creature enters, choose two creature types.",
        power="2", toughness="2", loyalty=None, defense=None,
        colors=(), color_identity=(), keywords=("Changeling",),
        produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )
    boundary_path = Path(directory) / "compound-entry.json"
    boundary_path.write_text(json.dumps({"schema_version": 1, "cards": [_card_payload(boundary)], "rulings": []}), encoding="utf-8")
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "changeling-characteristics-cards.json",
            boundary_path,
        ],
        database,
    )
    return CardDatabase(database)


def changeling_base() -> dict[str, object]:
    return {
        "name": "Universal Automaton",
        "mana_cost": "{1}",
        "mana_value": 1,
        "type_line": "Artifact Creature — Shapeshifter",
        "oracle_text": "Changeling (This card is every creature type.)",
        "executable_oracle_text": "Changeling (This card is every creature type.)",
        "power": "1",
        "toughness": "1",
        "loyalty": None,
        "defense": None,
        "keywords": ["Changeling"],
        "colors": [],
        "produced_mana": [],
        "ability_fragments": [CHANGELING_FRAGMENT],
        "activated_abilities": [],
    }


def changeling_card(*, zone: str = "battlefield") -> CardInstance:
    return CardInstance(
        object_id="changeling-test-object",
        ref="CHANGELING-TEST",
        oracle_id="d50b1d2e-1825-432a-ab73-068fdc356f50",
        printed_name="Universal Automaton",
        owner="A",
        controller="A",
        zone=zone,
        known_to=["A", "B", "C", "D"],
        revealed_to=["A", "B", "C", "D"],
    )


class ChangelingCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Universal Automaton")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_ordinary_changeling_compiles_to_all_zone_characteristic_definition(self):
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
        self.assertEqual(CHANGELING_FRAGMENT, node.handlers[0]["fragment"])

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
            == "ability.static.all_creature_types_characteristic_definition"
        ]
        self.assertEqual(1, len(components))
        self.assertEqual(HANDLER_ID, components[0]["handler_id"])
        self.assertTrue(components[0]["capability_closure"]["trusted"])

    def test_nonordinary_changeling_and_malformed_descriptors_fail_closed(self):
        record = copy.copy(self.record)
        object.__setattr__(record, "oracle_text", "Changeling 2")
        object.__setattr__(record, "keywords", ("Changeling",))
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

        descriptor = {
            "handler_id": HANDLER_ID,
            "schema_version": 1,
            "event": "continuous",
            "fragment": CHANGELING_FRAGMENT,
        }
        registry = default_ability_fragment_registry()
        registry.validate(descriptor)
        self.assertEqual(
            (AllCreatureTypesCharacteristicDefinitionSpec(),),
            registry.lower(descriptor, None),
        )
        for path, value in (
            (("schema_version",), True),
            (("event",), "characteristics.evaluate"),
            (("fragment", "kind"), "colorless_characteristic_definition"),
            (("fragment", "value", "schema_version"), 2),
        ):
            candidate = copy.deepcopy(descriptor)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(candidate)


    def test_original_changeling_entry_keeps_chosen_trigger_unavailable(self):
        registry = load_default_capability_registry()
        record = self.db.lookup("Bloodline Pretender")
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        entry = next(node for face in ir.faces for node in face.nodes if node.template_id == "intrinsic-entry-designation-v1")
        self.assertTrue(entry.exact)
        self.assertEqual(entry.text, record.oracle_text[entry.span.start:entry.span.end])
        self.assertIn("zone.entry.public_designation", entry.capability_dependencies)
        blockers = {blocker for residual in ir.material_residuals for blocker in residual.blockers}
        self.assertLessEqual({"normalized event binding", "intervening-if and reflexive-trigger grammar"}, blockers)
        program = compile_card_program(self.db, record, capability_registry=registry, capability_profile="commander_review", trust_level="provisional")
        binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])


class ChangelingRuntimeTests(unittest.TestCase):
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
        cls.record = cls.db.lookup("Universal Automaton")
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

    def test_changeling_is_every_creature_type_in_all_zones(self):
        session = self.session(702_730_001)
        card = changeling_card()
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
                current = session.engine._effective_card_data(card)
                card_types, subtypes, _ = type_parts(current["type_line"])
                self.assertIn("creature", card_types)
                self.assertEqual(CREATURE_SUBTYPES, frozenset(subtypes))

    def test_changeling_cda_orders_before_non_cda_type_setting_and_after_copy(self):
        card = changeling_card()
        set_frog = ContinuousEffect(
            effect_id="set-frog",
            source_id="type-source",
            layer=Layer.TYPE,
            sublayer="4",
            timestamp=10,
            operations=(
                ContinuousOperation("set_types", ["Frog"], field="subtypes"),
            ),
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
            changeling_base(),
            runtime_effects=(set_frog, remove_abilities),
        )
        self.assertEqual({"frog"}, type_parts(current["type_line"])[1])
        self.assertEqual([], current["keywords"])

        copied = changeling_card()
        copied.annotations["copy_overrides"] = {
            "type_line": "Creature — Shapeshifter",
            "keywords": ["Changeling"],
            "ability_fragments": [CHANGELING_FRAGMENT],
        }
        base = changeling_base()
        base["keywords"] = []
        base["ability_fragments"] = []
        self.assertEqual(
            CREATURE_SUBTYPES,
            frozenset(
                type_parts(
                    evaluate_card_characteristics(copied, base)["type_line"]
                )[1]
            ),
        )
        copied.annotations["copy_overrides"] = {
            "type_line": "Creature — Shapeshifter",
            "keywords": ["Changeling"],
            "ability_fragments": [],
        }
        self.assertEqual(
            {"shapeshifter"},
            type_parts(
                evaluate_card_characteristics(copied, changeling_base())[
                    "type_line"
                ]
            )[1],
        )

    def test_changeling_shared_subtype_counts_and_queries_use_effective_types(self):
        session = self.session(702_730_002)
        card = changeling_card()
        current = session.engine._effective_card_data(card)
        row = object_query_result(
            card,
            current,
            type_parts=type_parts(current["type_line"]),
            known_to_actor=True,
            attached_to_ref=None,
        )
        for subtype in ("army", "elf", "goblin", "human", "sliver"):
            with self.subTest(subtype=subtype):
                self.assertTrue(
                    object_matches_query(
                        row,
                        ObjectQuerySpec(
                            zones=(card.zone,),
                            subtypes_any=(subtype,),
                        ),
                    )
                )

    def test_changeling_face_down_and_untyped_prose_are_inert(self):
        card = changeling_card()
        untyped = changeling_base()
        untyped["ability_fragments"] = []
        self.assertEqual(
            {"shapeshifter"},
            type_parts(evaluate_card_characteristics(card, untyped)["type_line"])[
                1
            ],
        )

        card.face_down = True
        card.annotations[MORPH_FACE_DOWN_ANNOTATION] = {
            "name": "",
            "mana_cost": "",
            "mana_value": 0,
            "text": "",
            "supertypes": [],
            "card_types": ["Creature"],
            "subtypes": [],
            "colors": [],
            "abilities": [],
            "power": 2,
            "toughness": 2,
        }
        current = evaluate_card_characteristics(card, changeling_base())
        self.assertEqual(set(), type_parts(current["type_line"])[1])

    def test_changeling_executes_while_replacement_siblings_fail_closed(self):
        session = self.session(702_730_004)
        engine = session.engine
        record = self.db.lookup("Generic Changeling Compound Entry Boundary")
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
                and engine.semantic_program_is_current_trusted(runtime_program)
                for runtime_program in engine.semantics.programs_for_oracle(
                    record.oracle_id
                )
            )
        )

        card = CardInstance(
            object_id="changeling-compound-entry-boundary",
            ref="CHANGELING-COMPOUND-BOUNDARY",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        self.assertEqual(
            CREATURE_SUBTYPES,
            frozenset(
                type_parts(engine._effective_card_data(card)["type_line"])[1]
            ),
        )

    def test_changeling_compiler_and_runtime_mutations_are_killed(self):
        def assert_exact() -> None:
            program = compile_card_program(
                self.db,
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                trust_level="trusted",
            )
            self.assertEqual((), program.residuals)

        assert_exact()
        with patch.dict(
            "quorune.compiler.characteristic_definition_nodes._FAMILIES",
            {},
            clear=True,
        ):
            with self.assertRaises((AssertionError, ValueError)):
                assert_exact()

        with patch(
            "quorune.characteristic_evaluation.CREATURE_SUBTYPES",
            frozenset({"shapeshifter"}),
        ):
            current = evaluate_card_characteristics(
                changeling_card(),
                changeling_base(),
            )
            self.assertNotIn("goblin", type_parts(current["type_line"])[1])

    def test_changeling_projection_and_replay_are_identity_safe(self):
        session = self.session(702_730_003)
        engine = session.engine
        card = changeling_card()
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        current = engine._effective_card_data(card)
        self.assertEqual(CREATURE_SUBTYPES, frozenset(type_parts(current["type_line"])[1]))

        projected = session.projector._snapshot("pilot:B")
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
                "plan": "REPLAY_CHANGELING_CHARACTERISTICS",
                "reason": "Verify the typed all-zone characteristic checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "changeling-characteristics"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
