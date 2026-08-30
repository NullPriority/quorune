from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import (
    change_permanent_counter,
    keep_all,
    load_assets,
    make_session,
)
from quorune.aerial_blocking import aerial_block_verdict
from quorune.attachments import attach_objects
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.combat import (
    assigns_in_damage_step,
    first_strike_step_required,
    ordinary_second_step_combatants,
)
from quorune.combat_damage_assignment import (
    build_combat_damage_assignment_proposal,
    CombatDamageParticipant,
)
from quorune.combat_damage_snapshot import (
    CombatAttackRelationship,
    CombatBlockRelationship,
    CombatDamageRecipient,
    CombatDamageSnapshot,
)
from quorune.defender import defender_prohibits_attack
from quorune.compiler.continuous_templates import (
    fixed_query_characteristic_grant_handler,
    fixed_query_keyword_grant_handler,
    fixed_power_toughness_anthem_handler,
)
from quorune.continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
)
from quorune.counter_placement import (
    CounterPlacementRequest,
    place_counters,
)
from quorune.haste import (
    is_summoning_sick,
    summoning_sickness_prohibits_attack,
    summoning_sickness_prohibits_tap_or_untap_cost,
)
from quorune.menace import current_menace_restriction
from quorune.model import CardInstance
from quorune.oracle_ir import register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import (
    ContinuousEffectSourceContext,
    default_continuous_effect_component_registry,
    SemanticNodeError,
)
from quorune.tap_state import tap_declared_attackers


