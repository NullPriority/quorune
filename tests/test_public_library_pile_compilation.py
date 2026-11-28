from __future__ import annotations

from types import SimpleNamespace
import unittest

from quorune.carddb import CardRecord
from quorune.compiler.public_library_pile_templates import public_library_piles_template
from quorune.oracle_ir import compile_oracle_card
from quorune.public_library_piles_model import (
    DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION, PILE_CAPABILITY, PILE_MECHANIC, PILE_OPERATION, PUBLIC_SEARCH_SPLIT_VERSION,
)
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

    def test_distinct_search_split_has_closed_printed_and_persisted_dialects(self):
        from dataclasses import replace
        import json
        from unittest.mock import patch

        from common import ROOT
        from quorune.public_library_piles_model import (
            DISTINCT_PUBLIC_SEARCH_SPLIT_CAPABILITY, RevealedPileCard, SearchedLibraryPileState,
            distinct_public_search_split_effect, searched_pile_effect,
        )
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity

        text = (
            "Search your library for up to four cards with different names and reveal them. "
            "Target opponent chooses two of those cards. Put the chosen cards into your graveyard "
            "and the rest into your hand. Then shuffle."
        )
        record = spell("Distinct searched split", "fixture:distinct-searched-split", "{3}{U}", text)
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertEqual((distinct_public_search_split_effect(),), node.effects)
        self.assertIn(DISTINCT_PUBLIC_SEARCH_SPLIT_CAPABILITY, node.capability_dependencies)
        for variant in (
            text.replace("up to four", "five"),
            text.replace("different names", "the same name"),
            text.replace("chooses two", "chooses up to two"),
            text.replace("Target opponent", "An opponent"),
            text.removesuffix(" Then shuffle."),
        ):
            self.assertIsNone(public_library_piles_template(variant))
        for malformed in (
            {**node.effects[0], "schema_version": float(DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION)},
            {**node.effects[0], "schema_version": PUBLIC_SEARCH_SPLIT_VERSION},
            {**node.effects[0], "count": {"minimum": False, "maximum": 4}},
            {**node.effects[0], "count": {"minimum": 0, "maximum": 5}},
            {**node.effects[0], "public_search_chooser": "B"},
        ):
            self.assertEqual((), public_library_piles_node_capabilities(
                effects=(malformed,), target_schema=node.target_schema, mechanic_ids=node.mechanics,
            ))
        with patch("quorune.compiler.public_library_pile_templates._DISTINCT_SEARCHED_SPLIT_TEXT", ""):
            self.assertNotEqual("exact", self.compile(record).status)
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        cap = next(row for row in value["capabilities"] if row["id"] == DISTINCT_PUBLIC_SEARCH_SPLIT_CAPABILITY)
        cap.update(status="blocked", blockers=["Withdrawn by focused witness"])
        blocked = CapabilityRegistry(value)
        blocked.mark_evidence_verified("0" * 64)
        self.assertNotEqual("exact", compile_oracle_card(
            record, capability_registry=blocked, capability_profile="commander_review",
        ).status)

        cards = tuple(RevealedPileCard(LibrarySelectionObjectIdentity(
            f"object-{n}", f"object-{n}@0", f"ref-{n}",
        ), f"Card {n}") for n in range(4))
        legacy = SearchedLibraryPileState("A", "S1", "Legacy", "B", cards[:3], "commit", "B", "ref-0")
        self.assertNotIn("schema_version", legacy.to_dict())
        self.assertEqual(legacy, SearchedLibraryPileState.from_dict(legacy.to_dict()))
        self.assertEqual(2, searched_pile_effect(legacy)["schema_version"])
        current = SearchedLibraryPileState(
            "A", "S2", "Distinct", "B", cards, "commit", "B", ("ref-0", "ref-3"), schema_version=3,
        )
        self.assertEqual(("ref-1", "ref-2"), current.hand_refs())
        self.assertEqual(("ref-0", "ref-3"), current.graveyard_refs())
        self.assertEqual(current, SearchedLibraryPileState.from_dict(current.to_dict()))
        self.assertEqual(3, searched_pile_effect(current)["schema_version"])
        for malformed in (("ref-0",), ("ref-0", "ref-0"), ("ref-0", "foreign")):
            with self.assertRaises(ValueError):
                replace(current, chosen=malformed)
        with self.assertRaises(ValueError):
            SearchedLibraryPileState.from_dict({**legacy.to_dict(), "schema_version": 2})

    def test_distinct_effective_names_use_both_split_names_and_only_mdfc_front(self):
        from dataclasses import replace
        from quorune.errors import GameRuleError
        from quorune.selection.different_name_search import require_distinct_search_card_names

        inert = spell("Fixture", "fixture:name", "{U}", "")
        table = {
            "first": replace(inert, name="Split One // Shared", layout="split",
                             faces=({"name": "Split One"}, {"name": "Shared"})),
            "second": replace(inert, name="Shared // Split Two", layout="split",
                              faces=({"name": "Shared"}, {"name": "Split Two"})),
            "modal": replace(inert, name="Modal Front // Shared", layout="modal_dfc",
                             faces=({"name": "Modal Front"}, {"name": "Shared"})),
        }
        host = SimpleNamespace(card_record=lambda card: table[card.object_id])
        cards = {key: SimpleNamespace(object_id=key, zone="library") for key in table}
        with self.assertRaisesRegex(GameRuleError, "different names"):
            require_distinct_search_card_names(host, (cards["first"], cards["second"]))
        require_distinct_search_card_names(host, (cards["first"], cards["modal"]))

    def test_searched_split_is_exact_only_for_the_complete_opponent_procedure(self):
        from quorune.public_library_piles_model import public_search_split_effect, public_search_split_target_schema

        text = ("Search your library for three cards and reveal them. Target opponent chooses one. "
                "Put that card into your hand and the rest into your graveyard. Then shuffle.")
        ir = self.compile(spell("Searched split fixture", "fixture:searched-split", "{2}{U}", text))
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertEqual((public_search_split_effect(),), node.effects)
        self.assertEqual(public_search_split_target_schema(), node.target_schema)
        for bad in (
            text.replace("three cards", "up to three cards"),
            text.replace("Target opponent", "An opponent"),
            text.replace("your graveyard", "exile"),
            text.removesuffix(" Then shuffle."),
        ):
            self.assertIsNone(public_library_piles_template(bad))
        for malformed in (
            {**node.effects[0], "schema_version": float(PUBLIC_SEARCH_SPLIT_VERSION)},
            {**node.effects[0], "public_search_chooser": "B"},
            {**node.effects[0], "shuffle_after": False},
        ):
            self.assertFalse(public_library_piles_node_capabilities(
                effects=(malformed,), target_schema=node.target_schema, mechanic_ids=node.mechanics,
            ))
        self.assertFalse(public_library_piles_node_capabilities(
            effects=node.effects, target_schema={**node.target_schema, "player_relation": "any"},
            mechanic_ids=node.mechanics,
        ))

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
