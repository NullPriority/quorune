from __future__ import annotations

import unittest
from types import SimpleNamespace

from quorune.object_query import (
    ObjectQueryError,
    ObjectQueryResult,
    ObjectQuerySpec,
    exact_numeric_characteristic,
    query_objects,
)


class ObjectQueryTests(unittest.TestCase):
    def setUp(self):
        self.rows = (
            ObjectQueryResult(
                object_id="one",
                ref="C1",
                printed_name="Citadel",
                owner="A",
                controller="A",
                zone="battlefield",
                types=("artifact", "land"),
                subtypes=("swamp",),
                supertypes=(),
                colors=(),
                keywords=("indestructible",),
            ),
            ObjectQueryResult(
                object_id="two",
                ref="C2",
                printed_name="Familiar",
                owner="A",
                controller="B",
                zone="graveyard",
                types=("creature",),
                subtypes=("cat",),
                supertypes=(),
                colors=("B",),
                keywords=(),
                tapped=True,
            ),
        )

    def test_query_composes_zone_relation_and_effective_characteristics(self):
        self.assertEqual(
            ("C1",),
            tuple(
                row.ref
                for row in query_objects(
                    self.rows,
                    ObjectQuerySpec(
                        zones=("battlefield",),
                        controller="A",
                        types_all=("land",),
                        subtypes_all=("swamp",),
                        keywords_all=("indestructible",),
                    ),
                )
            ),
        )

    def test_types_any_is_distinct_from_types_all(self):
        self.assertEqual(
            ("C1", "C2"),
            tuple(
                row.ref
                for row in query_objects(
                    self.rows,
                    ObjectQuerySpec(types_any=("land", "creature")),
                )
            ),
        )
        self.assertEqual(
            (),
            tuple(
                row.ref
                for row in query_objects(
                    self.rows,
                    ObjectQuerySpec(types_all=("land", "creature")),
                )
            ),
        )

    def test_non_target_query_preserves_owner_controller_distinction(self):
        self.assertEqual(
            ("C2",),
            tuple(
                row.ref
                for row in query_objects(
                    self.rows,
                    ObjectQuerySpec(
                        zones=("graveyard",),
                        owner="A",
                        controller="B",
                        types_all=("creature",),
                        tapped=True,
                    ),
                )
            ),
        )

    def test_inputs_and_results_are_immutable(self):
        original = list(self.rows)
        result = query_objects(original, ObjectQuerySpec(types_all=("land",)))
        original.clear()
        self.assertEqual(("C1",), tuple(row.ref for row in result))

    def test_exact_numeric_characteristics_apply_counters_and_fail_closed(self):
        card = SimpleNamespace(
            annotations={"until_end_of_turn": {"power": 1}},
            counters={"+1/+1": 2, "-1/-1": 1},
        )
        self.assertEqual(
            5,
            exact_numeric_characteristic(
                card,
                {"power": "3", "toughness": "*"},
                "power",
            ),
        )
        self.assertIsNone(
            exact_numeric_characteristic(
                card,
                {"power": "3", "toughness": "*"},
                "toughness",
            )
        )
        with self.assertRaisesRegex(ValueError, "power or toughness"):
            exact_numeric_characteristic(card, {}, "loyalty")

    def test_color_all_any_and_known_visibility_are_distinct(self):
        rows = (
            ObjectQueryResult(
                "both", "BOTH", "Both", "A", "A", "battlefield",
                colors=("U", "R"),
            ),
            ObjectQueryResult(
                "red", "RED", "Red", "A", "A", "battlefield",
                colors=("R",),
            ),
            ObjectQueryResult(
                "hidden", "HIDDEN", "Hidden", "A", "A", "graveyard",
                colors=("U", "R"), known_to_actor=False,
            ),
        )
        self.assertEqual(
            ("BOTH",),
            tuple(
                row.ref
                for row in query_objects(
                    rows,
                    ObjectQuerySpec(
                        colors_all=("R", "U"), known_to_actor=True
                    ),
                )
            ),
        )
        self.assertEqual(
            ("BOTH", "RED"),
            tuple(
                row.ref
                for row in query_objects(
                    rows,
                    ObjectQuerySpec(colors_any=("R",), known_to_actor=True),
                )
            ),
        )

    def test_query_serialization_is_strict_and_canonical(self):
        spec = ObjectQuerySpec(
            zones=("GRAVEYARD", "battlefield"),
            types_all=("Creature",),
            colors_all=("u",),
            colors_any=("R",),
            known_to_actor=True,
        )
        serialized = spec.to_dict()
        self.assertEqual(spec, ObjectQuerySpec.from_dict(serialized))
        self.assertEqual(["battlefield", "graveyard"], serialized["zones"])
        self.assertEqual(["U"], serialized["colors_all"])
        self.assertEqual([], serialized["types_any"])
        malformed = dict(serialized)
        malformed["surprise"] = True
        with self.assertRaisesRegex(ObjectQueryError, "unknown surprise"):
            ObjectQuerySpec.from_dict(malformed)

    def test_opponent_exclusion_and_color_cardinality_are_canonical(self):
        rows = (
            ObjectQueryResult(
                "own", "OWN", "Own", "A", "A", "battlefield",
                colors=("R", "U"),
            ),
            ObjectQueryResult(
                "mono", "MONO", "Mono", "B", "B", "battlefield",
                colors=("U",),
            ),
            ObjectQueryResult(
                "multi", "MULTI", "Multi", "C", "C", "battlefield",
                colors=("G", "W"),
            ),
        )
        spec = ObjectQuerySpec(
            zones=("battlefield",),
            excluded_controllers=("A",),
            minimum_color_count=2,
        )
        self.assertEqual(
            ("MULTI",),
            tuple(row.ref for row in query_objects(rows, spec)),
        )
        serialized = spec.to_dict()
        self.assertEqual(["A"], serialized["excluded_controllers"])
        self.assertEqual(2, serialized["minimum_color_count"])
        self.assertEqual(spec, ObjectQuerySpec.from_dict(serialized))
        with self.assertRaisesRegex(ObjectQueryError, "minimum_color_count"):
            ObjectQuerySpec(minimum_color_count=True)
        with self.assertRaisesRegex(ObjectQueryError, "unique strings"):
            ObjectQuerySpec(excluded_controllers=("A", "A"))

    def test_legacy_query_shape_round_trips_without_changing_record_bytes(self):
        serialized = ObjectQuerySpec(types_all=("creature",)).to_dict()
        serialized.pop("types_any")
        restored = ObjectQuerySpec.from_dict(serialized)
        self.assertEqual(serialized, restored.to_dict())
        self.assertEqual([], restored.canonical_dict()["types_any"])

    def test_term_lists_reject_nonstrings_empty_values_and_case_duplicates(self):
        malformed_values = (
            (1,),
            (True,),
            ({"type": "creature"},),
            ("",),
            ("Creature", "creature"),
        )
        for values in malformed_values:
            with self.subTest(values=values), self.assertRaises(
                ObjectQueryError
            ):
                ObjectQuerySpec(types_all=values)

        serialized = ObjectQuerySpec().to_dict()
        for values in malformed_values:
            malformed = dict(serialized)
            malformed["keywords_all"] = list(values)
            with self.subTest(persisted=values), self.assertRaises(
                ObjectQueryError
            ):
                ObjectQuerySpec.from_dict(malformed)

    def test_identity_fields_reject_coercion_and_empty_values(self):
        for field_name in ("owner", "controller", "exclude_ref"):
            for value in (1, True, {"seat": "A"}, ""):
                with self.subTest(
                    field=field_name, value=value
                ), self.assertRaises(ObjectQueryError):
                    ObjectQuerySpec(**{field_name: value})

        canonical = ObjectQuerySpec(owner="A", controller="B")
        self.assertEqual("A", canonical.owner)
        self.assertEqual("B", canonical.controller)
