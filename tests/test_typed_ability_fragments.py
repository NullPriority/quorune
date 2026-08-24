from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.attachments import attach_objects
from quorune.abilities import ActivatedAbility
from quorune.ability_fragments import (
    AbilityFragmentError,
    DamageKeywordTriggerKind,
    DamageKeywordTriggerSpec,
    GrantedActivatedAbilitySpec,
    GrantedTriggeredAbilitySpec,
    ProtectionQualityKind,
    ProtectionSourcePredicateSpec,
    ProtectionSpec,
    ToxicSpec,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
    canonical_ability_fragments,
    parse_protection_line,
)
from quorune.aura import (
    LinkedGraveyardCreatureEnchantSpec,
    SimpleEnchantSpec,
    enchant_spec_from_dict,
    enchant_spec_to_dict,
)
from quorune.carddb import CardRecord
from quorune.compiler.continuous_templates import (
    attached_fixed_characteristics_handler,
)
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effects import (
    CharacteristicState,
    evaluate_continuous_effects,
)
from quorune.damage import (
    commit_prepared_damage_batch,
    damage_proposal,
    prepare_damage_batch,
)
from quorune.damage_source import DamageError, DamageSourceSnapshot
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card
from quorune.protection import (
    ProtectionSource,
    ProtectionVerdict,
    protection_verdict,
)
from quorune.rules.activation.query import activated_abilities
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime.ability_fragments import (
    default_ability_fragment_registry,
    fragments_from_descriptors,
)
from quorune.targets import TargetGroup


def wrapped(fragment):
    return ability_fragment_to_dict(fragment)


