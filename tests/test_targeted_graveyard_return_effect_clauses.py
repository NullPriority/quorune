from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT, without_direct_resolution_compiler
from inherited_composition_fixture import InheritedNativeFixture
import test_fixed_conspire as conspire_tests
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.compiler.return_to_hand_templates import (
    GraveyardCardTargetKind,
    TargetedOwnGraveyardReturnToHandEffectTemplate,
    targeted_own_graveyard_return_to_hand_effect_template,
    targeted_public_graveyard_return_to_hand_effect_template,
)
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.graveyard_card_targets import (
    GraveyardCardTargetError,
    OwnGraveyardCardTargetSpec,
    PUBLIC_GRAVEYARD_RETURN_CAPABILITY,
    PUBLIC_GRAVEYARD_RETURN_KINDS,
    public_graveyard_return_effect,
    public_graveyard_return_runtime_dependencies,
)
from quorune.semantics import SemanticRegistry
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


def card_record(
    oracle_text: str,
    *,
    type_line: str = "Instant",
    name: str = "Graveyard Return Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000231",
        name=name,
        mana_cost="{1}{G}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="1" if "Creature" in type_line else None,
        toughness="1" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class OwnGraveyardCardTargetTests(unittest.TestCase):
    def test_target_spec_is_immutable_canonical_and_copy_isolated(self):
        for kind in GraveyardCardTargetKind:
            with self.subTest(kind=kind):
                spec = OwnGraveyardCardTargetSpec(kind)
                schema = spec.to_target_schema()
                self.assertEqual(spec, OwnGraveyardCardTargetSpec.from_target_schema(schema))
                self.assertEqual(["graveyard"], schema["zones"])
                self.assertEqual(["card"], schema["categories"])
                self.assertEqual("you", schema["owner_relation"])
                schema["zones"].append("exile")
                self.assertEqual(["graveyard"], spec.to_target_schema()["zones"])
        with self.assertRaises(FrozenInstanceError):
            spec.kind = GraveyardCardTargetKind.CARD  # type: ignore[misc]
        with self.assertRaisesRegex(GraveyardCardTargetError, "typed value"):
            OwnGraveyardCardTargetSpec("card")  # type: ignore[arg-type]
        with self.assertRaisesRegex(
            GraveyardCardTargetError,
            "positive type",
        ):
            OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.PERMANENT_CARD,
                types_any=("creature",),
            )

    def test_target_spec_rejects_noncanonical_or_open_predicates(self):
        base = OwnGraveyardCardTargetSpec(
            GraveyardCardTargetKind.CREATURE_CARD
        ).to_target_schema()
        malformed = (
            {**base, "zones": ["exile"]},
            {**base, "categories": ["permanent"]},
            {**base, "owner_relation": "any"},
            {**base, "count": True},
            {**base, "types_any": ["creature", "goblin"]},
            {**base, "types_any": ["Creature"]},
            {**base, "subtypes_any": ["not-a-subtype"]},
            {**base, "colors_any": ["P"]},
            {**base, "color_count_min": 0},
        )
        for schema in malformed:
            with self.subTest(schema=schema):
                with self.assertRaises(GraveyardCardTargetError):
                    OwnGraveyardCardTargetSpec.from_target_schema(schema)

    def test_characteristic_target_spec_is_canonical_and_round_trips(self):
        cases = (
            OwnGraveyardCardTargetSpec(None, subtypes_any=("Goblin",)),
            OwnGraveyardCardTargetSpec(
                None,
                types_all=("Artifact", "Creature"),
            ),
            OwnGraveyardCardTargetSpec(
                None,
                types_any=("Creature",),
                colors_any=("U", "R"),
            ),
            OwnGraveyardCardTargetSpec(
                None,
                types_any=("Land",),
                supertypes_any=("Basic",),
            ),
            OwnGraveyardCardTargetSpec(None, color_count_min=2),
        )
        for spec in cases:
            with self.subTest(spec=spec):
                schema = spec.to_target_schema()
                self.assertEqual(
                    spec,
                    OwnGraveyardCardTargetSpec.from_target_schema(schema),
                )
                self.assertEqual("you", schema["owner_relation"])
        with self.assertRaisesRegex(
            GraveyardCardTargetError,
            "cannot mix",
        ):
            OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.CREATURE_CARD,
                subtypes_any=("Goblin",),
            )

    def test_type_predicates_use_the_shared_target_matcher(self):
        permanent = TargetGroup.from_mapping(
            OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.PERMANENT_CARD
            ).to_target_schema()
        )
        nonland = TargetGroup.from_mapping(
            OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.NONLAND_PERMANENT_CARD
            ).to_target_schema()
        )
        self.assertTrue(
            permanent.matches_type_characteristics(
                types=("artifact", "land"), subtypes=(), supertypes=()
            )
        )
        self.assertFalse(
            nonland.matches_type_characteristics(
                types=("artifact", "land"), subtypes=(), supertypes=()
            )
        )
        self.assertTrue(
            nonland.matches_type_characteristics(
                types=("battle",), subtypes=(), supertypes=()
            )
        )


class TargetedOwnGraveyardReturnCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            card_record(oracle_text, type_line=type_line),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_parser_accepts_only_the_closed_own_graveyard_family(self):
        for kind in GraveyardCardTargetKind:
            with self.subTest(kind=kind):
                template = targeted_own_graveyard_return_to_hand_effect_template(
                    f"Return target {kind.value} from your graveyard to your hand."
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(kind, template.target)
        for text, expected in (
            (
                "Return target Goblin card from your graveyard to your hand.",
                {"subtypes_any": ["goblin"]},
            ),
            (
                "Return target artifact creature card from your graveyard to your hand.",
                {"types_all": ["artifact", "creature"]},
            ),
            (
                "Return target green card from your graveyard to your hand.",
                {"colors_any": ["G"]},
            ),
            (
                "Return target basic land card from your graveyard to your hand.",
                {"types_any": ["land"], "supertypes_any": ["basic"]},
            ),
        ):
            with self.subTest(text=text):
                template = targeted_own_graveyard_return_to_hand_effect_template(
                    text
                )
                self.assertIsNotNone(template)
                assert template is not None
                for field, value in expected.items():
                    self.assertEqual(value, template.target_schema[field])
        for text in (
            "Return up to one target card from your graveyard to your hand.",
            "You may return target card from your graveyard to your hand.",
            "Return target card from a graveyard to its owner's hand.",
            "Return target card from an opponent's graveyard to your hand.",
            "Return target historic card from your graveyard to your hand.",
            "Return target colorless creature card from your graveyard to your hand.",
            "Return target creature card with flying from your graveyard to your hand.",
            "Return target creature card with mana value 2 from your graveyard to your hand.",
            "Return two target cards from your graveyard to your hand.",
            "Return target card from your graveyard to your hand, then draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    targeted_own_graveyard_return_to_hand_effect_template(text)
                )

    def test_qualified_permanent_card_keeps_its_graveyard_type_domain(self):
        permanent_types = {
            "artifact",
            "battle",
            "creature",
            "enchantment",
            "land",
            "planeswalker",
        }
        green_permanent_ir = self.compile(
            "Return target green permanent card from your graveyard to your hand."
        )
        self.assertEqual("exact", green_permanent_ir.status)
        green_permanent_schema = green_permanent_ir.faces[0].nodes[0].target_schema
        self.assertEqual(["G"], green_permanent_schema["colors_any"])
        self.assertEqual(
            permanent_types,
            set(green_permanent_schema["types_any"]),
        )
        green_permanent_group = TargetGroup.from_mapping(
            green_permanent_schema
        )
        self.assertTrue(
            green_permanent_group.matches_type_characteristics(
                types=("creature",), subtypes=(), supertypes=()
            )
        )
        self.assertFalse(
            green_permanent_group.matches_type_characteristics(
                types=("instant",), subtypes=(), supertypes=()
            )
        )

        green_card_ir = self.compile(
            "Return target green card from your graveyard to your hand."
        )
        self.assertEqual("exact", green_card_ir.status)
        green_card_group = TargetGroup.from_mapping(
            green_card_ir.faces[0].nodes[0].target_schema
        )
        for card_type in ("instant", "sorcery"):
            with self.subTest(green_card_type=card_type):
                self.assertTrue(
                    green_card_group.matches_type_characteristics(
                        types=(card_type,), subtypes=(), supertypes=()
                    )
                )

        for description, expected in (
            (
                "multicolored permanent",
                {"types_any": permanent_types, "color_count_min": 2},
            ),
            (
                "noncreature permanent",
                {
                    "types_any": permanent_types,
                    "types_none": {"creature"},
                },
            ),
        ):
            with self.subTest(description=description):
                compiled = self.compile(
                    f"Return target {description} card from your graveyard to your hand."
                )
                self.assertEqual("exact", compiled.status)
                schema = compiled.faces[0].nodes[0].target_schema
                for field, value in expected.items():
                    actual = schema[field]
                    self.assertEqual(
                        value,
                        set(actual) if isinstance(value, set) else actual,
                    )
                if description == "noncreature permanent":
                    group = TargetGroup.from_mapping(schema)
                    self.assertTrue(
                        group.matches_type_characteristics(
                            types=("enchantment",),
                            subtypes=(),
                            supertypes=(),
                        )
                    )
                    for card_type in ("creature", "instant", "sorcery"):
                        self.assertFalse(
                            group.matches_type_characteristics(
                                types=(card_type,),
                                subtypes=(),
                                supertypes=(),
                            )
                        )

        for kind in (
            GraveyardCardTargetKind.PERMANENT_CARD,
            GraveyardCardTargetKind.NONLAND_PERMANENT_CARD,
        ):
            with self.subTest(existing=kind.value):
                compiled = self.compile(
                    f"Return target {kind.value} from your graveyard to your hand."
                )
                self.assertEqual("exact", compiled.status)
                group = TargetGroup.from_mapping(
                    compiled.faces[0].nodes[0].target_schema
                )
                self.assertTrue(
                    group.matches_type_characteristics(
                        types=("artifact",), subtypes=(), supertypes=()
                    )
                )
                self.assertEqual(
                    kind is GraveyardCardTargetKind.PERMANENT_CARD,
                    group.matches_type_characteristics(
                        types=("land",), subtypes=(), supertypes=()
                    ),
                )
                self.assertFalse(
                    group.matches_type_characteristics(
                        types=("instant",), subtypes=(), supertypes=()
                    )
                )

    def test_spell_trigger_and_activated_contexts_share_graveyard_return_lowering(self):
        contexts = (
            (
                "Return target card from your graveyard to your hand.",
                "Sorcery",
                "spell_ability",
                "return-target-card-from-own-graveyard-v1",
            ),
            (
                "When this creature enters, return target creature card from your graveyard to your hand.",
                "Creature — Test",
                "triggered_ability",
                "return-target-creature-card-from-own-graveyard-v1",
            ),
            (
                "{1}{W}{U}, {T}: Return target artifact or enchantment card from your graveyard to your hand.",
                "Creature — Test",
                "activated_ability",
                "return-target-artifact-or-enchantment-card-from-own-graveyard-v1",
            ),
            (
                "Return target Goblin card from your graveyard to your hand.",
                "Sorcery",
                "spell_ability",
                "return-target-subtype-goblin-card-from-own-graveyard-v2",
            ),
            (
                "When this creature enters, return target artifact creature card from your graveyard to your hand.",
                "Creature — Test",
                "triggered_ability",
                "return-target-all-artifact-and-creature-card-from-own-graveyard-v2",
            ),
            (
                "{G}, {T}: Return target basic land card from your graveyard to your hand.",
                "Creature — Test",
                "activated_ability",
                "return-target-any-land-super-basic-card-from-own-graveyard-v2",
            ),
        )
        for text, type_line, kind, template_id in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(
                    {
                        "card.return.own_graveyard_to_owner_hand",
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_unsupported_graveyard_return_variants_remain_material_residuals(self):
        for text in (
            "Return target artifact or creature card from a graveyard to its owner's hand.",
            "Return target card from an opponent's graveyard to your hand.",
            "Return target historic card from your graveyard to your hand.",
            "Return target colorless creature card from your graveyard to your hand.",
            "Return target creature card with flying from your graveyard to your hand.",
            "Return target creature card with mana value 2 from your graveyard to your hand.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_graveyard_return_shape_mutants_fail_closed(self):
        template = TargetedOwnGraveyardReturnToHandEffectTemplate(
            GraveyardCardTargetKind.CREATURE_CARD
        )
        expected = {
            "card.return.own_graveyard_to_owner_hand",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        malformed_effects = (
            ({"op": "return_graveyard_card_to_owner_hand", "card": "$target.1"},),
            ({"op": "return_graveyard_card_to_owner_hand", "card": "$source"},),
            ({"op": "bounce", "card": "$target.0"},),
            (
                {
                    "op": "return_graveyard_card_to_owner_hand",
                    "card": "$target.0",
                    "destination": "hand",
                },
            ),
        )
        for effects in malformed_effects:
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        malformed_schemas = (
            {**template.target_schema, "zones": ["graveyard", "exile"]},
            {**template.target_schema, "owner_relation": "any"},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "types_any": ["goblin"]},
            {**template.target_schema, "controller_relation": "you"},
        )
        for schema in malformed_schemas:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )

    def test_compiler_mutant_is_killed(self):
        for text in (
            "Return target card from your graveyard to your hand.",
            "Return target Goblin card from your graveyard to your hand.",
        ):
            with self.subTest(text=text):
                self.assertEqual("exact", self.compile(text).status)
                from common import without_direct_resolution_compiler
                with without_direct_resolution_compiler("targeted_own_graveyard_return_to_hand_effect_template"):
                    mutated = self.compile(text)
                self.assertNotEqual("exact", mutated.status)
                self.assertTrue(mutated.material_residuals)

    def test_generated_regrowth_program_is_capability_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "graveyard-return.sqlite3"
            build_fixture_database(
                [
                    ROOT
                    / "tests"
                    / "fixtures"
                    / "targeted-graveyard-return-cards.json"
                ],
                database,
            )
            db = CardDatabase(database)
            try:
                registry = SemanticRegistry(include_builtin_packs=False)
                result = register_generated_programs(
                    db,
                    registry,
                    (db.lookup("Regrowth"),),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    promote_exact_effect_programs=True,
                )
            finally:
                db.close()
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(1, result["exact_effect_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertTrue(
            {
                "card.return.own_graveyard_to_owner_hand",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )


class PublicGraveyardReturnCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, *, registry=None):
        return compile_oracle_card(card_record(text), capability_registry=registry or self.registry,
                                   capability_profile="commander_review")

    def test_public_graveyard_return_lowering_and_runtime_scope(self):
        for kind in PUBLIC_GRAVEYARD_RETURN_KINDS:
            with self.subTest(kind=kind):
                text = f"Return target {kind.value} from a graveyard to its owner's hand."
                compiled = self.compile(text)
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                node, = compiled.faces[0].nodes
                self.assertEqual("any", node.target_schema["owner_relation"])
                self.assertEqual(["graveyard"], node.target_schema["zones"])
                self.assertEqual(kind.types_any, tuple(node.target_schema.get("types_any", ())))
                self.assertEqual((public_graveyard_return_effect(),), tuple(node.effects))
                self.assertIn(PUBLIC_GRAVEYARD_RETURN_CAPABILITY, node.capability_dependencies)
                program = {"effects": node.effects, "target_schema": node.target_schema}
                self.assertIn(PUBLIC_GRAVEYARD_RETURN_CAPABILITY,
                              public_graveyard_return_runtime_dependencies(SimpleNamespace(**program)))
                malformed = ({"effects": ({"op": node.effects[0]["op"], "card": "$target.0"},)},
                             {"target_schema": {**node.target_schema, "owner_relation": "you"}},
                             {"target_schema": {**node.target_schema, "zones": ["hand"]}},
                             {"target_schema": {**node.target_schema, "count": 2}})
                for changes in malformed:
                    with self.assertRaises(GraveyardCardTargetError):
                        public_graveyard_return_runtime_dependencies(SimpleNamespace(**{**program, **changes}))

    def test_public_graveyard_return_dependencies_fail_closed(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = "Return target creature card from a graveyard to its owner's hand."
        registry_path = ROOT / "quorune/rules/capability-registry.json"
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for capability in (PUBLIC_GRAVEYARD_RETURN_CAPABILITY, "card.return.own_graveyard_to_owner_hand",
                           "variant.commander.zone_return"):
            with self.subTest(capability=capability):
                raw = json.loads(registry_path.read_text(encoding="utf-8"))
                entry = next(row for row in raw["capabilities"] if row["id"] == capability)
                entry.update(status="blocked", blockers=["Withdrawn for the return scope witness"])
                registry = CapabilityRegistry(raw)
                index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", self.compile(source, registry=registry).status)
                self.assertEqual("exact", self.compile("Draw a card.", registry=registry).status)

    def test_public_graveyard_return_compiler_omission_is_killed(self):
        source = "Return target creature card from a graveyard to its owner's hand."
        self.assertIsNotNone(targeted_public_graveyard_return_to_hand_effect_template(source))
        self.assertEqual("exact", self.compile(source).status)
        with without_direct_resolution_compiler("targeted_public_graveyard_return_to_hand_effect_template"):
            self.assertNotEqual("exact", self.compile(source).status)
            self.assertEqual("exact", self.compile("Return target card from your graveyard to your hand.").status)


class PublicGraveyardReturnRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    conspire_option = conspire_tests.FixedConspireRuntimeTests.conspire_option
    cast_conspired = conspire_tests.FixedConspireRuntimeTests.cast_conspired
    choose_copy = conspire_tests.FixedConspireRuntimeTests.choose_copy
    reject = conspire_tests.FixedConspireRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-graveyard-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "targeted-graveyard-return-cards.json", "fixed-conspire-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public graveyard return witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_original_conspire_returns_cards_to_different_owners(self):
        cases = (
            ("Disturbing Plot", "B", "Walking Corpse", "Walking Corpse", "Walking Corpse"),
            ("Mine Excavation", "W", "Savannah Lions", "Sphere of Resistance", "Curiosity"),
        )
        for index, (name, color, donor, original_name, copy_name) in enumerate(cases):
            with self.subTest(card=name):
                session = self.session(854100 + index)
                engine = session.engine
                spell = self.add(engine, name, zone="hand")
                donors = [self.add(engine, donor, ref=f"graveyard-return-donor-{i}") for i in range(2)]
                original = self.add(engine, original_name, seat="B", zone="graveyard", ref="return-original")
                copied = self.add(engine, copy_name, seat="C", zone="graveyard", ref="return-copy")
                wrong = self.add(engine, "Forest", seat="B", zone="graveyard")
                private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="private-return-control")
                self.assert_closed(session, spell)
                self.prepare(session, mana={"C": 1, color: 1})
                self.checkpoint(session)

                offered = self.action(session, "A", spell)
                action_id, option = self.conspire_option(session, spell)
                paid_offer = next(cost for cost in offered["cost_options"] if cost["id"] == option)
                legal = set(paid_offer["target_schema"]["legal_refs"])
                self.assertTrue({original.ref, copied.ref} <= legal)
                self.assertNotIn(wrong.ref, legal)
                self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
                self.reject(session, action_id, cost_option=option, targets=[wrong.ref], pay="auto")
                self.assertTrue(all(not donor.tapped for donor in donors))
                self.cast_conspired(session, spell, [original.ref])
                self.assertTrue(all(session.state.cards[donor.object_id].tapped for donor in donors))
                self.assertEqual("semantic.storm", self.resolve(session).kind)
                self.reject(session, "choose", "C", copy_targets=[[copied.ref]])
                self.reject(session, "choose", copy_targets=[[wrong.ref]])

                session = self.replay(session, load=True)
                self.choose_copy(session, [copied.ref])
                self.assertIsNone(self.resolve(session))
                for target, owner in ((original, "B"), (copied, "C")):
                    self.assertIn(target.object_id, session.state.players[owner].zones["hand"])
                    self.assertNotIn(target.object_id, session.state.players["A"].zones["hand"])
                moves = [event for event in session.state.events
                         if event.code == "card.return_from_graveyard_to_owner_hand"]
                self.assertEqual({("A", "B", original.ref), ("A", "C", copied.ref)},
                                 {(event.actor, event.details["owner"], event.details["object"]) for event in moves})
                self.assertEqual(1, len(session.engine._current_turn_history("spell_cast")))
                self.replay(session)

    def test_pulse_of_murasa_gains_life_only_when_original_target_is_legal(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                session = self.session(854110 + int(stale))
                engine = session.engine
                pulse = self.add(engine, "Pulse of Murasa", zone="hand")
                target = self.add(engine, "Forest", seat="B", zone="graveyard")
                wrong = self.add(engine, "Generic Bound Growth", seat="B", zone="graveyard")
                exile = self.add(engine, "Cremate", seat="B", zone="hand")
                self.assert_closed(session, pulse)
                self.assert_closed(session, exile)
                self.prepare(session, mana={"C": 2, "G": 1})
                session.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(session)
                life = session.state.players["A"].life
                offered = self.action(session, "A", pulse)
                self.assertIn(target.ref, offered["target_schema"]["legal_refs"])
                self.assertNotIn(wrong.ref, offered["target_schema"]["legal_refs"])
                self.reject(session, offered["id"], targets=[wrong.ref], pay="auto")

                self.cast(session, "A", pulse, [target.ref])
                if stale:
                    self.cast(session, "B", exile, [target.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual("exile" if stale else "hand", session.state.cards[target.object_id].zone)
                self.assertEqual(life if stale else life + 6, session.state.players["A"].life)
                self.replay(session, load=True)

    def test_naya_charm_announces_only_selected_modes_graveyard_target(self):
        session = self.session(854120)
        engine = session.engine
        charm = self.add(engine, "Naya Charm", zone="hand")
        target = self.add(engine, "Generic Bound Growth", seat="C", zone="graveyard")
        self.assert_closed(session, charm)
        self.prepare(session, mana={"R": 1, "G": 1, "W": 1})
        self.checkpoint(session)
        offered = self.action(session, "A", charm)
        self.assertIn("mode_2", offered["target_schema"]["legal_modes"])
        self.assertNotIn("mode_1", offered["target_schema"]["legal_modes"])
        self.reject(session, offered["id"], modes=["mode_2"], targets=["C"], pay="auto")
        result = session.act("pilot:A", {
            "action_id": offered["id"], "modes": ["mode_2"], "targets": [target.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id, session.state.players["C"].zones["hand"])
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
