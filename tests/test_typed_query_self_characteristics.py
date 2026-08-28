from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.card_programs import bind_card_program_runtime, compile_card_program
from quorune.carddb import CardRecord
from quorune.characteristic_evaluation import evaluate_card_characteristics
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
    PowerToughnessCalculation,
    QueryCharacteristicModifierSpec,
)
from quorune.compiler.query_characteristic_templates import (
    query_self_characteristics_handler,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from quorune.model import CardInstance
from quorune.object_predicate import ObjectQuerySpec
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.ability_fragments import (
    default_ability_fragment_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError


QUERY_HANDLER = "ability.static.query-characteristic-modifier.v1"
QUERY_CAPABILITY = "continuous.characteristics.query_count_modifier"


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def permanent(text: str, *, suffix: int, name: str = "Query Source") -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        type_line="Creature — Shapeshifter",
        oracle_text=text,
        power="1",
        toughness="2",
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


def transforming_query_saga(
    *,
    suffix: int,
    front_name: str,
    back_name: str,
    back_keyword: str,
    query_text: str,
    color: str,
) -> CardRecord:
    front_text = (
        "(As this Saga enters and after your draw step, add a lore counter.)\n"
        "III — Exile this Saga, then return it to the battlefield transformed "
        "under your control."
    )
    back_text = f"{back_keyword}\n{query_text}"
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"{front_name} // {back_name}",
        mana_cost=f"{{3}}{{{color}}} // ",
        mana_value=4.0,
        type_line="Enchantment — Saga // Enchantment Creature — Spirit",
        oracle_text=(
            f"{front_name}: {front_text}\n//\n{back_name}: {back_text}"
        ),
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(color,),
        keywords=(back_keyword, "Transform"),
        produced_mana=(),
        layout="transform",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(
            {
                "name": front_name,
                "mana_cost": f"{{3}}{{{color}}}",
                "type_line": "Enchantment — Saga",
                "oracle_text": front_text,
                "colors": [color],
                "power": None,
                "toughness": None,
                "loyalty": None,
                "defense": None,
            },
            {
                "name": back_name,
                "mana_cost": "",
                "type_line": "Enchantment Creature — Spirit",
                "oracle_text": back_text,
                "colors": [color],
                "power": "0",
                "toughness": "0",
                "loyalty": None,
                "defense": None,
            },
        ),
        raw={},
    )


def query_fragment(
    *,
    scope: CharacteristicQuantityScope,
    zone: str = "battlefield",
    types: tuple[str, ...] = (),
    subtypes: tuple[str, ...] = (),
    exclude_source: bool = False,
    counter_name: str | None = None,
    power: int = 1,
    toughness: int = 1,
    minimum_count: int = 0,
    abilities: tuple[str, ...] = (),
) -> dict[str, object]:
    quantity = CharacteristicQuantitySpec(
        scope=scope,
        query=(
            None
            if scope is CharacteristicQuantityScope.SOURCE_COUNTER
            else ObjectQuerySpec(
                zones=(zone,),
                types_all=types,
                subtypes_all=subtypes,
            )
        ),
        counter_name=counter_name,
        exclude_source=exclude_source,
    )
    return ability_fragment_to_dict(
        QueryCharacteristicModifierSpec(
            quantity=quantity,
            calculation=(
                PowerToughnessCalculation.FIXED_IF_THRESHOLD
                if minimum_count
                else PowerToughnessCalculation.PER_MATCHING_OBJECT
            ),
            power=power,
            toughness=toughness,
            minimum_count=minimum_count,
            add_abilities=abilities,
        )
    )


class TypedQuerySelfCharacteristicCompilerTests(unittest.TestCase):
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

    def test_query_characteristic_grammar_compiles_closed_fragments(self):
        cases = (
            ("This creature gets +1/+1 for each artifact you control.", "Fixture"),
            ("This creature gets +1/+0 for each card in your hand.", "Fixture"),
            ("This creature gets +2/+2 for each Aura attached to it.", "Fixture"),
            ("This creature gets +1/+1 for each oil counter on it.", "Fixture"),
            ("This creature gets +1/+1 for each creature card in your opponents' graveyards.", "Fixture"),
            ("This creature gets -1/-1 for each other creature on the battlefield.", "Fixture"),
            ("This creature gets +2/+2 as long as you control three or more artifacts.", "Fixture"),
            ("This creature gets +1/+1 as long as you control a Forest.", "Fixture"),
            ("This creature gets +1/+1 as long as an opponent controls an Island.", "Fixture"),
            ("Threshold — This creature gets +1/+1 and has deathtouch as long as there are seven or more cards in your graveyard.", "Fixture"),
            ("Named Source gets +1/+0 for each other Rat you control.", "Named Source"),
        )
        for index, (text, name) in enumerate(cases):
            with self.subTest(text=text):
                program = self.compile(
                    permanent(text, suffix=122_000_000 + index, name=name)
                )
                descriptor = next(
                    descriptor
                    for ability in program.abilities
                    for descriptor in ability.handlers
                    if descriptor.get("handler_id") == QUERY_HANDLER
                )
                self.assertEqual(
                    "query_characteristic_modifier",
                    descriptor["fragment"]["kind"],
                )
                self.assertIn(
                    QUERY_CAPABILITY,
                    next(
                        ability.capability_dependencies
                        for ability in program.abilities
                        if descriptor in ability.handlers
                    ),
                )
                self.assertEqual((), program.residuals)

    def test_query_characteristic_grammar_keeps_ambiguous_families_residual(self):
        excluded = (
            "Domain — This creature gets +1/+1 for each basic land type among lands you control.",
            "This creature gets +1/+1 for each card type among cards in your graveyard.",
            "This creature gets +1/+1 for each creature named Query Source you control.",
            "This creature gets +1/+1 for each creature you control with flying.",
            "This creature gets +1/+1 for each color among permanents you control.",
            "This creature gets +1/+1 for each artifact you control and has flying.",
            "Equipped creature gets +1/+1 for each artifact you control.",
            "{2}: This creature gets +1/+1 for each artifact you control.",
        )
        for index, text in enumerate(excluded):
            with self.subTest(text=text):
                self.assertIsNone(
                    query_self_characteristics_handler(
                        text, source_name="Query Source"
                    )
                )
                program = self.compile(
                    permanent(text, suffix=122_001_000 + index),
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id") == QUERY_HANDLER
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_query_characteristic_descriptors_fail_closed_without_behavior(self):
        descriptor = query_self_characteristics_handler(
            "This creature gets +1/+1 for each artifact you control.",
            source_name="Query Source",
        )[1]
        registry = default_ability_fragment_registry()
        registry.validate(descriptor)
        malformed = (
            {**descriptor, "unknown": True},
            {
                **descriptor,
                "fragment": {
                    **descriptor["fragment"],
                    "value": {
                        **descriptor["fragment"]["value"],
                        "add_abilities": True,
                    },
                },
            },
            {
                **descriptor,
                "fragment": {
                    **descriptor["fragment"],
                    "value": {
                        **descriptor["fragment"]["value"],
                        "quantity": {
                            **descriptor["fragment"]["value"]["quantity"],
                            "scope": "chosen_player_zone",
                        },
                    },
                },
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(value)

    def test_query_characteristic_compiler_mutant_is_killed(self):
        record = permanent(
            "This creature gets +1/+1 for each artifact you control.",
            suffix=122_002_000,
        )

        def assert_compiled() -> None:
            program = self.compile(record, trust_level="provisional")
            self.assertTrue(
                any(
                    descriptor.get("handler_id") == QUERY_HANDLER
                    for ability in program.abilities
                    for descriptor in ability.handlers
                )
            )

        assert_compiled()
        with mock.patch(
            "quorune.compiler.runtime_templates.query_self_characteristics_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiled()

    def test_query_ability_removal_sibling_withholds_runtime_admission(self):
        program = self.compile(
            permanent(
                "This creature gets +1/+1 for each artifact you control.\n"
                "This creature loses all abilities.",
                suffix=122_002_001,
            ),
            trust_level="provisional",
        )
        self.assertTrue(
            any(
                descriptor.get("handler_id") == QUERY_HANDLER
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


class TypedQuerySelfCharacteristicRuntimeTests(unittest.TestCase):
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

    def test_transforming_query_characteristics_keep_replacement_siblings_fail_closed(self):
        capabilities = load_default_capability_registry()
        cases = (
            transforming_query_saga(
                suffix=122_002_002,
                front_name="Behold the Unspeakable",
                back_name="Vision of the Unspeakable",
                back_keyword="Flying",
                query_text="This creature gets +1/+1 for each card in your hand.",
                color="U",
            ),
            transforming_query_saga(
                suffix=122_002_003,
                front_name="Boseiju Reaches Skyward",
                back_name="Branch of Boseiju",
                back_keyword="Reach",
                query_text="This creature gets +1/+1 for each land you control.",
                color="G",
            ),
        )
        for record in cases:
            with self.subTest(name=record.name):
                program = compile_card_program(
                    _NoRulingsDatabase(),
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertTrue(
                    any(
                        descriptor.get("handler_id") == QUERY_HANDLER
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
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
                binding = bind_card_program_runtime(
                    program,
                    capability_registry=capabilities,
                    profile="commander_review",
                )
                self.assertFalse(binding["strict_capability_ready"])
                self.assertFalse(binding["compatible_ready"])

    @staticmethod
    def source(engine, *, seat: str, fragments, power: int = 1, toughness: int = 1):
        ref = engine.create_token(
            seat,
            name="Query Source",
            characteristics={
                "type_line": "Token Creature — Shapeshifter",
                "power": str(power),
                "toughness": str(toughness),
                "ability_fragments": list(fragments),
            },
            reason="typed query-characteristic fixture",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def token(engine, seat: str, *, type_line: str, name: str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={"type_line": type_line, "power": "1", "toughness": "1"},
            reason="typed query-characteristic counted object",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    def test_query_quantities_recompute_controller_correct_characteristics(self):
        session = self.session(122_003_000)
        engine = session.engine
        source = self.source(
            engine,
            seat="A",
            fragments=(
                query_fragment(
                    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                    types=("artifact",),
                ),
            ),
        )
        own = self.token(engine, "A", type_line="Token Artifact — Clue", name="A Clue")
        opposing = self.token(engine, "B", type_line="Token Artifact — Food", name="B Food")
        self.assertEqual("2", engine._effective_card_data(source)["power"])

        engine.change_control(source.object_id, "B", reason="query controller witness")
        self.assertEqual("2", engine._effective_card_data(source)["power"])
        engine.move_card(opposing.object_id, "graveyard", log=False)
        self.assertEqual("1", engine._effective_card_data(source)["power"])
        self.assertEqual("battlefield", own.zone)

        opponent_source = self.source(
            engine,
            seat="A",
            fragments=(
                query_fragment(
                    scope=CharacteristicQuantityScope.OPPONENT_ZONES,
                    types=("creature",),
                    power=1,
                    toughness=0,
                ),
            ),
        )
        for seat in ("B", "C", "D"):
            self.token(
                engine,
                seat,
                type_line="Token Creature — Citizen",
                name=f"{seat} Citizen",
            )
        self.assertEqual("5", engine._effective_card_data(opponent_source)["power"])

        graveyard_source = self.source(
            engine,
            seat="A",
            fragments=(
                query_fragment(
                    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                    zone="graveyard",
                    types=("creature",),
                ),
            ),
        )
        engine.change_control(
            graveyard_source.object_id,
            "B",
            reason="query graveyard controller witness",
        )

        def creature_from_library(seat: str):
            return next(
                engine.state.cards[object_id]
                for object_id in engine.state.players[seat].zones["library"]
                if "creature"
                in engine._type_parts(
                    engine.card_record(engine.state.cards[object_id]).type_line
                )[0]
            )

        b_creature = creature_from_library("B")
        engine.move_card(b_creature.object_id, "graveyard", log=False)
        a_creatures = []
        for _ in range(2):
            candidate = creature_from_library("A")
            a_creatures.append(candidate)
            engine.move_card(candidate.object_id, "graveyard", log=False)
        self.assertEqual(
            "2", engine._effective_card_data(graveyard_source)["power"]
        )
        self.assertEqual(2, len(a_creatures))

    def test_query_counts_use_layer_five_types_and_compose_in_layer_seven_c(self):
        session = self.session(122_003_001)
        engine = session.engine
        source = self.source(
            engine,
            seat="A",
            fragments=(
                query_fragment(
                    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                    types=("artifact",),
                    power=1,
                    toughness=0,
                ),
            ),
            power=2,
            toughness=5,
        )
        counted = self.token(
            engine,
            "A",
            type_line="Token Creature — Citizen",
            name="Layer Four Artifact",
        )
        counted.annotations["continuous_add_types"] = ["Artifact"]
        self.assertEqual("3", engine._effective_card_data(source)["power"])

        card = CardInstance(
            object_id="layered-source",
            ref="LAYERED-SOURCE",
            oracle_id="custom-token:layered-source",
            printed_name="Layered Source",
            owner="A",
            controller="A",
            zone="battlefield",
            is_token=True,
        )
        base = {
            "name": "Layered Source",
            "type_line": "Creature — Shapeshifter",
            "power": "1",
            "toughness": "1",
            "ability_fragments": [
                query_fragment(
                    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                    types=("artifact",),
                    power=1,
                    toughness=0,
                )
            ],
        }
        effects = (
            ContinuousEffect(
                effect_id="set-base",
                source_id="set-base",
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7b",
                timestamp=0,
                operations=(ContinuousOperation("set_power_toughness", [2, 5]),),
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            ),
            ContinuousEffect(
                effect_id="switch",
                source_id="switch",
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7d",
                timestamp=1,
                operations=(ContinuousOperation("switch_power_toughness"),),
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            ),
        )
        current = evaluate_card_characteristics(
            card,
            base,
            runtime_effects=effects,
            query_count_resolver=lambda _quantity: 2,
        )
        self.assertEqual("5", current["power"])
        self.assertEqual("4", current["toughness"])

    def test_query_fragment_copy_uses_current_source_and_controller(self):
        session = self.session(122_003_002)
        engine = session.engine
        source = self.source(
            engine,
            seat="A",
            fragments=(
                query_fragment(
                    scope=CharacteristicQuantityScope.SOURCE_COUNTER,
                    counter_name="oil",
                ),
                query_fragment(
                    scope=CharacteristicQuantityScope.ATTACHED_TO_SOURCE,
                    types=("artifact",),
                    subtypes=("equipment",),
                ),
            ),
        )
        source.counters["oil"] = 2
        equipment = self.token(
            engine,
            "A",
            type_line="Token Artifact — Equipment",
            name="Attached Equipment",
        )
        equipment.attached_to = source.object_id
        source.attachments.append(equipment.object_id)
        self.assertEqual("4", engine._effective_card_data(source)["power"])

        copied_ref = engine.create_token(
            "B",
            name="",
            copy_of=source.ref,
            reason="query fragment copy witness",
        )[0]
        copied = engine._resolve_object("B", copied_ref, zones={"battlefield"})
        copied.counters["oil"] = 1
        copied_data = engine._effective_card_data(copied)
        self.assertEqual("2", copied_data["power"])
        self.assertTrue(
            any(
                fragment.get("kind") == "query_characteristic_modifier"
                for fragment in copied_data["ability_fragments"]
            )
        )

    def test_query_hand_count_projects_and_replays_without_hidden_identity(self):
        session = self.session(122_003_003)
        engine = session.engine
        source = self.source(
            engine,
            seat="A",
            fragments=(
                query_fragment(
                    scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                    zone="hand",
                    power=1,
                    toughness=0,
                ),
            ),
        )
        hand = list(engine.state.players["A"].zones["hand"])
        hidden = engine.state.cards[hand[0]]
        self.assertEqual(
            1 + len(hand), int(engine._effective_card_data(source)["power"])
        )
        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn(hidden.object_id, rendered)
        self.assertNotIn(hidden.ref, rendered)
        self.assertNotIn(hidden.printed_name, rendered)

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
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "query-characteristic-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