HANDLER_ID = "continuous.ability.fixed-query-keyword-grant.v1"
TEMPLATE_ID = "continuous-fixed-query-keyword-grant-v2"
CHARACTERISTIC_HANDLER_ID = (
    "continuous.characteristics.fixed-query-grant.v1"
)
CHARACTERISTIC_TEMPLATE_ID = (
    "continuous-fixed-query-characteristic-grant-v1"
)
BASE_CAPABILITY = "continuous.ability.fixed_query_keyword_grant"
ANTHEM_HANDLER_ID = "continuous.anthem.fixed-query.v2"
SOURCE_NAMES = (
    "Aggressive Mammoth",
    "Cloudshredder Sliver",
    "Knighthood",
    "Levitation",
    "Mass Hysteria",
    "Rage Reflection",
    "Serra's Blessing",
)
COMPLETE_CARD_LOWER_BOUND = (
    "Abzan Battle Priest",
    "Abzan Falconer",
    "Ainok Bond-Kin",
    "Anaba Spirit Crafter",
    "Aven Brigadier",
    "Azorius Skyguard",
    "Bad Moon",
    "Blade Sliver",
    "Bonesplitter Sliver",
    "Bushmaster, Coiled Henchman",
    "Crowned Ceratok",
    "Cumber Stone",
    "Dampening Pulse",
    "Deranged Hermit",
    "Dread of Night",
    "Dreadhorde Twins",
    "Duskshell Crawler",
    "Elesh Norn, Grand Cenobite",
    "Eternal Skylord",
    "Exava, Rakdos Blood Witch",
    "Field Marshal",
    "Glass of the Guildpact",
    "Gleaming Overseer",
    "Gnarlid Colony",
    "Hagra Constrictor",
    "Haunter of Nightveil",
    "Kaervek, the Spiteful",
    "Longshot Squad",
    "Maze Abomination",
    "Maze Behemoth",
    "Maze Glider",
    "Maze Rusher",
    "Maze Sentinel",
    "Mer-Ek Nightblade",
    "Might Sliver",
    "Muscle Sliver",
    "Myr Matrix",
    "Night of Souls' Betrayal",
    "Plated Sliver",
    "Pridemalkin",
    "Sapphire Drake",
    "Sinew Sliver",
    "Skatewing Spy",
    "Stronghold Taskmaster",
    "Trollbred Guardian",
    "Tuskguard Captain",
    "Urborg Shambler",
    "Vizier of the Scorpion",
    "Watcher Sliver",
    "Zuberi, Golden Feather",
)


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _permanent(
    text: str,
    *,
    suffix: int,
    name: str,
    type_line: str = "Enchantment",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        type_line=type_line,
        oracle_text=text,
        power=None,
        toughness=None,
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


class FixedQueryKeywordGrantCompilerTests(unittest.TestCase):
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

    @staticmethod
    def keyword_ability(program):
        return next(
            ability
            for ability in program.abilities
            if any(
                descriptor.get("handler_id") == HANDLER_ID
                for descriptor in ability.handlers
            )
        )

    def test_closed_query_grammar_compiles_exact_with_consumer_capabilities(self):
        cases = (
            (
                "Creatures you control have haste.",
                "source_controller",
                False,
                {"types_all": ["creature"]},
                ["Haste"],
                {
                    "activation.tap_untap_cost.haste",
                    "combat.attack.haste",
                },
            ),
            (
                "Other creatures you control have trample.",
                "source_controller",
                True,
                {"types_all": ["creature"]},
                ["Trample"],
                {"combat.damage.assignment.trample"},
            ),
            (
                "White creatures you control have vigilance.",
                "source_controller",
                False,
                {"types_all": ["creature"], "colors_all": ["W"]},
                ["Vigilance"],
                {"combat.attack.vigilance"},
            ),
            (
                "Artifact creatures you control have flying.",
                "source_controller",
                False,
                {"types_all": ["artifact", "creature"]},
                ["Flying"],
                {"combat.block.flying"},
            ),
            (
                "Golem creatures you control have first strike.",
                "source_controller",
                False,
                {
                    "types_all": ["creature"],
                    "subtypes_all": ["golem"],
                },
                ["First Strike"],
                {"combat.damage.participation.strike_steps"},
            ),
            (
                "Vehicles you control have flying.",
                "source_controller",
                False,
                {
                    "types_all": ["artifact"],
                    "subtypes_all": ["vehicle"],
                },
                ["Flying"],
                {"combat.block.flying"},
            ),
            (
                "Creature tokens you control have haste.",
                "source_controller",
                False,
                {"types_all": ["creature"], "token": True},
                ["Haste"],
                {
                    "activation.tap_untap_cost.haste",
                    "combat.attack.haste",
                },
            ),
            (
                "All creatures have haste.",
                "any",
                False,
                {"types_all": ["creature"]},
                ["Haste"],
                {
                    "activation.tap_untap_cost.haste",
                    "combat.attack.haste",
                },
            ),
            (
                "All Sliver creatures have flying.",
                "any",
                False,
                {
                    "types_all": ["creature"],
                    "subtypes_all": ["sliver"],
                },
                ["Flying"],
                {"combat.block.flying"},
            ),
            (
                "All Slivers have trample.",
                "any",
                False,
                {
                    "types_all": ["creature"],
                    "subtypes_all": ["sliver"],
                },
                ["Trample"],
                {"combat.damage.assignment.trample"},
            ),
            (
                "Creatures you control have double strike and flying.",
                "source_controller",
                False,
                {"types_all": ["creature"]},
                ["Double Strike", "Flying"],
                {
                    "combat.block.flying",
                    "combat.damage.participation.strike_steps",
                },
            ),
            (
                "Artifacts you control have hexproof.",
                "source_controller",
                False,
                {"types_all": ["artifact"]},
                ["Hexproof"],
                {"target.protection.hexproof_permanent"},
            ),
        )
        for index, (
            text,
            relation,
            exclude_source,
            expected_predicate,
            abilities,
            consumer_capabilities,
        ) in enumerate(cases):
            with self.subTest(text=text):
                program = self.compile(
                    _permanent(
                        text,
                        suffix=119_000_000 + index,
                        name=f"Keyword Grant Fixture {index}",
                    )
                )
                ability = self.keyword_ability(program)
                descriptor = next(
                    value
                    for value in ability.handlers
                    if value.get("handler_id") == HANDLER_ID
                )
                self.assertEqual((), program.residuals)
                self.assertEqual(TEMPLATE_ID, ability.provenance["template_id"])
                self.assertEqual("battlefield", ability.active_zone)
                self.assertEqual(1, ability.provenance["source_span"]["line"])
                self.assertEqual(
                    relation, descriptor["condition"]["target_controller"]
                )
                self.assertEqual(
                    exclude_source, descriptor["condition"]["exclude_source"]
                )
                for field, value in expected_predicate.items():
                    self.assertEqual(
                        value, descriptor["condition"]["predicate"][field]
                    )
                self.assertEqual(abilities, descriptor["modifier"]["add_abilities"])
                self.assertGreaterEqual(
                    set(ability.capability_dependencies),
                    {BASE_CAPABILITY, *consumer_capabilities},
                )

    def test_expanded_query_grammar_and_combined_characteristics_compile_exact(
        self,
    ):
        expanded = self.compile(
            _permanent(
                (
                    "Creatures you control have deathtouch, defender, "
                    "hexproof, indestructible, infect, lifelink, menace, "
                    "reach, shadow, shroud, and wither."
                ),
                suffix=119_000_900,
                name="Expanded Keyword Grant",
            )
        )
        ability = self.keyword_ability(expanded)
        self.assertEqual((), expanded.residuals)
        self.assertGreaterEqual(
            set(ability.capability_dependencies),
            {
                BASE_CAPABILITY,
                "combat.attack.defender",
                "combat.block.menace",
                "combat.block.reach",
                "combat.block.shadow",
                "combat.damage.assignment.deathtouch",
                "damage.result.deathtouch",
                "damage.result.infect",
                "damage.result.lifelink",
                "damage.result.wither",
                "permanent.indestructible.ordinary",
                "target.protection.hexproof_permanent",
                "target.protection.shroud_permanent",
            },
        )

        text = (
            "Other Goblin creatures you control get +1/+1 and have haste."
        )
        combined = self.compile(
            _permanent(
                text,
                suffix=119_000_901,
                name="Combined Characteristic Grant",
            )
        )
        combined_ability = next(
            value
            for value in combined.abilities
            if any(
                descriptor.get("handler_id") == CHARACTERISTIC_HANDLER_ID
                for descriptor in value.handlers
            )
        )
        descriptor = next(
            value
            for value in combined_ability.handlers
            if value.get("handler_id") == CHARACTERISTIC_HANDLER_ID
        )
        self.assertEqual((), combined.residuals)
        self.assertEqual(
            CHARACTERISTIC_TEMPLATE_ID,
            combined_ability.provenance["template_id"],
        )
        self.assertEqual(
            {"add_abilities": ["Haste"], "power": 1, "toughness": 1},
            descriptor["modifier"],
        )
        self.assertEqual(
            ["goblin"],
            descriptor["condition"]["predicate"]["subtypes_all"],
        )
        self.assertTrue(descriptor["condition"]["exclude_source"])
        self.assertGreaterEqual(
            set(combined_ability.capability_dependencies),
            {
                BASE_CAPABILITY,
                "continuous.power_toughness.fixed_anthem",
                "activation.tap_untap_cost.haste",
                "combat.attack.haste",
            },
        )
        registry = default_continuous_effect_component_registry()
        registry.validate(descriptor)
        self.assertIsNotNone(fixed_query_characteristic_grant_handler(text))

    def test_fixed_battlefield_query_extensions_share_one_canonical_condition(
        self,
    ):
        cases = (
            (
                "All Sliver creatures get +1/+1.",
                ANTHEM_HANDLER_ID,
                "any",
                False,
                {"subtypes_all": ["sliver"]},
                {"power": 1, "toughness": 1},
            ),
            (
                "Other Soldier creatures get +1/+1 and have first strike.",
                CHARACTERISTIC_HANDLER_ID,
                "any",
                True,
                {"subtypes_all": ["soldier"]},
                {
                    "add_abilities": ["First Strike"],
                    "power": 1,
                    "toughness": 1,
                },
            ),
            (
                "Creatures your opponents control get -1/-0.",
                ANTHEM_HANDLER_ID,
                "source_opponents",
                False,
                {},
                {"power": -1, "toughness": 0},
            ),
            (
                "Multicolored creatures you control have flying.",
                HANDLER_ID,
                "source_controller",
                False,
                {"minimum_color_count": 2},
                {"add_abilities": ["Flying"]},
            ),
            (
                "Zombie tokens you control have hexproof and menace.",
                HANDLER_ID,
                "source_controller",
                False,
                {"subtypes_all": ["zombie"], "token": True},
                {"add_abilities": ["Hexproof", "Menace"]},
            ),
            (
                (
                    "Each other creature you control with a +1/+1 counter "
                    "on it has haste."
                ),
                HANDLER_ID,
                "source_controller",
                True,
                {
                    "state_predicate": {
                        "entered_this_turn": False,
                        "tapped": None,
                        "counter_name": "+1/+1",
                        "minimum_counter_count": 1,
                    }
                },
                {"add_abilities": ["Haste"]},
            ),
        )
        registry = default_continuous_effect_component_registry()
        for index, (
            text,
            handler_id,
            relation,
            exclude_source,
            predicate_fields,
            modifier,
        ) in enumerate(cases):
            with self.subTest(text=text):
                program = self.compile(
                    _permanent(
                        text,
                        suffix=119_000_950 + index,
                        name=f"Fixed Battlefield Query {index}",
                    )
                )
                self.assertEqual((), program.residuals)
                descriptor = next(
                    value
                    for ability in program.abilities
                    for value in ability.handlers
                    if value.get("handler_id") == handler_id
                )
                registry.validate(descriptor)
                condition = descriptor["condition"]
                self.assertEqual(relation, condition["target_controller"])
                self.assertEqual(exclude_source, condition["exclude_source"])
                self.assertEqual(modifier, descriptor["modifier"])
                self.assertEqual(
                    ["creature"], condition["predicate"]["types_all"]
                )
                for field, value in predicate_fields.items():
                    self.assertEqual(value, condition["predicate"][field])

    def test_multicolor_query_observes_the_cycle_safe_layer_five_boundary(self):
        compiled = fixed_query_keyword_grant_handler(
            "Multicolored creatures you control have flying."
        )
        self.assertIsNotNone(compiled)
        query_effects = default_continuous_effect_component_registry().lower(
            compiled[1],
            ContinuousEffectSourceContext(
                source_object_id="multicolor-source",
                source_ref="MULTICOLOR-SOURCE",
                source_controller="A",
                source_timestamp=2,
                component_id="multicolor:0",
            ),
        )
        add_red = ContinuousEffect(
            effect_id="add-red-before-query",
            source_id="color-source",
            layer=Layer.COLOR,
            sublayer="5",
            timestamp=1,
            operations=(ContinuousOperation("add_colors", ["R"]),),
        )
        evaluated = evaluate_continuous_effects(
            CharacteristicState(
                name="Layer Five Witness",
                controller="A",
                card_types={"Creature"},
                colors={"U"},
                power=2,
                toughness=2,
            ),
            (add_red, *query_effects),
            context={
                "object_id": "layer-five-witness",
                "logical_object_id": "layer-five-witness@0",
                "ref": "LAYER-FIVE-WITNESS",
                "zone": "battlefield",
                "owner": "A",
                "controller": "A",
            },
        )
        self.assertEqual(["R", "U"], evaluated.characteristics["colors"])
        self.assertIn("Flying", evaluated.characteristics["abilities"])

    def test_open_or_unsupported_queries_remain_material_residuals(self):
        unsupported = (
            "Creatures with a +1/+1 counter on them have trample.",
            "Attacking tapped creatures you control have flying.",
            "Attacking creatures you control with a +1/+1 counter on them "
            "have trample.",
            "Creatures you control have ward {2}.",
            "Creatures you control have protection from red.",
            "Artifact permanents you control have flying.",
            "Creatures you control get +1/+1 and have ward {2}.",
            "Creatures you control get +1/+1 and have protection from red.",
            "Creatures you control have haste until end of turn.",
        )
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                program = self.compile(
                    _permanent(
                        text,
                        suffix=119_001_000 + index,
                        name=f"Residual Keyword Grant {index}",
                    ),
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id") == HANDLER_ID
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_level_gated_class_keyword_grant_remains_residual(self):
        program = self.compile(
            _permanent(
                "Creatures you control have haste.",
                suffix=119_001_100,
                name="Level-Gated Keyword Grant",
                type_line="Enchantment — Class",
            ),
            trust_level="provisional",
        )
        self.assertTrue(program.residuals)
        self.assertFalse(
            any(
                descriptor.get("handler_id") == HANDLER_ID
                for ability in program.abilities
                for descriptor in ability.handlers
            )
        )

    def test_descriptors_fail_closed_and_rejection_has_no_state_effect(self):
        global_haste = fixed_query_keyword_grant_handler(
            "All creatures have haste."
        )[1]
        artifact_hexproof = fixed_query_keyword_grant_handler(
            "Artifacts you control have hexproof."
        )[1]
        registry = default_continuous_effect_component_registry()
        registry.validate(global_haste)
        registry.validate(artifact_hexproof)

        malformed = []
        for path, value in (
            (("unknown",), True),
            (("schema_version",), True),
            (("condition", "target_controller"), "all_opponents"),
            (("condition", "exclude_source"), 1),
            (("condition", "predicate", "controller"), "A"),
            (("modifier", "add_abilities"), []),
            (("modifier", "add_abilities"), ["Haste", "Haste"]),
            (("modifier", "add_abilities"), ["Ward {2}"]),
        ):
            candidate = copy.deepcopy(global_haste)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            malformed.append(candidate)

        session = make_session(*load_assets(), players=2, seed=119_002_001)
        try:
            keep_all(session)
            before = session.state.to_dict()
            for descriptor in malformed:
                with self.subTest(descriptor=descriptor):
                    with self.assertRaises(SemanticNodeError):
                        registry.validate(descriptor)
                    self.assertEqual(before, session.state.to_dict())
        finally:
            session.card_db.close()

    def test_descriptor_semantics_change_card_program_fingerprint(self):
        record = _permanent(
            "All creatures have haste.",
            suffix=119_003_001,
            name="Fingerprint Keyword Grant",
        )
        expected = self.compile(record)
        original = fixed_query_keyword_grant_handler

        def scoped_mutant(text: str):
            compiled = original(text)
            if compiled is None:
                return None
            template_id, descriptor, capabilities = compiled
            changed = copy.deepcopy(descriptor)
            changed["condition"]["target_controller"] = "source_controller"
            return template_id, changed, capabilities

        with mock.patch(
            "quorune.compiler.runtime_templates.fixed_query_keyword_grant_handler",
            side_effect=scoped_mutant,
        ):
            mutated = self.compile(record)
        self.assertNotEqual(expected.fingerprint, mutated.fingerprint)


class FixedQueryKeywordGrantRuntimeTests(unittest.TestCase):
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
            auto_pass_empty=False,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        return session

    def add_sources(self, engine) -> dict[str, CardInstance]:
        records = tuple(self.db.lookup(name) for name in SOURCE_NAMES)
        self.assertTrue(all(record is not None for record in records))
        for record in records:
            program = compile_card_program(
                self.db,
                record,
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
                trust_level="provisional",
            )
            self.assertEqual((), program.residuals, record.name)
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            records,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(len(records), registration["runtime_handlers_promoted"])

        sources: dict[str, CardInstance] = {}
        for index, record in enumerate(records):
            ref = f"KEYWORD-SOURCE-{index + 1}"
            card = CardInstance(
                object_id=f"fixed-query-keyword-grant:{index + 1}",
                ref=ref,
                oracle_id=record.oracle_id,
                printed_name=record.name,
                owner="A",
                controller="A",
                zone="battlefield",
                zone_timestamp=engine._next_zone_timestamp(),
                known_to=list(engine.seats),
                revealed_to=list(engine.seats),
            )
            engine.state.cards[card.object_id] = card
            engine.state.players["A"].zones["battlefield"].append(card.object_id)
            sources[record.name] = card
        return sources

    def add_registered_card(
        self,
        engine,
        *,
        name: str,
        ref: str,
        seat: str = "A",
    ) -> tuple[CardInstance, object, dict[str, object]]:
        record = self.db.lookup(name)
        self.assertIsNotNone(record, name)
        program = compile_card_program(
            self.db,
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            trust_level="provisional",
        )
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        card = CardInstance(
            object_id=f"fixed-query-keyword-grant:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card, program, registration

    @staticmethod
    def creature(
        engine,
        controller: str,
        name: str,
        *,
        subtype: str = "Test",
        power: str = "5",
        toughness: str = "5",
        colors: tuple[str, ...] = (),
    ) -> CardInstance:
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": f"Token Creature — {subtype}",
                "power": power,
                "toughness": toughness,
                "keywords": [],
                "colors": list(colors),
            },
            reason="fixed-query keyword-grant assurance",
        )[0]
        card = engine._resolve_object(controller, ref, zones={"battlefield"})
        card.acquired_control_turn_count = engine.state.players[
            controller
        ].turns_begun
        return card

    def test_measured_50_card_lower_bound_is_capability_closed(self):
        self.assertEqual(50, len(COMPLETE_CARD_LOWER_BOUND))
        expected_handlers = {
            ANTHEM_HANDLER_ID,
            CHARACTERISTIC_HANDLER_ID,
            HANDLER_ID,
        }
        capabilities = load_default_capability_registry()
        for name in COMPLETE_CARD_LOWER_BOUND:
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertEqual((), program.residuals)
                self.assertTrue(
                    any(
                        descriptor.get("handler_id") in expected_handlers
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_live_fixed_queries_track_relations_source_identity_and_phasing(self):
        session = self.session(119_100_006)
        engine = session.engine
        source, program, registration = self.add_registered_card(
            engine,
            name="Elesh Norn, Grand Cenobite",
            ref="ELESH-NORN",
        )
        self.assertEqual((), program.residuals)
        self.assertEqual(2, registration["runtime_handlers_promoted"])
        own = self.creature(engine, "A", "Own Anthem Witness")
        opponent_b = self.creature(engine, "B", "Opponent B Witness")
        opponent_c = self.creature(engine, "C", "Opponent C Witness")

        def power(card: CardInstance) -> int:
            return int(engine._effective_card_data(card)["power"])

        self.assertEqual(7, power(own))
        self.assertEqual(3, power(opponent_b))
        self.assertEqual(3, power(opponent_c))
        self.assertEqual(4, power(source))

        source.phased_out = True
        self.assertEqual(5, power(own))
        self.assertEqual(5, power(opponent_b))
        self.assertEqual(5, power(opponent_c))
        source.phased_out = False

        engine.move_card(
            source.object_id,
            "graveyard",
            reason="fixed battlefield-query source-presence assurance",
        )
        self.assertEqual(5, power(own))
        self.assertEqual(5, power(opponent_b))
        self.assertEqual(5, power(opponent_c))

    def test_counter_multicolor_and_token_queries_use_live_public_state_and_replay(
        self,
    ):
        session = self.session(119_100_007)
        engine = session.engine
        sapphire, sapphire_program, _ = self.add_registered_card(
            engine,
            name="Sapphire Drake",
            ref="SAPPHIRE-DRAKE",
        )
        glider, glider_program, _ = self.add_registered_card(
            engine,
            name="Maze Glider",
            ref="MAZE-GLIDER",
        )
        overseer, overseer_program, _ = self.add_registered_card(
            engine,
            name="Gleaming Overseer",
            ref="GLEAMING-OVERSEER",
        )
        twins, twins_program, _ = self.add_registered_card(
            engine,
            name="Dreadhorde Twins",
            ref="DREADHORDE-TWINS",
        )
        for program in (
            sapphire_program,
            glider_program,
            overseer_program,
            twins_program,
        ):
            self.assertEqual((), program.residuals)

        counter_witness = self.creature(engine, "A", "Counter Witness")
        opposing_counter_witness = self.creature(
            engine, "B", "Opposing Counter Witness"
        )
        multicolor_witness = self.creature(
            engine,
            "A",
            "Multicolor Witness",
            colors=("R", "U"),
        )
        zombie_token = self.creature(
            engine,
            "A",
            "Zombie Token Witness",
            subtype="Zombie",
        )

        self.assertNotIn("flying", engine._combat_keywords(counter_witness))
        place_counters(
            engine,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=counter_witness.object_id,
                    counter_name="+1/+1",
                    amount=1,
                    placing_player="A",
                    source_ref=sapphire.ref,
                ),
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=opposing_counter_witness.object_id,
                    counter_name="+1/+1",
                    amount=1,
                    placing_player="B",
                    source_ref=sapphire.ref,
                ),
            ),
            reason="fixed battlefield-query counter assurance",
        )
        self.assertIn("flying", engine._combat_keywords(counter_witness))
        self.assertNotIn(
            "flying", engine._combat_keywords(opposing_counter_witness)
        )
        change_permanent_counter(engine, counter_witness, "+1/+1", -1)
        self.assertNotIn("flying", engine._combat_keywords(counter_witness))

        self.assertIn("flying", engine._combat_keywords(multicolor_witness))

        self.assertGreaterEqual(
            engine._combat_keywords(zombie_token),
            {"hexproof", "menace", "trample"},
        )
        self.assertNotIn("trample", engine._combat_keywords(twins))

        glider.phased_out = True
        self.assertNotIn("flying", engine._combat_keywords(multicolor_witness))
        glider.phased_out = False

        place_counters(
            engine,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=counter_witness.object_id,
                    counter_name="+1/+1",
                    amount=1,
                    placing_player="A",
                    source_ref=sapphire.ref,
                ),
            ),
            reason="fixed battlefield-query replay assurance",
        )
        self.assertIn("flying", engine._combat_keywords(counter_witness))
        self.assertIn("flying", engine._combat_keywords(multicolor_witness))
        self.assertNotIn("hexproof", engine._combat_keywords(overseer))

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
                "plan": "REPLAY_FIXED_BATTLEFIELD_QUERY_CHARACTERISTICS",
                "reason": "Verify live typed battlefield queries from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-battlefield-query"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_compiled_grants_feed_haste_vigilance_flying_strike_and_trample_owners(
        self,
    ):
        session = self.session(119_100_001)
        engine = session.engine
        self.add_sources(engine)
        attacker = self.creature(engine, "A", "Keyword Attacker")
        blocker = self.creature(
            engine,
            "B",
            "Keyword Blocker",
            power="2",
            toughness="3",
        )

        attacker_keywords = engine._combat_keywords(attacker)
        blocker_keywords = engine._combat_keywords(blocker)
        self.assertGreaterEqual(
            attacker_keywords,
            {
                "double strike",
                "first strike",
                "flying",
                "haste",
                "trample",
                "vigilance",
            },
        )
        self.assertEqual({"haste"}, blocker_keywords)

        self.assertTrue(is_summoning_sick(engine, attacker))
        self.assertFalse(summoning_sickness_prohibits_attack(engine, attacker))
        self.assertFalse(
            summoning_sickness_prohibits_tap_or_untap_cost(engine, attacker)
        )
        self.assertEqual([], tap_declared_attackers(engine, (attacker,)))
        self.assertFalse(attacker.tapped)
        self.assertFalse(
            aerial_block_verdict(attacker_keywords, blocker_keywords).allowed
        )

        combatants = {
            attacker.object_id: attacker_keywords,
            blocker.object_id: blocker_keywords,
        }
        self.assertTrue(first_strike_step_required(combatants))
        ordinary = ordinary_second_step_combatants(combatants)
        self.assertNotIn(attacker.object_id, ordinary)
        for step_index in (0, 1):
            self.assertTrue(
                assigns_in_damage_step(
                    object_id=attacker.object_id,
                    current_keywords=attacker_keywords,
                    step_index=step_index,
                    first_strike_step=True,
                    ordinary_second_step=ordinary,
                )
            )

        proposal = build_combat_damage_assignment_proposal(
            seat="A",
            snapshot=CombatDamageSnapshot(
                damage_step_id="fixed-query-keyword-grant:damage:1",
                damage_step_index=0,
                first_strike_step=True,
                active_player="A",
                participants=(
                    CombatDamageParticipant(
                        object_id=attacker.object_id,
                        reference=attacker.ref,
                        controller="A",
                        power=5,
                        toughness=5,
                        marked_damage=0,
                        keywords=attacker_keywords,
                        assigns_damage=True,
                        logical_object_id=attacker.logical_object_id,
                    ),
                    CombatDamageParticipant(
                        object_id=blocker.object_id,
                        reference=blocker.ref,
                        controller="B",
                        power=2,
                        toughness=3,
                        marked_damage=0,
                        keywords=blocker_keywords,
                        assigns_damage=True,
                        logical_object_id=blocker.logical_object_id,
                    ),
                ),
                attacks=(
                    CombatAttackRelationship(
                        attacker.object_id,
                        CombatDamageRecipient(
                            reference="B",
                            logical_object_id="player:B",
                            controller="B",
                            kind="player",
                            legal=True,
                        ),
                    ),
                ),
                blocks=(
                    CombatBlockRelationship(
                        attacker.object_id,
                        blocker.object_id,
                    ),
                ),
                was_blocked=frozenset({attacker.object_id}),
            ),
        )
        self.assertEqual(1, len(proposal.trample_sources))
        self.assertEqual(
            ((blocker.ref, 3), ("B", 2)),
            tuple(
                (assignment.target, assignment.amount)
                for assignment in proposal.validate(
                    (
                        {
                            "source": attacker.ref,
                            "target": blocker.ref,
                            "amount": 3,
                        },
                        {
                            "source": attacker.ref,
                            "target": "B",
                            "amount": 2,
                        },
                    )
                )
            ),
        )

    def test_expanded_query_grants_feed_current_keyword_consumers(self):
        registry = default_continuous_effect_component_registry()
        expanded = fixed_query_keyword_grant_handler(
            (
                "Creatures you control have deathtouch, defender, hexproof, "
                "indestructible, infect, lifelink, menace, reach, shadow, "
                "shroud, and wither."
            )
        )
        combined = fixed_query_characteristic_grant_handler(
            (
                "Other Goblin creatures you control get +1/+1 and have "
                "haste."
            )
        )
        self.assertIsNotNone(expanded)
        self.assertIsNotNone(combined)
        effects = (
            *registry.lower(
                expanded[1],
                ContinuousEffectSourceContext(
                    source_object_id="expanded-source",
                    source_ref="EXPANDED-SOURCE",
                    source_controller="A",
                    source_timestamp=1,
                    component_id="expanded:0",
                ),
            ),
            *registry.lower(
                combined[1],
                ContinuousEffectSourceContext(
                    source_object_id="combined-source",
                    source_ref="COMBINED-SOURCE",
                    source_controller="A",
                    source_timestamp=2,
                    component_id="combined:0",
                ),
            ),
        )
        evaluated = evaluate_continuous_effects(
            CharacteristicState(
                name="Expanded Keyword Creature",
                controller="A",
                card_types={"Creature"},
                subtypes={"Goblin"},
                power=5,
                toughness=5,
            ),
            effects,
            context={
                "object_id": "target",
                "logical_object_id": "target@0",
                "ref": "TARGET",
                "zone": "battlefield",
                "owner": "A",
                "controller": "A",
            },
        )
        effective = {
            "type_line": "Creature — Goblin",
            "keywords": evaluated.characteristics["abilities"],
        }
        self.assertEqual(6, evaluated.characteristics["power"])
        self.assertEqual(6, evaluated.characteristics["toughness"])
        self.assertGreaterEqual(
            {value.casefold() for value in effective["keywords"]},
            {
                "deathtouch",
                "defender",
                "haste",
                "hexproof",
                "indestructible",
                "infect",
                "lifelink",
                "menace",
                "reach",
                "shadow",
                "shroud",
                "wither",
            },
        )
        self.assertTrue(defender_prohibits_attack(effective))
        menace = current_menace_restriction(
            effective,
            "TARGET",
            is_attacking=True,
        )
        self.assertIsNotNone(menace)
        self.assertEqual(2, menace.minimum_blockers)

    def test_live_queries_track_global_control_subtype_phase_and_zone_changes(self):
        session = self.session(119_100_002)
        engine = session.engine
        sources = self.add_sources(engine)
        own = self.creature(engine, "A", "Own Creature")
        opposing = self.creature(engine, "B", "Opposing Creature")
        own_sliver = self.creature(
            engine, "A", "Own Sliver", subtype="Sliver"
        )
        changed = self.creature(engine, "A", "Changed Subtype")
        changed.annotations["continuous_add_subtypes"] = ["Sliver"]

        self.assertIn("haste", engine._combat_keywords(opposing))
        self.assertGreaterEqual(
            engine._combat_keywords(own_sliver), {"flying", "haste"}
        )
        self.assertGreaterEqual(
            engine._combat_keywords(changed), {"flying", "haste"}
        )

        mass_hysteria = sources["Mass Hysteria"]
        mass_hysteria.phased_out = True
        self.assertNotIn("haste", engine._combat_keywords(own))
        self.assertNotIn("haste", engine._combat_keywords(opposing))
        self.assertIn("haste", engine._combat_keywords(own_sliver))
        mass_hysteria.phased_out = False

        levitation = sources["Levitation"]
        engine.change_control(
            levitation.object_id,
            "B",
            reason="fixed-query live controller relation",
        )
        self.assertNotIn("flying", engine._combat_keywords(own))
        self.assertIn("flying", engine._combat_keywords(opposing))
        self.assertIn("flying", engine._combat_keywords(own_sliver))

        cloudshredder = sources["Cloudshredder Sliver"]
        engine.move_card(
            cloudshredder.object_id,
            "graveyard",
            reason="fixed-query source-presence assurance",
        )
        self.assertNotIn("flying", engine._combat_keywords(own_sliver))
        self.assertNotIn("flying", engine._combat_keywords(changed))

    def test_aura_attachment_and_query_grant_execute_from_one_exact_card(self):
        session = self.session(119_100_004)
        engine = session.engine
        enchanted = self.creature(engine, "A", "Enchanted Creature")
        witness = self.creature(engine, "A", "Aura Grant Witness")
        opponent = self.creature(engine, "B", "Aura Opponent Witness")
        emblem, program, registration = self.add_registered_card(
            engine,
            name="Emblem of the Warmind",
            ref="EMBLEM-OF-THE-WARMIND",
        )
        self.assertEqual((), program.residuals)
        self.assertEqual(2, registration["runtime_handlers_promoted"])

        attach_objects(
            engine.state.cards,
            emblem,
            enchanted,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertEqual(enchanted.object_id, emblem.attached_to)
        self.assertIn("haste", engine._combat_keywords(witness))
        self.assertNotIn("haste", engine._combat_keywords(opponent))
        self.assertFalse(summoning_sickness_prohibits_attack(engine, witness))

        engine.move_card(
            emblem.object_id,
            "graveyard",
            reason="aura and keyword-grant composition assurance",
        )
        self.assertNotIn("haste", engine._combat_keywords(witness))
        self.assertTrue(summoning_sickness_prohibits_attack(engine, witness))

    def test_exact_grant_executes_while_replacement_siblings_fail_closed(self):
        session = self.session(119_100_005)
        engine = session.engine
        pulmonic, program, registration = self.add_registered_card(
            engine,
            name="Pulmonic Sliver",
            ref="PULMONIC-SLIVER",
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
        self.assertFalse(program.trust_closure["strict_capability_ready"])
        self.assertEqual(1, registration["runtime_handlers_promoted"])

        runtime_programs = tuple(
            engine.semantics.runtime_handler_programs_for_oracle(
                pulmonic.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
        )
        keyword_program = next(
            value
            for value in runtime_programs
            if any(
                descriptor.get("handler_id") == HANDLER_ID
                for descriptor in value.handlers
            )
        )
        self.assertTrue(engine.semantic_program_is_current_trusted(keyword_program))
        self.assertFalse(
            any(
                "replacement" in value.event
                and engine.semantic_program_is_current_trusted(value)
                for value in engine.semantics.programs_for_oracle(
                    pulmonic.oracle_id
                )
            )
        )

        sliver = self.creature(engine, "A", "Pulmonic Witness", subtype="Sliver")
        non_sliver = self.creature(engine, "A", "Pulmonic Non-Sliver")
        opponent_sliver = self.creature(
            engine,
            "B",
            "Opponent Pulmonic Witness",
            subtype="Sliver",
        )
        self.assertIn("flying", engine._combat_keywords(sliver))
        self.assertNotIn("flying", engine._combat_keywords(non_sliver))
        self.assertIn("flying", engine._combat_keywords(opponent_sliver))

    def test_projection_and_replay_preserve_grants_without_private_identities(self):
        session = self.session(119_100_003)
        engine = session.engine
        self.add_sources(engine)
        target = self.creature(engine, "A", "Projected Keyword Creature")

        projected = session.projector._snapshot("pilot:B")
        public_target = next(
            value
            for value in projected["players"]["A"]["bf"]
            if value["id"] == target.ref
        )
        self.assertGreaterEqual(
            {value.casefold() for value in public_target["k"]},
            {
                "double strike",
                "first strike",
                "flying",
                "haste",
                "trample",
                "vigilance",
            },
        )
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn(target.object_id, rendered)
        self.assertNotIn("continuous_effects", rendered)

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
                "plan": "REPLAY_FIXED_QUERY_KEYWORD_GRANT",
                "reason": "Verify typed keyword grants from the exact checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-query-keyword-grants"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