def card_record(
    oracle_id: str,
    *,
    type_line: str,
    oracle_text: str,
    keywords: tuple[str, ...],
) -> CardRecord:
    return CardRecord(
        oracle_id=oracle_id,
        name="Typed Static Keywords",
        mana_cost="{1}{W}",
        mana_value=2,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("W",),
        color_identity=("W",),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class AbilityFragmentModelTests(unittest.TestCase):
    def test_linked_graveyard_enchant_descriptor_is_closed_and_round_trips(
        self,
    ):
        spec = LinkedGraveyardCreatureEnchantSpec("linked_creature")
        serialized = enchant_spec_to_dict(spec)
        self.assertEqual(spec, enchant_spec_from_dict(serialized))
        self.assertEqual(
            ["graveyard"],
            spec.target_schema()["zones"],
        )
        source = CardInstance(
            object_id="aura",
            ref="A01",
            oracle_id="fixture:linked-aura",
            owner="A",
            controller="A",
            printed_name="Linked Aura",
            zone="battlefield",
            annotations={"linked_creature": "creature"},
        )
        self.assertEqual(
            ["battlefield"],
            spec.target_schema(source)["zones"],
        )
        self.assertEqual("creature", spec.linked_target_object_id(source))
        with self.assertRaisesRegex(ValueError, "exactly kind and value"):
            enchant_spec_from_dict({**serialized, "unknown": True})

    def test_round_trip_is_strict_immutable_and_preserves_multiplicity(self):
        mana = {"GENERIC": 2}
        activated = GrantedActivatedAbilitySpec(
            ability_id="granted:damage",
            semantic_key="fixture:granted:damage",
            cost_text="{2}, {T}",
            effect_text="This creature deals 1 damage to any target",
            mana=mana,
            tap_source=True,
        )
        mana["GENERIC"] = 9
        self.assertEqual({"GENERIC": 2}, activated.mana_bundle)
        values = (
            ProtectionSpec(ProtectionQualityKind.COLOR, "R"),
            ProtectionSpec(
                ProtectionQualityKind.PREDICATE,
                schema_version=2,
                source_predicate=ProtectionSourcePredicateSpec(
                    subtypes_all=("goblin",),
                ),
            ),
            activated,
            GrantedTriggeredAbilitySpec(
                ability_id="granted:untap",
                semantic_key="fixture:granted:untap",
                event="creature.dies",
                label="Whenever a creature dies, untap this creature.",
            ),
            SimpleEnchantSpec("creature"),
            LinkedGraveyardCreatureEnchantSpec("linked_creature"),
            DamageKeywordTriggerSpec(
                kind=DamageKeywordTriggerKind.RENOWN,
                amount=2,
            ),
            ToxicSpec(value=2),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                serialized = wrapped(value)
                self.assertEqual(
                    value,
                    ability_fragment_from_dict(serialized),
                )
                with self.assertRaises(AbilityFragmentError):
                    ability_fragment_from_dict(
                        {**serialized, "unknown": True}
                    )
        with self.assertRaisesRegex(
            AbilityFragmentError,
            "subtype requirements conflict",
        ):
            ProtectionSourcePredicateSpec(
                subtypes_all=("goblin",),
                excluded_subtypes=("goblin",),
            )
        duplicated = canonical_ability_fragments(
            (values[2], values[0], values[2])
        )
        reordered = canonical_ability_fragments(
            (values[2], values[2], values[0])
        )
        self.assertEqual(duplicated, reordered)
        self.assertEqual(2, duplicated.count(values[2]))

    def test_static_handler_descriptors_are_closed_and_typed(self):
        descriptors = [
            {
                "handler_id": "ability.static.enchant.v1",
                "schema_version": 1,
                "event": "continuous",
                "fragment": wrapped(SimpleEnchantSpec("creature")),
            },
            {
                "handler_id": "ability.static.protection.v1",
                "schema_version": 1,
                "event": "continuous",
                "fragment": wrapped(
                    ProtectionSpec(ProtectionQualityKind.COLOR, "U")
                ),
            },
            {
                "handler_id": (
                    "ability.enchant.linked_graveyard_creature.v1"
                ),
                "schema_version": 1,
                "event": "resolve",
                "fragment": wrapped(
                    LinkedGraveyardCreatureEnchantSpec("linked_creature")
                ),
            },
        ]
        self.assertEqual(
            canonical_ability_fragments(
                (
                    SimpleEnchantSpec("creature"),
                    ProtectionSpec(ProtectionQualityKind.COLOR, "U"),
                    LinkedGraveyardCreatureEnchantSpec("linked_creature"),
                )
            ),
            canonical_ability_fragments(
                fragments_from_descriptors(descriptors)
            ),
        )
        inventory = default_ability_fragment_registry().inventory()
        handler_ids = {str(row["handler_id"]) for row in inventory}
        self.assertEqual(len(inventory), len(handler_ids))
        self.assertTrue(
            {
                "ability.static.conditional-keyword.v1",
                "ability.static.dynamic-power-toughness.v1",
            }.issubset(handler_ids)
        )
        with self.assertRaisesRegex(ValueError, "unknown"):
            fragments_from_descriptors(
                [{**descriptors[0], "unknown": True}]
            )

    def test_compiler_emits_exact_typed_enchant_and_protection_handlers(self):
        registry = load_default_capability_registry()
        record = card_record(
            "fixture:typed-static-keywords",
            type_line="Enchantment — Aura",
            oracle_text="Enchant creature\nProtection from red",
            keywords=("Enchant", "Protection"),
        )
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        handlers = [
            handler
            for node in compiled.faces[0].nodes
            for handler in node.handlers
        ]
        self.assertEqual(
            {
                "ability.static.enchant.v1",
                "ability.static.protection.v1",
            },
            {handler["handler_id"] for handler in handlers},
        )
        self.assertFalse(compiled.faces[0].residuals)
        self.assertTrue(
            all(node.exact for node in compiled.faces[0].nodes)
        )
        unsupported = card_record(
            "fixture:unsupported-protection",
            type_line="Creature — Test",
            oracle_text="Protection from modified creatures",
            keywords=("Protection",),
        )
        unsupported_ir = compile_oracle_card(
            unsupported,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertFalse(unsupported_ir.faces[0].nodes[0].exact)
        self.assertEqual(
            "unsupported_protection_quality",
            unsupported_ir.faces[0].residuals[-1].kind,
        )

    def test_compiler_emits_source_spanned_toxic_fragment(self):
        record = card_record(
            "fixture:typed-toxic",
            type_line="Creature — Test",
            oracle_text="Toxic 2",
            keywords=("Toxic",),
        )
        compiled = compile_oracle_card(
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        node = compiled.faces[0].nodes[0]
        self.assertTrue(node.exact)
        self.assertEqual(
            "Toxic 2",
            record.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            (ToxicSpec(value=2),),
            fragments_from_descriptors(list(node.handlers)),
        )
        self.assertEqual(
            "ability.static.toxic.v1",
            node.handlers[0]["handler_id"],
        )

        malformed = wrapped(ToxicSpec(value=2))
        malformed["value"]["value"] = True
        with self.assertRaisesRegex(
            AbilityFragmentError,
            "positive integers",
        ):
            ability_fragment_from_dict(malformed)

        repeated = card_record(
            "fixture:repeated-typed-toxic",
            type_line="Creature — Test",
            oracle_text="Toxic 1, Toxic 3",
            keywords=("Toxic",),
        )
        repeated_ir = compile_oracle_card(
            repeated,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        self.assertEqual(
            ["Toxic 1", "Toxic 3"],
            [
                repeated.oracle_text[node.span.start : node.span.end]
                for node in repeated_ir.faces[0].nodes
            ],
        )
        self.assertEqual(
            [ToxicSpec(value=1), ToxicSpec(value=3)],
            [
                fragments_from_descriptors(list(node.handlers))[0]
                for node in repeated_ir.faces[0].nodes
            ],
        )
        self.assertEqual(
            2,
            len({node.node_id for node in repeated_ir.faces[0].nodes}),
        )


class TypedProtectionTests(unittest.TestCase):
    def test_untyped_and_unsupported_protection_fail_closed(self):
        for text in (
            "Protection from permanents that were cast this turn",
            "Protection from permanents with corruption counters on them",
            "Protection from modified creatures",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_protection_line(text))
        self.assertEqual(
            ProtectionVerdict.UNRESOLVED,
            protection_verdict(
                {"keywords": ["Protection"]},
                ProtectionSource(colors=frozenset({"R"})),
            ),
        )
        self.assertEqual(
            ProtectionVerdict.UNRESOLVED,
            protection_verdict(
                {
                    "keywords": ["Protection"],
                    "ability_fragments": "not-an-array",
                },
                ProtectionSource(colors=frozenset({"R"})),
            ),
        )

    def test_compiler_closes_fixed_source_quality_predicates(self):
        registry = load_default_capability_registry()
        cases = (
            "Protection from Goblins",
            "Protection from non-Spirit creatures",
            "Protection from each color",
            "Protection from monocolored",
            "Protection from multicolored",
            "Protection from snow",
            "Protection from legendary creatures",
            "Protection from mana value 3 or greater",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                record = card_record(
                    f"fixture:protection-predicate:{index}",
                    type_line="Creature — Test",
                    oracle_text=text,
                    keywords=("Protection",),
                )
                compiled = compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                node = compiled.faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertFalse(compiled.faces[0].residuals)
                self.assertEqual(
                    ("protection.typed.debt",),
                    node.capability_dependencies,
                )
                self.assertTrue(node.handlers)
                self.assertTrue(
                    all(
                        handler["handler_id"]
                        == "ability.static.protection.v1"
                        for handler in node.handlers
                    )
                )

        mixed_cases = (
            (
                "Flying, protection from black and from red",
                ("Flying", "Protection"),
                {"combat.block.flying", "protection.typed.debt"},
            ),
            (
                "Protection from artifacts; reach",
                ("Protection", "Reach"),
                {"combat.block.reach", "protection.typed.debt"},
            ),
            (
                "Protection from black; flanking",
                ("Protection", "Flanking"),
                {"combat.trigger.flanking", "protection.typed.debt"},
            ),
        )
        for index, (text, keywords, capabilities) in enumerate(mixed_cases):
            with self.subTest(text=text):
                mixed = card_record(
                    f"fixture:mixed-protection-predicates:{index}",
                    type_line="Creature — Angel",
                    oracle_text=text,
                    keywords=keywords,
                )
                mixed_node = compile_oracle_card(
                    mixed,
                    capability_registry=registry,
                    capability_profile="commander_review",
                ).faces[0].nodes[0]
                self.assertTrue(mixed_node.exact)
                self.assertEqual(
                    capabilities,
                    set(mixed_node.capability_dependencies),
                )
                self.assertTrue(mixed_node.handlers)

    def test_fixed_source_quality_predicates_match_current_characteristics(self):
        def verdict(text: str, source: ProtectionSource) -> ProtectionVerdict:
            specs = parse_protection_line(text)
            self.assertIsNotNone(specs)
            return protection_verdict(
                {
                    "keywords": ["Protection"],
                    "ability_fragments": [
                        wrapped(spec) for spec in specs or ()
                    ],
                },
                source,
            )

        cases = (
            (
                "Protection from Goblins",
                ProtectionSource(subtypes=frozenset({"goblin"})),
                ProtectionSource(subtypes=frozenset({"elf"})),
            ),
            (
                "Protection from non-Spirit creatures",
                ProtectionSource(card_types=frozenset({"creature"})),
                ProtectionSource(
                    card_types=frozenset({"creature"}),
                    subtypes=frozenset({"spirit"}),
                ),
            ),
            (
                "Protection from each color",
                ProtectionSource(colors=frozenset({"R"})),
                ProtectionSource(),
            ),
            (
                "Protection from monocolored",
                ProtectionSource(colors=frozenset({"R"})),
                ProtectionSource(colors=frozenset({"R", "G"})),
            ),
            (
                "Protection from multicolored",
                ProtectionSource(colors=frozenset({"R", "G"})),
                ProtectionSource(colors=frozenset({"R"})),
            ),
            (
                "Protection from snow",
                ProtectionSource(supertypes=frozenset({"snow"})),
                ProtectionSource(supertypes=frozenset({"legendary"})),
            ),
            (
                "Protection from legendary creatures",
                ProtectionSource(
                    card_types=frozenset({"creature"}),
                    supertypes=frozenset({"legendary"}),
                ),
                ProtectionSource(
                    card_types=frozenset({"artifact"}),
                    supertypes=frozenset({"legendary"}),
                ),
            ),
            (
                "Protection from mana value 3 or greater",
                ProtectionSource(mana_value=3),
                ProtectionSource(mana_value=2),
            ),
        )
        for text, blocked, allowed in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    ProtectionVerdict.BLOCKED,
                    verdict(text, blocked),
                )
                self.assertEqual(
                    ProtectionVerdict.ALLOWED,
                    verdict(text, allowed),
                )
        self.assertEqual(
            ProtectionVerdict.UNRESOLVED,
            verdict(
                "Protection from mana value 3 or greater",
                ProtectionSource(),
            ),
        )
    def test_matching_and_nonmatching_typed_protection_verdicts(self):
        protected = {
            "keywords": ["Protection"],
            "ability_fragments": [
                wrapped(
                    ProtectionSpec(ProtectionQualityKind.COLOR, "R")
                )
            ],
        }
        self.assertEqual(
            ProtectionVerdict.BLOCKED,
            protection_verdict(
                protected,
                ProtectionSource(colors=frozenset({"R"})),
            ),
        )
        self.assertEqual(
            ProtectionVerdict.ALLOWED,
            protection_verdict(
                protected,
                ProtectionSource(colors=frozenset({"U"})),
            ),
        )
        self.assertEqual(
            ProtectionVerdict.UNRESOLVED,
            protection_verdict(protected, None),
        )
        for spec, source in (
            (
                ProtectionSpec(
                    ProtectionQualityKind.CARD_TYPE,
                    "artifact",
                ),
                {"type_line": "Artifact Creature — Goblin"},
            ),
            (
                ProtectionSpec(ProtectionQualityKind.SUBTYPE, "aura"),
                {"type_line": "Enchantment — Aura"},
            ),
        ):
            with self.subTest(spec=spec):
                self.assertEqual(
                    ProtectionVerdict.BLOCKED,
                    protection_verdict(
                        {
                            "ability_fragments": [wrapped(spec)],
                            "keywords": ["Protection"],
                        },
                        ProtectionSource.from_characteristics(source),
                    ),
                )

    def test_damage_source_snapshot_preserves_mana_value_compatibly(self):
        snapshot = DamageSourceSnapshot(
            ref="source-ref",
            object_id="source-object",
            logical_object_id="source-logical",
            controller="A",
            owner="A",
            zone="battlefield",
            supertypes=("snow",),
            mana_value=3,
        )
        serialized = snapshot.to_dict()
        self.assertEqual(snapshot, DamageSourceSnapshot.from_dict(serialized))
        legacy = dict(serialized)
        legacy.pop("mana_value")
        self.assertIsNone(DamageSourceSnapshot.from_dict(legacy).mana_value)
        for malformed in (-1, float("inf"), float("nan")):
            with self.subTest(mana_value=malformed), self.assertRaisesRegex(
                DamageError,
                "mana value",
            ):
                DamageSourceSnapshot.from_dict(
                    {**serialized, "mana_value": malformed}
                )

    def test_layer_six_granted_protection_uses_typed_fragment(self):
        fragment = wrapped(
            ProtectionSpec(
                ProtectionQualityKind.PREDICATE,
                schema_version=2,
                source_predicate=ProtectionSourcePredicateSpec(
                    subtypes_all=("vampire",),
                ),
            )
        )
        state = CharacteristicState(
            name="Protected Bear",
            controller="A",
            text="Base text",
            executable_text="Base text",
            card_types={"creature"},
            abilities=[],
        )
        effect = ContinuousEffect(
            effect_id="fixture:grant-protection",
            source_id="fixture:source",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=1,
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            operations=(
                ContinuousOperation("add_ability", "Protection"),
                ContinuousOperation("add_ability_fragment", fragment),
            ),
        )
        evaluated = evaluate_continuous_effects(
            state,
            (effect,),
            context={"zone": "battlefield", "controller": "A"},
        ).characteristics
        self.assertIn("Protection", evaluated["abilities"])
        self.assertEqual([fragment], evaluated["ability_fragments"])
        self.assertEqual(
            ProtectionVerdict.BLOCKED,
            protection_verdict(
                {
                    "keywords": evaluated["abilities"],
                    "ability_fragments": evaluated["ability_fragments"],
                },
                ProtectionSource(subtypes=frozenset({"vampire"})),
            ),
        )
        handler = attached_fixed_characteristics_handler(
            "Enchanted creature has protection from Vampires."
        )
        self.assertIsNotNone(handler)
        self.assertEqual(
            [fragment], handler[1]["modifier"]["add_ability_fragments"]
        )

    def test_text_only_grants_are_display_only_and_typed_grants_execute(self):
        typed = wrapped(
            GrantedActivatedAbilitySpec(
                ability_id="granted:damage",
                semantic_key="fixture:granted:damage",
                cost_text="{2}, {T}",
                effect_text="This creature deals 1 damage to any target",
                mana={"GENERIC": 2},
                tap_source=True,
            )
        )

        class Host:
            @staticmethod
            def _type_parts(type_line: str):
                return ({"creature"}, {"test"}, set())

            @staticmethod
            def _effective_card_data(card):
                del card
                return {
                    "name": "Bear",
                    "type_line": "Creature — Test",
                    "oracle_text": (
                        "{2}, {T}: This creature deals 1 damage to any target"
                    ),
                    "executable_oracle_text": "",
                    "keywords": [],
                    "ability_fragments": [typed],
                }

        card = CardInstance(
            object_id="bear-object",
            ref="C1",
            oracle_id="fixture:bear",
            printed_name="Bear",
            owner="A",
            controller="A",
            zone="battlefield",
        )
        discovered = activated_abilities(Host(), card)
        self.assertEqual(1, len(discovered))
        self.assertIsInstance(discovered[0], ActivatedAbility)
        self.assertEqual(
            "fixture:granted:damage",
            discovered[0].builtin_semantic_key,
        )


class TypedProtectionEngineTests(unittest.TestCase):
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
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.stack.clear()
        return session

    @staticmethod
    def creature(
        engine,
        seat: str,
        name: str,
        *,
        colors: tuple[str, ...] = (),
        protection: str | ProtectionSpec | None = None,
        type_line: str = "Token Creature — Test",
        mana_value: float = 0,
    ):
        characteristics = {
            "type_line": type_line,
            "mana_value": mana_value,
            "power": "2",
            "toughness": "2",
            "colors": list(colors),
        }
        if protection is not None:
            protection_spec = (
                protection
                if isinstance(protection, ProtectionSpec)
                else ProtectionSpec(
                    ProtectionQualityKind.COLOR,
                    protection,
                )
            )
            characteristics.update(
                {
                    "oracle_text": "Protection",
                    "keywords": ["Protection"],
                    "ability_fragments": [
                        wrapped(protection_spec)
                    ],
                }
            )
        ref = engine.create_token(
            seat,
            name=name,
            characteristics=characteristics,
        )[0]
        return engine._resolve_object(
            seat,
            ref,
            zones={"battlefield"},
        )

    def test_typed_protection_blocks_target_attachment_and_block_operations(
        self,
    ):
        engine = self.session(7021601).engine
        protected = self.creature(
            engine,
            "B",
            "Protected Bear",
            protection=ProtectionSpec(
                ProtectionQualityKind.PREDICATE,
                schema_version=2,
                source_predicate=ProtectionSourcePredicateSpec(
                    color_count="colored",
                    supertypes_all=("snow",),
                    minimum_mana_value=3,
                ),
            ),
        )
        red_source = self.creature(
            engine,
            "A",
            "Red Source",
            colors=("R",),
            type_line="Snow Token Creature — Test",
            mana_value=3,
        )
        group = TargetGroup.from_mapping(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_all": ["creature"],
                "count": 1,
            }
        )
        row = next(
            row
            for row in engine._target_candidate_rows("A", group)
            if row["ref"] == protected.ref
        )
        self.assertFalse(
            engine._target_row_matches(
                "A",
                group,
                row,
                source_ref=red_source.ref,
            )
        )
        self.assertFalse(engine._can_block(protected, red_source)[0])

        equipment_ref = engine.create_token(
            "A",
            name="Red Equipment",
            characteristics={
                "type_line": "Snow Token Artifact — Equipment",
                "mana_value": 3,
                "colors": ["R"],
            },
        )[0]
        equipment = engine._resolve_object(
            "A", equipment_ref, zones={"battlefield"}
        )
        attach_objects(
            engine.state.cards,
            equipment,
            protected,
            source_timestamp=engine._next_zone_timestamp(),
        )
        self.assertFalse(
            engine._attachment_is_legal(
                equipment,
                subtypes={"equipment"},
            )
        )

        before = set(engine.state.cards)
        aura_refs = engine.create_token(
            "A",
            name="Red Aura",
            characteristics={
                "type_line": "Snow Token Enchantment — Aura",
                "mana_value": 3,
                "colors": ["R"],
                "oracle_text": "Enchant creature",
                "ability_fragments": [
                    wrapped(SimpleEnchantSpec("creature"))
                ],
            },
            aura_target_ref=protected.ref,
        )
        self.assertEqual([], aura_refs)
        self.assertEqual(before, set(engine.state.cards))

        proposal = damage_proposal(
            engine,
            proposal_id="damage:typed-protection-predicate",
            actor="A",
            source_ref=red_source.ref,
            target=protected.ref,
            amount=1,
            combat=False,
            reason="typed protection predicate witness",
        )
        result = commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(engine, (proposal,)),
        )
        self.assertEqual(0, result.events[0].dealt_amount)
        self.assertEqual(1, result.events[0].prevented_amount)
        self.assertEqual(0, protected.marked_damage)

    def test_four_player_protection_uses_the_actual_source(self):
        engine = self.session(7021602, players=4).engine
        protected = self.creature(
            engine,
            "C",
            "Protected Witness",
            protection="R",
        )
        red = self.creature(
            engine, "A", "Red Witness", colors=("R",)
        )
        blue = self.creature(
            engine, "D", "Blue Witness", colors=("U",)
        )
        group = TargetGroup.from_mapping(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_all": ["creature"],
                "count": 1,
            }
        )
        row = next(
            row
            for row in engine._target_candidate_rows("A", group)
            if row["ref"] == protected.ref
        )
        self.assertFalse(
            engine._target_row_matches(
                "A", group, row, source_ref=red.ref
            )
        )
        self.assertTrue(
            engine._target_row_matches(
                "D", group, row, source_ref=blue.ref
            )
        )


if __name__ == "__main__":
    unittest.main()
