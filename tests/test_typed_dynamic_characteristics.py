from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from dataclasses import replace
from quorune.oracle_ir import compile_oracle_card
from quorune.characteristic_evaluation import type_parts
from quorune.characteristic_fragments import CharacteristicQuantitySpec, CharacteristicFragmentError
from quorune.compiler.query_characteristic_templates import query_characteristic_quantity, query_characteristic_value, query_power_toughness_definition_handler
from quorune.continuous_effects import Layer
from quorune.dynamic_characteristics import query_characteristic_count
from quorune.model import CardInstance

from common import keep_all, load_assets, make_session
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
)
from quorune.compiler.continuous_templates import (
    conditional_self_keyword_handler,
    dynamic_self_power_toughness_handler,
    fixed_query_keyword_grant_handler,
)
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.ability_fragments import (
    default_ability_fragment_registry,
)
from quorune.semantic_runtime.continuous_components import (
    default_continuous_effect_component_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError


CONDITIONAL_HANDLER = "ability.static.conditional-keyword.v1"
PUBLIC_STATE_HANDLER = FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
DYNAMIC_HANDLER = "ability.static.dynamic-power-toughness.v1"
QUERY_HANDLER = "ability.static.query-characteristic-modifier.v1"
KEYWORD_GRANT_HANDLER = (
    "continuous.ability.fixed-query-keyword-grant.v1"
)
DYNAMIC_FRAGMENT = {
    "kind": "dynamic_power_toughness",
    "value": {
        "schema_version": 1,
        "count_kind": "controller_battlefield_artifacts",
        "calculation": "per_matching_object",
        "power": 1,
        "toughness": 1,
        "minimum_count": 0,
    },
}


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _permanent(text: str, *, suffix: int, name: str) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        type_line="Creature — Shapeshifter",
        oracle_text=text,
        power="1",
        toughness="1",
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


class PublicCollectionReductionTests(unittest.TestCase):
    """Independent CR 202.3/205.2/305.6/613 layer-safe expectation table."""

    def host(self, rows):
        state = SimpleNamespace(cards={}, players={seat: SimpleNamespace(in_game=True, zones={zone: [] for zone in ("battlefield", "graveyard", "hand")}) for seat in "AB"})
        values = {}
        layers = []
        for index, (type_line, mana_value, colors, zone, token, phased) in enumerate(rows):
            card = CardInstance(str(index), f"P{index}", "fixture", "Generic Object", "A", "A", zone)
            card.is_token = token
            card.phased_out = phased
            state.cards[card.object_id] = card
            state.players["A"].zones[zone].append(card.object_id)
            values[card.object_id] = {"type_line": type_line, "mana_value": mana_value, "colors": colors, "keywords": [], "power": "2", "toughness": "2"}
        def effective(card, *, maximum_layer, _enforce_static_component_applicability=True):
            layers.append(maximum_layer)
            return values[card.object_id]
        return SimpleNamespace(state=state, _effective_card_data=effective, _type_parts=type_parts), values, layers

    def count(self, text, host):
        quantity = query_characteristic_quantity(text, source_name="Generic Source", definition_extensions=True)
        self.assertIsNotNone(quantity, text)
        return query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), quantity)

    def test_basic_type_union_uses_current_land_domain_and_all_five_names(self):
        host, values, layers = self.host([
            ("Basic Land — Forest", 0, [], "battlefield", False, False),
            ("Snow Land — Forest Island", 0, [], "battlefield", False, False),
            ("Land — Desert", 0, [], "battlefield", False, False),
            ("Land — Plains", 0, [], "battlefield", False, True),
        ])
        self.assertEqual(2, self.count("basic land types among lands you control", host))
        values["1"]["type_line"] = "Artifact"
        self.assertEqual(1, self.count("basic land types among lands you control", host))
        self.assertTrue(all(layer is Layer.COLOR for layer in layers))
        for subtype in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            with self.subTest(subtype=subtype):
                one, _, _ = self.host([(f"Land — {subtype}", 0, [], "battlefield", False, False)] * 2)
                self.assertEqual(1, self.count("basic land types among lands you control", one))

    def test_graveyard_card_type_union_is_not_objects_subtypes_or_tokens(self):
        host, _, _ = self.host([
            ("Legendary Artifact Creature — Human", 3, [], "graveyard", False, False),
            ("Kindred Enchantment — Goblin", 2, [], "graveyard", False, False),
            ("Artifact Creature — Human", 3, [], "graveyard", False, False),
            ("Instant", 1, [], "graveyard", True, False),
        ])
        self.assertEqual(4, self.count("card types among cards in your graveyard", host))

    def test_distinct_colors_and_mana_value_reductions_are_not_object_counts(self):
        host, _, layers = self.host([
            ("Creature", 2, ["W", "U"], "battlefield", False, False),
            ("Artifact Creature", 5, ["U", "R"], "battlefield", False, False),
            ("Artifact", 9, [], "battlefield", False, False),
        ])
        self.assertEqual(3, self.count("colors among permanents you control", host))
        self.assertEqual(5, self.count("greatest mana value among creatures you control", host))
        self.assertEqual(7, self.count("total mana value of creatures you control", host))
        self.assertTrue(all(layer is Layer.COLOR for layer in layers))

    def test_empty_collection_differs_from_unavailable_mana_value(self):
        host, _, _ = self.host([])
        self.assertEqual(0, self.count("greatest mana value among creatures you control", host))
        for invalid in (None, True, "5", 1.5):
            with self.subTest(invalid=invalid):
                malformed, _, _ = self.host([("Creature", invalid, [], "battlefield", False, False)])
                with self.assertRaises(ValueError):
                    self.count("greatest mana value among creatures you control", malformed)

    def test_collection_unavailable_member_is_not_a_known_empty_set(self):
        host, _, _ = self.host([])
        host.state.players["A"].zones["battlefield"].append("missing")
        with self.assertRaisesRegex(ValueError, "collection member is unavailable"):
            self.count("greatest mana value among creatures you control", host)

    def test_versioned_codec_keeps_legacy_counts_and_closed_exclusions(self):
        legacy = query_characteristic_quantity("creatures you control", source_name="Generic Source").to_dict()
        self.assertEqual(1, legacy["schema_version"])
        self.assertNotIn("reduction", legacy)
        self.assertEqual(legacy, CharacteristicQuantitySpec.from_dict(legacy).to_dict())
        quantity = query_characteristic_quantity("basic land types among lands you control", source_name="Generic Source")
        self.assertIsNotNone(quantity)
        encoded = quantity.to_dict()
        self.assertEqual(2, encoded["schema_version"])
        self.assertEqual(encoded, CharacteristicQuantitySpec.from_dict(encoded).to_dict())
        with self.assertRaises(CharacteristicFragmentError):
            CharacteristicQuantitySpec.from_dict({**encoded, "reduction": "callback"})
        for excluded in ("greatest power among creatures you control", "colors among cards in your hand", "card types among cards in your hand", "card types among cards of a chosen player"):
            self.assertIsNone(query_characteristic_quantity(excluded, source_name="Generic Source"))

    def test_whole_card_collection_values_flow_to_existing_fixed_result_owners(self):
        registry = load_default_capability_registry()
        cases = (
            ("Draw cards equal to the greatest mana value among permanents you control.", "Sorcery"),
            ("You gain life equal to the total mana value of creature cards in your graveyard.", "Sorcery"),
            ("Collection Source deals damage to any target equal to the number of basic land types among lands you control.", "Sorcery"),
            ("Draw X cards, where X is the number of card types among cards in your graveyard.", "Sorcery"),
            ("Create a 1/1 white Soldier creature token for each basic land type among lands you control.", "Sorcery"),
            ("Target creature gets +X/+X until end of turn, where X is the number of lands you control plus 1.", "Sorcery"),
            ("Collection Source's power is equal to the greatest mana value among creatures you control.", "Creature — Test"),
        )
        for index, (text, type_line) in enumerate(cases):
            with self.subTest(text=text):
                card = replace(_permanent(text, suffix=129_009_000 + index, name="Collection Source"), type_line=type_line)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(1, len(ir.faces[0].nodes))

    def test_fixed_affine_value_retains_multiplier_offset_and_distinctness(self):
        host, _, _ = self.host([("Land — Forest Island", 0, [], "battlefield", False, False)] * 2)
        for text, expected in (("one plus the number of lands you control", 3),
                               ("twice the number of lands you control", 4),
                               ("the number of basic land types among lands you control plus 1", 3)):
            with self.subTest(text=text):
                quantity = query_characteristic_value(text, source_name="Generic Source")
                self.assertIsNotNone(quantity)
                self.assertEqual(expected, query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), quantity))
        for excluded in ("the number of lands you control plus the number of creatures you control plus the number of artifacts you control",
                         "half the number of lands you control", "the number of chosen creatures"):
            self.assertIsNone(query_characteristic_value(excluded, source_name="Generic Source"))

    def test_power_and_toughness_share_one_quantity_without_losing_toughness_offset(self):
        text = "Generic Source's power is equal to the number of card types among cards in all graveyards and its toughness is equal to that number plus 1."
        lowered = query_power_toughness_definition_handler(text, source_name="Generic Source")
        self.assertIsNotNone(lowered)
        fragment = lowered[1]["fragment"]["value"]
        self.assertTrue(fragment["define_power"])
        self.assertTrue(fragment["define_toughness"])
        self.assertEqual(0, fragment["power_adjustment"])
        self.assertEqual(1, fragment["toughness_adjustment"])

    def test_two_closed_quantifiers_add_without_becoming_a_union(self):
        host, _, layers = self.host([("Artifact Creature — Elf", 3, [], "battlefield", False, False),
                                     ("Creature — Elf", 2, [], "graveyard", False, False)])
        text = "the number of artifacts you control plus the number of creatures you control"
        quantity = query_characteristic_value(text, source_name="Generic Source")
        self.assertIsNotNone(quantity)
        self.assertEqual(2, query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), quantity))
        across = query_characteristic_value("the number of Elves you control plus the number of Elf cards in your graveyard", source_name="Generic Source")
        self.assertIsNotNone(across)
        self.assertEqual(2, query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), across))
        host.state.cards["1"].is_token = True
        self.assertEqual(1, query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), across))
        host.state.cards["1"].is_token = False
        host.state.cards["1"].object_kind = "card_copy"
        self.assertEqual(1, query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), across))
        self.assertTrue(all(layer is Layer.COLOR for layer in layers))
        self.assertEqual(across.to_dict(), CharacteristicQuantitySpec.from_dict(across.to_dict()).to_dict())
        self.assertIsNone(query_characteristic_value("the number of artifacts you control plus the number of creatures you control plus the number of lands you control", source_name="Generic Source"))

    def test_colored_mana_symbols_use_current_cost_not_color_or_mana_value(self):
        host, values, layers = self.host([("Artifact", 8, [], "battlefield", False, False),
                                         ("Creature", 2, ["R"], "battlefield", False, False)])
        values["0"]["mana_cost"] = "{2}{R/U}{2/R}{R/P}{U/B}"
        values["1"]["mana_cost"] = "{2}"
        quantity = query_characteristic_quantity("red mana symbols in the mana costs of permanents you control", source_name="Generic Source")
        self.assertIsNotNone(quantity)
        self.assertEqual(3, query_characteristic_count(host, SimpleNamespace(controller="A", ref="SOURCE"), quantity))
        self.assertTrue(all(layer is Layer.COLOR for layer in layers))
        self.assertIsNone(query_characteristic_value("your devotion to red", source_name="Generic Source"))
        self.assertIsNone(query_characteristic_quantity("red mana symbols in the mana costs of cards in your hand", source_name="Generic Source"))

    def test_extended_quantity_codecs_reject_forged_parameters_and_nested_sums(self):
        count = query_characteristic_value("one plus the number of lands you control", source_name="Generic Source").to_dict()
        for field, invalid in (("multiplier", True), ("multiplier", 0), ("offset", False), ("offset", -1), ("mana_colors", ["R"])):
            with self.subTest(field=field, invalid=invalid):
                with self.assertRaises(CharacteristicFragmentError):
                    CharacteristicQuantitySpec.from_dict({**count, field: invalid})
        added = query_characteristic_value("the number of lands you control plus the number of creatures you control", source_name="Generic Source").to_dict()
        for terms in ([], [count], [added, count], [count, count, count]):
            with self.subTest(terms=terms):
                with self.assertRaises(CharacteristicFragmentError):
                    CharacteristicQuantitySpec.from_dict({**added, "terms": terms})

    def test_collection_object_count_mutant_is_killed(self):
        host, _, _ = self.host([("Land — Forest", 0, [], "battlefield", False, False)] * 2)
        text = "basic land types among lands you control"
        self.assertEqual(1, self.count(text, host))
        with mock.patch("quorune.dynamic_characteristics.reduce_public_quantity", side_effect=lambda rows, reduction, **kwargs: len(rows)):
            with self.assertRaises(AssertionError):
                self.assertEqual(1, self.count(text, host))


