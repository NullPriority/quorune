from __future__ import annotations

from dataclasses import replace
import unittest

from quorune.compiler.fixed_counter_trigger_nodes import (
    fixed_counter_trigger_binding,
    fixed_typed_event_effect_trigger_node,
)
from quorune.compiler.fixed_public_event_trigger_bindings import (
    fixed_public_event_binding_spec,
)
from quorune.compiler.ir_model import SourceSpan
from quorune.compiler.opponent_library_exile_templates import (
    EACH_OPPONENT_TOP_EXILE_MECHANIC,
    OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC,
    controlled_opponent_library_exile_play_result,
    each_opponent_library_top_exile_result,
)
from quorune.compiler.trigger_event_vocabulary import FixedCounterTriggerEvent
from quorune.opponent_library_exile_model import (
    OPPONENT_LIBRARY_EXILE_EVENT,
    each_opponent_top_exile_effect,
    opponent_library_exile_play_effect,
)


ATTACK_LINE = (
    "Whenever The Madcap Jester attacks, each opponent exiles the top card "
    "of their library."
)
PLAY_LINE = (
    "Whenever a spell or ability you control causes one or more cards to be "
    "exiled from an opponent's library, you may play those cards until the "
    "end of your next turn. Mana of any type can be spent to cast spells this way."
)


def _effect_template(_text, *, card_name):
    return None, (), None, ()


def _compile_node(line: str, *, card_name: str):
    return fixed_typed_event_effect_trigger_node(
        node_id="madcap-fixture:trigger",
        line=line,
        material_line=line,
        span=SourceSpan(start=0, end=len(line), line=1),
        card_name=card_name,
        trusted_mechanics=frozenset(),
        capability_registry=None,
        capability_profile="commander_review",
        residuals=[],
        effect_template=_effect_template,
    )


class MadcapJesterCompilerTests(unittest.TestCase):
    def test_named_attack_emits_explicit_self_only_scope_and_each_opponent_effect(self):
        node = _compile_node(ATTACK_LINE, card_name="The Madcap Jester")

        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual("creature.attacks", node.event)
        self.assertEqual(
            {"field": "card", "op": "eq", "value": "$source.ref"},
            node.event_condition,
        )
        self.assertEqual((each_opponent_top_exile_effect(),), node.effects)
        self.assertEqual(EACH_OPPONENT_TOP_EXILE_MECHANIC, node.mechanics[-1])

    def test_exile_play_binding_uses_the_causal_effect_controller_occurrence(self):
        binding_spec = fixed_public_event_binding_spec(PLAY_LINE, card_name="The Madcap Jester")
        self.assertIsNotNone(binding_spec)
        assert binding_spec is not None
        self.assertEqual("library.opponent_exile_batch", binding_spec.event)
        self.assertEqual(
            {"field": "effect_controller", "op": "eq", "value": "$source.controller"},
            binding_spec.condition,
        )

        node = _compile_node(PLAY_LINE, card_name="The Madcap Jester")
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(OPPONENT_LIBRARY_EXILE_EVENT, node.event)
        self.assertEqual(binding_spec.condition, node.event_condition)
        self.assertEqual((opponent_library_exile_play_effect(),), node.effects)
        self.assertEqual(OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC, node.mechanics[-1])

    def test_result_templates_reject_open_or_changed_occurrences(self):
        attack_binding = fixed_counter_trigger_binding(
            ATTACK_LINE,
            card_name="The Madcap Jester",
        )
        play_binding = fixed_counter_trigger_binding(
            PLAY_LINE,
            card_name="The Madcap Jester",
        )
        self.assertIsNotNone(attack_binding)
        self.assertIsNotNone(play_binding)
        assert attack_binding is not None and play_binding is not None

        self.assertIsNotNone(
            each_opponent_library_top_exile_result(
                attack_binding,
                attack_binding.body,
            )
        )
        self.assertIsNone(
            each_opponent_library_top_exile_result(
                attack_binding,
                "each opponent exiles the top two cards of their library.",
            )
        )
        self.assertIsNotNone(
            controlled_opponent_library_exile_play_result(
                play_binding,
                play_binding.body,
            )
        )
        self.assertIsNone(
            controlled_opponent_library_exile_play_result(
                replace(
                    play_binding,
                    public_condition={
                        "field": "effect_controller",
                        "op": "ne",
                        "value": "$source.controller",
                    },
                ),
                play_binding.body,
            )
        )


if __name__ == "__main__":
    unittest.main()
