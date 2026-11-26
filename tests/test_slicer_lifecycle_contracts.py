"""Closed source handoff, conversion, and sacrifice lifetime contracts."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
from quorune.delayed_object_actions import apply_delayed_object_action
from quorune.delayed_object_model import (
    DELAYED_SOURCE_CONVERT_CAPABILITY,
    RESOLVE_OBJECT_ACTION,
    delayed_source_convert_effect,
    resolved_source_convert_effect,
    validate_delayed_object_instruction,
)
from quorune.rules.source_upkeep_handoff import (
    SOURCE_UPKEEP_HANDOFF_CAPABILITY,
    decode_source_upkeep_handoff,
    source_control_or_conversion_runtime_dependencies,
    source_upkeep_handoff_condition,
    source_upkeep_handoff_effect,
)
from quorune.sacrifice_prohibition_model import ResolvedSacrificeProhibition, sacrifice_is_prohibited
from quorune.spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE


def _program(effect, event, condition, coverage):
    return SimpleNamespace(
        effects=(effect,), handlers=(), event=event, event_condition=condition,
        active_zone="battlefield", target_schema=None, cost_schema=None, trigger_limit=None,
        coverage=coverage,
    )


class SlicerLifecycleContractTests(unittest.TestCase):
    def test_upkeep_handoff_requires_its_exact_opponent_trigger_and_seals(self):
        effect = source_upkeep_handoff_effect()
        program = _program(effect, "step.begin", source_upkeep_handoff_condition(),
                           (CURRENT_ABILITY_FRAGMENT_COVERAGE, SPELL_HISTORY_TRANSFORM_COVERAGE))
        self.assertIn(SOURCE_UPKEEP_HANDOFF_CAPABILITY, source_control_or_conversion_runtime_dependencies(program))
        condition = deepcopy(program.event_condition)
        condition["all"][1]["op"] = "eq"
        program.event_condition = condition
        with self.assertRaisesRegex(ValueError, "opponent-upkeep"):
            source_control_or_conversion_runtime_dependencies(program)
        forged = deepcopy(effect)
        forged["effects"][0]["recipient"] = "$controller"
        with self.assertRaisesRegex(ValueError, "printed instruction"):
            decode_source_upkeep_handoff(forged)

    def test_delayed_source_conversion_requires_its_combat_envelope_and_epoch(self):
        effect = delayed_source_convert_effect()
        condition = {"all": [
            {"field": "target_kind", "op": "eq", "value": "player"},
            {"field": "combat", "op": "truthy", "value": True},
            {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
        ]}
        program = _program(effect, "damage.dealt.self", condition, (CURRENT_ABILITY_FRAGMENT_COVERAGE,))
        self.assertIn(DELAYED_SOURCE_CONVERT_CAPABILITY, source_control_or_conversion_runtime_dependencies(program))
        program.event = "damage.dealt"
        with self.assertRaisesRegex(ValueError, "source combat damage to a player"):
            source_control_or_conversion_runtime_dependencies(program)
        with self.assertRaisesRegex(ValueError, "at end of combat"):
            validate_delayed_object_instruction({**effect, "timing": "next_end_step"}, compiled=True)
        with self.assertRaisesRegex(ValueError, "nonnegative creation count"):
            validate_delayed_object_instruction(resolved_source_convert_effect(
                reference="permanent-ref", logical_object_id="sample@2", transform_count=True,
            ))

    def test_conversion_rejects_stale_count(self):
        current = SimpleNamespace(
            object_id="sample", ref="permanent-ref", logical_object_id="sample@2",
            zone="battlefield", phased_out=False, face_down=False, transform_count=2,
            permanent_composition=None, annotations={}, object_kind="card",
        )
        host = SimpleNamespace(state=SimpleNamespace(cards={current.object_id: current}))
        other_face = Mock(return_value=("back", "front"))
        with patch("quorune.permanent_transform._other_face", other_face):
            result = apply_delayed_object_action(host, resolved_source_convert_effect(
                reference=current.ref, logical_object_id=current.logical_object_id, transform_count=1,
            ), actor="A", operation=RESOLVE_OBJECT_ACTION, reason="end-combat conversion")
        self.assertIsNone(result)
        other_face.assert_not_called()

    def test_sacrifice_prohibition_locks_one_incarnation_and_expires_in_cleanup(self):
        subject = SimpleNamespace(object_id="sample", logical_object_id="sample@2")
        another = SimpleNamespace(object_id="sample", logical_object_id="sample@3")
        effect = ResolvedSacrificeProhibition("prohibition:resolution", 1, ContinuousObjectIdentity(
            subject.object_id, subject.logical_object_id,
        ))
        state = SimpleNamespace(continuous_effects=[ResolvedSacrificeProhibition.from_dict(effect.to_dict())])
        self.assertTrue(sacrifice_is_prohibited(state, subject))
        self.assertFalse(sacrifice_is_prohibited(state, another))
        self.assertEqual(1, expire_end_of_turn_continuous_effects(state))
        self.assertFalse(sacrifice_is_prohibited(state, subject))
