from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import (
    StaticComponentSpec,
    ability_fragment_to_dict,
)
from quorune.card_programs import (
    bind_card_program_runtime,
    compile_card_program,
)
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from quorune.compiler.continuous_templates import (
    fixed_public_state_characteristics_handler,
)
from quorune.continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionError,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSnapshot,
    FixedPublicStateConditionSpec,
)
from quorune.continuous_effects import (
    CharacteristicState,
    Layer,
    evaluate_continuous_effects,
)
from quorune.deck import DeckLoader
from quorune.engine import CommanderEngine
from quorune.model import CardInstance
from quorune.object_predicate import (
    ObjectQuerySpec,
    PermanentStatePredicateSpec,
)
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import (
    ContinuousEffectSourceContext,
    default_continuous_effect_component_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


CAPABILITY_ID = "continuous.characteristics.fixed_public_state"
TEMPLATE_ID = "continuous-fixed-public-state-characteristics-v1"
QUERY_HANDLER_ID = "ability.static.query-characteristic-modifier.v1"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-public-state-characteristics-cards.json"
)


def focused_card_database(directory: str) -> CardDatabase:
    path = Path(directory) / "fixed-public-state-characteristics.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-public-state-interaction-cards.json",
        ],
        path,
    )
    return CardDatabase(path)


def source_context(
    *,
    active_player: str | None = "A",
    hand_count: int = 3,
    graveyard_count: int = 0,
    controller_life: int = 40,
    opponent_life: tuple[int, ...] = (40, 40, 40),
    turn_sequence: int = 4,
    entered_turn_sequence: int = 1,
    counters: tuple[tuple[str, int], ...] = (),
    draw_count: int = 0,
    spell_count: int = 0,
    noncreature_spell_count: int = 0,
    instant_sorcery_count: int = 0,
    opponent_poison: tuple[int, ...] = (),
    controller_is_monarch: bool = False,
    source_query_matches: bool | None = None,
    attached_query_matches: bool | None = None,
    condition_quantity: int | None = None,
) -> ContinuousEffectSourceContext:
    return ContinuousEffectSourceContext(
        source_object_id="public-state-source",
        source_ref="PUBLIC-STATE-SOURCE",
        source_controller="A",
        source_timestamp=7,
        component_id="public-state:0",
        source_logical_object_id="public-state-source@1",
        public_state=FixedPublicStateConditionSnapshot(
            source_controller="A",
            active_player=active_player,
            controller_hand_count=hand_count,
            controller_graveyard_card_count=graveyard_count,
            controller_life=controller_life,
            opponent_life_totals=opponent_life,
            turn_sequence=turn_sequence,
            source_entered_battlefield_turn_sequence=(
                entered_turn_sequence
            ),
            source_counters=counters,
            controller_draw_count=draw_count,
            controller_spell_cast_count=spell_count,
            controller_noncreature_spell_cast_count=(
                noncreature_spell_count
            ),
            controller_instant_sorcery_cast_count=instant_sorcery_count,
            opponent_poison_counter_counts=opponent_poison,
            controller_is_monarch=controller_is_monarch,
            source_query_matches=source_query_matches,
            attached_query_matches=attached_query_matches,
            condition_quantity=condition_quantity,
        ),
    )


class FixedPublicStateCharacteristicCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_closed_public_state_grammar_compiles_capability_closed(self):
        cases = {
            "Fresh-Faced Recruit": "controller_turn",
            "Street Riot": "controller_turn",
            "Chaos Imps": "source_counter_at_least",
            "Keldon Strike Team": "source_entered_this_turn",
            "Neheb, the Worthy": "controller_hand_count_at_most",
        }
        for name, condition_kind in cases.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == TEMPLATE_ID
                ]
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(
                    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
                    node.handlers[0]["handler_id"],
                )
                self.assertEqual(
                    condition_kind,
                    node.handlers[0]["source_condition"]["kind"],
                )
                self.assertIn(CAPABILITY_ID, node.capability_dependencies)
                self.assertGreater(node.span.line, 0)

        street = compile_card_program(
            self.db,
            self.db.lookup("Street Riot"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual((), street.residuals)
        ability = next(
            ability
            for ability in street.abilities
            if ability.provenance.get("template_id") == TEMPLATE_ID
        )
        self.assertGreaterEqual(
            set(ability.capability_dependencies),
            {
                CAPABILITY_ID,
                "continuous.power_toughness.fixed_anthem",
                "continuous.ability.fixed_query_keyword_grant",
                "combat.damage.assignment.trample",
            },
        )

    def test_graveyard_threshold_migrates_to_typed_query_owner(self):
        program = compile_card_program(
            self.db,
            self.db.lookup("Krosan Beast"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual((), program.residuals)
        handlers = [
            descriptor.get("handler_id")
            for ability in program.abilities
            for descriptor in ability.handlers
        ]
        self.assertIn(QUERY_HANDLER_ID, handlers)
        self.assertNotIn(FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID, handlers)

    def test_public_object_state_and_query_conditions_compile_closed(self):
        cases = {
            "Public State Equipment Standard": "source_matches_query",
            "Public State Attachment Standard": "attached_matches_query",
            "Public State Metalcraft Standard": "query_count_at_least",
        }
        for name, condition_kind in cases.items():
            with self.subTest(name=name):
                program = compile_card_program(
                    self.db,
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual((), program.residuals)
                descriptor = next(
                    descriptor
                    for ability in program.abilities
                    for descriptor in ability.handlers
                    if descriptor.get("handler_id")
                    == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
                )
                self.assertEqual(
                    condition_kind,
                    descriptor["source_condition"]["kind"],
                )
                self.assertEqual(2, descriptor["source_condition"]["schema_version"])
                default_continuous_effect_component_registry().validate(
                    descriptor
                )

    def test_fixed_public_condition_queries_compile_across_authoritative_facts(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        cases = (
            (
                "This creature has first strike as long as it's attacking.",
                "source_matches_query",
            ),
            (
                "As long as this enchantment has seven or more quest "
                "counters on it, creatures you control get +5/+5.",
                "source_counter_at_least",
            ),
            (
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1 and has flying.",
                "query_count_at_least",
            ),
            (
                "As long as you control no untapped lands, this creature "
                "gets +2/+1.",
                "query_count_at_most",
            ),
            (
                "This creature gets +3/+3 as long as there is a land card "
                "in your graveyard.",
                "query_count_at_least",
            ),
            (
                "As long as you have seven or more cards in hand, this "
                "creature has first strike.",
                "controller_hand_count_at_least",
            ),
            (
                "As long as you've drawn two or more cards this turn, this "
                "creature has lifelink.",
                "controller_draw_count_at_least",
            ),
            (
                "As long as you've cast two or more noncreature spells this "
                "turn, this creature has double strike.",
                "controller_noncreature_spell_cast_count_at_least",
            ),
            (
                "As long as you've cast two or more spells this turn, this "
                "creature gets +2/+0.",
                "controller_spell_cast_count_at_least",
            ),
            (
                "This creature has flying as long as you've cast an instant "
                "or sorcery spell this turn.",
                "controller_instant_sorcery_cast_count_at_least",
            ),
            (
                "This creature has deathtouch as long as an opponent has "
                "three or more poison counters.",
                "opponent_poison_counter_at_least",
            ),
            (
                "As long as you're the monarch, permanents you control have "
                "hexproof.",
                "controller_is_monarch",
            ),
        )
        for index, (text, expected_kind) in enumerate(cases):
            with self.subTest(text=text):
                record = replace(
                    base,
                    oracle_id=(
                        f"00000000-0000-4000-8000-{118_240_000 + index:012d}"
                    ),
                    name=f"Public Condition Query {index}",
                    type_line="Creature — Fixture",
                    oracle_text=text,
                    keywords=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(TEMPLATE_ID, node.template_id)
                condition = node.handlers[0]["source_condition"]
                self.assertEqual(expected_kind, condition["kind"])
                if expected_kind == "controller_is_monarch":
                    self.assertIn(
                        "variant.monarch.designate",
                        node.capability_dependencies,
                    )
                default_continuous_effect_component_registry().validate(
                    node.handlers[0]
                )

        attached_state = fixed_public_state_characteristics_handler(
            "Enchanted creature has shroud as long as it's untapped.",
            source_name="Attached Condition Query",
        )
        self.assertIsNotNone(attached_state)
        self.assertEqual(
            "attached_matches_query",
            attached_state[1]["source_condition"]["kind"],
        )
        attached_color = fixed_public_state_characteristics_handler(
            "Enchanted creature gets +1/+2 as long as it's white.",
            source_name="Attached Color Query",
        )
        self.assertIsNotNone(attached_color)
        self.assertEqual(
            ["W"],
            attached_color[1]["source_condition"]["predicate"]["colors_all"],
        )

    def test_fixed_public_condition_models_use_closed_current_facts(self):
        cases = (
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_LEAST,
                    amount=7,
                ),
                source_context(hand_count=7).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.CONTROLLER_DRAW_COUNT_AT_LEAST,
                    amount=2,
                ),
                source_context(draw_count=2).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind
                    .CONTROLLER_NONCREATURE_SPELL_CAST_COUNT_AT_LEAST,
                    amount=2,
                ),
                source_context(noncreature_spell_count=2).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.OPPONENT_POISON_COUNTER_AT_LEAST,
                    amount=3,
                ),
                source_context(opponent_poison=(0, 3, 1)).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH,
                ),
                source_context(controller_is_monarch=True).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,
                    amount=0,
                    quantity=CharacteristicQuantitySpec(
                        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                        query=ObjectQuerySpec(zones=("battlefield",)),
                    ),
                    schema_version=2,
                ),
                source_context(condition_quantity=0).public_state,
            ),
        )
        for condition, snapshot in cases:
            with self.subTest(kind=condition.kind):
                assert snapshot is not None
                self.assertTrue(condition.matches(snapshot))
                self.assertFalse(
                    condition.matches(
                        replace(
                            snapshot,
                            controller_hand_count=0,
                            controller_draw_count=0,
                            controller_noncreature_spell_cast_count=0,
                            opponent_poison_counter_counts=(),
                            controller_is_monarch=False,
                            condition_quantity=1,
                        )
                    )
                )

    def test_fixed_public_condition_dependencies_fail_closed(self):
        registry_value = json.loads(
            (
                ROOT
                / "quorune"
                / "rules"
                / "capability-registry.json"
            ).read_text(encoding="utf-8")
        )
        base = self.db.lookup("Fresh-Faced Recruit")
        cases = (
            (
                "state_query.permanent.public_state_predicate",
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1.",
            ),
            (
                "variant.monarch.designate",
                "As long as you're the monarch, this creature gets +1/+1.",
            ),
        )
        for index, (blocked_id, text) in enumerate(cases):
            with self.subTest(blocked_id=blocked_id):
                mutated = copy.deepcopy(registry_value)
                blocked = next(
                    row
                    for row in mutated["capabilities"]
                    if row["id"] == blocked_id
                )
                blocked["status"] = "blocked"
                blocked["blockers"] = ["focused public-condition mutation"]
                record = replace(
                    base,
                    oracle_id=(
                        f"00000000-0000-4000-8000-{118_250_000 + index:012d}"
                    ),
                    name=f"Blocked Public Condition {index}",
                    type_line="Creature — Fixture",
                    oracle_text=text,
                    keywords=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(mutated),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)

        kindred_condition = fixed_public_state_characteristics_handler(
            "As long as enchanted permanent is a Goblin, it gets +1/+1.",
            source_name="Kindred Condition Fixture",
        )
        self.assertIsNotNone(kindred_condition)
        kindred_predicate = kindred_condition[1]["source_condition"]["predicate"]
        self.assertEqual([], kindred_predicate["types_all"])
        self.assertEqual(["goblin"], kindred_predicate["subtypes_any"])

        base = self.db.lookup("Fresh-Faced Recruit")
        conditional_indestructible = replace(
            base,
            oracle_id="00000000-0000-4000-8000-000011820005",
            name="Conditional Indestructible Standard",
            oracle_text=(
                "This creature has indestructible as long as it is tapped."
            ),
            keywords=(),
        )
        program = compile_card_program(
            self.db,
            conditional_indestructible,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual((), program.residuals)
        descriptor = next(
            descriptor
            for ability in program.abilities
            for descriptor in ability.handlers
            if descriptor.get("handler_id")
            == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
        )
        self.assertIs(
            descriptor["source_condition"]["predicate"]["state_predicate"][
                "tapped"
            ],
            True,
        )
        self.assertEqual(
            ["Indestructible"], descriptor["modifier"]["add_abilities"]
        )
        self.assertIn(
            "permanent.indestructible.ordinary",
            program.capability_dependencies,
        )

        direct = {
            "Public State Attack Standard": (
                "continuous.characteristics.fixed-query-grant.v1",
                "attacking",
            ),
            "Public State Modification Standard": (
                "continuous.ability.fixed-query-keyword-grant.v1",
                "modified",
            ),
        }
        for name, (handler_id, state_field) in direct.items():
            with self.subTest(name=name):
                program = compile_card_program(
                    self.db,
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual((), program.residuals)
                descriptor = next(
                    descriptor
                    for ability in program.abilities
                    for descriptor in ability.handlers
                    if descriptor.get("handler_id") == handler_id
                )
                state = descriptor["condition"]["predicate"]["state_predicate"]
                self.assertIs(state[state_field], True)
                default_continuous_effect_component_registry().validate(
                    descriptor
                )

    def test_extended_state_predicates_preserve_legacy_serialization(self):
        legacy = PermanentStatePredicateSpec(
            counter_name="+1/+1",
            minimum_counter_count=1,
        )
        self.assertEqual(
            {
                "entered_this_turn",
                "tapped",
                "counter_name",
                "minimum_counter_count",
            },
            set(legacy.to_dict()),
        )
        current = ObjectQuerySpec(
            zones=("battlefield",),
            state_predicate=PermanentStatePredicateSpec(attacking=True),
        )
        restored = ObjectQuerySpec.from_dict(current.to_dict())
        self.assertEqual(current, restored)
        self.assertIs(restored.state_predicate.attacking, True)
        with self.assertRaises(FixedPublicStateConditionError):
            FixedPublicStateConditionSpec(
                FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
                predicate=current,
                schema_version=1,
            )

    def test_threshold_opponent_anthem_preserves_the_query_relation(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        record = replace(
            base,
            oracle_id="00000000-0000-4000-8000-000011820004",
            oracle_text=(
                "Threshold — As long as there are seven or more cards in "
                "your graveyard, creatures your opponents control get -1/-0."
            ),
            keywords=(),
        )
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual((), program.residuals)
        descriptor = next(
            descriptor
            for ability in program.abilities
            for descriptor in ability.handlers
            if descriptor.get("handler_id")
            == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
        )
        self.assertEqual(
            "source_opponents", descriptor["target"]["target_controller"]
        )
        default_continuous_effect_component_registry().validate(descriptor)

    def test_unrepresented_conditions_and_bodies_remain_residual(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        unsupported = (
            "Delirium — This creature gets +2/+2 as long as there are four "
            "or more card types among cards in your graveyard.",
            "This creature gets +X/+X during your turn.",
            "During your turn, this creature has ward {2}.",
            "During your turn, this creature has \"{T}: Draw a card.\"",
            "As long as this creature is equipped, it has ward {2}.",
            "Creatures you control have flying as long as you control an "
            "artifact with flying.",
            "Artifacts you control have shroud as long as you control three "
            "artifacts.",
            "Artifacts you control have shroud as long as an opponent "
            "controls two or more artifacts.",
            "As long as you control a creature with flying, this creature "
            "gets +1/+1.",
            "As long as an opponent has eight or more cards in their "
            "graveyard, creatures you control have flying.",
            "As long as there are five or more mana values among cards in "
            "your graveyard, this creature gets +1/+1.",
            "As long as this Equipment has four or more counters on it, "
            "equipped creature has double strike.",
        )
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                record = replace(
                    base,
                    oracle_id=f"00000000-0000-4000-8000-{118_200_000 + index:012d}",
                    oracle_text=text,
                    keywords=(),
                )
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id")
                        == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_public_state_descriptors_fail_closed_without_effects(self):
        compiled = fixed_public_state_characteristics_handler(
            "During your turn, this creature gets +2/+0 and has first strike.",
            source_name="Descriptor Fixture",
        )
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        registry = default_continuous_effect_component_registry()
        registry.validate(descriptor)
        self.assertEqual(2, len(registry.lower(descriptor, source_context())))
        self.assertEqual(
            (),
            registry.lower(
                descriptor,
                source_context(active_player="B"),
            ),
        )

        malformed = []
        for path, value in (
            (("unknown",), True),
            (("schema_version",), True),
            (("source_condition", "kind"), "metalcraft"),
            (("source_condition", "amount"), 1),
            (("target", "kind"), "opponents"),
            (("target", "exclude_source"), 1),
            (("modifier", "add_abilities"), ["Ward {2}"]),
            (("modifier", "power"), True),
        ):
            candidate = copy.deepcopy(descriptor)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            malformed.append(candidate)
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(candidate)

    def test_condition_and_target_mutants_change_card_program_fingerprint(self):
        record = self.db.lookup("Street Riot")
        expected = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        original = fixed_public_state_characteristics_handler

        for mutation in ("condition", "target"):
            def mutated(text: str, *, source_name: str):
                compiled = original(text, source_name=source_name)
                if compiled is None:
                    return None
                template_id, descriptor, capabilities = compiled
                changed = copy.deepcopy(descriptor)
                if mutation == "condition":
                    changed["source_condition"]["kind"] = "other_turn"
                else:
                    changed["target"]["target_controller"] = "any"
                return template_id, changed, capabilities

            with self.subTest(mutation=mutation), mock.patch(
                "quorune.compiler.runtime_templates."
                "fixed_public_state_characteristics_handler",
                side_effect=mutated,
            ):
                changed = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertNotEqual(
                    expected.fingerprint,
                    changed.fingerprint,
                )


class FixedPublicStateCharacteristicRuntimeTests(unittest.TestCase):
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
        engine = session.engine
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return session

    def add_source(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "battlefield",
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(registration["runtime_handlers_promoted"], 1)
        card = CardInstance(
            object_id=f"public-state:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            entered_battlefield_turn_sequence=engine.state.turn_sequence,
            known_to=(
                [seat]
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
            revealed_to=(
                []
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def add_constructed_condition_source(
        self,
        session,
        *,
        ref: str,
        texts: tuple[str, ...],
    ) -> CardInstance:
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name=f"Condition Source {ref}",
        )
        characteristics = (
            source.annotations.get("object_characteristics")
            or source.annotations["token_characteristics"]
        )
        fragments = characteristics.setdefault(
            "ability_fragments",
            [],
        )
        for index, text in enumerate(texts):
            compiled = fixed_public_state_characteristics_handler(
                text,
                source_name=source.printed_name,
            )
            self.assertIsNotNone(compiled)
            assert compiled is not None
            key = f"test:public-condition:{ref}:{index}"
            engine.semantics.put(
                SemanticProgram(
                    key=key,
                    label=f"Public condition {ref} {index}",
                    oracle_id=source.oracle_id,
                    ability_id=f"static:{key}",
                    active_zone="battlefield",
                    event="characteristics.evaluate",
                    handlers=[compiled[1]],
                    trust_level="provisional",
                )
            )
            fragments.append(
                ability_fragment_to_dict(StaticComponentSpec(key))
            )
        return source

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def creature(
        engine,
        *,
        seat: str,
        name: str,
        subtype: str = "Minotaur",
        colors: tuple[str, ...] = (),
    ) -> CardInstance:
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": f"Token Creature — {subtype}",
                "power": "3",
                "toughness": "3",
                "keywords": [],
                "colors": list(colors),
            },
            reason="fixed public-state characteristic assurance",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def attach(source: CardInstance, target: CardInstance) -> None:
        source.attached_to = target.object_id
        if source.object_id not in target.attachments:
            target.attachments.append(source.object_id)

    def test_live_public_object_state_and_layer_five_queries_recompute(self):
        session = self.session(118_220_004)
        engine = session.engine
        self.add_source(
            session,
            name="Public State Attack Standard",
            ref="ATTACK-STANDARD",
        )
        self.add_source(
            session,
            name="Public State Modification Standard",
            ref="MODIFIED-STANDARD",
        )
        metalcraft = self.add_source(
            session,
            name="Public State Metalcraft Standard",
            ref="METALCRAFT-STANDARD",
        )
        target = self.creature(
            engine,
            seat="A",
            name="Public State Target",
        )

        baseline = engine._effective_card_data(target)
        self.assertEqual("3", baseline["power"])
        self.assertNotIn("Lifelink", baseline["keywords"])
        self.assertNotIn("Trample", baseline["keywords"])

        target.attacking = "B"
        attacking = engine._effective_card_data(target)
        self.assertEqual("4", attacking["power"])
        self.assertIn("Lifelink", attacking["keywords"])
        target.attacking = None

        target.counters["stun"] = 1
        self.assertIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )
        target.counters.clear()

        opposing_aura = self.add_source(
            session,
            name="Aboshan's Desire",
            ref="OPPOSING-AURA",
            seat="B",
        )
        self.attach(opposing_aura, target)
        aura_state = engine._public_object_query_result(target)
        self.assertTrue(aura_state.enchanted)
        self.assertFalse(aura_state.modified)
        self.assertNotIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )
        engine.change_control(
            opposing_aura.object_id,
            "A",
            reason="modified-state controller witness",
        )
        controlled_aura_state = engine._public_object_query_result(target)
        self.assertTrue(controlled_aura_state.enchanted)
        self.assertTrue(controlled_aura_state.modified)
        self.assertIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )

        conditioned = self.add_source(
            session,
            name="Public State Equipment Standard",
            ref="EQUIPPED-STANDARD",
        )
        equipment = self.add_source(
            session,
            name="Javelin of Lightning",
            ref="STATE-EQUIPMENT",
            seat="B",
        )
        self.attach(equipment, conditioned)
        equipment_state = engine._public_object_query_result(conditioned)
        self.assertTrue(equipment_state.equipped)
        self.assertTrue(equipment_state.modified)
        equipped = engine._effective_card_data(conditioned)
        self.assertEqual("3", equipped["power"])
        self.assertEqual("3", equipped["toughness"])
        self.assertIn("Flying", equipped["keywords"])
        equipment.attached_to = None
        conditioned.attachments.remove(equipment.object_id)
        unequipped = engine._effective_card_data(conditioned)
        self.assertEqual("2", unequipped["power"])
        self.assertNotIn("Flying", unequipped["keywords"])

        attachment = self.add_source(
            session,
            name="Public State Attachment Standard",
            ref="ATTACHMENT-STANDARD",
        )
        black_target = self.creature(
            engine,
            seat="A",
            name="Black Attachment Target",
            colors=("B",),
        )
        self.attach(attachment, black_target)
        black = engine._effective_card_data(black_target)
        self.assertEqual("4", black["power"])
        self.assertIn("Wither", black["keywords"])
        black_target.annotations["copy_overrides"]["colors"] = ["U"]
        blue = engine._effective_card_data(black_target)
        self.assertEqual("3", blue["power"])
        self.assertNotIn("Wither", blue["keywords"])

        artifact_refs = engine.create_token(
            "A",
            name="Public Artifact",
            quantity=2,
            characteristics={
                "type_line": "Token Artifact",
                "keywords": [],
            },
            reason="public-state count witness",
        )
        artifact = engine._resolve_object(
            "A",
            artifact_refs[0],
            zones={"battlefield"},
        )
        type_changed = self.creature(
            engine,
            seat="A",
            name="Layer Five Type Witness",
        )
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )
        type_changed.annotations["continuous_add_types"] = ["Artifact"]
        self.assertIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )
        type_changed.annotations["continuous_add_types"] = []
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )

        type_changed.annotations["continuous_add_types"] = ["Artifact"]
        engine.move_card(metalcraft.object_id, "graveyard", log=False)
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )

        engine.permissions.invalidate_current()
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
                "plan": "REPLAY_TYPED_PUBLIC_STATE_CHARACTERISTICS",
                "reason": "Verify typed public-state queries from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-state-query-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_public_condition_queries_follow_authoritative_history_and_control(self):
        session = self.session(118_220_007)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="HISTORY-QUERY",
            texts=(
                "This creature gets +2/+0 as long as you've drawn two or "
                "more cards this turn.",
                "This creature gets +0/+2 as long as you've cast two or "
                "more noncreature spells this turn.",
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1 and has vigilance.",
            ),
        )
        status_source = self.add_constructed_condition_source(
            session,
            ref="PLAYER-STATUS",
            texts=(
                "This creature gets +1/+0 as long as an opponent has three "
                "or more poison counters.",
                "This creature gets +0/+1 as long as you're the monarch.",
            ),
        )
        absence_source = self.add_constructed_condition_source(
            session,
            ref="ABSENCE-QUERY",
            texts=(
                "This creature gets +1/+1 as long as you control no untapped "
                "lands.",
            ),
        )

        def stats() -> tuple[int, int, set[str]]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                current = engine._effective_card_data(source)
            return (
                int(current["power"]),
                int(current["toughness"]),
                set(current["keywords"]),
            )

        self.assertEqual((3, 3, set()), stats())
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            absent = engine._effective_card_data(absence_source)
        self.assertEqual((4, 4), (int(absent["power"]), int(absent["toughness"])))
        land_ref = engine.create_token(
            "A",
            name="Condition Land",
            characteristics={"type_line": "Token Land — Forest"},
            reason="public condition tapped-state quantity",
        )[0]
        land = engine._resolve_object("A", land_ref, zones={"battlefield"})
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            present = engine._effective_card_data(absence_source)
        self.assertEqual(
            (3, 3),
            (int(present["power"]), int(present["toughness"])),
        )
        land.tapped = True
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            tapped = engine._effective_card_data(absence_source)
        self.assertEqual((4, 4), (int(tapped["power"]), int(tapped["toughness"])))
        engine.state.players["B"].poison = 3
        engine.become_monarch("A", reason="condition status fixture")
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            status = engine._effective_card_data(status_source)
        self.assertEqual((4, 4), (int(status["power"]), int(status["toughness"])))
        engine.state.players["B"].poison = 0
        engine.become_monarch("B", reason="condition status fixture")
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            inactive_status = engine._effective_card_data(status_source)
        self.assertEqual(
            (3, 3),
            (int(inactive_status["power"]), int(inactive_status["toughness"])),
        )
        multicolor = self.creature(
            engine,
            seat="A",
            name="Multicolor Condition Witness",
            colors=("R", "W"),
        )
        self.assertEqual((4, 4, {"Vigilance"}), stats())

        engine.apply_effect(
            {"op": "draw", "player": "A", "count": 2},
            actor="A",
        )
        self.assertEqual((6, 4, {"Vigilance"}), stats())
        engine._record_turn_history("spell_cast", actor="A", types=("creature",))
        self.assertEqual((6, 4, {"Vigilance"}), stats())
        engine._record_turn_history("spell_cast", actor="A", types=("instant",))
        engine._record_turn_history("spell_cast", actor="A", types=("sorcery",))
        self.assertEqual((6, 6, {"Vigilance"}), stats())

        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        for object_id in engine.state.players["A"].zones["hand"]:
            hidden = engine.state.cards[object_id]
            self.assertNotIn(hidden.object_id, rendered)
            self.assertNotIn(hidden.ref, rendered)

        engine.change_control(source.object_id, "B", reason="condition owner")
        self.assertEqual((3, 3, set()), stats())
        engine.change_control(source.object_id, "A", reason="condition owner")
        source.annotations["token_characteristics"]["colors"] = ["R", "W"]
        engine.move_card(multicolor.object_id, "graveyard", log=False)
        self.assertEqual((5, 5, set()), stats())

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
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
                "plan": "REPLAY_PUBLIC_CONDITION_QUERIES",
                "reason": "Verify public condition facts from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-condition-query-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_unrelated_fixed_conditions_do_not_scan_turn_history(self):
        session = self.session(118_220_008)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="NO-HISTORY-SCAN",
            texts=("During your turn, this creature gets +2/+0.",),
        )
        engine.state.active_player = "A"

        with (
            mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ),
            mock.patch(
                "quorune.card_programs.runtime.drawn_this_turn",
                side_effect=AssertionError("unexpected draw-history scan"),
            ),
            mock.patch(
                "quorune.card_programs.runtime.current_turn_history_events",
                side_effect=AssertionError("unexpected cast-history scan"),
            ),
        ):
            current = engine._effective_card_data(source)

        self.assertEqual(
            (5, 3),
            (int(current["power"]), int(current["toughness"])),
        )

    def test_public_state_conditions_recompute_layer_six_and_seven_results(self):
        registry = default_continuous_effect_component_registry()
        descriptor = fixed_public_state_characteristics_handler(
            "During your turn, this creature gets +2/+1 and has first strike.",
            source_name="State Source",
        )[1]
        effects = registry.lower(descriptor, source_context(active_player="A"))
        self.assertEqual([Layer.ABILITY, Layer.POWER_TOUGHNESS], [
            effect.layer for effect in effects
        ])
        source = evaluate_continuous_effects(
            CharacteristicState(
                name="State Source",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "public-state-source",
                "logical_object_id": "public-state-source@1",
                "ref": "PUBLIC-STATE-SOURCE",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(4, source["power"])
        self.assertEqual(3, source["toughness"])
        self.assertIn("First Strike", source["abilities"])

        unrelated = evaluate_continuous_effects(
            CharacteristicState(
                name="Other Creature",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "other",
                "logical_object_id": "other@1",
                "ref": "OTHER",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(2, unrelated["power"])
        self.assertNotIn("First Strike", unrelated["abilities"])
        stale_source = evaluate_continuous_effects(
            CharacteristicState(
                name="State Source",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "public-state-source",
                "logical_object_id": "public-state-source@2",
                "ref": "PUBLIC-STATE-SOURCE",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(2, stale_source["power"])
        self.assertNotIn("First Strike", stale_source["abilities"])
        self.assertEqual(
            (),
            registry.lower(descriptor, source_context(active_player="B")),
        )

        threshold = fixed_public_state_characteristics_handler(
            "Threshold — This creature gets +7/+7 as long as there are seven "
            "or more cards in your graveyard.",
            source_name="Threshold Source",
        )[1]
        self.assertEqual(
            (),
            registry.lower(threshold, source_context(graveyard_count=6)),
        )
        self.assertEqual(
            1,
            len(registry.lower(threshold, source_context(graveyard_count=7))),
        )

        counter = fixed_public_state_characteristics_handler(
            "This creature has trample as long as it has a +1/+1 counter on it.",
            source_name="Counter Source",
        )[1]
        self.assertEqual(
            (),
            registry.lower(counter, source_context(counters=())),
        )
        self.assertEqual(
            1,
            len(
                registry.lower(
                    counter,
                    source_context(counters=(("+1/+1", 1),)),
                )
            ),
        )

        attached_pronoun = fixed_public_state_characteristics_handler(
            "Enchanted creature has shroud as long as it's untapped.",
            source_name="Attached Pronoun Source",
        )[1]
        attached_condition = FixedPublicStateConditionSpec.from_dict(
            attached_pronoun["source_condition"]
        )
        self.assertFalse(
            attached_condition.matches(
                source_context(attached_query_matches=False).public_state
            )
        )
        self.assertTrue(
            attached_condition.matches(
                source_context(attached_query_matches=True).public_state
            )
        )

        opponent_anthem = fixed_public_state_characteristics_handler(
            "Threshold — As long as there are seven or more cards in your "
            "graveyard, creatures your opponents control get -1/-0.",
            source_name="Threshold Opponent Anthem",
        )[1]
        opponent_effects = registry.lower(
            opponent_anthem,
            source_context(graveyard_count=7),
        )

        def opponent_power(controller: str) -> int:
            return int(
                evaluate_continuous_effects(
                    CharacteristicState(
                        name=f"{controller} relation witness",
                        controller=controller,
                        card_types={"Creature"},
                        power=3,
                        toughness=3,
                    ),
                    opponent_effects,
                    context={
                        "object_id": f"{controller}-witness",
                        "logical_object_id": f"{controller}-witness@1",
                        "ref": f"{controller}-WITNESS",
                        "owner": controller,
                        "controller": controller,
                        "zone": "battlefield",
                    },
                ).characteristics["power"]
            )

        self.assertEqual(3, opponent_power("A"))
        self.assertEqual(2, opponent_power("B"))
        self.assertEqual(2, opponent_power("C"))

    def test_turn_gated_attachment_and_anthem_compose(self):
        session = self.session(118_220_001)
        engine = session.engine
        riot = self.add_source(session, name="Street Riot", ref="RIOT")
        javelin = self.add_source(
            session,
            name="Javelin of Lightning",
            ref="JAVELIN",
        )
        desire = self.add_source(
            session,
            name="Aboshan's Desire",
            ref="DESIRE",
            zone="hand",
        )
        target = self.creature(engine, seat="A", name="Conditional Target")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool["C"] = 4
        engine._activate(
            "A",
            {
                "source": javelin.ref,
                "ability": "ab4",
                "targets": [target.ref],
            },
        )
        self.assertEqual("builtin:equip", engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, javelin.attached_to)

        engine.state.players["A"].mana_pool["U"] = 1
        engine._cast(
            "A",
            {
                "card": desire.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.resolve_top(engine)
        self.assertEqual(target.object_id, desire.attached_to)

        active = engine._effective_card_data(target)
        self.assertEqual("6", active["power"])
        self.assertIn("First Strike", active["keywords"])
        self.assertIn("Trample", active["keywords"])
        self.assertIn("Flying", active["keywords"])

        threshold_cards = list(
            engine.state.players["A"].zones["library"][-7:]
        )
        for object_id in threshold_cards:
            engine.move_card(object_id, "graveyard", log=False)
        self.assertIn(
            "Shroud",
            engine._effective_card_data(target)["keywords"],
        )
        for object_id in threshold_cards:
            engine.move_card(object_id, "library", log=False)

        engine.state.active_player = "B"
        inactive = engine._effective_card_data(target)
        self.assertEqual("3", inactive["power"])
        self.assertNotIn("First Strike", inactive["keywords"])
        self.assertNotIn("Trample", inactive["keywords"])
        self.assertIn("Flying", inactive["keywords"])
        self.assertNotIn("Shroud", inactive["keywords"])

        engine.change_control(
            riot.object_id,
            "B",
            reason="public-state controller boundary witness",
        )
        engine.change_control(
            target.object_id,
            "B",
            reason="public-state target boundary witness",
        )
        changed = engine._effective_card_data(target)
        self.assertEqual("4", changed["power"])
        self.assertIn("Trample", changed["keywords"])
        self.assertNotIn("First Strike", changed["keywords"])

        engine.move_card(riot.object_id, "graveyard", log=False)
        departed = engine._effective_card_data(target)
        self.assertEqual("3", departed["power"])
        self.assertNotIn("Trample", departed["keywords"])

    def test_typed_aura_and_public_state_characteristics_compose(self):
        session = self.session(118_220_006)
        engine = session.engine
        aura = self.add_source(
            session,
            name="Public State Attachment Standard",
            ref="TYPED-PUBLIC-STATE-AURA",
            zone="hand",
        )
        target = self.creature(
            engine,
            seat="A",
            name="Typed Aura Public-State Target",
            colors=("B",),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})

        engine._cast(
            "A",
            {
                "card": aura.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.resolve_top(engine)

        self.assertEqual(target.object_id, aura.attached_to)
        self.assertTrue(engine._attachment_is_legal(aura, subtypes={"aura"}))
        enhanced = engine._effective_card_data(target)
        self.assertEqual("4", enhanced["power"])
        self.assertEqual("4", enhanced["toughness"])
        self.assertIn("Wither", enhanced["keywords"])

        target.annotations["copy_overrides"] = {
            "type_line": "Token Creature — Wall"
        }
        self.assertFalse(
            engine._attachment_is_legal(aura, subtypes={"aura"})
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", aura.zone)
        self.assertNotIn(
            "Wither",
            engine._effective_card_data(target)["keywords"],
        )

    def test_public_state_characteristics_execute_while_replacement_siblings_fail_closed(self):
        session = self.session(118_220_003)
        engine = session.engine
        record = self.db.lookup("Angel of Vitality")
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
        binding = bind_card_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])

        angel = self.add_source(
            session,
            name="Angel of Vitality",
            ref="ANGEL-OF-VITALITY",
        )
        engine.state.players["A"].life = 25
        active = engine._effective_card_data(angel)
        self.assertEqual("4", active["power"])
        self.assertEqual("4", active["toughness"])
        self.assertIn("Flying", active["keywords"])
        self.assertFalse(
            any(
                "replacement" in runtime_program.event
                and engine.semantic_program_is_current_trusted(runtime_program)
                for runtime_program in engine.semantics.programs_for_oracle(
                    angel.oracle_id
                )
            )
        )

    def test_hand_count_condition_projects_and_replays_without_hidden_identity(self):
        session = self.session(118_220_002)
        engine = session.engine
        self.add_source(session, name="Neheb, the Worthy", ref="NEHEB")
        target = self.creature(engine, seat="A", name="Private Count Target")
        hand = list(engine.state.players["A"].zones["hand"])
        for object_id in hand[1:]:
            engine.move_card(object_id, "library", log=False)
        hidden = engine.state.cards[hand[0]]
        engine.state.active_player = "A"

        self.assertEqual("5", engine._effective_card_data(target)["power"])
        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn(hidden.object_id, rendered)
        self.assertNotIn(hidden.ref, rendered)
        self.assertNotIn(hidden.printed_name, rendered)

        engine.move_card(
            engine.state.players["A"].zones["library"][-1],
            "hand",
            log=False,
        )
        self.assertEqual("3", engine._effective_card_data(target)["power"])
        engine.move_card(
            engine.state.players["A"].zones["hand"][-1],
            "library",
            log=False,
        )
        self.assertEqual("5", engine._effective_card_data(target)["power"])

        engine.permissions.invalidate_current()
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
            record_dir = Path(directory) / "public-state-characteristic-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
