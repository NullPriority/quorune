from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune import aerial_blocking as aerial_blocking_module
from quorune import (
    combat_damage_assignment as combat_assignment_module,
)
from quorune import combat as combat_module
from quorune import combat_evasion as combat_evasion_module
from quorune import block_transitions as block_transitions_module
from quorune import attack_transition_model as attack_transitions_module
from quorune import combat_damage_trample as trample_module
from quorune import deathtouch as deathtouch_module
from quorune import defender as defender_module
from quorune import menace as menace_module
from quorune import landwalk as landwalk_module
from quorune import damage_results as damage_results_module
from quorune import destruction as destruction_module
from quorune import replacement_effects
from quorune.replacement import application as replacement_application
from quorune import tap_state
from quorune import haste as haste_module
from quorune import trigger_batches as trigger_batches_module
from quorune import zone_trigger_events as zone_trigger_events_module
from quorune.rules.activation import commit as activation_commit
from quorune.rules.activation import resolution as activation_resolution
from quorune.rules.casting import proposal as casting_proposal
from quorune.aura import SimpleEnchantSpec
from quorune.abilities import ActivatedAbility
from quorune.errors import GameRuleError
from quorune.model import CardInstance, CombatState
from quorune import protection as protection_module
from quorune import target_protection as target_protection_module
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
    ProtectionQualityKind,
    ProtectionSourcePredicateSpec,
    ProtectionSpec,
    ToxicSpec,
    ability_fragment_to_dict,
)
from quorune.protection import (
    ProtectionSource,
    ProtectionVerdict,
)
from quorune.target_protection import (
    TargetProtectionSnapshot,
    TargetProtectionVerdict,
)
from quorune.effect_runtime import life_effects
from quorune.effect_runtime import objects_stack_and_tokens
from quorune.continuous_effects import (
    CharacteristicState,
    evaluate_continuous_effects,
)
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune import damage as damage_module
from quorune import damage_prevention as damage_prevention_module
from quorune.drawing import transaction as draw_transaction_module
from quorune.drawing import (
    DiscardDrawnCardUnlessType,
    DrawCommitResult,
    DrawEventRequest,
    RevealDrawnCard,
    prepare_draw_event,
)
from quorune.damage import DamageEvent
from quorune.damage_prevention import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    PreventionDamageKind,
    PreventionMode,
    PreventionRecipientKind,
)
from quorune.damage_modifier_state import (
    ChosenDamageSource,
    DamageAftermathRecipient,
    DealDamagePreventionAftermath,
    GainLifePreventionAftermath,
)
from quorune.damage_source import DamageSourceSnapshot
from quorune.prevention_triggers import (
    DrawCardsPreventionTrigger,
    PreventionTriggeredAbility,
    PreventionTriggerOccurrence,
)
from quorune.engine import CommanderEngine
from quorune import oracle_ir as oracle_ir_module
from quorune.rules import capabilities as capabilities_module
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune import object_predicate as object_predicate_module
from quorune import object_query as object_query_module
from quorune.object_query import ObjectQueryError, ObjectQuerySpec
from quorune.semantic_runtime.counter_replacements import (
    CounterPlacementEventSpec,
    CounterQuantityReplacementHandler,
    CounterQuantityReplacementV2Handler,
    CounterReplacementSourceContext,
    resolve_counter_placement_replacements,
)
from quorune.semantic_runtime import draw_restrictions as draw_restriction_module
from quorune.semantic_runtime import draw_replacements as draw_replacement_module
from quorune.semantic_runtime.continuous_components import (
    AddBasicLandTypeHandler,
    ContinuousEffectSourceContext,
    FixedQueryPowerToughnessAnthemHandler,
)
from quorune.compiler.continuous_templates import (
    attached_fixed_characteristics_handler,
    fixed_power_toughness_anthem_handler,
)
from quorune.semantic_runtime.attached_continuous import (
    AttachedFixedCharacteristicsHandler,
)
from quorune.semantic_runtime.damage_replacements import (
    DamageQuantityReplacementHandler,
    DamageReplacementSourceContext,
    FixedDamagePreventionHandler,
    StaticDamageRedirectionHandler,
)
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime.damage_results import (
    DamageResultLifeFloorHandler,
    DamageResultReplacementSourceContext,
)
from quorune.semantic_runtime.life_replacements import (
    LifeGainMultiplierHandler,
    LifeReplacementSourceContext,
)
from quorune.targets import PUBLIC_TARGET_ZONES, TargetGroup
from quorune.zone_trigger_events import ZoneChangeOccurrence


def _event(*, assigned: int, dealt: int, prevented: int) -> DamageEvent:
    return DamageEvent(
        source="C1",
        source_object_id="source-object",
        source_logical_object_id="source-incarnation",
        source_oracle_id=None,
        source_commander_designation_id=None,
        source_controller="A",
        source_owner="A",
        source_types=("instant",),
        source_subtypes=(),
        source_colors=("R",),
        source_keywords=(),
        source_is_commander=False,
        target="B",
        target_kind="player",
        target_object_id=None,
        target_controller="B",
        target_types=(),
        target_subtypes=(),
        assigned_amount=assigned,
        dealt_amount=dealt,
        prevented_amount=prevented,
        combat=False,
    )


