from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.ability_fragments import (
    AbilityFragmentError,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
)
from quorune.card_programs import bind_card_program_runtime, compile_card_program
from quorune.carddb import CardRecord
from quorune.characteristic_evaluation import evaluate_card_characteristics
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
    QueryPowerToughnessDefinitionSpec,
)
from quorune.compiler.query_characteristic_templates import (
    query_power_toughness_definition_handler,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from quorune.model import CardInstance
from quorune.object_predicate import ObjectQuerySpec
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.ability_fragments import fragments_from_descriptors
from quorune.session import CommanderSession


HANDLER_ID = "ability.static.query-power-toughness-definition.v1"
CAPABILITY_ID = "continuous.characteristics.query_power_toughness_definition"


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def permanent(text: str, *, suffix: int, name: str = "Definition Source") -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        type_line="Creature — Shapeshifter",
        oracle_text=text,
        power="*",
        toughness="*",
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def definition_fragment(
    *,
    scope: CharacteristicQuantityScope,
    zone: str = "battlefield",
    types_all: tuple[str, ...] = (),
    types_any: tuple[str, ...] = (),
    define_power: bool = True,
    define_toughness: bool = True,
) -> dict[str, object]:
    return ability_fragment_to_dict(
        QueryPowerToughnessDefinitionSpec(
            quantity=CharacteristicQuantitySpec(
                scope=scope,
                query=ObjectQuerySpec(
                    zones=(zone,),
                    types_all=types_all,
                    types_any=types_any,
                ),
            ),
            define_power=define_power,
            define_toughness=define_toughness,
        )
    )


class QueryPowerToughnessDefinitionCompilerTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = load_default_capability_registry()

    def compile(self, record: CardRecord, *, trust_level: str = "trusted"):
        return compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level=trust_level,
        )

    def test_query_power_toughness_definition_grammar_compiles_closed_fragments(self):
        cases = (
            (
                "Definition Source's power and toughness are each equal to the number of creatures you control.",
                True,
                True,
            ),
            (
                "Definition Source's power is equal to the number of instant and sorcery cards in your graveyard.",
                True,
                False,
            ),
            (
                "Definition Source's toughness is equal to the number of Forests on the battlefield.",
                False,
                True,
            ),
            (
                "Definition Source's power and toughness are each equal to the number of artifact cards in all graveyards.",
                True,
                True,
            ),
            (
                "Definition Source's power and toughness are each equal to the number of nonland permanents you control.",
                True,
                True,
            ),
            (
                "Kinship — Definition Source's power is equal to the number of Crabs, Oozes, and/or Horrors you control.",
                True,
                False,
            ),
        )
        for index, (text, define_power, define_toughness) in enumerate(cases):
            with self.subTest(text=text):
                record = permanent(text, suffix=129_000_000 + index)
                program = self.compile(record, trust_level="provisional")
                ability = next(
                    ability
                    for ability in program.abilities
                    if any(
                        descriptor.get("handler_id") == HANDLER_ID
                        for descriptor in ability.handlers
                    )
                )
                descriptor = next(
                    descriptor
                    for descriptor in ability.handlers
                    if descriptor.get("handler_id") == HANDLER_ID
                )
                fragment = fragments_from_descriptors((descriptor,))[0]
                self.assertIsInstance(fragment, QueryPowerToughnessDefinitionSpec)
                self.assertEqual(define_power, fragment.define_power)
                self.assertEqual(define_toughness, fragment.define_toughness)
                self.assertEqual("all", ability.active_zone)
                self.assertIn(CAPABILITY_ID, ability.capability_dependencies)

    def test_query_power_toughness_definition_grammar_keeps_open_families_residual(self):
        rejected = (
            "Definition Source's power and toughness are each equal to the number of Forests you control plus the number of Elves you control.",
            "Definition Source's power is equal to the number of card types among cards in all graveyards.",
            "Definition Source's power is equal to the number of tapped creatures you control.",
            "Definition Source's power is equal to the number of cards in the chosen player's hand.",
            "Definition Source's power is equal to the number of creatures you control plus 1.",
            "Definition Source's power and toughness are each equal to the number of colors among permanents you control.",
            "Definition Source's power is equal to the number of cards exiled with it.",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertIsNone(
                    query_power_toughness_definition_handler(
                        text,
                        source_name="Definition Source",
                    )
                )
                program = self.compile(
                    permanent(text, suffix=129_001_000),
                    trust_level="provisional",
                )
                self.assertFalse(
                    any(
                        descriptor.get("handler_id") == HANDLER_ID
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )
                self.assertTrue(program.residuals)

    def test_query_power_toughness_definition_descriptors_fail_closed(self):
        valid = definition_fragment(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            zone="hand",
        )
        malformed = json.loads(json.dumps(valid))
        malformed["value"]["define_power"] = False
        malformed["value"]["define_toughness"] = False
        with self.assertRaises(AbilityFragmentError):
            ability_fragment_from_dict(malformed)

        descriptor = {
            "handler_id": HANDLER_ID,
            "schema_version": 1,
            "event": "continuous",
            "fragment": malformed,
        }
        with self.assertRaises(ValueError):
            fragments_from_descriptors((descriptor,))

    def test_query_definition_ability_removal_sibling_withholds_runtime_admission(self):
        record = permanent(
            "Definition Source's power and toughness are each equal to the number of creatures you control.\n"
            "Definition Source loses all abilities.",
            suffix=129_002_000,
        )
        program = self.compile(record, trust_level="provisional")
        self.assertTrue(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for ability in program.abilities
                for descriptor in ability.handlers
            )
        )
        self.assertTrue(program.residuals)
        binding = bind_card_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])

    def test_query_power_toughness_definition_compiler_mutant_is_killed(self):
        record = permanent(
            "Definition Source's power and toughness are each equal to the number of creatures you control.",
            suffix=129_002_001,
        )
        baseline = self.compile(record)
        self.assertTrue(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for ability in baseline.abilities
                for descriptor in ability.handlers
            )
        )
        with mock.patch(
            "quorune.compiler.runtime_templates.query_power_toughness_definition_handler",
            return_value=None,
        ):
            mutant = self.compile(record, trust_level="provisional")
        self.assertFalse(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for ability in mutant.abilities
                for descriptor in ability.handlers
            )
        )
        self.assertTrue(mutant.residuals)


class QueryPowerToughnessDefinitionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        return session

    @staticmethod
    def add_object(
        engine,
        *,
        seat: str,
        zone: str,
        suffix: str,
        fragment: dict[str, object],
        power: str = "*",
        toughness: str = "*",
    ) -> CardInstance:
        card = CardInstance(
            object_id=f"definition-source:{suffix}",
            ref=f"definition-source-ref:{suffix}",
            oracle_id=f"custom-token:definition-source:{suffix}",
            printed_name="Definition Source",
            owner=seat,
            controller=seat,
            zone=zone,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        card.annotations["object_characteristics"] = {
            "name": "Definition Source",
            "type_line": "Creature — Shapeshifter",
            "power": power,
            "toughness": toughness,
            "ability_fragments": [fragment],
        }
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def token(engine, seat: str, *, type_line: str, name: str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": type_line,
                "power": "1",
                "toughness": "1",
            },
            reason="query definition counted object",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    def test_query_definitions_apply_in_every_zone_and_preserve_undefined_field(self):
        session = self.session(129_003_000)
        engine = session.engine
        fragment = definition_fragment(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            zone="hand",
            define_power=True,
            define_toughness=False,
        )
        baseline_hand = len(engine.state.players["A"].zones["hand"])
        zones = ("battlefield", "graveyard", "hand", "library", "exile", "command")
        sources = tuple(
            self.add_object(
                engine,
                seat="A",
                zone=zone,
                suffix=f"zone-{zone}",
                fragment=fragment,
                power="*",
                toughness="7",
            )
            for zone in zones
        )
        expected_hand = baseline_hand + 1
        for source in sources:
            with self.subTest(zone=source.zone):
                current = engine._effective_card_data(source)
                self.assertEqual(str(expected_hand), current["power"])
                self.assertEqual("7", current["toughness"])

    def test_query_definition_counts_layer_five_types_and_orders_in_layer_seven_a(self):
        session = self.session(129_003_001)
        engine = session.engine
        source = self.add_object(
            engine,
            seat="A",
            zone="battlefield",
            suffix="layer-five",
            fragment=definition_fragment(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                types_all=("artifact",),
            ),
        )
        counted = self.token(
            engine,
            "A",
            type_line="Token Creature — Citizen",
            name="Layer Four Artifact",
        )
        counted.annotations["continuous_add_types"] = ["Artifact"]
        self.assertEqual(
            ("1", "1"),
            (
                engine._effective_card_data(source)["power"],
                engine._effective_card_data(source)["toughness"],
            ),
        )

        card = CardInstance(
            object_id="definition-layer-source",
            ref="definition-layer-source-ref",
            oracle_id="custom-token:definition-layer-source",
            printed_name="Definition Source",
            owner="A",
            controller="A",
            zone="battlefield",
        )
        base = {
            "name": "Definition Source",
            "type_line": "Creature — Shapeshifter",
            "power": "*",
            "toughness": "*",
            "ability_fragments": [
                definition_fragment(
                    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                    types_all=("artifact",),
                )
            ],
        }
        current = evaluate_card_characteristics(
            card,
            base,
            runtime_effects=(
                ContinuousEffect(
                    effect_id="ordinary-modifier",
                    source_id="ordinary-modifier",
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    timestamp=1,
                    operations=(
                        ContinuousOperation("modify_power_toughness", [2, 1]),
                    ),
                    duration=ContinuousEffectDuration.ZONE_OBJECT,
                ),
            ),
            query_count_resolver=lambda _quantity: 3,
        )
        self.assertEqual(("5", "4"), (current["power"], current["toughness"]))

    def test_query_definition_copy_and_control_change_use_current_source(self):
        session = self.session(129_003_002)
        engine = session.engine
        source = self.add_object(
            engine,
            seat="A",
            zone="battlefield",
            suffix="controller",
            fragment=definition_fragment(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                types_all=("land",),
            ),
        )
        self.token(engine, "A", type_line="Token Land", name="A Land")
        self.token(engine, "B", type_line="Token Land", name="B Land One")
        self.token(engine, "B", type_line="Token Land", name="B Land Two")
        self.assertEqual("1", engine._effective_card_data(source)["power"])

        engine.change_control(source.object_id, "B", reason="definition controller witness")
        self.assertEqual("2", engine._effective_card_data(source)["power"])

        copied_ref = engine.create_token(
            "A",
            name="",
            copy_of=source.ref,
            reason="query definition copy witness",
        )[0]
        copied = engine._resolve_object("A", copied_ref, zones={"battlefield"})
        copied_data = engine._effective_card_data(copied)
        self.assertEqual("1", copied_data["power"])
        self.assertTrue(
            any(
                fragment.get("kind") == "query_power_toughness_definition"
                for fragment in copied_data["ability_fragments"]
            )
        )

    def test_query_definition_hand_count_projects_without_hidden_identity(self):
        session = self.session(129_003_003)
        engine = session.engine
        source = self.add_object(
            engine,
            seat="A",
            zone="battlefield",
            suffix="privacy",
            fragment=definition_fragment(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                zone="hand",
            ),
        )
        hand = list(engine.state.players["A"].zones["hand"])
        hidden = engine.state.cards[hand[0]]
        current = engine._effective_card_data(source)
        self.assertEqual(str(len(hand)), current["power"])
        rendered = json.dumps(session.projector._snapshot("pilot:B"), sort_keys=True)
        self.assertNotIn(hidden.object_id, rendered)
        self.assertNotIn(hidden.ref, rendered)
        self.assertNotIn(hidden.printed_name, rendered)

    def test_query_definition_save_load_and_replay_exactly(self):
        session = self.session(129_003_004)
        engine = session.engine
        source = self.add_object(
            engine,
            seat="A",
            zone="graveyard",
            suffix="replay",
            fragment=definition_fragment(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                zone="hand",
            ),
        )
        expected_values = engine._effective_card_data(source)
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"a": "pass"})
            self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "query-definition-replay"
            session.save(record_dir)
            loaded = CommanderSession.load(self.db, record_dir)
            loaded_source = loaded.engine.state.cards[source.object_id]
            loaded_values = loaded.engine._effective_card_data(loaded_source)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertEqual(
            (expected_values["power"], expected_values["toughness"]),
            (loaded_values["power"], loaded_values["toughness"]),
        )
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
