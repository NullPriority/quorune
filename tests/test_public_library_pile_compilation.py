from __future__ import annotations

from types import SimpleNamespace
import unittest

from quorune.carddb import CardRecord
from quorune.compiler.public_library_pile_templates import public_library_piles_template
from quorune.oracle_ir import compile_oracle_card
from quorune.public_library_piles_model import PILE_CAPABILITY, PILE_MECHANIC, PILE_OPERATION
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_result_capability_shapes import (
    is_closed_public_library_piles_program,
    library_result_payload_dependencies,
    public_library_piles_node_capabilities,
)


FACT = (
    "Reveal the top five cards of your library. An opponent separates those cards into two piles. "
    "Put one pile into your hand and the other into your graveyard."
)
STEAM = (
    "Reveal the top five cards of your library and separate them into two piles. "
    "An opponent chooses one of those piles. Put that pile into your hand and the other into your graveyard."
)


def spell(name, oracle_id, mana_cost, text):
    colors = ("U", "R") if "{R}" in mana_cost else ("U",)
    return CardRecord(
        oracle_id=oracle_id, name=name, mana_cost=mana_cost, mana_value=4, type_line="Instant",
        oracle_text=text, power=None, toughness=None, loyalty=None, defense=None,
        colors=colors, color_identity=colors, keywords=(), produced_mana=(),
        layout="normal", released_at="2023-01-13", legalities={"commander": "legal"},
        faces=(), raw={},
    )


class PublicLibraryPileCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def test_complete_printed_spell_compilation_keeps_opponent_roles(self):
        examples = (
            (spell("Fact or Fiction", "437b2dab-15e0-4b9a-a204-58622d37a3b3", "{3}{U}", FACT), "divider"),
            (spell("Steam Augury", "0aa556a6-66aa-42f1-ba41-0001e10c20a4", "{2}{U}{R}", STEAM), "chooser"),
        )
        for record, role in examples:
            with self.subTest(record=record.name):
                effect = {
                    "op": PILE_OPERATION, "player": "$controller", "schema_version": 1,
                    "look_count": 5, "opponent_role": role,
                }
                ir = self.compile(record)
                matching = [
                    node for face in ir.faces for node in face.nodes
                    if node.template_id == "public-library-piles-v1"
                ]
                self.assertEqual(1, len(matching))
                node, = matching
                self.assertEqual((effect,), node.effects)
                self.assertIn(PILE_MECHANIC, node.mechanics)
                self.assertIn(PILE_CAPABILITY, node.capability_dependencies)
                self.assertIsNone(node.target_schema)
                self.assertEqual(("stack", "resolve"), (node.active_zone, node.event))

    def test_inexact_or_partially_matched_pile_spells_remain_residual(self):
        alternatives = (
            FACT.replace("top five", "top six"),
            FACT.replace("Reveal", "Look at"),
            FACT.replace("An opponent", "Target opponent"),
            FACT.replace("two piles", "three piles"),
            FACT.replace("two piles.", "a face-down pile and a face-up pile."),
            STEAM.replace("top five", "top X plus one"),
            STEAM + " Draw a card.",
            STEAM + "\nDraw a card.",
            "Draw a card. " + FACT,
            "Draw a card.\n" + FACT,
        )
        for text in alternatives:
            with self.subTest(text=text):
                self.assertIsNone(public_library_piles_template(text))
                record = spell("Unrepresented pile variant", "a-pile-variant", "{3}{U}", text)
                ir = self.compile(record)
                self.assertFalse(any(
                    node.template_id == "public-library-piles-v1"
                    for face in ir.faces for node in face.nodes
                ))
                self.assertNotEqual("exact", ir.status)

    def test_strict_payload_and_whole_spell_shape(self):
        _, effects, _, mechanics = public_library_piles_template(FACT)
        effect, = effects
        self.assertEqual((PILE_CAPABILITY,), library_result_payload_dependencies(effect))
        self.assertEqual((PILE_CAPABILITY,), public_library_piles_node_capabilities(
            effects=effects, target_schema=None, mechanic_ids=mechanics,
        ))
        spelling = SimpleNamespace(
            effects=effects, target_schema=None, coverage=mechanics, active_zone="stack", event="resolve",
            capability_dependencies=(PILE_CAPABILITY,),
        )
        self.assertTrue(is_closed_public_library_piles_program(spelling))
        for update in (
            {"capability_dependencies": ()},
            {"coverage": ()},
            {"effects": (*effects, *effects)},
            {"target_schema": {}},
            {"event": "activate"},
            {"active_zone": "battlefield"},
        ):
            self.assertFalse(is_closed_public_library_piles_program(SimpleNamespace(**(vars(spelling) | update))))
        for update in (
            {"schema_version": True},
            {"schema_version": 2},
            {"look_count": 4},
            {"opponent_role": "both"},
            {"player": "B"},
            {"extra": 1},
        ):
            with self.subTest(update=update):
                malformed = effect | update
                self.assertEqual((), public_library_piles_node_capabilities(
                    effects=(malformed,), target_schema=None, mechanic_ids=mechanics,
                ))
                with self.assertRaises(ValueError):
                    library_result_payload_dependencies(malformed)
