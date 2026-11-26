from __future__ import annotations

import copy
import json
import unittest

from quorune.carddb import CardRecord
from quorune.compiler.library_search_templates import (
    FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_LIBRARY_SEARCH_MECHANIC_ID,
)
from quorune.compiler.saga_printed_cost_search import (
    printed_mana_cost_artifact_search_template,
)
from quorune.library_search_model import (
    PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
)
from quorune.model import CardInstance, StackItem
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.rules.library_search_capability_shapes import (
    fixed_library_search_covered_mechanics,
    fixed_library_search_node_capabilities,
)
from quorune.selection.searching import (
    HiddenSearchOwnerMixin,
    SemanticSearchCompletionContext,
)


ORACLE_CHAPTER = (
    "Search your library for an artifact card with mana cost {0} or {1}, "
    "put it onto the battlefield, then shuffle."
)


def _record(
    mana_cost: str,
    *,
    layout: str = "normal",
    faces: tuple[dict[str, str], ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:printed-cost-search",
        name="Printed Cost Search Fixture",
        mana_cost=mana_cost,
        mana_value=1.0,
        type_line="Artifact",
        oracle_text="",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout=layout,
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=faces,
        raw={},
    )


def _row(*, zone: str = "library") -> ObjectQueryResult:
    return ObjectQueryResult(
        object_id="printed-cost-fixture",
        ref="B-PRINTED-COST-FIXTURE",
        printed_name="Printed Cost Search Fixture",
        owner="B",
        controller="B",
        zone=zone,
        types=("artifact",),
    )


class _SearchLogHarness(HiddenSearchOwnerMixin):
    def __init__(self):
        self.events = []
        self.shuffles = []
        self.continuations = []

    def _log(
        self,
        actor,
        code,
        summary,
        details=None,
        *,
        visibility=None,
        importance=1,
        changed_objects=(),
        changed_players=(),
    ):
        self.events.append(
            {
                "actor": actor,
                "code": code,
                "summary": summary,
                "details": dict(details or {}),
                "visibility": list(visibility or ("A", "B")),
                "importance": importance,
                "changed_objects": list(changed_objects),
                "changed_players": list(changed_players),
            }
        )

    def shuffle_library(self, seat, *, reason):
        self.shuffles.append((seat, reason))

    def _continue_resolution(self, **kwargs):
        self.continuations.append(kwargs)


def _search_context(effect: dict, item: StackItem) -> SemanticSearchCompletionContext:
    return SemanticSearchCompletionContext(
        seat="B",
        response={},
        continuation={"remaining": []},
        stack_ref=item.ref,
        item=item,
        frame={"instruction_pointer": 0},
        effect=effect,
        options=frozenset(),
    )


class PrintedManaCostArtifactSearchCompilerTests(unittest.TestCase):
    def test_exact_instruction_compiles_a_private_single_result_search(self):
        compiled = printed_mana_cost_artifact_search_template(ORACLE_CHAPTER)

        self.assertIsNotNone(compiled)
        assert compiled is not None
        template, effects, target_schema, mechanics = compiled
        self.assertEqual("printed-mana-cost-artifact-search-v1", template)
        self.assertIsNone(target_schema)
        self.assertEqual(
            (
                FIXED_LIBRARY_SEARCH_MECHANIC_ID,
                PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID,
            ),
            mechanics,
        )
        self.assertEqual(
            {
                "op": "search",
                "zone": "library",
                "selector": {
                    "types": ["artifact"],
                    "predicate": PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
                },
                "count": {"minimum": 1, "maximum": 1},
                "destination": "battlefield",
                "shuffle_after": True,
            },
            effects[0],
        )
        self.assertNotIn("reveal", effects[0])
        self.assertNotIn("searching_player", effects[0])

    def test_nearby_mana_value_and_open_search_wording_remain_uncompiled(self):
        for text in (
            ORACLE_CHAPTER.replace("mana cost {0} or {1}", "mana value 0 or 1"),
            ORACLE_CHAPTER.replace("artifact card", "artifact cards"),
            ORACLE_CHAPTER.replace(", then shuffle.", "."),
            ORACLE_CHAPTER.replace("{0} or {1}", "{0}, {1}, or {X}"),
            ORACLE_CHAPTER + " Reveal it.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(printed_mana_cost_artifact_search_template(text))

    def test_capability_shape_is_specific_to_printed_cost_artifacts(self):
        compiled = printed_mana_cost_artifact_search_template(ORACLE_CHAPTER)
        assert compiled is not None
        _, effects, _, mechanics = compiled

        self.assertEqual(
            (
                FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
                PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
            ),
            fixed_library_search_node_capabilities(
                effects=effects,
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )
        self.assertIn(
            PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID,
            fixed_library_search_covered_mechanics(
                (
                    FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
                    PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
                )
            ),
        )

        immutable_shape = copy.deepcopy(effects[0])
        immutable_shape["selector"]["types"] = ("artifact",)
        self.assertEqual(
            (
                FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
                PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
            ),
            fixed_library_search_node_capabilities(
                effects=(immutable_shape,),
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )

        without_predicate_mechanic = tuple(
            value
            for value in mechanics
            if value != PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID
        )
        self.assertEqual(
            (),
            fixed_library_search_node_capabilities(
                effects=effects,
                target_schema=None,
                mechanic_ids=without_predicate_mechanic,
            ),
        )

        variants = []
        wrong_type = copy.deepcopy(effects[0])
        wrong_type["selector"]["types"] = ["land"]
        variants.append(wrong_type)
        old_predicate = copy.deepcopy(effects[0])
        old_predicate["selector"]["predicate"] = "mana_cost_0_or_1"
        variants.append(old_predicate)
        open_selector = copy.deepcopy(effects[0])
        open_selector["selector"]["mana_value"] = 1
        variants.append(open_selector)
        legacy_reveal = copy.deepcopy(effects[0])
        legacy_reveal["reveal"] = True
        variants.append(legacy_reveal)
        for effect in variants:
            with self.subTest(effect=effect):
                self.assertEqual(
                    (),
                    fixed_library_search_node_capabilities(
                        effects=(effect,),
                        target_schema=None,
                        mechanic_ids=mechanics,
                    ),
                )


class PrintedManaCostArtifactSearchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.row = _row()
        self.query = ObjectQuerySpec(
            zones=("library",),
            types_all=("artifact",),
        )
        self.new_selector = {
            "types": ["artifact"],
            "predicate": PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
        }

    def matches(self, record, selector=None, *, row=None):
        return HiddenSearchOwnerMixin._search_leaf_matches(
            row or self.row,
            record,
            self.query,
            selector or self.new_selector,
        )

    def test_single_face_cost_is_exact_not_mana_value(self):
        for cost in ("{0}", "{1}"):
            with self.subTest(cost=cost):
                self.assertTrue(self.matches(_record(cost)))
                self.assertTrue(
                    self.matches(
                        _record(cost),
                        {"types": ["artifact"], "predicate": "mana_cost_0_or_1"},
                    )
                )
        for cost in ("{X}", "{U}", "{2}", "{0}{U}"):
            with self.subTest(cost=cost):
                self.assertFalse(self.matches(_record(cost)))

    def test_double_faced_library_search_uses_front_face_and_preserves_legacy_path(self):
        front_zero = _record(
            "{0} // {6}",
            layout="transform",
            faces=({"mana_cost": "{0}"}, {"mana_cost": "{6}"}),
        )
        front_one = _record(
            "{1} // {5}{R}",
            layout="modal_dfc",
            faces=({"mana_cost": "{1}"}, {"mana_cost": "{5}{R}"}),
        )
        front_two_back_zero = _record(
            "{2} // {0}",
            layout="transform",
            faces=({"mana_cost": "{2}"}, {"mana_cost": "{0}"}),
        )

        self.assertTrue(self.matches(front_zero))
        self.assertTrue(self.matches(front_one))
        self.assertFalse(self.matches(front_two_back_zero))
        # Historical persisted descriptors keep their former top-level behavior.
        self.assertFalse(
            self.matches(
                front_zero,
                {"types": ["artifact"], "predicate": "mana_cost_0_or_1"},
            )
        )
        self.assertFalse(
            self.matches(
                front_one,
                {"types": ["artifact"], "predicate": "mana_cost_0_or_1"},
            )
        )

    def test_printed_cost_predicate_is_restrictive_and_library_scoped(self):
        self.assertTrue(HiddenSearchOwnerMixin._search_is_restrictive(self.new_selector))
        self.assertFalse(
            self.matches(_record("{1}"), row=_row(zone="graveyard"))
        )

    def test_nonrevealing_search_logs_only_actual_public_movement(self):
        effect = printed_mana_cost_artifact_search_template(ORACLE_CHAPTER)[1][0]
        hidden_card = CardInstance(
            object_id="private-artifact-object",
            ref="B-PRIVATE-ARTIFACT",
            oracle_id="fixture:private-artifact",
            printed_name="Private Artifact Candidate",
            owner="B",
            controller="B",
            zone="hand",
        )
        item = StackItem(
            stack_id="stack-1",
            ref="S-CHAPTER-III",
            kind="triggered_ability",
            controller="B",
            label="Chapter III",
        )
        harness = _SearchLogHarness()

        harness._record_semantic_search_result(
            "choice-1",
            _search_context(effect, item),
            destination_spec="battlefield",
            destination="battlefield",
            moved=(hidden_card,),
        )

        public_event, private_event = harness.events
        self.assertEqual("library.search", public_event["code"])
        public_payload = json.dumps(public_event)
        self.assertNotIn(hidden_card.ref, public_payload)
        self.assertNotIn(hidden_card.printed_name, public_payload)
        self.assertNotIn(hidden_card.object_id, public_payload)
        self.assertEqual([], public_event["details"]["objects"])
        self.assertEqual(0, public_event["details"]["count"])
        self.assertIn("found 0 card(s)", public_event["summary"])
        self.assertEqual(1, private_event["details"]["count"])
        self.assertEqual(["B-PRIVATE-ARTIFACT"], private_event["details"]["objects"])
        self.assertEqual([], public_event["changed_objects"])
        self.assertEqual([("B", "Chapter III resolved")], harness.shuffles)

        moved_public = CardInstance(
            object_id="public-artifact-object",
            ref="B-PUBLIC-ARTIFACT",
            oracle_id="fixture:public-artifact",
            printed_name="Public Artifact Candidate",
            owner="B",
            controller="B",
            zone="battlefield",
        )
        public_harness = _SearchLogHarness()
        public_item = StackItem(
            stack_id="stack-2",
            ref="S-CHAPTER-III-2",
            kind="triggered_ability",
            controller="B",
            label="Chapter III",
        )
        public_harness._record_semantic_search_result(
            "choice-2",
            _search_context(effect, public_item),
            destination_spec="battlefield",
            destination="battlefield",
            moved=(moved_public,),
        )
        public_payload = json.dumps(public_harness.events[0])
        self.assertIn(moved_public.ref, public_payload)
        self.assertIn(moved_public.object_id, public_payload)

    def test_legacy_reveal_search_keeps_its_historical_public_event_shape(self):
        legacy_effect = {
            "op": "search",
            "zone": "library",
            "selector": {"types": ["artifact"], "predicate": "mana_cost_0_or_1"},
            "count": {"minimum": 1, "maximum": 1},
            "destination": "battlefield",
            "reveal": True,
            "shuffle_after": True,
        }
        hidden_card = CardInstance(
            object_id="legacy-public-object",
            ref="B-LEGACY-REVEALED",
            oracle_id="fixture:legacy-artifact",
            printed_name="Legacy Revealed Artifact",
            owner="B",
            controller="B",
            zone="hand",
        )
        item = StackItem(
            stack_id="stack-legacy",
            ref="S-LEGACY",
            kind="triggered_ability",
            controller="B",
            label="Legacy search",
        )
        harness = _SearchLogHarness()

        harness._record_semantic_search_result(
            "choice-legacy",
            _search_context(legacy_effect, item),
            destination_spec="battlefield",
            destination="battlefield",
            moved=(hidden_card,),
        )

        public_payload = json.dumps(harness.events[0])
        self.assertIn(hidden_card.ref, public_payload)
        self.assertIn(hidden_card.object_id, public_payload)


if __name__ == "__main__":
    unittest.main()
