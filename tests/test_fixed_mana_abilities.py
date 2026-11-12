from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.abilities import parse_activated_abilities
from quorune.activation_usage import ActivationLimit
from quorune.carddb import CardRecord
from quorune.fixed_mana_abilities import (
    FixedActivatedManaAbilitySpec,
    FixedManaAbilityError,
    FixedManaMode,
    compile_fixed_activated_mana_ability,
    fixed_mana_modes_from_effect,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.mana_provenance import ManaProvenanceError
from quorune.mana_restrictions import (
    ability_mana_spend_context,
    mana_restriction_allows,
    spell_mana_spend_context,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime.mana_abilities import (
    fixed_mana_specs_from_descriptors,
)


def record(oracle_text: str, *, name: str = "Typed Mana Relic") -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000605",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact",
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=("C",),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedManaModelTests(unittest.TestCase):
    def test_fixed_mana_result_codecs_reject_unknown_kinds_and_preserve_v1_bytes(self):
        from quorune.fixed_mana_results import FixedManaResultSpec
        from quorune.semantic_runtime.mana_abilities import FixedActivatedManaAbilityHandler,FixedManaResultAbilityHandler
        from quorune.fixed_mana_abilities import fixed_mana_handler_descriptor
        ability=parse_activated_abilities(card_name='Typed Mana Relic',oracle_text='{T}: Add {G}.')[0]
        legacy=compile_fixed_activated_mana_ability(ability);self.assertIsNotNone(legacy)
        value=legacy.to_dict();self.assertNotIn('result',value)
        self.assertEqual(value,FixedActivatedManaAbilitySpec.from_dict(value).to_dict())
        descriptor=fixed_mana_handler_descriptor(legacy);self.assertEqual(1,descriptor['schema_version'])
        result=replace(legacy,result=FixedManaResultSpec('damage_controller',1))
        typed=fixed_mana_handler_descriptor(result);self.assertEqual(result,FixedManaResultAbilityHandler().lower(typed,object())[0])
        for malformed in ({'schema_version':1,'kind':'damage_controller','amount':True},
                          {'schema_version':1,'kind':'skip_actor_untap','amount':1},
                          {'schema_version':1,'kind':'unknown','amount':0}):
            with self.assertRaises(ValueError):FixedManaResultSpec.from_dict(malformed)
        for wrong in ({**typed,'schema_version':1},{**descriptor,'schema_version':2},{**typed,'schema_version':True}):
            handler=FixedManaResultAbilityHandler() if wrong['handler_id']==typed['handler_id'] else FixedActivatedManaAbilityHandler()
            with self.assertRaises(ValueError):handler.lower(wrong,object())

    def test_fixed_mana_results_reject_unknown_tails_and_missing_result_capability(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from common import ROOT
        for text, operations in (
            ('{T}: Add {G}. This artifact deals 1 damage to target player.', ('mana', 'damage')),
            ('{T}: Add {G}. This artifact deals 1 damage to you. Draw a card.', ('mana', 'damage', 'draw')),
        ):
            ir = compile_oracle_card(record(text), capability_registry=load_default_capability_registry())
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertEqual(operations, tuple(effect['op'] for effect in ir.faces[0].nodes[0].effects))
            # CR 605.1a excludes target and library-moving activations.
            self.assertEqual('activated_ability', ir.faces[0].nodes[0].kind)
        for text in ('{T}: Add {G}. This artifact deals X damage to you.',
                     '{T}: Add {G}. This artifact deals 1 damage to target player with the chosen name.',
                     '{T}: Add {G}. This artifact deals 1 damage to you. Draw a card with the chosen name.',
                     '{T}, Sacrifice this artifact: Add {G}. This artifact deals 1 damage to you.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text),capability_registry=load_default_capability_registry()).status)
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in data['capabilities'] if c['id']=='mana.activated.fixed_result').update(status='blocked',blockers=['missing canonical result'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        text='{T}: Add {G}. This artifact deals 1 damage to you.'
        self.assertNotEqual('exact',compile_oracle_card(record(text),capability_registry=registry).status)

    def fixed_rider_node(self,tail,kind):
        text='{T}: Add {G} or {U}. '+tail
        ir=compile_oracle_card(record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(node for node in ir.faces[0].nodes if node.kind=='mana_ability')
        self.assertEqual(2,node.handlers[0]['schema_version'])
        self.assertEqual(kind,node.handlers[0]['ability']['result']['kind'])

    def test_fixed_mana_damage_rider_has_closed_typed_result_after_production(self):
        self.fixed_rider_node('This artifact deals 1 damage to you.','damage_controller')

    def test_fixed_mana_next_actor_untap_rider_has_closed_typed_result_after_production(self):
        self.fixed_rider_node("This artifact doesn't untap during your next untap step.",'skip_actor_untap')

    def test_closed_output_grammar_accepts_fixed_modes(self):
        cases = {
            "Add {C}{C}.": [{"C": 2}],
            "Add {G} or {U}.": [{"G": 1}, {"U": 1}],
            "Add {G}{G}, {G}{W}, or {W}{W}.": [
                {"G": 2},
                {"G": 1, "W": 1},
                {"W": 2},
            ],
            "Add one mana of any color.": [
                {color: 1} for color in "WUBRG"
            ],
            "Add one mana of any type.": [
                {color: 1} for color in "WUBRGC"
            ],
            "Add three mana of any one color.": [
                {color: 3} for color in "WUBRG"
            ],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                modes = fixed_mana_modes_from_effect(text)
                self.assertIsNotNone(modes)
                self.assertEqual(
                    expected,
                    [
                        {
                            key: amount
                            for key, amount in mode.bundle.items()
                            if amount
                        }
                        for mode in modes or ()
                    ],
                )

    def test_closed_output_grammar_rejects_ambient_variants(self):
        for text in (
            "Add {G} for each creature you control.",
            "Add one mana of any color in your commander's color identity.",
            "Add {G}. Spend this mana only to cast a creature spell.",
            "Add {G}. This artifact deals 1 damage to you.",
            "Target player adds {G}.",
            "Choose a color. Add one mana of that color.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_mana_modes_from_effect(text))

    def test_typed_spend_restrictions_match_public_characteristics(self):
        cases = (
            (
                "{T}: Add {U}. Spend this mana only to cast an instant or sorcery spell.",
                spell_mana_spend_context("Instant"),
                spell_mana_spend_context("Creature — Wizard"),
            ),
            (
                "{T}: Add {C}{C}. Spend this mana only to activate abilities of artifacts.",
                ability_mana_spend_context("Artifact Creature — Golem"),
                ability_mana_spend_context("Creature — Golem"),
            ),
            (
                "{T}: Add {C}. Spend this mana only to cast artifact spells or activate abilities of artifacts.",
                ability_mana_spend_context("Artifact — Vehicle"),
                ability_mana_spend_context("Creature — Pilot"),
            ),
            (
                "{T}: Add two mana of any one color. Spend this mana only to cast a Dragon spell or activate abilities of Dragons.",
                spell_mana_spend_context("Kindred Enchantment — Dragon"),
                spell_mana_spend_context("Creature — Angel"),
            ),
            (
                "{T}: Add {W}. Spend this mana only to cast legendary spells.",
                spell_mana_spend_context("Legendary Planeswalker — Test"),
                spell_mana_spend_context("Planeswalker — Test"),
            ),
        )
        for text, admitted, rejected in cases:
            with self.subTest(text=text):
                ability = parse_activated_abilities(
                    card_name="Constrained Mana Fixture",
                    oracle_text=text,
                )[0]
                spec = compile_fixed_activated_mana_ability(ability)
                self.assertIsNotNone(spec)
                assert spec is not None and spec.spend_restriction is not None
                self.assertTrue(
                    mana_restriction_allows(spec.spend_restriction, admitted)
                )
                self.assertFalse(
                    mana_restriction_allows(spec.spend_restriction, rejected)
                )

    def test_descriptor_is_immutable_canonical_and_strict(self):
        parsed = parse_activated_abilities(
            card_name="Typed Mana Relic",
            oracle_text="{1}, {T}, Pay 2 life: Add {U} or {B}.",
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        self.assertIsNotNone(spec)
        assert spec is not None
        payload = spec.to_dict()
        payload["mana_cost"]["GENERIC"] = 99
        payload["modes"][0]["bundle"]["U"] = 99
        self.assertEqual(1, spec.mana_cost["GENERIC"])
        self.assertEqual(1, spec.modes[0].bundle["U"])
        self.assertEqual(spec, FixedActivatedManaAbilitySpec.from_dict(spec.to_dict()))
        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["oracle_line"] = 1
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["spend_restriction"] = "unsupported"
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["activation_conditions"] = ["unsupported"]
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)

        conditioned = compile_fixed_activated_mana_ability(
            parse_activated_abilities(
                card_name="Conditional Mana Fixture",
                oracle_text=(
                    "{T}: Add {U}. Activate only if you control an Island."
                ),
            )[0]
        )
        assert conditioned is not None
        self.assertEqual(
            conditioned,
            FixedActivatedManaAbilitySpec.from_dict(conditioned.to_dict()),
        )

    def test_fixed_mode_rejects_unknown_or_empty_outputs(self):
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({"X": 1})
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({})
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({"G": True})
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({"G": "1"})


class FixedManaCompilerTests(unittest.TestCase):
    def test_legacy_restriction_prefix_requires_complete_suffix(self):
        chosen_type = "{T}: Add {G}. Spend this mana only to cast a creature spell of the chosen type."
        chosen_ability = parse_activated_abilities(card_name="Complete Restriction Fixture", oracle_text=chosen_type)[0]
        self.assertIsNotNone(compile_fixed_activated_mana_ability(chosen_ability))
        unsupported = (
            (
                "{T}: Add {G}. Spend this mana only to cast a creature "
                "spell from your hand."
            ),
            (
                "{T}: Add {U}. Spend this mana only to cast an artifact "
                "spell from your graveyard."
            ),
            (
                "{T}: Add {G}. Spend this mana only to cast a creature "
                "spell of a type chosen by an opponent this turn."
            ),
            (
                "{T}: Add {G}. Spend this mana only to cast a creature "
                "spell with flashback."
            ),
            (
                "{T}: Add {G}. Spend this mana only to cast a creature "
                "spell. If this mana is spent on that spell, it gains haste."
            ),
        )
        capabilities = load_default_capability_registry()
        for text in unsupported:
            with self.subTest(text=text):
                ability = parse_activated_abilities(
                    card_name="Complete Restriction Fixture",
                    oracle_text=text,
                )[0]
                self.assertIsNone(compile_fixed_activated_mana_ability(ability))
                compiled = compile_oracle_card(
                    record(text),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)
                self.assertFalse(
                    any(
                        node.handlers
                        for face in compiled.faces
                        for node in face.nodes
                    )
                )
        stale = parse_activated_abilities(
            card_name="Stale Restriction Fixture",
            oracle_text=unsupported[0],
        )[0]
        self.assertIsNone(stale.mana_spend_restriction)
        self.assertIsNone(
            compile_fixed_activated_mana_ability(
                replace(
                    stale,
                    mana_spend_restriction="creature_spell_only",
                )
            )
        )

    def test_complete_legacy_restrictions_preserve_serialized_identity(self):
        cases = (
            (
                "{T}: Add {G}. Spend this mana only to cast a creature spell.",
                "creature_spell_only",
            ),
            (
                "{T}: Add {U}. Spend this mana only to cast an artifact spell.",
                "artifact_spell_only",
            ),
            (
                "{T}: Add {C}. Spend this mana only to cast artifact spells "
                "or activate abilities of artifacts.",
                "artifact_spell_or_ability",
            ),
            (
                "{T}: Add {C}. This mana can't be spent to cast "
                "nonartifact spells.",
                "nonartifact_spell_prohibited",
            ),
            (
                "{T}: Add {G}. Spend this mana only to cast a legendary "
                "spell, and that spell can't be countered.",
                "legendary_spell_uncounterable",
            ),
        )
        for text, expected in cases:
            with self.subTest(expected=expected):
                ability = parse_activated_abilities(
                    card_name="Legacy Restriction Fixture",
                    oracle_text=text,
                )[0]
                self.assertEqual(expected, ability.mana_spend_restriction)
                spec = compile_fixed_activated_mana_ability(ability)
                self.assertIsNotNone(spec)
                assert spec is not None
                self.assertEqual(expected, spec.spend_restriction)
                self.assertEqual(
                    spec,
                    FixedActivatedManaAbilitySpec.from_dict(spec.to_dict()),
                )

    def test_shadowed_legacy_prefixes_use_complete_typed_restrictions(self):
        cases = (
            (
                "{T}: Add {U}. Spend this mana only to cast an artifact "
                "spell or activate an ability.",
                ability_mana_spend_context("Creature — Wizard"),
                spell_mana_spend_context("Creature — Wizard"),
            ),
            (
                "{T}: Add {U}. Spend this mana only to cast an artifact "
                "spell or activate an ability of an artifact source.",
                ability_mana_spend_context("Artifact Creature — Golem"),
                ability_mana_spend_context("Creature — Wizard"),
            ),
        )
        for text, admitted, rejected in cases:
            with self.subTest(text=text):
                ability = parse_activated_abilities(
                    card_name="Typed Restriction Fixture",
                    oracle_text=text,
                )[0]
                spec = compile_fixed_activated_mana_ability(ability)
                self.assertIsNotNone(spec)
                assert spec is not None and spec.spend_restriction is not None
                self.assertTrue(
                    spec.spend_restriction.startswith("mana-restriction-v1|")
                )
                self.assertTrue(
                    mana_restriction_allows(spec.spend_restriction, admitted)
                )
                self.assertFalse(
                    mana_restriction_allows(spec.spend_restriction, rejected)
                )

    def test_fixed_output_compiler_rejects_library_movement_costs_and_effects(self):
        for text in (
            "Mill a card, {T}: Add {C}.",
            "{T}: Add {C}, then draw a card.",
            "{T}: Add {C}, then put a card from your graveyard on the "
            "bottom of your library.",
        ):
            with self.subTest(text=text):
                ability = parse_activated_abilities(
                    card_name="Library Mana Relic",
                    oracle_text=text,
                )[0]
                self.assertIsNone(compile_fixed_activated_mana_ability(ability))

    def test_fixed_output_compiler_lowers_source_spanned_cardprogram(self):
        first_line = "{T}: Add {C}{C}."
        second_line = "{1}, {T}: Add {U} or {B}."
        card = record(f"{first_line}\n{second_line}")
        capabilities = load_default_capability_registry()
        ir = compile_oracle_card(card, capability_registry=capabilities)

        self.assertEqual("exact", ir.status)
        self.assertEqual([1, 2], [node.span.line for node in ir.faces[0].nodes])
        self.assertEqual(
            [(0, len(first_line)), (len(first_line) + 1, len(card.oracle_text))],
            [
                (node.span.start, node.span.end)
                for node in ir.faces[0].nodes
            ],
        )
        self.assertEqual(
            [("mana.activated.fixed_output", "mana.activated.restricted_fixed_output")] * 2,
            [node.capability_dependencies for node in ir.faces[0].nodes],
        )

        class Rulings:
            @staticmethod
            def rulings(_record):
                return ()

        programs = generated_programs(
            Rulings(),
            card,
            trust_level="trusted",
            capability_registry=capabilities,
        )
        self.assertEqual(
            ["ability:ab1", "ability:ab2"],
            [program.ability_id for program in programs],
        )
        self.assertEqual(
            [1, 2],
            [program.provenance["source_span"]["line"] for program in programs],
        )
        self.assertEqual(
            [
                {"line": 1, "start": 0, "end": len(first_line)},
                {
                    "line": 2,
                    "start": len(first_line) + 1,
                    "end": len(card.oracle_text),
                },
            ],
            [program.provenance["source_span"] for program in programs],
        )
        specs = tuple(
            spec
            for program in programs
            for spec in fixed_mana_specs_from_descriptors(program.handlers)
        )
        self.assertEqual(["ab1", "ab2"], [spec.ability_id for spec in specs])
        self.assertEqual(1, specs[1].mana_cost["GENERIC"])

    def test_constrained_mana_compiles_spend_limit_and_land_conditions(self):
        cases = (
            (
                "{T}: Add {U}. Spend this mana only to cast an instant or sorcery spell.",
                {"mana.activated.restricted_fixed_output"},
            ),
            (
                "{T}: Add {G}. Spend this mana only to cast a creature spell.",
                {"mana.activated.restricted_fixed_output"},
            ),
            (
                "{T}: Add one mana of any color. Activate only once each turn.",
                {"activation.usage.once_per_turn", "mana.activated.fixed_output"},
            ),
            (
                "{T}: Add {U} or {B}. Activate only if you control an Island or a Swamp.",
                {"activation.condition.public_query", "mana.activated.fixed_output"},
            ),
            (
                "{T}: Add {G}. Activate only if you control a Goblin or an Elf.",
                {"activation.condition.fixed_public_state", "mana.activated.fixed_output"},
            ),
            (
                "{T}: Add {C}. Activate only if you control three or more artifacts.",
                {"mana.activated.fixed_output"},
            ),
            (
                "{T}: Add {W}. Activate only during your upkeep and only once each turn.",
                {"activation.condition.phase_window", "activation.usage.once_per_turn", "mana.activated.fixed_output"},
            ),
        )
        capabilities = load_default_capability_registry()
        for text, expected in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    record(text),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertLessEqual(expected, set(node.capability_dependencies))
                self.assertTrue(node.handlers)

        for unsupported in (
            "{T}: Add one mana of any color. Spend this mana only to cast spells from exile.",
            "{T}: Add {C}{C}. Spend this mana only to cast spells with mana value 5 or greater.",
            "{T}: Add one mana of any color. Activate only if this land entered this turn or if you control a basic land.",
            "{T}: Add {G}. If this mana is spent on a creature spell, it gains haste.",
        ):
            with self.subTest(unsupported=unsupported):
                ir = compile_oracle_card(
                    record(unsupported),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_once_per_turn_mana_excludes_relative_and_nonstandard_limits(self):
        capabilities = load_default_capability_registry()
        for unsupported in (
            "{T}: Add {U}. Activate only once during your turn.",
            "{T}: Add {U}. Activate only once during each player's turn.",
            "{T}: Add {U}. Activate only once per game.",
        ):
            with self.subTest(unsupported=unsupported):
                ir = compile_oracle_card(
                    record(unsupported),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
    def test_fixed_output_compiler_leaves_dynamic_and_restricted_variants_residual(self):
        for text in (
            "{T}: Add {G} for each creature you control if you attacked this turn.",
            "{T}: Add {G}. Spend this mana only to cast spells from exile.",
            "+1: Add one mana of any color.",
            "{T}: Target player adds {G}.",
            "Discard a card: Add {G}.",
            "Exile a card from your graveyard: Add {B}.",
            "{Q}: Add {U}.",
            "Remove a counter from this artifact: Add {C}.",
            "Sacrifice two creatures: Add {C}{C}.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    record(text),
                    capability_registry=load_default_capability_registry(),
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(any(node.handlers for node in ir.faces[0].nodes))

    def test_fixed_loyalty_mana_uses_resolution_producer_without_promoting_mana_leaf(self):
        text = "+1: Add {G}."
        ability = parse_activated_abilities(card_name="Typed Mana Relic", oracle_text=text)[0]
        self.assertIsNone(compile_fixed_activated_mana_ability(ability))
        ir = compile_oracle_card(replace(record(text), type_line="Legendary Planeswalker — Generic", loyalty="3"),
            capability_registry=load_default_capability_registry(), capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        node = next(node for face in ir.faces for node in face.nodes if node.kind == "activated_ability")
        self.assertEqual(1, node.cost["loyalty_delta"])
        self.assertEqual("fixed-resolution-mana-bundle-v1", node.template_id)
        self.assertEqual(({"op": "mana", "player": "$controller", "color": "G", "amount": 1,
            "source": "$source"},), node.effects)
        self.assertIn("mana.production.fixed_resolution", node.capability_dependencies)

    def test_parenthesized_basic_land_reminder_is_not_an_executable_program(self):
        card = replace(
            record("({T}: Add {B} or {R}.)", name="Typed Dual"),
            mana_cost="",
            type_line="Land — Swamp Mountain",
            produced_mana=("B", "R"),
        )
        ir = compile_oracle_card(
            card,
            capability_registry=load_default_capability_registry(),
        )

        self.assertEqual("exact", ir.status)
        self.assertFalse(any(node.handlers for node in ir.faces[0].nodes))
        self.assertEqual(
            ("mana.intrinsic.basic_land_type",),
            ir.faces[0].nodes[0].capability_dependencies,
        )

    def test_fixed_output_mana_mode_mutants_are_killed(self):
        def assert_exact_modes() -> None:
            ability = replace(
                parse_activated_abilities(
                    card_name="Typed Mana Relic",
                    oracle_text="{T}: Add {G} or {U}.",
                )[0],
                ability_id="ab4",
                line_index=3,
            )
            spec = compile_fixed_activated_mana_ability(ability)
            self.assertIsNotNone(spec)
            assert spec is not None
            observed = [mode.bundle for mode in spec.modes]
            self.assertEqual(1, observed[0]["G"])
            self.assertEqual(1, observed[1]["U"])
            self.assertEqual(0, observed[0]["U"])
            self.assertEqual(0, observed[1]["G"])

        assert_exact_modes()
        with mock.patch(
            "quorune.fixed_mana_abilities._symbol_bundle",
            side_effect=lambda text: FixedManaMode.from_bundle(
                {"U" if "G" in text else "G": 1}
            ),
        ):
            with self.assertRaises(AssertionError):
                assert_exact_modes()


class FixedManaResultRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.build_test_database import build_fixture_database
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'mana-results.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-mana-result-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mana result',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Witnesses
    session=_Witnesses.session
    add=_Witnesses.add
    checkpoint=_Witnesses.checkpoint
    replay=_Witnesses.replay

    def ready(self,s,source,seat='A'):
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.started=True
        e.state.active_player=seat;e.state.phase='precombat_main';e.state.step='main';e.state.priority_passes=[]
        e._grant_priority(seat);e.pump()
        return [row for row in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions'] if row.get('source')==source.ref]

    def test_original_pain_land_mana_result_uses_damage_authority_rollback_and_replay(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s=self.session(359001);e=s.engine;source=self.add(e,'Shivan Reef')
        p=compile_best_available_card_program(self.db,self.db.lookup('Shivan Reef'),semantic_registry=e.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        actions=self.ready(s,source);action=next(row for row in actions if row['id'].endswith(':ab2'))
        self.checkpoint(s);before=authoritative_state_hash(e.state);life=e.state.players['A'].life
        denied=s.act('pilot:C',{'action_id':action['id'],'mana_output':{'U':1}})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        denied=s.act('pilot:A',{'action_id':action['id'],'mana_output':{'G':1}})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        accepted=s.act('pilot:A',{'action_id':action['id'],'mana_output':{'U':1}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(source.tapped)
        self.assertEqual(1,e.state.players['A'].mana_pool['U']);self.assertEqual(life-1,e.state.players['A'].life)
        self.assertFalse(e.state.stack);self.replay(s,load=True)
        projection=json.dumps(s.projector._snapshot('pilot:D'),sort_keys=True)
        self.assertNotIn(source.object_id,projection)
        for seat in 'ABC':
            for object_id in e.state.players[seat].zones['hand']:self.assertNotIn(e.state.cards[object_id].ref,projection)

    def test_original_pain_land_colorless_mode_has_no_damage_result_and_replays(self):
        s=self.session(359005);e=s.engine;source=self.add(e,'Shivan Reef')
        action=next(row for row in self.ready(s,source) if row['ability']=='ab1');self.checkpoint(s)
        life=e.state.players['A'].life;accepted=s.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(1,e.state.players['A'].mana_pool['C'])
        self.assertEqual(life,e.state.players['A'].life);self.assertTrue(source.tapped);self.replay(s,load=True)

    def test_original_pain_land_damage_prevention_preserves_mana_and_replays(self):
        from quorune.damage_prevention_creation import PreventionShieldCreationRequest,PreventionSubjectAllocation,plan_prevention_shield_creation,commit_prevention_shield_creation
        from quorune.damage_modifier_state import PreventionMode,DamageModifierDuration
        s=self.session(359003);e=s.engine;source=self.add(e,'Shivan Reef',seat='B')
        e.change_control(source.object_id,'A',reason='pain land current controller')
        request=PreventionShieldCreationRequest(source_id='fixture:mana-prevention',controller='A',
            mode=PreventionMode.AMOUNT,duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            subjects=(PreventionSubjectAllocation('A',1),),label='Prevent next point')
        commit_prevention_shield_creation(e,plan_prevention_shield_creation(e,request))
        action=next(row for row in self.ready(s,source) if row['ability']=='ab2')
        self.checkpoint(s);life={seat:e.state.players[seat].life for seat in 'ABCD'}
        accepted=s.act('pilot:A',{'action_id':action['id'],'mana_output':{'R':1}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(source.tapped)
        self.assertEqual(1,e.state.players['A'].mana_pool['R']);self.assertFalse(e.state.damage_prevention_shields)
        self.assertEqual(life,{seat:e.state.players[seat].life for seat in 'ABCD'});self.replay(s,load=True)

    def test_original_pain_land_auto_payment_applies_result_once_and_replays(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        s=self.session(359004);e=s.engine;source=self.add(e,'Shivan Reef')
        spell=self.add(e,'Generic Bound Next Untap',zone='hand');target=self.add(e,'Generic Bound Body',seat='B')
        self.ready(s,source);self.checkpoint(s);life=e.state.players['A'].life
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==spell.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(source.tapped)
        self.assertEqual(life-1,e.state.players['A'].life);self.assertEqual(0,e.state.players['A'].mana_pool['U'])
        BoundEffectProgramRuntimeTests.resolve(self,s);self.assertTrue(target.annotations.get('does_not_untap_next'))
        self.replay(s,load=True)

    def test_original_mogg_mana_result_retains_acting_player_untap_duration_and_replay(self):
        from quorune.engine import TURN_STEPS
        from quorune.semantic_runtime.untap_steps import current_untap_step_plan
        from quorune.untap_step_coordination import commit_untap_step
        s=self.session(359002);e=s.engine;source=self.add(e,'Mogg Hollows')
        action=next(row for row in self.ready(s,source) if row['id'].endswith(':ab2'));self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'mana_output':{'G':1}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(['A'],source.annotations.get('exert_next_untap_players'))
        self.assertEqual(1,e.state.players['A'].mana_pool['G']);self.assertTrue(source.tapped);self.replay(s,load=True)
        e.change_control(source.object_id,'B',reason='fixed mana duration controller boundary')
        for seat in ('B','A'):
            e.state.active_player=seat;e.state.phase='beginning';e.state.step='untap';e.state.phase_index=TURN_STEPS.index(('beginning','untap'))
            commit_untap_step(e,current_untap_step_plan(e,seat),(),selected_optional_ids=())
            if seat=='B':self.assertFalse(source.tapped);self.assertEqual(['A'],source.annotations['exert_next_untap_players']);source.tapped=True
        self.assertTrue(source.tapped);self.assertNotIn('exert_next_untap_players',source.annotations)

    def test_fixed_mana_result_omission_mutant_is_killed(self):
        with mock.patch('quorune.mana_ability_runtime._mana_result_effects',return_value=()):
            with self.assertRaises(AssertionError):
                self.test_original_pain_land_mana_result_uses_damage_authority_rollback_and_replay()

    def test_fixed_mana_untap_result_omission_mutant_is_killed(self):
        with mock.patch('quorune.mana_ability_runtime._mana_result_effects',return_value=()):
            with self.assertRaises(AssertionError):
                self.test_original_mogg_mana_result_retains_acting_player_untap_duration_and_replay()

    def test_original_pain_land_pending_prevention_choice_resumes_once_and_replays(self):
        from quorune.damage_prevention_creation import PreventionShieldCreationRequest,PreventionSubjectAllocation,plan_prevention_shield_creation,commit_prevention_shield_creation
        from quorune.damage_modifier_state import PreventionMode,DamageModifierDuration
        s=self.session(359006);e=s.engine;source=self.add(e,'Shivan Reef')
        for label in ('one','two'):
            request=PreventionShieldCreationRequest(source_id='fixture:mana-prevention:'+label,controller='A',
                mode=PreventionMode.AMOUNT,duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                subjects=(PreventionSubjectAllocation('A',1),),label='Prevent '+label)
            commit_prevention_shield_creation(e,plan_prevention_shield_creation(e,request))
        action=next(row for row in self.ready(s,source) if row['ability']=='ab2');self.checkpoint(s)
        life=e.state.players['A'].life
        accepted=s.act('pilot:A',{'action_id':action['id'],'mana_output':{'U':1}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIn('replacement',s.state.pending_decision.kind)
        self.assertFalse(e.state.cards[source.object_id].tapped);self.assertEqual(0,e.state.players['A'].mana_pool['U'])
        resumed=self.replay(s,load=True);decision=resumed.packet('pilot:A',full=True)['decision']
        chosen=decision['ctx']['options'][0]['id'];before=authoritative_state_hash(resumed.state)
        denied=resumed.act('pilot:C',{'action_id':'choose','replacement':chosen})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        accepted=resumed.act('pilot:A',{'action_id':'choose','replacement':chosen})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(resumed.state.cards[source.object_id].tapped)
        self.assertEqual(1,resumed.state.players['A'].mana_pool['U']);self.assertEqual(life,resumed.state.players['A'].life)
        self.assertEqual(1,len(resumed.state.damage_prevention_shields));self.replay(resumed,load=True)


class FixedTriggeredManaRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = FixedManaResultRuntimeTests.session
    add = FixedManaResultRuntimeTests.add
    checkpoint = FixedManaResultRuntimeTests.checkpoint
    replay = FixedManaResultRuntimeTests.replay
    ready = FixedManaResultRuntimeTests.ready

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'triggered-mana.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                                ROOT / 'tests/fixtures/fixed-triggered-mana-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed mana triggers', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def actions(self, session):
        return session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']

    def activate(self, session, source, **choices):
        offer = next(action for action in self.actions(session) if action.get('source') == source.ref)
        accepted = session.act('pilot:A', {'action_id': offer['id'], **choices})
        self.assertTrue(accepted.ok, accepted.summary)

    def attach(self, session, aura, land):
        from quorune.attachments import attach_objects

        attach_objects(session.state.cards, aura, land, source_timestamp=aura.zone_timestamp,
                       players=session.state.players)

    def assert_closed(self, session, card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program

        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry,
            capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])

    def test_original_wild_growth_and_overgrowth_add_for_the_land_controller_immediately(self):
        session = self.session(6054001)
        land = self.add(session.engine, 'Island')
        growth = self.add(session.engine, 'Wild Growth', seat='B')
        overgrowth = self.add(session.engine, 'Overgrowth', seat='C')
        private = self.add(session.engine, 'Generic Bound Body', seat='D', zone='hand')
        for aura in (growth, overgrowth):
            self.attach(session, aura, land)
            self.assert_closed(session, aura)
        offer, = self.ready(session, land)
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        command = {'action_id': offer['id'], 'mana_output': {'U': 1}}
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertFalse(session.act('pilot:A', {**command, 'mana_output': {'U': 1, 'G': 3}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((1, 3), tuple(session.state.players['A'].mana_pool[c] for c in 'UG'))
        self.assertEqual([0, 0, 0], [sum(session.state.players[s].mana_pool.values()) for s in 'BCD'])
        self.assertFalse(session.state.stack)
        self.assertFalse(session.state.pending_trigger_batches)
        self.assertFalse(any(action['action'] == 'undo_mana' for action in self.actions(session)))
        self.assertEqual([growth.ref, overgrowth.ref], [e.details['source'] for e in session.state.events if e.code == 'mana.triggered'])
        for seat in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet(f'pilot:{seat}', full=True)))
        self.replay(session, load=True)

    def test_original_wild_growth_funds_a_nonartifact_cast_from_restricted_base(self):
        session = self.session(6054002)
        workshop = self.add(session.engine, "Mishra's Workshop")
        aura = self.add(session.engine, 'Wild Growth', seat='B')
        spell = self.add(session.engine, 'Giant Growth', zone='hand')
        body = self.add(session.engine, 'Generic Bound Body', seat='C')
        self.attach(session, aura, workshop)
        self.assert_closed(session, aura)
        self.ready(session, workshop)
        self.checkpoint(session)
        offer = next(action for action in self.actions(session) if action.get('card') == spell.ref)
        command = {'action_id': offer['id'], 'targets': [body.ref]}
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {**command, 'pay': 'manual', 'payment': {'C': 1},
            'mana': [{'source': workshop.ref, 'bundle': {'C': 3}}]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {**command, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[workshop.object_id].tapped)
        self.assertEqual((3, 0), tuple(session.state.players['A'].mana_pool[c] for c in 'CG'))
        self.assertEqual(0, sum(session.engine._spendable_mana_pool('A', spell_mana_spend_context('Instant')).values()))
        self.assertEqual(['spell'], [item.kind for item in session.state.stack])
        self.replay(session, load=True)

    def test_original_wild_growth_snow_lots_and_ordinary_taps_stay_separate(self):
        from quorune.mana_provenance import mana_provenance_lots
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests

        session = self.session(6054003)
        snowy = self.add(session.engine, 'Snow-Covered Forest')
        plain = self.add(session.engine, 'Forest')
        aura = self.add(session.engine, 'Wild Growth', seat='B')
        second = self.add(session.engine, 'Overgrowth', seat='B')
        icy = self.add(session.engine, 'Icy Manipulator')
        pillar = self.add(session.engine, 'Icebind Pillar')
        target = self.add(session.engine, 'Generic Bound Body', seat='C')
        self.attach(session, aura, snowy)
        self.attach(session, second, plain)
        session.state.players['A'].mana_pool['C'] = 1
        self.ready(session, icy)
        self.checkpoint(session)
        self.activate(session, icy, targets=[plain.ref], pay='auto')
        BoundEffectProgramRuntimeTests.resolve(self, session)
        self.assertTrue(session.state.cards[plain.object_id].tapped)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(any(e.code == 'mana.triggered' for e in session.state.events))
        self.activate(session, snowy)
        self.assertEqual(2, session.state.players['A'].mana_pool['G'])
        self.assertEqual([({'G': 1}, True)], [(dict(lot.bundle), lot.snow) for lot in mana_provenance_lots(session.state.players['A'])])
        self.activate(session, pillar, targets=[target.ref], pay='auto')
        self.assertEqual(1, session.state.players['A'].mana_pool['G'])
        self.assertFalse(mana_provenance_lots(session.state.players['A']))
        BoundEffectProgramRuntimeTests.resolve(self, session)
        self.assertTrue(session.state.cards[target.object_id].tapped)
        self.replay(session, load=True)

    def test_original_controlled_and_global_triggers_use_type_and_base_output(self):
        session = self.session(6054004)
        leyline = self.add(session.engine, 'Leyline of Abundance')
        bloom = self.add(session.engine, 'Vernal Bloom', seat='B')
        monument = self.add(session.engine, 'Forsaken Monument')
        tropical = self.add(session.engine, 'Tropical Island')
        arbor = self.add(session.engine, 'Dryad Arbor')
        arbor.acquired_control_turn_count = -1
        ring = self.add(session.engine, 'Sol Ring')
        for provider in (leyline, bloom, monument):
            self.assert_closed(session, provider)
        self.ready(session, tropical)
        self.checkpoint(session)
        blue = next(action for action in self.actions(session)
            if action.get('source') == tropical.ref and '{U}' in action['label'])
        result = session.act('pilot:A', {'action_id': blue['id'], 'mana_output': {'U': 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((1, 1), tuple(session.state.players['A'].mana_pool[c] for c in 'UG'))
        self.activate(session, arbor)
        self.assertEqual(4, session.state.players['A'].mana_pool['G'])
        self.activate(session, ring)
        self.assertEqual(3, session.state.players['A'].mana_pool['C'])
        self.assertEqual(4, sum(event.code == 'mana.triggered' for event in session.state.events))
        self.replay(session, load=True)

    def test_original_wild_growth_bonus_can_fund_a_priced_mana_step(self):
        session = self.session(6054005)
        island = self.add(session.engine, 'Snow-Covered Island')
        thicket = self.add(session.engine, 'Fire-Lit Thicket')
        aura = self.add(session.engine, 'Wild Growth', seat='B')
        spell = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.attach(session, aura, island)
        self.ready(session, island)
        self.checkpoint(session)
        action = next(action for action in self.actions(session) if action.get('card') == spell.ref)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': ['C'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[island.object_id].tapped)
        self.assertTrue(session.state.cards[thicket.object_id].tapped)
        self.assertEqual(1, session.state.players['A'].mana_pool['U'])
        self.assertEqual(2, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(1, sum(e.code == 'mana.triggered' for e in session.state.events))
        self.replay(session, load=True)

    def test_original_wild_growth_requires_a_live_attachment_at_production(self):
        session = self.session(6054007)
        vein = self.add(session.engine, 'Crystal Vein')
        aura = self.add(session.engine, 'Wild Growth', seat='B')
        self.attach(session, aura, vein)
        actions = self.ready(session, vein)
        self.checkpoint(session)
        sacrifice = next(action for action in actions if action['ability'] == 'ab2')
        result = session.act('pilot:A', {'action_id': sacrifice['id'], 'mana_output': {'C': 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[vein.object_id].zone)
        self.assertEqual((2, 0), tuple(session.state.players['A'].mana_pool[c] for c in 'CG'))
        self.assertFalse(any(e.code == 'mana.triggered' for e in session.state.events))
        self.replay(session, load=True)

    def test_original_groundchuck_and_annie_share_immediate_mana_timing(self):
        session = self.session(6054008)
        land = self.add(session.engine, 'Forest')
        creature = self.add(session.engine, 'Groundchuck & Dirtbag')
        enchantment = self.add(session.engine, 'Annie Joins Up')
        self.assert_closed(session, creature)
        self.assert_closed(session, enchantment)
        self.ready(session, land)
        self.checkpoint(session)
        self.activate(session, land)
        self.assertEqual(3, session.state.players['A'].mana_pool['G'])
        self.assertEqual(2, sum(event.code == 'mana.triggered' for event in session.state.events))
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_fixed_mana_trigger_runtime_binding_rejects_wrong_event_and_missing_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.triggered_mana_model import FIXED_TRIGGERED_MANA_CAPABILITY, FIXED_TRIGGERED_MANA_EVENT

        session = self.session(6054006)
        growth = self.add(session.engine, 'Wild Growth')
        self.assert_closed(session, growth)
        program, = (program for program in session.engine.semantics.programs_for_oracle(growth.oracle_id)
                    if program.event == FIXED_TRIGGERED_MANA_EVENT)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != FIXED_TRIGGERED_MANA_CAPABILITY)
        missing = replace(program, capability_dependencies=dependencies,
            capability_closure=None)
        wrong_event = replace(program, event='permanent.tap')
        for mutated in (missing, wrong_event):
            binding = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict'], binding['blockers'])
        original = self.db.lookup('Wild Growth')
        one_color = 'Enchant land\nWhenever enchanted land is tapped for mana, its controller adds one mana of any color.'
        options = dict(capability_registry=self.registry, capability_profile='commander_review')
        chosen = compile_oracle_card(replace(original, oracle_text=one_color), **options)
        self.assertEqual('exact', chosen.status, chosen.material_residuals)
        from quorune.triggered_mana_model import CHOSEN_TRIGGERED_MANA_CAPABILITY
        self.assertTrue(any(CHOSEN_TRIGGERED_MANA_CAPABILITY in node.capability_dependencies
                            for face in chosen.faces for node in face.nodes))
        dynamic = one_color.removesuffix('.') + ' that a land an opponent controls could produce.'
        held = compile_oracle_card(replace(original, oracle_text=dynamic), **options)
        self.assertNotEqual('exact', held.status)
        self.assertTrue(held.material_residuals)

    def test_fixed_mana_trigger_omission_is_detected_by_native_output(self):
        with mock.patch('quorune.mana_activation.commit_triggered_mana', return_value=False):
            with self.assertRaises(AssertionError):
                self.test_original_wild_growth_and_overgrowth_add_for_the_land_controller_immediately()

    @staticmethod
    def bonus_option(base, *choices):
        from quorune.triggered_mana_model import TriggeredManaOption

        return TriggeredManaOption(FixedManaMode.from_bundle(base),
            tuple(FixedManaMode.from_bundle(choice) for choice in choices)).encode()

    def test_chosen_triggered_mana_direct_choice_uses_only_base_types_and_is_actor_scoped(self):
        from quorune.choice_forms import build_action_form
        from quorune.triggered_mana_model import TRIGGERED_MANA_OPTION_FIELD

        session = self.session(6054201)
        land = self.add(session.engine, 'Boros Garrison')
        colorless = self.add(session.engine, 'Crystal Vein')
        aura = self.add(session.engine, 'Fertile Ground', seat='C')
        flare = self.add(session.engine, 'Mana Flare', seat='B')
        private = self.add(session.engine, 'Generic Bound Body', seat='D', zone='hand')
        self.attach(session, aura, land)
        for card in (aura, flare, land):
            self.assert_closed(session, card)
        offer, = self.ready(session, land)
        self.checkpoint(session)
        field = next(field for field in build_action_form(offer, decision_kind='priority', context={})['fields']
                     if field['name'] == TRIGGERED_MANA_OPTION_FIELD)
        valid = self.bonus_option({'R': 1, 'W': 1}, {'G': 1}, {'W': 1})
        self.assertIn(valid, {row['value'] for row in field['options']})
        self.assertTrue(any(aura.ref in row['label'] and flare.ref in row['label'] for row in field['options']))
        before = authoritative_state_hash(session.state)
        invalid = self.bonus_option({'R': 1, 'W': 1}, {'G': 1}, {'G': 1})
        bad_base = self.bonus_option({'U': 1}, {'G': 1})
        for seat, response in (
            ('B', {TRIGGERED_MANA_OPTION_FIELD: valid}),
            ('C', {TRIGGERED_MANA_OPTION_FIELD: valid}),
            ('A', {}),
            ('A', {TRIGGERED_MANA_OPTION_FIELD: invalid}),
            ('A', {TRIGGERED_MANA_OPTION_FIELD: bad_base}),
            ('A', {TRIGGERED_MANA_OPTION_FIELD: valid, 'mana_output': {'U': 1}}),
        ):
            with self.subTest(seat=seat, response=response):
                self.assertFalse(session.act(f'pilot:{seat}', {'action_id': offer['id'], **response}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': offer['id'], 'choices': {TRIGGERED_MANA_OPTION_FIELD: valid}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((1, 2, 1), tuple(session.state.players['A'].mana_pool[color] for color in 'RWG'))
        self.assertEqual([0, 0, 0], [sum(session.state.players[seat].mana_pool.values()) for seat in 'BCD'])
        self.assertEqual([(aura.ref, {'G': 1}), (flare.ref, {'W': 1})], [
            (e.details['source'], e.details['bundle']) for e in session.state.events if e.code == 'mana.triggered'
        ])
        self.assertFalse(session.state.stack)
        self.assertFalse(session.state.pending_trigger_batches)
        colorless_offer = next(action for action in self.actions(session)
                               if action.get('source') == colorless.ref and action['ability'] == 'ab1')
        self.assertNotIn(TRIGGERED_MANA_OPTION_FIELD, colorless_offer.get('choice_schema') or {})
        result = session.act('pilot:A', {'action_id': colorless_offer['id']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['C'])
        for seat in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet(f'pilot:{seat}', full=True)))
        self.replay(session, load=True)

    def test_chosen_triggered_mana_auto_payment_selects_one_produced_type(self):
        session = self.session(6054202)
        land = self.add(session.engine, 'Boros Garrison')
        flare = self.add(session.engine, 'Mana Flare', seat='B')
        spell = self.add(session.engine, "Krenko's Enforcer", zone='hand')
        self.assert_closed(session, flare)
        self.ready(session, land)
        self.checkpoint(session)
        offer = next(action for action in self.actions(session) if action.get('card') == spell.ref)
        result = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual([{'R': 1}], [e.details['bundle'] for e in session.state.events if e.code == 'mana.triggered'])
        self.assertEqual(['spell'], [item.kind for item in session.state.stack])
        self.replay(session, load=True)

    def test_chosen_triggered_mana_auto_payment_can_choose_a_color_absent_from_base(self):
        session = self.session(6054210)
        land = self.add(session.engine, 'Island')
        aura = self.add(session.engine, 'Fertile Ground', seat='B')
        spell = self.add(session.engine, 'Giant Growth', zone='hand')
        target = self.add(session.engine, 'Generic Bound Body')
        self.attach(session, aura, land)
        self.ready(session, land)
        self.checkpoint(session)
        offer = next(action for action in self.actions(session) if action.get('card') == spell.ref)
        result = session.act('pilot:A', {'action_id': offer['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((1, 0), tuple(session.state.players['A'].mana_pool[color] for color in 'UG'))
        self.assertEqual([{'G': 1}], [e.details['bundle'] for e in session.state.events if e.code == 'mana.triggered'])
        self.replay(session, load=True)

    def test_chosen_triggered_mana_two_colors_can_repeat_and_keep_source_provenance(self):
        from quorune.mana_provenance import mana_provenance_lots
        from quorune.triggered_mana_model import TRIGGERED_MANA_OPTION_FIELD

        session = self.session(6054203)
        land = self.add(session.engine, 'Snow-Covered Island')
        aura = self.add(session.engine, 'Market Festival', seat='B')
        self.attach(session, aura, land)
        self.assert_closed(session, aura)
        offer, = self.ready(session, land)
        self.checkpoint(session)
        selected = self.bonus_option({'U': 1}, {'R': 2})
        self.assertIn(selected, offer['choice_schema'][TRIGGERED_MANA_OPTION_FIELD]['legal_values'])
        before = authoritative_state_hash(session.state)
        invalid = self.bonus_option({'U': 1}, {'C': 2})
        self.assertFalse(session.act('pilot:A', {'action_id': offer['id'], TRIGGERED_MANA_OPTION_FIELD: invalid}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': offer['id'], TRIGGERED_MANA_OPTION_FIELD: selected})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((1, 2), tuple(session.state.players['A'].mana_pool[color] for color in 'UR'))
        self.assertEqual([({'U': 1}, True)], [(dict(lot.bundle), lot.snow)
            for lot in mana_provenance_lots(session.state.players['A'])])
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_chosen_triggered_mana_uses_basic_status_and_matches_subtype_union_once(self):
        session = self.session(6054204)
        dual = self.add(session.engine, 'Taiga')
        basic = self.add(session.engine, 'Mountain')
        keeper = self.add(session.engine, 'Keeper of Progenitus', seat='C')
        leaper = self.add(session.engine, 'Lavaleaper', seat='B')
        for card in (keeper, leaper):
            self.assert_closed(session, card)
        self.ready(session, dual)
        self.checkpoint(session)
        red = next(action for action in self.actions(session) if action.get('source') == dual.ref and '{R}' in action['label'])
        result = session.act('pilot:A', {'action_id': red['id'], 'mana_output': {'R': 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['R'])
        self.activate(session, basic)
        self.assertEqual(5, session.state.players['A'].mana_pool['R'])
        self.assertCountEqual([keeper.ref, keeper.ref, leaper.ref],
                              [e.details['source'] for e in session.state.events if e.code == 'mana.triggered'])
        self.replay(session, load=True)

    def test_chosen_triggered_mana_monarch_condition_controls_offer_and_output(self):
        from quorune.triggered_mana_model import TRIGGERED_MANA_OPTION_FIELD

        for monarch in ('A', 'B'):
            with self.subTest(monarch=monarch):
                session = self.session(6054205 + ord(monarch))
                land = self.add(session.engine, 'Island')
                behemoth = self.add(session.engine, 'Regal Behemoth')
                session.state.monarch = monarch
                self.assert_closed(session, behemoth)
                offer, = self.ready(session, land)
                self.checkpoint(session)
                command = {'action_id': offer['id']}
                schema = offer.get('choice_schema') or {}
                if monarch == 'A':
                    selected = self.bonus_option({'U': 1}, {'G': 1})
                    self.assertIn(selected, schema[TRIGGERED_MANA_OPTION_FIELD]['legal_values'])
                    command[TRIGGERED_MANA_OPTION_FIELD] = selected
                else:
                    self.assertNotIn(TRIGGERED_MANA_OPTION_FIELD, schema)
                result = session.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual((1, int(monarch == 'A')), tuple(session.state.players['A'].mana_pool[color] for color in 'UG'))
                self.replay(session, load=True)

    def test_chosen_triggered_mana_descriptor_and_assist_step_are_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.casting.assist_frame import AssistManaStep
        from quorune.rules.casting.assist_payment import _available_steps
        from quorune.triggered_mana_model import CHOSEN_TRIGGERED_MANA_CAPABILITY, FIXED_TRIGGERED_MANA_EVENT

        session = self.session(6054208)
        land = self.add(session.engine, 'Boros Garrison')
        flare = self.add(session.engine, 'Mana Flare', seat='B')
        self.ready(session, land)
        steps = [step for step, _ in _available_steps(session.engine, 'A', None, set()) if step.source == land.ref]
        self.assertEqual({self.bonus_option({'R': 1, 'W': 1}, {color: 1}) for color in 'RW'},
                         {step.triggered_mana_option for step in steps})
        self.assertTrue(all(AssistManaStep.from_dict(step.to_dict()) == step for step in steps))
        program, = (program for program in session.engine.semantics.programs_for_oracle(flare.oracle_id)
                    if program.event == FIXED_TRIGGERED_MANA_EVENT)
        omitted = replace(program, capability_dependencies=(), capability_closure=None)
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + CHOSEN_TRIGGERED_MANA_CAPABILITY, binding['blockers'])

    def test_chosen_triggered_mana_assist_helper_locks_the_selected_bonus(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests

        session = self.session(6054209)
        spell = self.add(session.engine, 'Bring Down', zone='hand')
        land = self.add(session.engine, 'Boros Garrison', seat='B')
        flare = self.add(session.engine, 'Mana Flare', seat='D')
        target = self.add(session.engine, 'Ancient Brontodon', seat='C')
        for card in (spell, land, flare, target):
            self.assert_closed(session, card)
        session.state.players['A'].mana_pool['W'] = 1
        self.ready(session, spell)
        self.checkpoint(session)
        offer = next(action for action in self.actions(session) if action.get('card') == spell.ref)
        result = session.act('pilot:A', {'action_id': offer['id'], 'targets': [target.ref], 'pay': 'auto', 'assist_helper': 'B'})
        self.assertTrue(result.ok, result.summary)
        decision = session.packet('pilot:B', full=True)['decision']
        selected = self.bonus_option({'R': 1, 'W': 1}, {'R': 1})
        mana = next(action for action in decision['legal_actions']
            if action.get('assist_step', {}).get('triggered_mana_option') == selected)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': mana['id']}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:B', {'action_id': mana['id']})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        for seat, action in (('B', 'assist:finish-mana'), ('A', 'assist:finish-mana'),
                             ('B', 'assist:contribute'), ('A', 'assist:pay')):
            command = {'action_id': action}
            if action == 'assist:contribute':
                command['assist_amount'] = 3
            result = session.act(f'pilot:{seat}', command)
            self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertEqual([0, 0], [sum(session.state.players[seat].mana_pool.values()) for seat in 'AB'])
        self.assertEqual([{'R': 1}], [e.details['bundle'] for e in session.state.events if e.code == 'mana.triggered'])
        self.assertIsNone(BoundEffectProgramRuntimeTests.resolve(self, session))
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.replay(session, load=True)

    def test_chosen_triggered_mana_planner_keeps_snow_and_departure_dependencies_distinct(self):
        from quorune.triggered_mana import TriggeredManaOutput, _output_variants

        options = tuple(FixedManaMode.from_bundle({color: 1}) for color in 'RW')
        left = tuple(TriggeredManaOutput('left', 'left', option, False, needs_choice=True) for option in options)
        right = tuple(TriggeredManaOutput('right', 'right', option, False, needs_choice=True) for option in options)
        snowy = tuple(replace(output, snow=True) for output in right)
        self.assertEqual(3, len(_output_variants((left, right), frozenset())))
        self.assertEqual(4, len(_output_variants((left, right), frozenset({'left'}))))
        self.assertEqual(4, len(_output_variants((left, snowy), frozenset())))

    def test_chosen_triggered_mana_option_omission_is_caught_by_automatic_cast(self):
        from quorune.triggered_mana import _selected_outputs

        with mock.patch('quorune.triggered_mana._selected_outputs', side_effect=lambda rows, base, _: _selected_outputs(rows, base, None)):
            with self.assertRaises(AssertionError):
                self.test_chosen_triggered_mana_auto_payment_selects_one_produced_type()


class FixedManaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        return session

    @staticmethod
    def _card(engine, name: str, *, owner: str | None = None):
        return next(
            card
            for card in engine.state.cards.values()
            if card.is_card_object
            and card.printed_name == name
            and (owner is None or card.owner == owner)
        )

    @staticmethod
    def _prepare_priority(session, source):
        engine = session.engine
        seat = source.controller
        engine.move_card(
            source.object_id,
            "battlefield",
            controller=seat,
            tapped=False,
            log=False,
        )
        source.tapped = False
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    def _ring(self, session):
        ring = self._card(session.engine, "Sol Ring")
        self._prepare_priority(session, ring)
        ability = next(
            ability
            for ability in session.engine._activated_abilities(ring)
            if ability.fixed_mana_outputs
        )
        return ring, ability

    def test_compiled_fixed_output_offer_and_activation_share_modes(self):
        session = self.session(60520)
        ring, ability = self._ring(session)
        engine = session.engine
        with mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_compiled_activated_abilities",
            side_effect=AssertionError("runtime activation discovery recompiled Oracle"),
        ):
            rediscovered = engine._activated_abilities(ring)
        self.assertEqual(ability, next(row for row in rediscovered if row.fixed_mana_outputs))
        offered = engine._mana_modes_for_ability(ring.controller, ring, ability)
        self.assertEqual([{"C": 2}], [
            {key: amount for key, amount in mode.bundle.items() if amount}
            for mode in offered
        ])
        before = engine.state.players[ring.controller].mana_pool["C"]
        result = session.act(
            f"pilot:{ring.controller}",
            {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(ring.tapped)
        self.assertEqual(before + 2, engine.state.players[ring.controller].mana_pool["C"])

    def test_compiled_fixed_output_can_pay_during_casting(self):
        session = self.session(60529)
        ring, ability = self._ring(session)
        engine = session.engine
        spell = self._card(
            engine, "Strionic Resonator", owner=ring.controller
        )
        engine.move_card(spell.object_id, "hand", log=False)

        self.assertTrue(ability.fixed_mana_outputs)
        engine._cast(
            ring.controller,
            {"card": spell.ref, "pay": "auto"},
        )

        self.assertTrue(ring.tapped)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(0, engine.state.players[ring.controller].mana_pool["C"])

    def test_changed_executable_oracle_fails_closed_instead_of_reparsing_mana(self):
        session = self.session(60526)
        ring, _ = self._ring(session)
        engine = session.engine
        changed = dict(engine._effective_card_data(ring))
        changed["executable_oracle_text"] = "{T}: Add {U}."
        changed["activated_abilities"] = []

        with mock.patch.object(
            engine,
            "_effective_card_data",
            return_value=changed,
        ), mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_compiled_activated_abilities",
            side_effect=AssertionError("runtime activation discovery recompiled Oracle"),
        ):
            abilities = engine._activated_abilities(ring)

        self.assertFalse(any(ability.mana_ability for ability in abilities))

    def test_invalid_compiled_mana_mode_rolls_back(self):
        session = self.session(60521)
        ring, ability = self._ring(session)
        player = session.state.players[ring.controller]
        before_pool = dict(player.mana_pool)
        before_events = len(session.state.events)

        result = session.act(
            f"pilot:{ring.controller}",
            {
                "a": "activate",
                "source": ring.ref,
                "ability": ability.ability_id,
                "mana_output": {"U": 2},
            },
        )

        self.assertFalse(result.ok)
        restored_ring = session.state.cards[ring.object_id]
        self.assertFalse(restored_ring.tapped)
        self.assertEqual(
            before_pool,
            dict(session.state.players[ring.controller].mana_pool),
        )
        self.assertEqual(before_events, len(session.state.events))

    def test_fixed_mandatory_costs_commit_through_canonical_owners(self):
        session = self.session(60527)
        ring, _ = self._ring(session)
        engine = session.engine
        player = session.state.players[ring.controller]
        player.mana_pool["C"] = 1
        life_before = player.life
        parsed = parse_activated_abilities(
            card_name="Typed Mana Relic",
            oracle_text=(
                "{1}, {T}, Pay 2 life, Sacrifice this artifact: "
                "Add {B}{B}{B}."
            ),
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        self.assertIsNotNone(spec)
        assert spec is not None
        ability = spec.to_activated_ability()

        with mock.patch.object(
            engine, "_activated_abilities", return_value=(ability,)
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": ability.ability_id,
                },
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", ring.zone)
        self.assertEqual(life_before - 2, player.life)
        self.assertEqual(0, player.mana_pool["C"])
        self.assertEqual(3, player.mana_pool["B"])

    def test_invalid_mode_rolls_back_fixed_mandatory_costs(self):
        session = self.session(60528)
        ring, _ = self._ring(session)
        engine = session.engine
        player = session.state.players[ring.controller]
        player.mana_pool["C"] = 1
        before_pool = dict(player.mana_pool)
        life_before = player.life
        parsed = parse_activated_abilities(
            card_name="Typed Mana Relic",
            oracle_text=(
                "{1}, {T}, Pay 2 life, Sacrifice this artifact: "
                "Add {B}{B}{B}."
            ),
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        self.assertIsNotNone(spec)
        assert spec is not None
        ability = spec.to_activated_ability()

        with mock.patch.object(
            engine, "_activated_abilities", return_value=(ability,)
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": ability.ability_id,
                    "mana_output": {"U": 3},
                },
            )

        self.assertFalse(result.ok)
        restored_ring = session.state.cards[ring.object_id]
        self.assertEqual("battlefield", restored_ring.zone)
        self.assertFalse(restored_ring.tapped)
        self.assertEqual(life_before, session.state.players[ring.controller].life)
        self.assertEqual(
            before_pool,
            dict(session.state.players[ring.controller].mana_pool),
        )

    def test_compiled_fixed_output_creature_respects_haste_cost_dependency(self):
        session = self.session(60522)
        engine = session.engine
        creature = self._card(engine, "Elves of Deep Shadow")
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller=creature.owner,
            tapped=False,
            log=False,
        )
        parsed = parse_activated_abilities(
            card_name="Typed Mana Creature",
            oracle_text="{T}: Add {G}.",
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        assert spec is not None
        ability = spec.to_activated_ability()

        self.assertEqual(
            ("unavailable", "summoning_sickness"),
            engine._ability_availability(creature.owner, creature, ability),
        )
        creature.temporary_keywords.append("haste")
        self.assertEqual(
            ("payable", None),
            engine._ability_availability(creature.owner, creature, ability),
        )

    def test_compiled_fixed_output_is_controller_scoped_in_four_player_game(self):
        session = self.session(60523, players=4)
        ring = self._card(session.engine, "Sol Ring", owner="A")
        session.engine.move_card(
            ring.object_id,
            "battlefield",
            controller="C",
            tapped=False,
            log=False,
        )
        self._prepare_priority(session, ring)
        ability = next(
            row
            for row in session.engine._activated_abilities(ring)
            if row.fixed_mana_outputs
        )
        own_sources = {
            action.get("source")
            for action in session.engine._priority_action_hints("C")["actions"]
        }
        opposing_sources = {
            action.get("source")
            for action in session.engine._priority_action_hints("A")["actions"]
        }
        self.assertEqual("A", ring.owner)
        self.assertEqual("C", ring.controller)
        self.assertIn(ring.ref, own_sources)
        self.assertNotIn(ring.ref, opposing_sources)
        result = session.act(
            "pilot:C",
            {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players["C"].mana_pool["C"])
        self.assertEqual(0, session.state.players["A"].mana_pool["C"])

    def test_compiled_fixed_output_projection_exposes_no_hidden_cards(self):
        session = self.session(60524, players=4)
        ring = self._card(session.engine, "Sol Ring", owner="A")
        hidden = next(
            card
            for card in session.engine.state.cards.values()
            if card.owner == "D" and card.zone == "hand"
        )
        self._prepare_priority(session, ring)
        packet = json.dumps(session.packet("pilot:A", full=True), sort_keys=True)
        self.assertNotIn(hidden.object_id, packet)
        self.assertIn(ring.ref, packet)

    def test_compiled_fixed_output_activation_replays_exactly(self):
        session = self.session(60525)
        ring, ability = self._ring(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            f"pilot:{ring.controller}",
            {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-mana-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_compiled_alternative_output_is_advertised_accepted_and_replayed(self):
        session = self.session(60530)
        source = self._card(session.engine, "Luxury Suite", owner="A")
        self._prepare_priority(session, source)
        ability = next(
            row
            for row in session.engine._activated_abilities(source)
            if row.fixed_mana_outputs
        )
        modes = session.engine._mana_modes_for_ability(
            source.controller, source, ability
        )
        self.assertEqual(
            [{"B": 1}, {"R": 1}],
            [
                {
                    color: amount
                    for color, amount in mode.bundle.items()
                    if amount
                }
                for mode in modes
            ],
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            f"pilot:{source.controller}",
            {
                "a": "activate",
                "source": source.ref,
                "ability": ability.ability_id,
                "mana_output": {"R": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players[source.controller].mana_pool["R"])
        self.assertEqual(0, session.state.players[source.controller].mana_pool["B"])

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-mana-choice-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_restricted_fixed_mana_filters_payment_and_rolls_back(self):
        session = self.session(60531)
        ring, _ = self._ring(session)
        engine = session.engine
        parsed = parse_activated_abilities(
            card_name="Constrained Mana Fixture",
            oracle_text=(
                "{T}: Add {U}. Spend this mana only to cast an instant or "
                "sorcery spell."
            ),
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        assert spec is not None
        ability = spec.to_activated_ability()
        with mock.patch.object(
            engine,
            "_activated_abilities",
            return_value=(ability,),
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
            )
        self.assertTrue(result.ok, result.summary)
        player = engine.state.players[ring.controller]
        self.assertEqual(1, player.mana_pool["U"])
        self.assertEqual(
            0,
            engine._spendable_mana_pool(
                ring.controller,
                engine._spell_mana_spend_context("Creature — Wizard"),
            )["U"],
        )
        before = dict(player.mana_pool)
        with self.assertRaises(ManaProvenanceError):
            engine._apply_mana_spend(
                ring.controller,
                {"U": 1},
                engine._spell_mana_spend_context("Creature — Wizard"),
            )
        self.assertEqual(before, dict(player.mana_pool))
        engine._apply_mana_spend(
            ring.controller,
            {"U": 1},
            engine._spell_mana_spend_context("Instant"),
        )
        self.assertEqual(0, player.mana_pool["U"])

        ring.tapped = False
        artifact_only = compile_fixed_activated_mana_ability(
            parse_activated_abilities(
                card_name="Constrained Mana Fixture",
                oracle_text=(
                    "{T}: Add {U}. Spend this mana only to activate abilities "
                    "of artifacts."
                ),
            )[0]
        )
        assert artifact_only is not None
        artifact_mana_ability = artifact_only.to_activated_ability()
        with mock.patch.object(
            engine,
            "_activated_abilities",
            return_value=(artifact_mana_ability,),
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": artifact_mana_ability.ability_id,
                },
            )
        self.assertTrue(result.ok, result.summary)
        costed_ability = parse_activated_abilities(
            card_name="Constrained Ability Fixture",
            oracle_text="{U}: Draw a card.",
        )[0]
        creature = next(
            card
            for card in engine.state.cards.values()
            if card.owner == ring.controller
            and card.is_card_object
            and "creature"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller=ring.controller,
            tapped=False,
            log=False,
        )
        self.assertEqual(
            ("unpayable", "insufficient_mana"),
            engine._ability_availability(
                ring.controller,
                creature,
                costed_ability,
            ),
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability(
                ring.controller,
                ring,
                costed_ability,
            ),
        )
        with mock.patch.object(
            engine,
            "_activated_abilities",
            return_value=(costed_ability,),
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": costed_ability.ability_id,
                },
            )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, player.mana_pool["U"])

    def test_unsupported_restriction_suffix_cannot_seed_a_payment(self):
        session = self.session(60_532)
        ring, _ability = self._ring(session)
        engine = session.engine
        leaky_record = record(
            "{T}: Add {G}. Spend this mana only to cast a creature spell "
            "of a type chosen by an opponent this turn.",
            name="Unsupported Restriction Source",
        )
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (leaky_record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(0, registration["runtime_handlers_promoted"])
        ring.oracle_id = leaky_record.oracle_id
        ring.printed_name = leaky_record.name
        original_card_record = engine.card_record

        def card_record_for_test(_host, card):
            if card.oracle_id == leaky_record.oracle_id:
                return leaky_record
            return original_card_record(card)

        with mock.patch.object(
            type(engine),
            "card_record",
            autospec=True,
            side_effect=card_record_for_test,
        ):
            self._prepare_priority(session, ring)
            self.assertFalse(
                any(
                    ability.mana_ability
                    for ability in engine._activated_abilities(ring)
                )
            )
            self.assertNotIn(
                ring.ref,
                {
                    action.get("source")
                    for action in engine._priority_action_hints("A")["actions"]
                },
            )
            before = authoritative_state_hash(engine.state)
            rejected = session.act(
                "pilot:A",
                {"a": "activate", "source": ring.ref, "ability": "ab1"},
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
            self.assertFalse(ring.tapped)
            with self.assertRaises(ManaProvenanceError):
                engine._apply_mana_spend(
                    "A",
                    {"G": 1},
                    engine._spell_mana_spend_context("Creature — Elf"),
                )
            self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_once_per_turn_and_land_condition_use_current_source_state(self):
        session = self.session(60532, players=4)
        ring, _ = self._ring(session)
        engine = session.engine
        limited = compile_fixed_activated_mana_ability(
            parse_activated_abilities(
                card_name="Limited Mana Fixture",
                oracle_text=(
                    "{T}: Add one mana of any color. Activate only once each turn."
                ),
            )[0]
        )
        assert limited is not None
        ability = limited.to_activated_ability()
        self.assertIs(ActivationLimit.ONCE_PER_TURN, ability.activation_limit)
        with mock.patch.object(
            engine,
            "_activated_abilities",
            return_value=(ability,),
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": ability.ability_id,
                    "mana_output": {"U": 1},
                },
            )
            self.assertTrue(result.ok, result.summary)
            ring.tapped = False
            self.assertEqual(
                ("unavailable", "already_activated_this_turn"),
                engine._ability_availability(ring.controller, ring, ability),
            )
            ring.controller = "B"
            self.assertEqual(
                ("unavailable", "already_activated_this_turn"),
                engine._ability_availability("B", ring, ability),
            )
            engine.move_card(ring.object_id, "hand", log=False)
            engine.move_card(
                ring.object_id,
                "battlefield",
                controller="B",
                tapped=False,
                log=False,
            )
            self.assertEqual(
                ("payable", None),
                engine._ability_availability("B", ring, ability),
            )

        conditioned = compile_fixed_activated_mana_ability(
            parse_activated_abilities(
                card_name="Conditional Mana Fixture",
                oracle_text=(
                    "{T}: Add {U} or {B}. Activate only if you control an "
                    "Island or a Swamp."
                ),
            )[0]
        )
        assert conditioned is not None
        conditional_ability = conditioned.to_activated_ability()
        matching_lands = [
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.zone == "battlefield"
            and not {"island", "swamp"}.isdisjoint(
                engine._type_parts(
                    str(engine._effective_card_data(card).get("type_line") or "")
                )[1]
            )
        ]
        for card in matching_lands:
            engine.move_card(card.object_id, "hand", log=False)
        self.assertEqual(
            ("unavailable", "requires_public_activation_query"),
            engine._ability_availability("B", ring, conditional_ability),
        )
        island = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and "Island" in card.printed_name
        )
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("B", ring, conditional_ability),
        )


if __name__ == "__main__":
    unittest.main()