class TypedDynamicCharacteristicCompilerTests(unittest.TestCase):
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

    def test_closed_characteristic_families_compile_exact_and_near_misses_remain_residual(
        self,
    ):
        cases = (
            (
                "Bloodghast",
                "This creature has haste as long as an opponent has 10 or less life.",
                PUBLIC_STATE_HANDLER,
                "continuous.characteristics.fixed_public_state",
            ),
            (
                "Padeem, Consul of Innovation",
                "Artifacts you control have hexproof. (They can't be the targets "
                "of spells or abilities your opponents control.)",
                KEYWORD_GRANT_HANDLER,
                "continuous.ability.fixed_query_keyword_grant",
            ),
            (
                "Brudiclad, Telchor Engineer",
                "Creature tokens you control have haste.",
                KEYWORD_GRANT_HANDLER,
                "continuous.ability.fixed_query_keyword_grant",
            ),
            (
                "Menace Anthem",
                "Creatures you control have menace.",
                KEYWORD_GRANT_HANDLER,
                "continuous.ability.fixed_query_keyword_grant",
            ),
            (
                "Hexproof Anthem",
                "Other permanents you control have hexproof.",
                KEYWORD_GRANT_HANDLER,
                "continuous.ability.fixed_query_keyword_grant",
            ),
            (
                "Construct",
                "This creature gets +1/+1 for each artifact you control.",
                QUERY_HANDLER,
                "continuous.characteristics.query_count_modifier",
            ),
            (
                "Elvish Reclaimer",
                "This creature gets +2/+2 as long as there are three or more "
                "land cards in your graveyard.",
                QUERY_HANDLER,
                "continuous.characteristics.query_count_modifier",
            ),
            (
                "Wight of the Reliquary",
                "This creature gets +1/+1 for each creature card in your graveyard.",
                QUERY_HANDLER,
                "continuous.characteristics.query_count_modifier",
            ),
            (
                "Jarad, Golgari Lich Lord",
                "Jarad gets +1/+1 for each creature card in your graveyard.",
                QUERY_HANDLER,
                "continuous.characteristics.query_count_modifier",
            ),
            (
                "Opponent Ward Grant",
                "Creatures your opponents control have ward {1}.",
                KEYWORD_GRANT_HANDLER,
                "trigger.keyword.ward.fixed_generic",
            ),
            (
                "Generic Distinct Color Modifier",
                "This creature gets +1/+1 for each color among permanents you control.",
                QUERY_HANDLER,
                "continuous.characteristics.query_count_modifier",
            ),
        )
        for index, (name, text, handler_id, capability_id) in enumerate(cases):
            with self.subTest(name=name):
                program = self.compile(
                    _permanent(text, suffix=117_000_000 + index, name=name)
                )
                ability = next(
                    ability
                    for ability in program.abilities
                    if any(
                        descriptor.get("handler_id") == handler_id
                        for descriptor in ability.handlers
                    )
                )
                self.assertEqual("battlefield", ability.active_zone)
                self.assertEqual("front", ability.provenance["face_id"])
                self.assertEqual(1, ability.provenance["source_span"]["line"])
                self.assertIn(capability_id, ability.capability_dependencies)
                self.assertEqual((), program.residuals)

        unsupported = (
            "Multicolored creatures you control have protection from red.",
            "This creature has haste as long as you have exactly 10 life.",
            "This creature gets +1/+1 for each color among cards in your hand.",
            "This creature gets +2/+2 if there are three land cards in your graveyard.",
        )
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                program = self.compile(
                    _permanent(
                        text,
                        suffix=117_001_000 + index,
                        name=f"Unsupported Fixture {index}",
                    ),
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id")
                        in {
                            CONDITIONAL_HANDLER,
                            DYNAMIC_HANDLER,
                            QUERY_HANDLER,
                            KEYWORD_GRANT_HANDLER,
                            PUBLIC_STATE_HANDLER,
                        }
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_characteristic_descriptors_reject_malformed_values(self):
        keyword = fixed_query_keyword_grant_handler(
            "Creature tokens you control have haste."
        )[1]
        continuous_registry = default_continuous_effect_component_registry()
        continuous_registry.validate(keyword)
        malformed_keywords = (
            {**keyword, "unknown": True},
            {**keyword, "schema_version": True},
            {
                **keyword,
                "modifier": {"add_abilities": ["Ward"]},
            },
            {
                **keyword,
                "condition": {
                    **keyword["condition"],
                    "target_controller": "source_allies",
                },
            },
        )
        for malformed in malformed_keywords:
            with self.subTest(malformed=malformed):
                with self.assertRaises(SemanticNodeError):
                    continuous_registry.validate(malformed)

        conditional = conditional_self_keyword_handler(
            "This creature has haste as long as an opponent has 10 or less life.",
            source_name="Fixture",
        )[1]
        dynamic = dynamic_self_power_toughness_handler(
            "This creature gets +1/+1 for each artifact you control.",
            source_name="Fixture",
        )[1]
        fragment_registry = default_ability_fragment_registry()
        fragment_registry.validate(conditional)
        fragment_registry.validate(dynamic)
        malformed_fragments = (
            {
                **conditional,
                "fragment": {
                    **conditional["fragment"],
                    "value": {
                        **conditional["fragment"]["value"],
                        "opponent_life_at_most": -1,
                    },
                },
            },
            {
                **dynamic,
                "fragment": {
                    **dynamic["fragment"],
                    "value": {
                        **dynamic["fragment"]["value"],
                        "count_kind": "opponent_library_cards",
                    },
                },
            },
        )
        for malformed in malformed_fragments:
            with self.subTest(malformed=malformed):
                with self.assertRaises(SemanticNodeError):
                    fragment_registry.validate(malformed)

    def test_characteristic_lowering_mutants_are_killed(self):
        fixtures = (
            (
                "query_self_characteristics_handler",
                _permanent(
                    "This creature gets +1/+1 for each artifact you control.",
                    suffix=117_002_002,
                    name="Dynamic Fixture",
                ),
                QUERY_HANDLER,
            ),
            (
                "fixed_query_keyword_grant_handler",
                _permanent(
                    "Creature tokens you control have haste.",
                    suffix=117_002_003,
                    name="Keyword Fixture",
                ),
                KEYWORD_GRANT_HANDLER,
            ),
        )
        for function_name, record, handler_id in fixtures:
            def assert_boundary() -> None:
                program = self.compile(record, trust_level="provisional")
                self.assertTrue(
                    any(
                        descriptor.get("handler_id") == handler_id
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

            assert_boundary()
            with mock.patch(
                f"quorune.compiler.runtime_templates.{function_name}",
                return_value=None,
            ):
                with self.assertRaises(AssertionError):
                    assert_boundary()

        conditional_record = _permanent(
            "This creature has haste as long as an opponent has 10 or less life.",
            suffix=117_002_001,
            name="Conditional Fixture",
        )

        def assert_compatibility_boundary() -> None:
            with mock.patch(
                "quorune.compiler.runtime_templates."
                "fixed_public_state_characteristics_handler",
                return_value=None,
            ):
                program = self.compile(
                    conditional_record,
                    trust_level="provisional",
                )
            self.assertTrue(
                any(
                    descriptor.get("handler_id") == CONDITIONAL_HANDLER
                    for ability in program.abilities
                    for descriptor in ability.handlers
                )
            )

        assert_compatibility_boundary()
        with mock.patch(
            "quorune.compiler.runtime_templates.conditional_self_keyword_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compatibility_boundary()


class TypedDynamicCharacteristicRuntimeTests(unittest.TestCase):
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
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def zone_cards(engine, owner: str, *, card_type: str, count: int, exclude=()):
        excluded = {value.object_id for value in exclude}
        return [
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.object_id not in excluded
            and card_type
            in engine._type_parts(engine.card_record(card).type_line)[0]
        ][:count]

    def test_four_player_keyword_grants_are_controller_scoped_and_phase_with_source(
        self,
    ):
        session = self.session(117_003_001)
        engine = session.engine
        padeem = self.card(engine, "A", "Padeem, Consul of Innovation")
        brudiclad = self.card(engine, "A", "Brudiclad, Telchor Engineer")
        own_ring = self.card(engine, "A", "Sol Ring")
        opposing_ring = self.card(engine, "B", "Sol Ring")
        for card, controller in (
            (padeem, "A"),
            (brudiclad, "A"),
            (own_ring, "A"),
            (opposing_ring, "B"),
        ):
            engine.move_card(card.object_id, "battlefield", controller=controller, log=False)
        token_ref = engine.create_token(
            "A",
            name="Soldier",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "1",
                "toughness": "1",
            },
            reason="typed keyword grant fixture",
        )[0]
        token = engine._resolve_object("A", token_ref)

        self.assertIn("Hexproof", engine._effective_card_data(own_ring)["keywords"])
        self.assertNotIn(
            "Hexproof", engine._effective_card_data(opposing_ring)["keywords"]
        )
        self.assertIn("Haste", engine._effective_card_data(token)["keywords"])

        padeem.phased_out = True
        brudiclad.phased_out = True
        self.assertNotIn(
            "Hexproof", engine._effective_card_data(own_ring)["keywords"]
        )
        self.assertNotIn("Haste", engine._effective_card_data(token)["keywords"])

    def test_self_conditions_and_counts_use_typed_fragments_only(self):
        session = self.session(117_003_002)
        engine = session.engine
        bloodghast = self.card(engine, "B", "Bloodghast")
        wight = self.card(engine, "B", "Wight of the Reliquary")
        reclaimer = self.card(engine, "B", "Elvish Reclaimer")
        for card in (bloodghast, wight, reclaimer):
            engine.move_card(card.object_id, "battlefield", controller="B", log=False)

        engine.state.players["A"].life = 11
        engine.state.players["C"].life = 10
        self.assertIn("Haste", engine._effective_card_data(bloodghast)["keywords"])
        engine.state.players["C"].life = 11
        self.assertNotIn(
            "Haste", engine._effective_card_data(bloodghast)["keywords"]
        )

        creatures = self.zone_cards(
            engine,
            "B",
            card_type="creature",
            count=2,
            exclude=(bloodghast, wight, reclaimer),
        )
        for creature in creatures:
            engine.move_card(creature.object_id, "graveyard", log=False)
        printed_wight = self.db.lookup("Wight of the Reliquary")
        current_wight = engine._effective_card_data(wight)
        self.assertEqual(
            int(printed_wight.power) + len(creatures),
            int(current_wight["power"]),
        )
        self.assertEqual(
            int(printed_wight.toughness) + len(creatures),
            int(current_wight["toughness"]),
        )

        lands = self.zone_cards(
            engine,
            "B",
            card_type="land",
            count=3,
            exclude=(reclaimer,),
        )
        for land in lands[:2]:
            engine.move_card(land.object_id, "graveyard", log=False)
        printed_reclaimer = self.db.lookup("Elvish Reclaimer")
        self.assertEqual(
            int(printed_reclaimer.power),
            int(engine._effective_card_data(reclaimer)["power"]),
        )
        engine.move_card(lands[2].object_id, "graveyard", log=False)
        self.assertEqual(
            int(printed_reclaimer.power) + 2,
            int(engine._effective_card_data(reclaimer)["power"]),
        )

    def test_raw_oracle_text_without_fragment_does_not_select_behavior(self):
        session = self.session(117_003_003)
        engine = session.engine
        ring = self.card(engine, "A", "Sol Ring")
        engine.move_card(ring.object_id, "battlefield", controller="A", log=False)
        ring.annotations["copy_overrides"] = {
            "name": "Raw Text Construct",
            "type_line": "Artifact Creature — Construct",
            "oracle_text": "This creature gets +1/+1 for each artifact you control.",
            "power": "0",
            "toughness": "0",
            "ability_fragments": [],
            "activated_abilities": [],
        }
        current = engine._effective_card_data(ring)
        self.assertEqual("0", current["power"])
        self.assertEqual("0", current["toughness"])

        bloodghast = self.card(engine, "B", "Bloodghast")
        engine.move_card(bloodghast.object_id, "battlefield", controller="B", log=False)
        engine.state.players["A"].life = 10
        for event in ("continuous", "characteristics.evaluate"):
            for program in tuple(
                engine.semantics.runtime_handler_programs_for_oracle(
                    bloodghast.oracle_id,
                    active_zone="battlefield",
                    event=event,
                )
            ):
                if any(
                    descriptor.get("handler_id")
                    in {CONDITIONAL_HANDLER, PUBLIC_STATE_HANDLER}
                    for descriptor in program.handlers
                ):
                    engine.semantics.remove(program.key)
        self.assertNotIn(
            "Haste", engine._effective_card_data(bloodghast)["keywords"]
        )

    def test_copied_dynamic_fragment_uses_current_public_state(self):
        session = self.session(117_003_004)
        engine = session.engine
        source = self.card(engine, "B", "Wight of the Reliquary")
        engine.move_card(source.object_id, "battlefield", controller="B", log=False)
        copied_ref = engine.create_token(
            "B",
            name="",
            copy_of=source.ref,
            reason="typed dynamic fragment copy fixture",
        )[0]
        copied = engine._resolve_object("B", copied_ref)
        creatures = self.zone_cards(
            engine,
            "B",
            card_type="creature",
            count=2,
            exclude=(source, copied),
        )
        for creature in creatures:
            engine.move_card(creature.object_id, "graveyard", log=False)
        source_data = engine._effective_card_data(source)
        copied_data = engine._effective_card_data(copied)
        self.assertEqual(source_data["power"], copied_data["power"])
        self.assertEqual(source_data["toughness"], copied_data["toughness"])
        self.assertTrue(
            any(
                value.get("kind") == "query_characteristic_modifier"
                for value in copied_data["ability_fragments"]
            )
        )

    def test_dynamic_characteristics_replay_exactly(self):
        session = self.session(117_003_005)
        engine = session.engine
        brudiclad = self.card(engine, "A", "Brudiclad, Telchor Engineer")
        bloodghast = self.card(engine, "B", "Bloodghast")
        engine.move_card(
            brudiclad.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(
            bloodghast.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.players["C"].life = 10
        token_ref = engine.create_token(
            "A",
            name="Construct",
            characteristics={
                "type_line": "Token Artifact Creature — Construct",
                "power": "0",
                "toughness": "0",
                "ability_fragments": [DYNAMIC_FRAGMENT],
            },
            reason="typed replay fixture",
        )[0]
        token = engine._resolve_object("A", token_ref)
        token_data = engine._effective_card_data(token)
        self.assertGreater(int(token_data["power"]), 0)
        self.assertIn("Haste", token_data["keywords"])
        self.assertIn(
            "Haste",
            engine._effective_card_data(bloodghast)["keywords"],
        )
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_TYPED_CHARACTERISTIC",
                "reason": "Record a command after the typed fragment checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-dynamic-characteristics"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