class CapabilityImplementationMutationTests(unittest.TestCase):
    """Small executable mutations proving critical assertions kill defects.

    These are implementation mutations, not registry-dependency mutations.
    Each test first proves the behavioral assertion against the real code,
    then installs a deliberately broken implementation and proves that the
    same assertion fails.
    """

    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_target_validation_mutant_is_killed(self):
        def assert_hidden_target_rejected() -> None:
            with self.assertRaisesRegex(ValueError, "hidden/nonpublic"):
                TargetGroup.from_mapping({"zones": ["hand"]})

        assert_hidden_target_rejected()
        mutated_zones = set(PUBLIC_TARGET_ZONES) | {"hand"}
        with patch(
            "quorune.targets.PUBLIC_TARGET_ZONES", mutated_zones
        ):
            with self.assertRaises(AssertionError):
                assert_hidden_target_rejected()

    def test_flash_cast_timing_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=7020899,
        )
        keep_all(session)
        engine = session.engine
        endurance = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Endurance"
        )
        engine.move_card(endurance.object_id, "hand", log=False)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = "B"
        engine.state.players["B"].mana_pool.update({"C": 1, "G": 2})

        def assert_flash_offer() -> None:
            result = casting_proposal.build_cast_offer(
                engine, "B", endurance
            )
            self.assertEqual("payable", result.status)

        assert_flash_offer()
        with patch.object(
            casting_proposal,
            "compiled_cast_timing_permissions",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_flash_offer()

    def test_aura_cast_targeting_mutant_is_killed(self):
        def assert_mandatory_aura_target() -> None:
            schema = casting_proposal._aura_spell_target_schema(
                type_line="Enchantment — Aura",
                enchant_spec=SimpleEnchantSpec("creature"),
            )
            self.assertIsNotNone(schema)
            self.assertEqual(["creature"], schema["types_all"])

        assert_mandatory_aura_target()
        with patch.object(
            casting_proposal,
            "_aura_spell_target_schema",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_mandatory_aura_target()

    def test_typed_protection_verdict_mutant_is_killed(self):
        protected = {
            "keywords": ["Protection"],
            "ability_fragments": [
                ability_fragment_to_dict(
                    ProtectionSpec(
                        ProtectionQualityKind.PREDICATE,
                        schema_version=2,
                        source_predicate=ProtectionSourcePredicateSpec(
                            subtypes_all=("goblin",),
                            supertypes_all=("snow",),
                            minimum_mana_value=3,
                        ),
                    )
                )
            ],
        }
        source = ProtectionSource(
            subtypes=frozenset({"goblin"}),
            supertypes=frozenset({"snow"}),
            mana_value=3,
        )

        def assert_matching_source_is_blocked() -> None:
            self.assertEqual(
                ProtectionVerdict.BLOCKED,
                protection_module.protection_verdict(protected, source),
            )

        assert_matching_source_is_blocked()
        with patch.object(
            protection_module,
            "protection_verdict",
            return_value=ProtectionVerdict.ALLOWED,
        ):
            with self.assertRaises(AssertionError):
                assert_matching_source_is_blocked()

    def test_plain_hexproof_target_protection_mutant_is_killed(self):
        snapshot = TargetProtectionSnapshot(
            acting_controller="A",
            protected_controller="B",
            target_keywords=frozenset({"hexproof"}),
        )

        def assert_opponent_target_is_blocked() -> None:
            self.assertEqual(
                TargetProtectionVerdict.HEXPROOF,
                target_protection_module.target_protection_verdict(snapshot),
            )

        assert_opponent_target_is_blocked()
        with patch.object(
            target_protection_module,
            "target_protection_verdict",
            return_value=TargetProtectionVerdict.ALLOWED,
        ):
            with self.assertRaises(AssertionError):
                assert_opponent_target_is_blocked()

    def test_plain_shroud_target_protection_mutant_is_killed(self):
        snapshot = TargetProtectionSnapshot(
            acting_controller="A",
            protected_controller="A",
            target_keywords=frozenset({"shroud"}),
        )

        def assert_controller_target_is_blocked() -> None:
            self.assertEqual(
                TargetProtectionVerdict.SHROUD,
                target_protection_module.target_protection_verdict(snapshot),
            )

        assert_controller_target_is_blocked()
        with patch.object(
            target_protection_module,
            "target_protection_verdict",
            return_value=TargetProtectionVerdict.ALLOWED,
        ):
            with self.assertRaises(AssertionError):
                assert_controller_target_is_blocked()

    def test_object_query_string_coercion_mutant_is_killed(self):
        def assert_malformed_term_rejected() -> None:
            with self.assertRaises(ObjectQueryError):
                ObjectQuerySpec(types_all=(1,))

        assert_malformed_term_rejected()

        def coercing_terms(values, *, field_name, upper=False):
            del field_name
            normalize = str.upper if upper else str.casefold
            return tuple(sorted(normalize(str(value)) for value in values))

        with patch.object(
            object_predicate_module,
            "_normalized_terms",
            coercing_terms,
        ):
            with self.assertRaises(AssertionError):
                assert_malformed_term_rejected()

    def test_chosen_source_predicate_validator_mutant_is_killed(self):
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            known_to_actor=True,
            token=True,
        )

        def assert_unsupported_predicate_rejected() -> None:
            with self.assertRaises(ObjectQueryError):
                object_query_module.validate_chosen_damage_source_predicate(
                    predicate
                )

        assert_unsupported_predicate_rejected()
        with patch.object(
            object_query_module,
            "validate_chosen_damage_source_predicate",
            lambda value: value,
        ):
            with self.assertRaises(AssertionError):
                assert_unsupported_predicate_rejected()

    def test_prevention_immediate_sequence_mutants_are_killed(self):
        record = replace(
            self.db.lookup("Force of Vigor"),
            oracle_id="fixture:prevention-immediate-mutation",
            name="Fixture Prevention Immediate Mutation",
            oracle_text=(
                "Prevent the next 3 damage that would be dealt to any target "
                "this turn by a source of your choice. You gain 3 life."
            ),
        )

        def assert_sequence() -> None:
            node = oracle_ir_module.compile_oracle_card(
                record
            ).faces[0].nodes[0]
            self.assertEqual(
                "damage-prevention-chosen-source-fixed-life-v2",
                node.template_id,
            )
            self.assertEqual(2, len(node.effects))
            source_choice, life_gain = node.effects
            self.assertEqual("choose_damage_source", source_choice["op"])
            self.assertNotIn("aftermath", source_choice["shield"])
            self.assertEqual("life", life_gain["op"])
            self.assertEqual(3, life_gain["delta"])

        assert_sequence()
        original = oracle_ir_module.fixed_prevention_effect_template

        def mutated(mutator):
            def compile_template(text, **kwargs):
                result = original(text, **kwargs)
                if result is None:
                    return None
                template_id, effects, targets, rules = result
                return template_id, mutator(effects), targets, rules

            return compile_template

        def remove_life(effects):
            return effects[:1]

        def move_life_to_aftermath(effects):
            choice = dict(effects[0])
            shield = dict(choice["shield"])
            shield["aftermath"] = [
                {
                    "kind": "gain_life",
                    "player": "$controller",
                    "per_prevented": 0,
                    "fixed_amount": 3,
                }
            ]
            choice["shield"] = shield
            return (choice,)

        mutants = (
            remove_life,
            move_life_to_aftermath,
            lambda effects: (*effects, effects[1]),
            lambda effects: tuple(reversed(effects)),
        )
        for mutant in mutants:
            with self.subTest(mutant=mutant.__name__):
                with patch.object(
                    oracle_ir_module,
                    "fixed_prevention_effect_template",
                    mutated(mutant),
                ):
                    with self.assertRaises(AssertionError):
                        assert_sequence()

    def test_basic_land_type_intrinsic_mana_mutant_is_killed(self):
        descriptor = {
            "handler_id": "continuous.basic_land_type.add_all_lands.v1",
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": {"target_types_all": ["land"]},
            "modifier": {"basic_land_type": "swamp"},
        }
        context = ContinuousEffectSourceContext(
            source_object_id="urborg",
            source_ref="U1",
            source_controller="A",
            source_timestamp=1,
            component_id="mutation",
        )

        def assert_swamp_is_added() -> None:
            effects = AddBasicLandTypeHandler().lower(
                descriptor, context
            )
            result = evaluate_continuous_effects(
                CharacteristicState(
                    name="Darksteel Citadel",
                    controller="A",
                    card_types={"Artifact", "Land"},
                    subtypes=set(),
                    abilities=["Indestructible", "{T}: Add {C}."],
                ),
                effects,
            )
            self.assertIn("swamp", result.characteristics["subtypes"])

        assert_swamp_is_added()
        with patch.object(
            AddBasicLandTypeHandler,
            "lower",
            lambda _handler, _descriptor, _context: (),
        ):
            with self.assertRaises(AssertionError):
                assert_swamp_is_added()

    def test_fixed_anthem_applicability_mutant_is_killed(self):
        descriptor = fixed_power_toughness_anthem_handler(
            "Creatures you control get +1/+1."
        )[1]
        context = ContinuousEffectSourceContext(
            source_object_id="anthem",
            source_ref="A01",
            source_controller="A",
            source_timestamp=1,
            component_id="mutation",
        )

        def assert_anthem_applies() -> None:
            effects = FixedQueryPowerToughnessAnthemHandler().lower(
                descriptor, context
            )
            result = evaluate_continuous_effects(
                CharacteristicState(
                    name="Creature",
                    controller="A",
                    card_types={"Creature"},
                    power=1,
                    toughness=1,
                ),
                effects,
                context={"ref": "A02", "owner": "A"},
            )
            self.assertEqual(2, result.characteristics["power"])

        assert_anthem_applies()
        with patch.object(
            FixedQueryPowerToughnessAnthemHandler,
            "lower",
            lambda _handler, _descriptor, _context: (),
        ):
            with self.assertRaises(AssertionError):
                assert_anthem_applies()

    def test_attached_characteristic_relation_mutant_is_killed(self):
        compiled = attached_fixed_characteristics_handler(
            "Equipped creature gets +1/-1 and has haste."
        )
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        context = ContinuousEffectSourceContext(
            source_object_id="equipment",
            source_ref="E1",
            source_controller="A",
            source_timestamp=2,
            component_id="mutation",
            attached_object=ContinuousObjectIdentity(
                object_id="target",
                logical_object_id="target@0",
            ),
        )

        def assert_attached_characteristics() -> None:
            effects = AttachedFixedCharacteristicsHandler().lower(
                descriptor, context
            )
            result = evaluate_continuous_effects(
                CharacteristicState(
                    name="Target",
                    controller="B",
                    card_types={"Creature"},
                    power=1,
                    toughness=2,
                ),
                effects,
                context={
                    "object_id": "target",
                    "logical_object_id": "target@0",
                    "zone": "battlefield",
                    "owner": "B",
                },
            )
            self.assertEqual(2, result.characteristics["power"])
            self.assertEqual(1, result.characteristics["toughness"])
            self.assertIn("Haste", result.characteristics["abilities"])

        assert_attached_characteristics()
        with patch.object(
            AttachedFixedCharacteristicsHandler,
            "lower",
            lambda _handler, _descriptor, _context: (),
        ):
            with self.assertRaises(AssertionError):
                assert_attached_characteristics()

    def test_generic_equip_resolution_mutant_is_killed(self):
        def assert_equip_lowers() -> None:
            resolution = activation_resolution.builtin_activation_resolution(
                "builtin:equip", "A"
            )
            self.assertIsNotNone(resolution)
            self.assertEqual(
                [
                    {
                        "op": "attach",
                        "equipment": "$source",
                        "creature": "$target.0",
                        "reason": "Equip",
                    }
                ],
                resolution.effect_dicts(),
            )

        assert_equip_lowers()
        with patch.object(activation_resolution, "_EQUIP_KEY", "mutant:equip"):
            with self.assertRaises(AssertionError):
                assert_equip_lowers()

    def test_resolution_continuous_effect_commit_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=6112999,
        )
        keep_all(session)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Mutation creature",
            characteristics={
                "type_line": "Token Creature",
                "power": "1",
                "toughness": "1",
            },
            reason="mutation witness",
        )[0]
        card = engine._resolve_object("A", ref, zones={"battlefield"})

        def resolved_power() -> int:
            engine.state.continuous_effects.clear()
            engine.apply_effect(
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": card.ref,
                    "power": 1,
                    "toughness": 1,
                },
                actor="A",
            )
            return engine._numeric_stat(card.object_id, "power")

        self.assertEqual(2, resolved_power())
        with patch.object(
            objects_stack_and_tokens,
            "create_resolution_continuous_effect",
            lambda *_args, **_kwargs: None,
        ):
            with self.assertRaisesRegex(
                GameRuleError,
                "Resolution continuous-effect commit returned no effect",
            ):
                resolved_power()
            self.assertEqual([], engine.state.continuous_effects)
            self.assertEqual(1, engine._numeric_stat(card.object_id, "power"))

    def test_damage_amount_guard_mutant_is_killed(self):
        def assert_negative_assignment_rejected() -> None:
            with self.assertRaisesRegex(ValueError, "positive assignment"):
                _event(assigned=-1, dealt=-1, prevented=0)

        assert_negative_assignment_rejected()
        with patch.object(DamageEvent, "__post_init__", lambda _event: None):
            with self.assertRaises(AssertionError):
                assert_negative_assignment_rejected()

    def test_life_effect_commit_mutant_is_killed(self):
        def resolved_life(commit) -> int:
            session = make_session(
                self.db,
                self.mishra,
                self.zimone,
                players=2,
                seed=119001,
            )
            keep_all(session)
            with patch.object(
                life_effects,
                "commit_life_change_batch",
                commit,
            ):
                session.engine.apply_effect(
                    {"op": "life", "player": "A", "delta": 4},
                    actor="A",
                )
            return session.state.players["A"].life

        real_commit = life_effects.commit_life_change_batch
        self.assertEqual(44, resolved_life(real_commit))
        self.assertNotEqual(44, resolved_life(lambda *_args, **_kwargs: None))

    def test_draw_commit_mutant_is_killed(self):
        def assert_draw_commit() -> None:
            session = make_session(
                self.db,
                self.mishra,
                self.zimone,
                players=2,
                seed=121099,
            )
            keep_all(session)
            engine = session.engine
            player = engine.state.players["A"]
            top = player.zones["library"][-1]
            prepared = prepare_draw_event(
                DrawEventRequest(
                    event_id="draw:mutation",
                    player="A",
                    library_size=len(player.zones["library"]),
                    reason="draw mutation",
                ),
                apnap_order=engine.apnap_order(),
            )
            result = draw_transaction_module.commit_prepared_draw(
                engine, prepared
            )
            self.assertEqual((top,), result)
            self.assertEqual("hand", engine.state.cards[top].zone)
            self.assertEqual("draw mutation", player.draw_history[-1]["reason"])

        assert_draw_commit()
        with patch.object(
            draw_transaction_module,
            "_commit_ordinary_draw",
            lambda *_args, **_kwargs: DrawCommitResult(
                kind="prevented", player="A"
            ),
        ):
            with self.assertRaises(AssertionError):
                assert_draw_commit()

    def test_draw_result_and_post_action_mutants_are_killed(self):
        descriptor = {
            "handler_id": "replacement.draw.result.multiply.v1",
            "schema_version": 1,
            "event": "draw",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"factor": 2},
        }
        context = draw_replacement_module.DrawReplacementSourceContext(
            source_ref="A11",
            source_object_id="object:A11",
            source_zone_change_counter=1,
            source_owner="A",
            source_controller="A",
            component_id="mutation",
        )
        expected_operation_type = draw_replacement_module.CreateResultDraws

        def assert_result_operation() -> None:
            effect = draw_replacement_module.DrawResultMultiplierHandler().replacement_effect(
                descriptor, context
            )
            self.assertIsInstance(
                effect.operations[0], expected_operation_type
            )

        assert_result_operation()
        with patch.object(
            draw_replacement_module,
            "CreateResultDraws",
            lambda **_kwargs: replacement_effects.MultiplyAmount(
                field="count", factor=2
            ),
        ):
            with self.assertRaises(AssertionError):
                assert_result_operation()

        def assert_post_draw_action() -> None:
            session = make_session(
                self.db,
                self.mishra,
                self.zimone,
                players=2,
                seed=121603,
            )
            keep_all(session)
            engine = session.engine
            player = engine.state.players["A"]
            object_id = next(
                value
                for value in player.zones["library"]
                if not engine.card_record(engine.state.cards[value]).is_land
            )
            player.zones["library"].remove(object_id)
            player.zones["library"].append(object_id)
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._begin_draw_sequence(
                "A",
                1,
                reason="post-action mutation",
                post_draw_actions=(
                    RevealDrawnCard(),
                    DiscardDrawnCardUnlessType(card_type="land"),
                ),
            )
            self.assertIn(object_id, player.zones["graveyard"])

        assert_post_draw_action()
        with patch.object(
            draw_transaction_module,
            "_apply_drawn_card_actions",
            lambda *_args, **_kwargs: None,
        ):
            with self.assertRaises(AssertionError):
                assert_post_draw_action()

    def test_draw_restriction_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=121100,
        )
        keep_all(session)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Island"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.semantics.put(
            SemanticProgram(
                key="test:draw-restriction-mutation",
                label="Draw restriction mutation",
                oracle_id=source.oracle_id,
                active_zone="battlefield",
                event="draw.permission",
                handlers=[
                    {
                        "handler_id": "restriction.draw.maximum-per-turn.v1",
                        "schema_version": 1,
                        "event": "draw.permission",
                        "condition": {
                            "affected_player_relation": "source_controller",
                        },
                        "restriction": {"maximum_per_turn": 0},
                    }
                ],
                trust_level="provisional",
            )
        )

        def assert_prohibition_collected() -> None:
            permission = draw_restriction_module.current_draw_permission(
                engine, "A"
            )
            self.assertEqual(0, permission.maximum_per_turn)
            self.assertFalse(permission.allows_individual_draw())

        with patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            assert_prohibition_collected()
            with patch.object(
                draw_restriction_module,
                "collect_draw_restrictions",
                lambda *_args, **_kwargs: (),
            ):
                with self.assertRaises(AssertionError):
                    assert_prohibition_collected()

    def test_fixed_activated_draw_capability_gate_mutant_is_killed(self):
        record = self.db.lookup("Mind Stone")
        registry = load_default_capability_registry()

        def assert_draw_ability_is_capability_closed() -> None:
            ir = oracle_ir_module.compile_oracle_card(
                record,
                capability_registry=registry,
                capability_profile="commander_review",
            )
            draw = next(
                node
                for node in ir.faces[0].nodes
                if node.kind == "activated_ability"
                and any(
                    effect.get("op") == "draw"
                    for effect in node.effects
                )
            )
            self.assertTrue(draw.exact)
            self.assertEqual(
                (
                    "activation.source_zone_change.fixed",
                    "zone.draw.library_to_hand",
                ),
                draw.capability_dependencies,
            )

        assert_draw_ability_is_capability_closed()
        with patch.object(
            capabilities_module,
            "fixed_draw_node_capabilities",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_draw_ability_is_capability_closed()

    def test_zone_trigger_detection_mutant_is_killed(self):
        value = ZoneChangeOccurrence(
            object_id="mutation-zone-object",
            card_ref="A01",
            owner="A",
            origin="battlefield",
            destination="graveyard",
            previous_controller="A",
            current_controller="A",
            previous_logical_object_id="mutation-zone-object:0",
            current_logical_object_id="mutation-zone-object:1",
            zone_change_counter=1,
            token=False,
            card_object=True,
            previous_characteristics={"type_line": "Creature — Test"},
            current_characteristics={"type_line": "Creature — Test"},
        )

        def assert_dies_event() -> None:
            self.assertIn(
                "creature.dies",
                {
                    event.kind
                    for event in zone_trigger_events_module.normalized_zone_trigger_events(
                        value
                    )
                },
            )

        assert_dies_event()
        with patch.object(
            zone_trigger_events_module,
            "normalized_zone_trigger_events",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_dies_event()

    def test_commander_identity_mutant_is_killed(self):
        def assert_physical_designations_remain_separate() -> None:
            first = damage_module.commander_damage_key(
                source_is_commander=True,
                designation_id="commander:A:1",
                oracle_id="shared-oracle-id",
                identity_version=2,
            )
            second = damage_module.commander_damage_key(
                source_is_commander=True,
                designation_id="commander:C:1",
                oracle_id="shared-oracle-id",
                identity_version=2,
            )
            self.assertNotEqual(first, second)

        assert_physical_designations_remain_separate()

        def oracle_identity_mutant(**values):
            return (
                values["oracle_id"]
                if values["source_is_commander"]
                else None
            )

        with patch.object(
            damage_module,
            "commander_damage_key",
            oracle_identity_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_physical_designations_remain_separate()

    def test_damage_result_dispatch_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=1203,
        )
        keep_all(session)
        engine = session.engine
        card = next(
            value
            for value in engine.state.cards.values()
            if value.owner == "A" and not value.is_commander
            and not engine._type_parts(
                str(engine._effective_card_data(value).get("type_line") or "")
            )[0].intersection({"instant", "sorcery"})
        )
        card = engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            reason="implementation mutation fixture",
            semantic_events=False,
        )
        card.annotations["copy_overrides"] = {
            "name": "Mutation Fixture",
            "type_line": "Creature Planeswalker Battle",
            "oracle_text": "",
        }
        card.counters["loyalty"] = 4
        card.counters["defense"] = 5
        source_ref = engine.create_token(
            "A",
            name="Damage Result Mutation Source",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        source = engine._resolve_object(
            "A", source_ref, zones={"battlefield"}
        )

        def assert_all_permanent_results() -> None:
            card.marked_damage = 0
            card.counters["loyalty"] = 4
            card.counters["defense"] = 5
            damage = damage_module.damage_proposal(
                engine,
                proposal_id="damage:result-dispatch-mutation",
                actor="A",
                source_ref=source.ref,
                target=card.ref,
                amount=2,
                combat=False,
                reason="implementation mutation fixture",
            ).event()
            prepared = damage_results_module.prepare_damage_results(
                engine,
                (damage,),
                effects=(),
            )
            plan = damage_results_module.plan_damage_result_commit(
                engine, prepared
            )
            damage_results_module.commit_damage_result_plan(engine, plan)
            self.assertEqual(2, card.marked_damage)
            self.assertEqual(2, card.counters["loyalty"])
            self.assertEqual(3, card.counters["defense"])

        assert_all_permanent_results()

        with patch.object(
            damage_results_module,
            "materialize_damage_results",
            lambda _host, _events: (),
        ):
            with self.assertRaises(AssertionError):
                assert_all_permanent_results()

    def test_keyword_damage_result_mutants_are_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=12031,
        )
        keep_all(session)
        engine = session.engine

        def token(name: str, *, keywords=(), toxic_values=()):
            ref = engine.create_token(
                "A" if "Source" in name else "B",
                name=name,
                characteristics={
                    "type_line": "Token Creature — Test",
                    "power": "3",
                    "toughness": "5",
                    "keywords": list(keywords),
                    "ability_fragments": [
                        ability_fragment_to_dict(ToxicSpec(value=value))
                        for value in toxic_values
                    ],
                },
            )[0]
            return engine._resolve_object(
                "A" if "Source" in name else "B",
                ref,
                zones={"battlefield"},
            )

        infect = token(
            "Infect Lifelink Toxic Source",
            keywords=("Infect", "Lifelink", "Toxic"),
            toxic_values=(2,),
        )
        wither = token("Wither Source", keywords=("Wither",))
        target = token("Mutation Target")
        damage_events = (
            damage_module.damage_proposal(
                engine,
                proposal_id="damage:keyword-mutation:player",
                actor="A",
                source_ref=infect.ref,
                target="B",
                amount=3,
                combat=True,
                reason="implementation mutation fixture",
            ).event(),
            damage_module.damage_proposal(
                engine,
                proposal_id="damage:keyword-mutation:creature",
                actor="A",
                source_ref=wither.ref,
                target=target.ref,
                amount=2,
                combat=True,
                reason="implementation mutation fixture",
            ).event(),
        )

        def assert_keyword_results() -> None:
            roots = damage_results_module.materialize_damage_results(
                engine, damage_events
            )
            leaves = [child for root in roots for child in root.children]
            amounts = {
                str(child.payload.get("cause")): int(
                    child.payload.get("amount", 0)
                )
                for child in leaves
            }
            self.assertEqual(3, amounts["infect"])
            self.assertEqual(2, amounts["toxic"])
            self.assertEqual(3, amounts["lifelink"])
            self.assertEqual(2, amounts["infect_or_wither"])

        assert_keyword_results()
        original = damage_results_module.materialize_damage_results

        def strip_cause(cause: str):
            def mutant(host, events):
                def visit(event):
                    return replace(
                        event,
                        children=tuple(
                            visit(child)
                            for child in event.children
                            if child.payload.get("cause") != cause
                        ),
                    )

                return tuple(visit(event) for event in original(host, events))

            return mutant

        for cause in ("infect", "toxic", "lifelink", "infect_or_wither"):
            with self.subTest(cause=cause), patch.object(
                damage_results_module,
                "materialize_damage_results",
                strip_cause(cause),
            ):
                with self.assertRaises((AssertionError, KeyError)):
                    assert_keyword_results()

    def test_damage_result_replacement_component_mutants_are_killed(self):
        gain_descriptor = {
            "handler_id": "replacement.life.gain.multiplier.v1",
            "schema_version": 1,
            "event": "life.change",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"multiplier": 2},
        }
        floor_descriptor = {
            "handler_id": "replacement.damage.result.life_floor.v1",
            "schema_version": 1,
            "event": "damage.results",
            "condition": {
                "affected_player_relation": "source_controller",
                "requires_controlled_creature": True,
            },
            "modification": {"minimum_life": 1},
        }
        context = LifeReplacementSourceContext(
            source_ref="result-replacement-mutation-source",
            source_controller="A",
        )
        gain = replacement_effects.ReplaceableEvent(
            event_id="life:gain:mutation",
            kind="life.change",
            affected_player="A",
            payload={"direction": "gain", "amount": 3},
        )
        loss = replacement_effects.ReplaceableEvent(
            event_id="life:loss:mutation",
            kind="life.change",
            affected_player="A",
            payload={
                "direction": "loss",
                "amount": 5,
                "requested_amount": 5,
            },
        )
        root = replacement_effects.ReplaceableEvent(
            event_id="damage:results:mutation",
            kind="damage.results",
            affected_player="A",
            payload={
                "subject_kind": "player",
                "life_before": 5,
                "life_loss_amount": 5,
                "life_after_without_replacement": 0,
                "controls_creature": True,
            },
            children=(loss,),
        )

        def assert_components_transform_results() -> None:
            gain_effect = LifeGainMultiplierHandler().replacement_effect(
                gain_descriptor, context
            )
            floor_effect = DamageResultLifeFloorHandler().replacement_effect(
                floor_descriptor, context
            )
            doubled = replacement_effects.resolve_replacements(
                gain, (gain_effect,), selections=(gain_effect.effect_id,)
            )
            floored = replacement_effects.resolve_replacements(
                root, (floor_effect,), selections=(floor_effect.effect_id,)
            )
            self.assertEqual(6, doubled.payload["amount"])
            self.assertEqual(4, floored.children[0].payload["amount"])

        assert_components_transform_results()
        original_gain = LifeGainMultiplierHandler.replacement_effect

        def identity_gain(handler, descriptor, source_context):
            effect = original_gain(handler, descriptor, source_context)
            return replace(
                effect,
                operations=(
                    {"op": "multiply", "field": "amount", "factor": 1},
                ),
            )

        with patch.object(
            LifeGainMultiplierHandler,
            "replacement_effect",
            identity_gain,
        ):
            with self.assertRaises(AssertionError):
                assert_components_transform_results()

        original_floor = DamageResultLifeFloorHandler.replacement_effect

        def skip_floor(handler, descriptor, source_context):
            effect = original_floor(handler, descriptor, source_context)
            return replace(
                effect,
                operations=(
                    {"op": "cap_result_life_loss", "minimum": -100},
                ),
            )

        with patch.object(
            DamageResultLifeFloorHandler,
            "replacement_effect",
            skip_floor,
        ):
            with self.assertRaises(
                (AssertionError, replacement_effects.ReplacementEffectError)
            ):
                assert_components_transform_results()

    def test_semantic_tap_state_mutants_are_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=7012699,
        )
        keep_all(session)
        engine = session.engine
        first_ref = engine.create_token(
            "A",
            name="Tap Mutation Witness",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        second_ref = engine.create_token(
            "B",
            name="Untap Mutation Witness",
            tapped=True,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        first = engine._resolve_object("A", first_ref, zones={"battlefield"})
        second = engine._resolve_object("B", second_ref, zones={"battlefield"})

        def assert_single_tap() -> None:
            first.tapped = False
            engine.apply_effect({"op": "tap", "card": first.ref}, actor="A")
            self.assertTrue(first.tapped)

        assert_single_tap()
        with patch.object(
            tap_state,
            "set_permanent_tapped",
            lambda _host, object_ref, **_kwargs: object_ref,
        ):
            with self.assertRaises(AssertionError):
                assert_single_tap()

        def assert_stun_replaces_untap() -> None:
            second.tapped = True
            second.counters["stun"] = 1
            engine.apply_effect(
                {"op": "untap", "card": second.ref}, actor="A"
            )
            self.assertTrue(second.tapped)
            self.assertNotIn("stun", second.counters)

        assert_stun_replaces_untap()

        def ignore_stun_mutant(
            _host,
            card,
            *,
            actor,
            reason,
        ) -> bool:
            card.tapped = False
            return True

        with patch.object(
            tap_state, "untap_permanent", ignore_stun_mutant
        ):
            with self.assertRaises(AssertionError):
                assert_stun_replaces_untap()

        def assert_aggregate_untap() -> None:
            first.tapped = True
            second.tapped = True
            second.counters.pop("stun", None)
            engine.apply_effect({"op": "untap_all_creatures"}, actor="A")
            self.assertFalse(first.tapped)
            self.assertFalse(second.tapped)

        assert_aggregate_untap()
        with patch.object(
            tap_state,
            "untap_all_creatures",
            lambda _host, **_kwargs: [],
        ):
            with self.assertRaises(AssertionError):
                assert_aggregate_untap()

    def test_destruction_disposition_mutants_are_killed(self):
        def assert_dispositions() -> None:
            self.assertEqual(
                destruction_module.DestructionDisposition.INDESTRUCTIBLE,
                destruction_module._destruction_disposition(
                    cause=destruction_module.DestructionCause.EFFECT,
                    indestructible=True,
                    shield_counters=1,
                ),
            )
            self.assertEqual(
                destruction_module.DestructionDisposition.SHIELD_COUNTER,
                destruction_module._destruction_disposition(
                    cause=destruction_module.DestructionCause.EFFECT,
                    indestructible=False,
                    shield_counters=1,
                ),
            )
            self.assertEqual(
                destruction_module.DestructionDisposition.DESTROY,
                destruction_module._destruction_disposition(
                    cause=(
                        destruction_module.DestructionCause.STATE_BASED_ACTION
                    ),
                    indestructible=False,
                    shield_counters=1,
                ),
            )

        assert_dispositions()
        with patch.object(
            destruction_module,
            "_destruction_disposition",
            lambda **_kwargs: (
                destruction_module.DestructionDisposition.DESTROY
            ),
        ):
            with self.assertRaises(AssertionError):
                assert_dispositions()

        def shield_every_destruction(*, cause, indestructible, shield_counters):
            if indestructible:
                return destruction_module.DestructionDisposition.INDESTRUCTIBLE
            if shield_counters:
                return destruction_module.DestructionDisposition.SHIELD_COUNTER
            return destruction_module.DestructionDisposition.DESTROY

        with patch.object(
            destruction_module,
            "_destruction_disposition",
            shield_every_destruction,
        ):
            with self.assertRaises(AssertionError):
                assert_dispositions()

    def test_combat_vigilance_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=7022020,
        )
        keep_all(session)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Vigilance Mutation Witness",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            temporary_keywords=("Haste", "Vigilance"),
        )[0]
        card = engine._resolve_object("A", ref, zones={"battlefield"})

        def assert_vigilance_prevents_declaration_tap() -> None:
            card.tapped = False
            tap_state.tap_declared_attackers(engine, (card,))
            self.assertFalse(card.tapped)

        assert_vigilance_prevents_declaration_tap()
        with patch.object(
            tap_state,
            "VIGILANCE_KEYWORD",
            "mutated-vigilance",
        ):
            with self.assertRaises(AssertionError):
                assert_vigilance_prevents_declaration_tap()

    def test_haste_attack_and_activation_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=7021010,
        )
        keep_all(session)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Haste Mutation Witness",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            temporary_keywords=("Haste",),
        )[0]
        card = engine._resolve_object("A", ref, zones={"battlefield"})
        ability = ActivatedAbility(
            ability_id="ab-haste-mutation",
            line_index=0,
            oracle_line="{T}: Add {G}.",
            cost_text="{T}",
            effect_text="Add {G}.",
            zones=("battlefield",),
            mana={},
            tap_source=True,
        )

        def assert_haste_exceptions_apply() -> None:
            card.tapped = False
            self.assertIsNone(engine._attack_declaration_error(card, "A"))
            self.assertEqual(
                ("payable", None),
                engine._ability_availability("A", card, ability),
            )

        assert_haste_exceptions_apply()
        with patch.object(
            haste_module,
            "HASTE_KEYWORD",
            "mutated-haste",
        ):
            with self.assertRaises(AssertionError):
                assert_haste_exceptions_apply()

    def test_defender_attack_restriction_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=702_003,
        )
        keep_all(session)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Defender Mutation Witness",
            characteristics={
                "type_line": "Token Creature — Wall",
                "power": "1",
                "toughness": "1",
                "keywords": ["Defender", "Haste"],
            },
        )[0]
        card = engine._resolve_object("A", ref, zones={"battlefield"})

        def assert_defender_prohibits_attack() -> None:
            self.assertIn(
                "defender",
                engine._attack_declaration_error(card, "A") or "",
            )

        assert_defender_prohibits_attack()
        with patch.object(
            defender_module,
            "DEFENDER_KEYWORD",
            "mutated-defender",
        ):
            with self.assertRaises(AssertionError):
                assert_defender_prohibits_attack()

    def test_menace_block_restriction_mutant_is_killed(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=702_111,
        )
        keep_all(session)
        engine = session.engine
        attacker_ref = engine.create_token(
            "A",
            name="Menace Mutation Witness",
            characteristics={
                "type_line": "Token Creature — Horror",
                "power": "2",
                "toughness": "2",
                "keywords": ["Menace"],
            },
        )[0]
        blocker_ref = engine.create_token(
            "B",
            name="Mutation Blocker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
        )[0]
        attacker = engine._resolve_object(
            "A", attacker_ref, zones={"battlefield"}
        )
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )

        def assert_single_blocker_is_rejected() -> None:
            problem = engine._block_declaration_problem("B")
            self.assertFalse(
                problem.evaluate({blocker_ref: attacker_ref}).legal
            )

        assert_single_blocker_is_rejected()
        with patch.object(
            menace_module,
            "MENACE_KEYWORD",
            "mutated-menace",
        ):
            with self.assertRaises(AssertionError):
                assert_single_blocker_is_rejected()

    def test_basic_landwalk_keyword_mapping_mutant_is_killed(self):
        def assert_matching_swamp_prohibits_block() -> None:
            verdict = landwalk_module.basic_landwalk_block_verdict(
                frozenset({"landwalk", "swampwalk"}),
                frozenset({"swamp"}),
            )
            self.assertFalse(verdict.allowed)

        assert_matching_swamp_prohibits_block()
        with patch.object(
            landwalk_module,
            "BASIC_LANDWALK_TYPES",
            tuple(
                pair
                for pair in landwalk_module.BASIC_LANDWALK_TYPES
                if pair[0] != "swampwalk"
            ),
        ):
            with self.assertRaises(AssertionError):
                assert_matching_swamp_prohibits_block()

    def test_ordinary_evasion_keyword_mutants_are_killed(self):
        combatant = combat_evasion_module.CombatantEvasionCharacteristics
        creature = frozenset({"creature"})

        cases = (
            (
                "FEAR_KEYWORD",
                combatant(frozenset({"fear"}), frozenset({"B"}), creature, 2),
                combatant(frozenset(), frozenset({"G"}), creature, 2),
            ),
            (
                "HORSEMANSHIP_KEYWORD",
                combatant(
                    frozenset({"horsemanship"}), frozenset(), creature, 2
                ),
                combatant(frozenset(), frozenset(), creature, 2),
            ),
            (
                "INTIMIDATE_KEYWORD",
                combatant(
                    frozenset({"intimidate"}), frozenset({"G"}), creature, 2
                ),
                combatant(frozenset(), frozenset({"B"}), creature, 2),
            ),
            (
                "SHADOW_KEYWORD",
                combatant(frozenset({"shadow"}), frozenset(), creature, 2),
                combatant(frozenset(), frozenset(), creature, 2),
            ),
            (
                "SKULK_KEYWORD",
                combatant(frozenset({"skulk"}), frozenset(), creature, 2),
                combatant(frozenset(), frozenset(), creature, 3),
            ),
        )
        for constant, attacker, blocker in cases:
            with self.subTest(constant=constant):
                def assert_restriction_applies() -> None:
                    self.assertFalse(
                        combat_evasion_module.combat_evasion_verdict(
                            attacker,
                            blocker,
                            frozenset(),
                        ).allowed
                    )

                assert_restriction_applies()
                with patch.object(
                    combat_evasion_module,
                    constant,
                    f"mutated-{constant.casefold()}",
                ):
                    with self.assertRaises(AssertionError):
                        assert_restriction_applies()

    def test_flanking_qualifying_blocker_mutant_is_killed(self):
        flanking = CombatKeywordTriggerSpec(
            CombatKeywordTriggerKind.FLANKING,
            1,
        )
        attacker = block_transitions_module.BlockTransitionParticipant(
            object_id="attacker-object",
            logical_object_id="attacker-incarnation",
            reference="A1",
            controller="A",
            trigger_specs=(flanking,),
        )
        blocker = block_transitions_module.BlockTransitionParticipant(
            object_id="blocker-object",
            logical_object_id="blocker-incarnation",
            reference="B1",
            controller="B",
            trigger_specs=(flanking,),
        )
        event = block_transitions_module.BlockTransitionEvent.create(
            turn_sequence=3,
            priority_epoch=5,
            active_player="A",
            participants=(attacker, blocker),
            assignments=(
                block_transitions_module.BlockTransitionAssignment(
                    attacker_object_id=attacker.object_id,
                    blocker_object_id=blocker.object_id,
                ),
            ),
        )
        original = (
            block_transitions_module.derive_block_keyword_trigger_occurrences
        )

        def assert_qualifying_blocker_filter() -> None:
            occurrences = (
                block_transitions_module
                .derive_block_keyword_trigger_occurrences(event)
            )
            self.assertFalse(
                any(
                    occurrence.kind is CombatKeywordTriggerKind.FLANKING
                    for occurrence in occurrences
                )
            )

        def ignore_blocker_flanking(value):
            stripped_blocker = replace(blocker, trigger_specs=())
            stripped = block_transitions_module.BlockTransitionEvent.create(
                turn_sequence=value.turn_sequence,
                priority_epoch=value.priority_epoch,
                active_player=value.active_player,
                participants=(attacker, stripped_blocker),
                assignments=value.assignments,
            )
            return original(stripped)

        assert_qualifying_blocker_filter()
        with patch.object(
            block_transitions_module,
            "derive_block_keyword_trigger_occurrences",
            ignore_blocker_flanking,
        ):
            with self.assertRaises(AssertionError):
                assert_qualifying_blocker_filter()

    def test_bushido_instance_quantity_mutant_is_killed(self):
        bushido = CombatKeywordTriggerSpec(
            CombatKeywordTriggerKind.BUSHIDO,
            2,
        )
        attacker = block_transitions_module.BlockTransitionParticipant(
            object_id="attacker-object",
            logical_object_id="attacker-incarnation",
            reference="A1",
            controller="A",
            trigger_specs=(bushido, bushido),
        )
        blocker = block_transitions_module.BlockTransitionParticipant(
            object_id="blocker-object",
            logical_object_id="blocker-incarnation",
            reference="B1",
            controller="B",
        )
        event = block_transitions_module.BlockTransitionEvent.create(
            turn_sequence=3,
            priority_epoch=5,
            active_player="A",
            participants=(attacker, blocker),
            assignments=(
                block_transitions_module.BlockTransitionAssignment(
                    attacker_object_id=attacker.object_id,
                    blocker_object_id=blocker.object_id,
                ),
            ),
        )
        original = (
            block_transitions_module.derive_block_keyword_trigger_occurrences
        )

        def assert_instance_quantity() -> None:
            occurrences = (
                block_transitions_module
                .derive_block_keyword_trigger_occurrences(event)
            )
            self.assertEqual(
                2,
                sum(
                    occurrence.kind is CombatKeywordTriggerKind.BUSHIDO
                    for occurrence in occurrences
                ),
            )

        def collapse_instances(value):
            occurrences = original(value)
            kept_bushido = False
            collapsed = []
            for occurrence in occurrences:
                if occurrence.kind is CombatKeywordTriggerKind.BUSHIDO:
                    if kept_bushido:
                        continue
                    kept_bushido = True
                collapsed.append(occurrence)
            return tuple(collapsed)

        assert_instance_quantity()
        with patch.object(
            block_transitions_module,
            "derive_block_keyword_trigger_occurrences",
            collapse_instances,
        ):
            with self.assertRaises(AssertionError):
                assert_instance_quantity()

    def test_attack_keyword_trigger_mutants_are_killed(self):
        exalted = CombatKeywordTriggerSpec(
            CombatKeywordTriggerKind.EXALTED,
            1,
        )
        battle_cry = CombatKeywordTriggerSpec(
            CombatKeywordTriggerKind.BATTLE_CRY,
            1,
        )
        melee = CombatKeywordTriggerSpec(
            CombatKeywordTriggerKind.MELEE,
            1,
        )

        def participant(object_id, reference, *specs, creature=True):
            return attack_transitions_module.AttackTransitionParticipant(
                object_id=object_id,
                logical_object_id=f"logical:{object_id}",
                reference=reference,
                controller="A",
                is_creature=creature,
                trigger_specs=specs,
            )

        def assignment(object_id, opponent):
            return attack_transitions_module.AttackTransitionAssignment(
                attacker_object_id=object_id,
                recipient=attack_transitions_module.AttackRecipient(
                    attack_transitions_module.AttackRecipientKind.PLAYER,
                    opponent,
                    opponent,
                ),
            )

        exalted_source = participant(
            "exalted-source",
            "A01",
            exalted,
            creature=False,
        )
        first = participant("first-attacker", "A02")
        second = participant("second-attacker", "A03")
        two_attacker_event = attack_transitions_module.AttackTransitionEvent.create(
            turn_sequence=3,
            priority_epoch=5,
            active_player="A",
            participants=(exalted_source, first, second),
            assignments=(
                assignment(first.object_id, "B"),
                assignment(second.object_id, "C"),
            ),
        )
        lone_attacker_event = attack_transitions_module.AttackTransitionEvent.create(
            turn_sequence=3,
            priority_epoch=5,
            active_player="A",
            participants=(exalted_source, first),
            assignments=(assignment(first.object_id, "B"),),
        )
        original = (
            attack_transitions_module.derive_attack_keyword_trigger_occurrences
        )

        def assert_two_attackers_do_not_trigger_exalted():
            self.assertFalse(
                any(
                    value.kind is CombatKeywordTriggerKind.EXALTED
                    for value in (
                        attack_transitions_module
                        .derive_attack_keyword_trigger_occurrences(
                            two_attacker_event
                        )
                    )
                )
            )

        assert_two_attackers_do_not_trigger_exalted()
        with patch.object(
            attack_transitions_module,
            "derive_attack_keyword_trigger_occurrences",
            lambda _event: original(lone_attacker_event),
        ):
            with self.assertRaises(AssertionError):
                assert_two_attackers_do_not_trigger_exalted()

        battle_source = participant(
            "battle-source",
            "A04",
            battle_cry,
            battle_cry,
        )
        battle_other = participant("battle-other", "A05")
        battle_event = attack_transitions_module.AttackTransitionEvent.create(
            turn_sequence=3,
            priority_epoch=6,
            active_player="A",
            participants=(battle_source, battle_other),
            assignments=(
                assignment(battle_source.object_id, "B"),
                assignment(battle_other.object_id, "B"),
            ),
        )

        def assert_battle_cry_preserves_instances():
            self.assertEqual(
                2,
                len(
                    attack_transitions_module
                    .derive_attack_keyword_trigger_occurrences(battle_event)
                ),
            )

        def collapse_battle_cry(event):
            values = original(event)
            return values[:1]

        assert_battle_cry_preserves_instances()
        with patch.object(
            attack_transitions_module,
            "derive_attack_keyword_trigger_occurrences",
            collapse_battle_cry,
        ):
            with self.assertRaises(AssertionError):
                assert_battle_cry_preserves_instances()

        melee_source = participant("melee-source", "A06", melee)
        melee_other = participant("melee-other", "A07")
        melee_event = attack_transitions_module.AttackTransitionEvent.create(
            turn_sequence=3,
            priority_epoch=7,
            active_player="A",
            participants=(melee_source, melee_other),
            assignments=(
                assignment(melee_source.object_id, "B"),
                assignment(melee_other.object_id, "C"),
            ),
        )

        def assert_melee_counts_direct_opponents():
            occurrence = (
                attack_transitions_module
                .derive_attack_keyword_trigger_occurrences(melee_event)[0]
            )
            self.assertEqual(2, occurrence.amount)

        def overcount_melee(event):
            values = original(event)
            return tuple(
                attack_transitions_module.AttackKeywordTriggerOccurrence.create(
                    transition_id=value.transition_id,
                    kind=value.kind,
                    controller=value.controller,
                    source=value.source,
                    affected=value.affected,
                    amount=value.amount + 1,
                    instance_index=value.instance_index,
                )
                for value in values
            )

        assert_melee_counts_direct_opponents()
        with patch.object(
            attack_transitions_module,
            "derive_attack_keyword_trigger_occurrences",
            overcount_melee,
        ):
            with self.assertRaises(AssertionError):
                assert_melee_counts_direct_opponents()

    def test_aerial_blocking_flying_and_reach_mutants_are_killed(self):
        def assert_ground_cannot_block_flying() -> None:
            verdict = aerial_blocking_module.aerial_block_verdict(
                frozenset({"flying"}),
                frozenset(),
            )
            self.assertFalse(verdict.allowed)

        def assert_reach_can_block_flying() -> None:
            verdict = aerial_blocking_module.aerial_block_verdict(
                frozenset({"flying"}),
                frozenset({"reach"}),
            )
            self.assertTrue(verdict.allowed)

        assert_ground_cannot_block_flying()
        with patch.object(
            aerial_blocking_module,
            "FLYING_KEYWORD",
            "mutated-flying",
        ):
            with self.assertRaises(AssertionError):
                assert_ground_cannot_block_flying()

        assert_reach_can_block_flying()
        with patch.object(
            aerial_blocking_module,
            "AERIAL_BLOCKER_KEYWORDS",
            frozenset({"flying"}),
        ):
            with self.assertRaises(AssertionError):
                assert_reach_can_block_flying()

    def test_trample_lethal_assignment_mutant_is_killed(self):
        proposal = combat_assignment_module.CombatDamageAssignmentProposal(
            damage_step_id="combat-damage:1:0:0",
            actor="A",
            sources=(
                combat_assignment_module.CombatDamageSourceSpec(
                    source="attacker",
                    controller="A",
                    logical_object_id="attacker@1",
                    power=4,
                    targets=("blocker", "B"),
                ),
            ),
            attacking_sources=frozenset({"attacker"}),
            deathtouch_sources=frozenset(),
            trample_sources=(
                combat_assignment_module.TrampleDamageSpec(
                    attacker="attacker",
                    spill_target="B",
                    blockers=(
                        (
                            "blocker",
                            combat_assignment_module.CreatureDamageState(
                                toughness=3,
                                marked_damage=0,
                            ),
                        ),
                    ),
                ),
            ),
        )
        early_spill = [
            {"source": "attacker", "target": "blocker", "amount": 2},
            {"source": "attacker", "target": "B", "amount": 2},
        ]

        def assert_early_spill_is_rejected() -> None:
            with self.assertRaises(
                combat_assignment_module.CombatDamageAssignmentError
            ):
                proposal.validate(early_spill)

        assert_early_spill_is_rejected()
        with patch.object(
            combat_assignment_module,
            "trample_assignment_error",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_early_spill_is_rejected()

    def test_deathtouch_assignment_and_result_mutants_are_killed(self):
        assignments = (
            combat_assignment_module.DamageAssignment(
                source="attacker",
                target="blocker",
                amount=1,
            ),
            combat_assignment_module.DamageAssignment(
                source="attacker",
                target="B",
                amount=3,
            ),
        )

        def assert_deathtouch_assignment_is_lethal() -> None:
            self.assertIsNone(
                trample_module.trample_assignment_error(
                    attacker_ref="attacker",
                    spill_target="B",
                    blockers=(
                        (
                            "blocker",
                            combat_assignment_module.CreatureDamageState(
                                toughness=8,
                                marked_damage=0,
                            ),
                        ),
                    ),
                    assignments=assignments,
                    attacking_source_refs=frozenset({"attacker"}),
                    deathtouch_source_refs=frozenset({"attacker"}),
                )
            )

        assert_deathtouch_assignment_is_lethal()
        with patch.object(
            deathtouch_module,
            "deathtouch_assignment_is_lethal",
            return_value=False,
        ):
            with self.assertRaises(AssertionError):
                assert_deathtouch_assignment_is_lethal()

        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=702_002,
        )
        keep_all(session)
        engine = session.engine
        source_ref = engine.create_token(
            "A",
            name="Deathtouch mutation source",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
                "keywords": ["Deathtouch"],
            },
        )[0]
        target_ref = engine.create_token(
            "B",
            name="Deathtouch mutation target",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "8",
            },
        )[0]
        event = damage_module.damage_proposal(
            engine,
            proposal_id="damage:deathtouch-mutation",
            actor="A",
            source_ref=source_ref,
            target=target_ref,
            amount=1,
            combat=False,
            reason="deathtouch mutation",
        ).event()

        def assert_deathtouch_result_applies() -> None:
            roots = damage_results_module.materialize_damage_results(
                engine,
                (event,),
            )
            self.assertIn(
                "damage.deathtouch",
                {child.kind for root in roots for child in root.children},
            )

        assert_deathtouch_result_applies()
        with patch.object(
            deathtouch_module,
            "deathtouch_damage_result_applies",
            return_value=False,
        ):
            with self.assertRaises(AssertionError):
                assert_deathtouch_result_applies()

    def test_canonical_assignment_order_mutant_is_killed(self):
        proposal = combat_assignment_module.CombatDamageAssignmentProposal(
            damage_step_id="combat-damage:1:0:0",
            actor="A",
            sources=(
                combat_assignment_module.CombatDamageSourceSpec(
                    source="attacker",
                    controller="A",
                    logical_object_id="attacker@1",
                    power=4,
                    targets=("blocker", "B"),
                ),
            ),
            attacking_sources=frozenset({"attacker"}),
            deathtouch_sources=frozenset(),
            trample_sources=(),
        )
        submitted = [
            {"source": "attacker", "target": "B", "amount": 1},
            {"source": "attacker", "target": "blocker", "amount": 3},
        ]
        original = combat_assignment_module.CombatDamageAssignmentProposal.validate

        def assert_proposal_order_wins() -> None:
            accepted = proposal.validate(submitted)
            self.assertEqual(
                ["blocker", "B"],
                [value.target for value in accepted],
            )

        assert_proposal_order_wins()

        def reversed_result(self, values):
            return tuple(reversed(original(self, values)))

        with patch.object(
            combat_assignment_module.CombatDamageAssignmentProposal,
            "validate",
            reversed_result,
        ):
            with self.assertRaises(AssertionError):
                assert_proposal_order_wins()

    def test_strike_step_participation_mutant_is_killed(self):
        def assert_double_strike_participates_twice() -> None:
            self.assertTrue(
                combat_module.first_strike_step_required(
                    {"source": frozenset({"double strike"})}
                )
            )
            self.assertTrue(
                combat_module.assigns_in_damage_step(
                    object_id="source",
                    current_keywords=frozenset({"double strike"}),
                    step_index=1,
                    first_strike_step=True,
                    ordinary_second_step=frozenset(),
                )
            )

        assert_double_strike_participates_twice()
        with patch.object(combat_module, "DOUBLE_STRIKE", "mutated strike"):
            with self.assertRaises(AssertionError):
                assert_double_strike_participates_twice()

    def test_replacement_nested_order_mutant_is_killed(self):
        child = replacement_effects.ReplaceableEvent(
            event_id="counter:child",
            kind="counter.add",
            affected_player="A",
            payload={"amount": 1},
        )
        root = replacement_effects.ReplaceableEvent(
            event_id="token:root",
            kind="token.create",
            affected_player="A",
            payload={"quantity": 1},
            children=(child,),
        )
        effects = (
            replacement_effects.ReplacementEffect(
                effect_id="outer",
                source_id="outer-source",
                event_kind="token.create",
                replacement_class=replacement_effects.ReplacementClass.OTHER,
                operations=(
                    {"op": "multiply", "field": "quantity", "factor": 2},
                ),
            ),
            replacement_effects.ReplacementEffect(
                effect_id="inner",
                source_id="inner-source",
                event_kind="counter.add",
                replacement_class=replacement_effects.ReplacementClass.OTHER,
                operations=(
                    {"op": "multiply", "field": "amount", "factor": 2},
                ),
            ),
        )

        def assert_containing_event_first() -> None:
            pending = replacement_effects.replacement_tree_choice(
                root, effects
            )
            self.assertEqual((), pending.path)
            self.assertEqual(("outer",), pending.choice.options)

        assert_containing_event_first()

        def child_first_mutant(event, available_effects):
            choice = replacement_effects.replacement_choice(
                event.children[0], available_effects
            )
            return replacement_effects.ReplacementTreeChoice(
                path=(0,), choice=choice
            )

        with patch.object(
            replacement_effects,
            "replacement_tree_choice",
            child_first_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_containing_event_first()

    def test_additional_token_replacement_mutant_is_killed(self):
        event = replacement_effects.ReplaceableEvent(
            event_id="token:mutation",
            kind="token.create",
            affected_player="A",
            payload={
                "event_controller": "A",
                "tokens": [{"name": "Cat", "quantity": 1}],
                "created_types": ["creature"],
                "created_subtypes": ["cat"],
            },
        )
        replacement = replacement_effects.ReplacementEffect(
            effect_id="token:add-food",
            source_id="A-source",
            event_kind="token.create",
            replacement_class=replacement_effects.ReplacementClass.OTHER,
            operations=(
                replacement_effects.CreateAdditionalToken(
                    name="Food",
                    quantity=1,
                    characteristics={
                        "type_line": "Token Artifact — Food"
                    },
                    card_types=("artifact",),
                    subtypes=("food",),
                    handler_id="replacement.token.additional.v2",
                    source_ref="A-source",
                ),
            ),
        )

        def assert_additional_token_applied() -> None:
            resolved = replacement_effects.apply_replacement(
                replacement_effects.replacement_choice(
                    event, (replacement,)
                ),
                (replacement,),
                replacement.effect_id,
            )
            self.assertEqual(2, len(resolved.payload["tokens"]))
            self.assertIn("artifact", resolved.payload["created_types"])
            self.assertIn("food", resolved.payload["created_subtypes"])

        assert_additional_token_applied()
        with patch.object(
            replacement_application,
            "_apply_additional_token",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_additional_token_applied()

    def test_counter_quantity_replacement_mutant_is_killed(self):
        descriptor = {
            "handler_id": "replacement.counter.quantity.v1",
            "schema_version": 1,
            "event": "counter.place",
            "condition": {
                "placing_player_relation": "any",
                "target_controller_relation": "source_controller",
                "counter_names": [],
                "target_types_all": [],
                "effect_generated": True,
            },
            "modification": {"multiplier": 2, "additional": 0},
        }
        context = CounterReplacementSourceContext(
            source_ref="doubling",
            source_controller="A",
        )
        event = CounterPlacementEventSpec(
            event_id="counter-mutation",
            subject_kind="permanent",
            subject_id="target",
            owner="A",
            controller="A",
            target_zone="battlefield",
            target_types=("creature",),
            placing_player="A",
            counter_name="+1/+1",
            amount=2,
            source_ref=None,
            effect_generated=True,
        ).event()

        def assert_quantity_replaced() -> None:
            effect = CounterQuantityReplacementHandler().replacement_effect(
                descriptor,
                context,
            )
            resolution = resolve_counter_placement_replacements(
                batch_id="counter-mutation-batch",
                events=(event,),
                effects=(effect,),
                apnap_order=("A",),
            )
            self.assertEqual(
                4,
                resolution.batch.events[0].payload["amount"],
            )

        assert_quantity_replaced()
        original = CounterQuantityReplacementHandler.replacement_effect

        def identity_quantity_mutant(handler, mapping, source_context):
            effect = original(handler, mapping, source_context)
            return replace(
                effect,
                operations=(
                    {"op": "multiply", "field": "amount", "factor": 1},
                ),
            )

        with patch.object(
            CounterQuantityReplacementHandler,
            "replacement_effect",
            identity_quantity_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_quantity_replaced()

    def test_counter_quantity_cost_scope_mutant_is_killed(self):
        descriptor = {
            "handler_id": "replacement.counter.quantity.v2",
            "schema_version": 2,
            "event": "counter.place",
            "condition": {
                "effect_scope": "any",
                "placing_player_relation": "source_controller",
                "target_controller_relation": "source_controller",
                "target_kinds": ["permanent"],
                "counter_names": [],
                "target_types_all": [],
                "target_types_any": [],
            },
            "modification": {"multiplier": 1, "additional": 1},
        }
        context = CounterReplacementSourceContext(
            source_ref="doc",
            source_controller="A",
        )
        event = CounterPlacementEventSpec(
            event_id="loyalty-cost-mutation",
            subject_kind="permanent",
            subject_id="walker",
            owner="A",
            controller="A",
            target_zone="battlefield",
            target_types=("planeswalker",),
            placing_player="A",
            counter_name="loyalty",
            amount=2,
            source_ref="walker",
            effect_generated=False,
        ).event()

        def assert_cost_quantity_replaced() -> None:
            effect = CounterQuantityReplacementV2Handler().replacement_effect(
                descriptor,
                context,
            )
            resolution = resolve_counter_placement_replacements(
                batch_id="loyalty-cost-mutation-batch",
                events=(event,),
                effects=(effect,),
                apnap_order=("A", "B"),
            )
            self.assertEqual(
                3,
                resolution.batch.events[0].payload["amount"],
            )

        assert_cost_quantity_replaced()
        original = CounterQuantityReplacementV2Handler.replacement_effect

        def effect_only_mutant(handler, mapping, source_context):
            effect = original(handler, mapping, source_context)
            conditions = dict(effect.conditions)
            conditions["effect_generated"] = {"eq": True}
            return replace(effect, conditions=conditions)

        with patch.object(
            CounterQuantityReplacementV2Handler,
            "replacement_effect",
            effect_only_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_cost_quantity_replaced()

    def test_loyalty_cost_provenance_mutant_is_killed(self):
        def assert_effect_only_replacement_ignores_cost() -> None:
            session = make_session(
                self.db,
                self.mishra,
                self.zimone,
                players=2,
                seed=6061499,
                auto_pass_empty=False,
            )
            keep_all(session)
            engine = session.engine
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            engine.state.stack.clear()
            daretti = next(
                card
                for card in engine.state.cards.values()
                if card.owner == "A"
                and card.printed_name == "Daretti, Scrap Savant"
            )
            engine.move_card(
                daretti.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
            doubling = self.db.lookup("Doubling Season")
            source = CardInstance(
                object_id="fixture:doubling-season-mutation",
                ref="A-doubling-mutation",
                oracle_id=doubling.oracle_id,
                printed_name=doubling.name,
                owner="A",
                controller="A",
                zone="battlefield",
                zone_timestamp=engine.state.event_sequence + 1,
                known_to=list(engine.seats),
                revealed_to=list(engine.seats),
            )
            engine.state.cards[source.object_id] = source
            engine.state.players["A"].zones["battlefield"].append(
                source.object_id
            )
            ability = next(
                value
                for value in engine._activated_abilities(daretti)
                if value.loyalty_delta == 2
            )
            before = daretti.counters["loyalty"]
            engine._activate(
                "A", {"source": daretti.ref, "ability": ability.ability_id}
            )
            self.assertEqual(before + 2, daretti.counters["loyalty"])

        assert_effect_only_replacement_ignores_cost()
        original = activation_commit.prepare_counter_placements

        def effect_provenance_mutant(host, requests, **kwargs):
            return original(
                host,
                tuple(
                    replace(request, effect_generated=True)
                    for request in requests
                ),
                **kwargs,
            )

        with patch.object(
            activation_commit,
            "prepare_counter_placements",
            effect_provenance_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_effect_only_replacement_ignores_cost()

    def test_prevention_aftermath_quantity_mutant_is_killed(self):
        aftermath = GainLifePreventionAftermath(
            player="A", per_prevented=2, fixed_amount=1
        )

        def assert_scaled_aftermath() -> None:
            self.assertEqual(7, aftermath.amount(3))

        assert_scaled_aftermath()
        with patch.object(
            GainLifePreventionAftermath,
            "amount",
            lambda value, _prevented: value.fixed_amount,
        ):
            with self.assertRaises(AssertionError):
                assert_scaled_aftermath()

    def test_prevention_damage_aftermath_quantity_mutant_is_killed(self):
        aftermath = DealDamagePreventionAftermath(
            source=damage_module.DamageSourceSnapshot(
                ref="palm",
                object_id="palm-object",
                logical_object_id="palm-incarnation",
                controller="A",
                owner="A",
            ),
            recipient=DamageAftermathRecipient(
                kind="prevented_source_controller"
            ),
            per_prevented=1,
        )

        def assert_scaled_damage() -> None:
            self.assertEqual(3, aftermath.amount(3))

        assert_scaled_damage()
        with patch.object(
            DealDamagePreventionAftermath,
            "amount",
            lambda value, _prevented: value.fixed_amount,
        ):
            with self.assertRaises(AssertionError):
                assert_scaled_damage()

    def test_damage_replacement_prevention_mutants_are_killed(self):
        condition = {
            "source_controller_relation": "any",
            "target_controller_relation": "any",
            "target_kinds": [],
            "source_types_all": [],
            "target_types_all": [],
            "combat": None,
        }
        quantity_descriptor = {
            "handler_id": "replacement.damage.quantity.v1",
            "schema_version": 1,
            "event": "damage",
            "condition": condition,
            "modification": {"multiplier": 2, "additional": 0},
        }
        prevention_descriptor = {
            "handler_id": "prevention.damage.fixed.v1",
            "schema_version": 1,
            "event": "damage",
            "condition": condition,
            "modification": {"amount": 1},
        }
        context = DamageReplacementSourceContext(
            source_ref="damage-mutation-source",
            source_controller="A",
        )
        event = replacement_effects.ReplaceableEvent(
            event_id="damage:mutation",
            kind="damage",
            affected_player="B",
            payload={
                "amount": 3,
                "prevented": 0,
                "unpreventable": False,
            },
        )

        def assert_prevent_then_double() -> None:
            quantity = (
                DamageQuantityReplacementHandler().replacement_effect(
                    quantity_descriptor, context
                )
            )
            prevention = FixedDamagePreventionHandler().replacement_effect(
                prevention_descriptor, context
            )
            resolved = replacement_effects.resolve_replacements(
                event,
                (quantity, prevention),
                selections=(prevention.effect_id, quantity.effect_id),
            )
            self.assertEqual(4, resolved.payload["amount"])
            self.assertEqual(1, resolved.payload["prevented"])

        assert_prevent_then_double()
        original_quantity = (
            DamageQuantityReplacementHandler.replacement_effect
        )

        def identity_quantity_mutant(handler, descriptor, source_context):
            effect = original_quantity(handler, descriptor, source_context)
            return replace(
                effect,
                operations=(
                    {"op": "multiply", "field": "amount", "factor": 1},
                ),
            )

        with patch.object(
            DamageQuantityReplacementHandler,
            "replacement_effect",
            identity_quantity_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_prevent_then_double()

        original_prevention = FixedDamagePreventionHandler.replacement_effect

        def skip_prevention_mutant(handler, descriptor, source_context):
            effect = original_prevention(
                handler, descriptor, source_context
            )
            return replace(effect, operations=({"op": "prevent", "amount": 0},))

        with patch.object(
            FixedDamagePreventionHandler,
            "replacement_effect",
            skip_prevention_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_prevent_then_double()

    def test_persistent_prevention_commit_mutant_is_killed(self):
        def assert_shield_is_consumed() -> None:
            session = make_session(
                self.db,
                self.mishra,
                self.zimone,
                players=2,
                seed=615_900,
            )
            keep_all(session)
            engine = session.engine
            source_ref = engine.create_token(
                "A",
                name="Damage Source",
                characteristics={
                    "type_line": "Token Creature — Test",
                    "power": "1",
                    "toughness": "1",
                },
            )[0]
            engine.state.damage_prevention_shields.append(
                DamagePreventionShield(
                    shield_id="mutation-shield",
                    source_id="mutation-effect",
                    controller="B",
                    subject=DamageSubject("B", "player", "B"),
                    mode=PreventionMode.AMOUNT,
                    remaining=3,
                    duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                    created_turn_sequence=engine.state.turn_sequence,
                )
            )
            proposal = damage_module.damage_proposal(
                engine,
                proposal_id="damage:prevention-mutation",
                actor="A",
                source_ref=source_ref,
                target="B",
                amount=2,
                combat=False,
                reason="prevention mutation witness",
            )
            prepared = damage_module.prepare_damage_batch(
                engine, (proposal,)
            )
            damage_module.commit_prepared_damage_batch(engine, prepared)
            self.assertEqual(
                1, engine.state.damage_prevention_shields[0].remaining
            )

        assert_shield_is_consumed()
        with patch.object(
            damage_module,
            "commit_damage_modifier_plan",
            lambda _host, _plan: None,
        ):
            with self.assertRaises(AssertionError):
                assert_shield_is_consumed()

    def test_prevention_scope_condition_mutants_are_killed(self):
        shield = DamagePreventionShield(
            shield_id="scoped-mutation-shield",
            source_id="scoped-mutation-effect",
            controller="B",
            subject=DamageSubject("*", "any", "B"),
            mode=PreventionMode.ALL,
            remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=1,
            damage_kind=PreventionDamageKind.COMBAT,
            recipient_kind=PreventionRecipientKind.PLAYER,
        )

        def damage_event(*, combat: bool, target_kind: str):
            return replacement_effects.ReplaceableEvent(
                event_id=f"damage:{combat}:{target_kind}",
                kind="damage",
                affected_player="B",
                payload={
                    "amount": 1,
                    "combat": combat,
                    "target_kind": target_kind,
                },
            )

        def assert_scope() -> None:
            effect = damage_prevention_module._shield_replacement_effect(
                shield
            )
            self.assertIsNotNone(
                replacement_effects.replacement_choice(
                    damage_event(combat=True, target_kind="player"),
                    (effect,),
                )
            )
            self.assertIsNone(
                replacement_effects.replacement_choice(
                    damage_event(combat=False, target_kind="player"),
                    (effect,),
                )
            )
            self.assertIsNone(
                replacement_effects.replacement_choice(
                    damage_event(combat=True, target_kind="permanent"),
                    (effect,),
                )
            )

        assert_scope()
        original = damage_prevention_module._shield_replacement_effect
        for removed in ("combat", "target_kind"):
            with self.subTest(removed=removed):
                def scope_mutant(value, *, removed_field=removed):
                    effect = original(value)
                    return replace(
                        effect,
                        conditions={
                            field: condition
                            for field, condition in effect.conditions.items()
                            if field != removed_field
                        },
                    )

                with patch.object(
                    damage_prevention_module,
                    "_shield_replacement_effect",
                    scope_mutant,
                ):
                    with self.assertRaises(AssertionError):
                        assert_scope()

    def test_prevention_trigger_quantity_mutant_is_killed(self):
        source = DamageSourceSnapshot(
            ref="prevention-source",
            object_id="prevention-source-object",
            logical_object_id="prevention-source-incarnation",
            controller="B",
            owner="B",
            zone="stack",
            types=("instant",),
        )
        result = DrawCardsPreventionTrigger(
            player="B",
            per_prevented=1,
        )
        occurrence = PreventionTriggerOccurrence(
            ability=PreventionTriggeredAbility(
                controller="B",
                source=source,
                label="Damage prevented this way",
                results=(result,),
            ),
            effect_id="prevention.shield:mutation",
            prevented_amount=4,
            damage_event_ids=("damage:mutation",),
            prevented_source_controllers=("A",),
        )

        def assert_scaled_draw() -> None:
            effects = occurrence.runtime_effects()
            self.assertEqual(1, len(effects))
            self.assertEqual(4, effects[0]["count"])

        assert_scaled_draw()
        with patch.object(
            DrawCardsPreventionTrigger,
            "amount",
            lambda value, prevented: value.fixed_amount,
        ):
            with self.assertRaises(AssertionError):
                assert_scaled_draw()

    def test_trigger_apnap_grouping_mutant_is_killed(self):
        original_grouping = (
            trigger_batches_module.group_pending_trigger_items
        )
        values = (
            {
                "stack_id": "stack-a",
                "controller": "A",
                "ref": "trigger-a",
                "kind": "triggered_ability",
                "label": "Trigger A",
            },
            {
                "stack_id": "stack-c",
                "controller": "C",
                "ref": "trigger-c",
                "kind": "triggered_ability",
                "label": "Trigger C",
            },
            {
                "stack_id": "stack-b",
                "controller": "B",
                "ref": "trigger-b",
                "kind": "triggered_ability",
                "label": "Trigger B",
            },
        )

        def assert_apnap_grouping() -> None:
            groups = trigger_batches_module.group_pending_trigger_items(
                values,
                apnap_order=("C", "D", "A", "B"),
            )
            self.assertEqual(
                ["C", "A", "B"],
                [group.controller for group in groups],
            )

        def alphabetical_grouping(candidates, *, apnap_order):
            return original_grouping(
                candidates,
                apnap_order=tuple(sorted(apnap_order)),
            )

        assert_apnap_grouping()
        with patch.object(
            trigger_batches_module,
            "group_pending_trigger_items",
            alphabetical_grouping,
        ):
            with self.assertRaises(AssertionError):
                assert_apnap_grouping()

    def test_chosen_source_incarnation_mutants_are_killed(self):
        chosen = ChosenDamageSource(
            ref="C1",
            object_id="physical-source",
            snapshot_version=2,
            logical_object_id="spell-incarnation",
            oracle_id="source-oracle",
            printed_name="Chosen Source",
            controller="A",
            owner="A",
            zone="stack",
            types=("creature",),
            identity_keys=(
                "spell-incarnation|stack",
                "spell-incarnation|battlefield",
            ),
        )
        shield = DamagePreventionShield(
            shield_id="chosen-source-shield",
            source_id="prevention-effect",
            controller="B",
            subject=DamageSubject("B", "player", "B"),
            mode=PreventionMode.NEXT_INSTANCE,
            remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=1,
            chosen_source=chosen,
        )

        def damage_event(identity_key: str) -> replacement_effects.ReplaceableEvent:
            return replacement_effects.ReplaceableEvent(
                event_id=f"damage:{identity_key}",
                kind="damage",
                affected_player="B",
                payload={
                    "amount": 1,
                    "target": "B",
                    "target_kind": "player",
                    "source_object_id": "physical-source",
                    "source_identity_key": identity_key,
                },
            )

        def assert_incarnation_boundary() -> None:
            effect = damage_prevention_module._shield_replacement_effect(shield)
            self.assertIsNotNone(
                replacement_effects.replacement_choice(
                    damage_event("spell-incarnation|battlefield"),
                    (effect,),
                )
            )
            self.assertIsNone(
                replacement_effects.replacement_choice(
                    damage_event("new-incarnation|battlefield"),
                    (effect,),
                )
            )

        assert_incarnation_boundary()

        with patch.object(
            ChosenDamageSource,
            "event_conditions",
            lambda value: {
                "source_object_id": {"eq": value.object_id},
            },
        ):
            with self.assertRaises(AssertionError):
                assert_incarnation_boundary()

        with patch.object(
            ChosenDamageSource,
            "event_conditions",
            lambda value: {
                "source_identity_key": {
                    "eq": next(
                        key
                        for key in value.identity_keys
                        if key.endswith("|stack")
                    ),
                },
            },
        ):
            with self.assertRaises(AssertionError):
                assert_incarnation_boundary()

    def test_static_redirection_mutant_is_killed(self):
        descriptor = {
            "handler_id": "replacement.damage.redirect-to-source.v1",
            "schema_version": 1,
            "event": "damage",
            "condition": {
                "source_controller_relation": "any",
                "target_controller_relation": "source_controller",
                "target_kinds": ["player"],
                "source_types_all": [],
                "target_types_all": [],
                "combat": None,
            },
            "modification": {"destination": "source"},
        }
        destination = replacement_effects.RedirectDamage(
            target="C1",
            target_kind="permanent",
            target_controller="B",
            target_object_id="destination-object",
            target_logical_object_id="destination-incarnation",
            target_owner="B",
            target_types=("creature",),
        )
        context = DamageReplacementSourceContext(
            source_ref="C1",
            source_controller="B",
            source_destination=destination,
        )
        event = replacement_effects.ReplaceableEvent(
            event_id="damage:redirection-mutation",
            kind="damage",
            affected_player="B",
            payload={
                "source_controller": "A",
                "target": "B",
                "target_kind": "player",
                "target_controller": "B",
                "amount": 2,
                "prevented": 0,
                "unpreventable": False,
                "combat": False,
            },
        )

        def assert_redirected() -> None:
            effect = StaticDamageRedirectionHandler().replacement_effect(
                descriptor, context
            )
            resolved = replacement_effects.resolve_replacements(
                event, (effect,), selections=(effect.effect_id,)
            )
            self.assertEqual("C1", resolved.payload["target"])
            self.assertEqual(
                "destination-object", resolved.affected_object.object_id
            )

        assert_redirected()
        original = StaticDamageRedirectionHandler.replacement_effect

        def retain_recipient_mutant(handler, mapping, source_context):
            effect = original(handler, mapping, source_context)
            return replace(
                effect,
                operations=(
                    replacement_effects.RedirectDamage(
                        target="B",
                        target_kind="player",
                        target_controller="B",
                    ),
                ),
            )

        with patch.object(
            StaticDamageRedirectionHandler,
            "replacement_effect",
            retain_recipient_mutant,
        ):
            with self.assertRaises(AssertionError):
                assert_redirected()
