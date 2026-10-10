from __future__ import annotations

import json
import unittest

from quorune.choice_forms import (
    build_action_form,
    delegated_choice_fields,
)


class ChoiceFormTests(unittest.TestCase):
    def test_public_quantity_mana_form_preserves_allocation_constraints(self):
        action={"id":"activate:source:ab1","action":"activate","choice_schema":{
            "mana_output":{"type":"mana_bundle","label":"Mana to add",
                "allowed_colors":["G","U"],"total":3,"single_color":False}}}
        form=build_action_form(action,decision_kind="priority",context={})
        field=form['fields'][0]
        self.assertEqual('mana_modes',field['control'])
        self.assertEqual(['G','U'],field['allowed_colors'])
        self.assertEqual(3,field['total']);self.assertFalse(field['single_color'])
        self.assertEqual({'G':3},field['default'])
        self.assertEqual({'mana_output'},delegated_choice_fields(action,decision_kind='priority',context={}))

    def test_copy_retention_forms_expose_indices_not_internal_snapshots(self):
        schema = {"groups": [{"id": "target", "min": 2, "max": 2, "legal_refs": ["CURRENT"]}]}
        context = {"default_target_groups": {"target": ["OLD", "OTHER"]},
                   "target_snapshots": {"OLD": {"object_id": "private-id", "zone_change_counter": 0}}}
        action = {"id": "choose", "action": "choose", "choice_schema": {
            "field": "targets", "optional": True, "default": ["OLD", "OTHER"],
            "target_schema": schema, "may_retain_each_default": True,
        }}
        form = build_action_form(action, decision_kind="semantic.choice", context=context)
        field = form["fields"][0]
        self.assertEqual("copy_targets", field["control"])
        self.assertTrue(field["single_copy"])
        self.assertTrue(field["may_retain_each_default"])
        self.assertEqual(context["default_target_groups"], field["copies"][0]["default_target_groups"])
        self.assertNotIn("private-id", json.dumps(form))
        self.assertEqual({"targets"}, delegated_choice_fields(action, decision_kind="semantic.choice", context=context))
        legacy = {**action, "choice_schema": {key: value for key, value in action["choice_schema"].items()
                                             if key != "may_retain_each_default"}}
        legacy_form = build_action_form(legacy, decision_kind="semantic.choice", context=context)
        self.assertEqual("targets", legacy_form["fields"][0]["control"])
        self.assertNotIn("single_copy", legacy_form["fields"][0])

    def test_disjoint_object_cardinalities_are_preserved_in_projected_form(self):
        action={'id':'choose','action':'choose','choice_schema':{'field':'cards','legal_refs':['A01','A02'],'minimum':0,'maximum':2,'optional':True,'distinct':True,'allowed_cardinalities':[0,2]}}
        form=build_action_form(action,decision_kind='semantic.choice',context={})
        field=form['fields'][0]
        self.assertEqual('refs',field['control'])
        self.assertEqual([0,2],field['allowed_cardinalities'])
        self.assertTrue(field['distinct'])
        self.assertEqual({'cards'},delegated_choice_fields(action,decision_kind='semantic.choice',context={}))
        legacy={**action,'choice_schema':{key:value for key,value in action['choice_schema'].items()if key!='allowed_cardinalities'}}
        legacy_field=build_action_form(legacy,decision_kind='semantic.choice',context={})['fields'][0]
        self.assertNotIn('allowed_cardinalities',legacy_field)
        self.assertNotIn('distinct',legacy_field)

    def test_ordered_partition_preserves_private_scry_groups(self):
        action = {
            "id": "choose",
            "action": "choose",
            "choice_schema": {
                "field": "cards",
                "shape": "ordered_partition",
                "legal_refs": ["A01", "A02"],
                "partitions": {
                    "top": {"order": "top_to_bottom"},
                    "bottom": {"order": "bottom_to_top"},
                },
                "complete": True,
                "distinct": True,
            },
        }
        form = build_action_form(
            action,
            decision_kind="semantic.choice",
            context={
                "objects": [
                    {"id": "A01", "name": "First"},
                    {"id": "A02", "name": "Second"},
                ]
            },
        )

        field = form["fields"][0]
        self.assertEqual("ordered_partition", field["control"])
        self.assertEqual(
            ["A01", "A02"],
            [option["value"] for option in field["options"]],
        )
        self.assertEqual(
            ["First", "Second"],
            [option["label"] for option in field["options"]],
        )
        self.assertEqual(
            {"cards"},
            delegated_choice_fields(
                action,
                decision_kind="semantic.choice",
                context={},
            ),
        )

    def test_simple_private_ref_array_is_normalized(self):
        action = {
            "id": "choose",
            "action": "choose",
            "choice_schema": {
                "field": "search_cards",
                "shape": "ref_array",
                "minimum": 0,
                "maximum": 2,
                "legal_refs": ["A01", "A02"],
                "rules_may_fail_to_find": True,
            },
        }
        context = {
            "search_cards": [
                {"id": "A01", "name": "Forest"},
                {"id": "A02", "name": "Island"},
            ]
        }

        form = build_action_form(
            action,
            decision_kind="semantic.search",
            context=context,
        )

        self.assertEqual(1, form["v"])
        self.assertEqual("refs", form["fields"][0]["control"])
        self.assertEqual(
            ["Forest", "Island"],
            [option["label"] for option in form["fields"][0]["options"]],
        )
        self.assertEqual(
            {"search_cards"},
            delegated_choice_fields(
                action,
                decision_kind="semantic.search",
                context=context,
            ),
        )
        json.dumps(form)

    def test_cost_variants_delegate_selector_cost_and_targets(self):
        action = {
            "id": "cast:A17",
            "action": "cast",
            "cost_options": [
                {
                    "id": "normal",
                    "label": "Normal cost",
                    "choice_schema": {
                        "x": {"type": "integer", "minimum": 0, "maximum": 4}
                    },
                },
                {
                    "id": "pitch",
                    "label": "Pitch cost",
                    "choice_schema": {
                        "exile_card": {
                            "type": "object_ref",
                            "legal_refs": ["A03"],
                        }
                    },
                    "target_schema": {
                        "groups": [
                            {
                                "id": "target",
                                "min": 1,
                                "max": 1,
                                "legal_refs": ["B09"],
                            }
                        ]
                    },
                },
            ],
        }

        form = build_action_form(
            action,
            decision_kind="priority",
            context={},
        )

        self.assertEqual("cost_option", form["variants"]["field"])
        self.assertEqual(2, len(form["variants"]["options"]))
        self.assertEqual(
            {"cost_option", "x", "exile_card", "targets", "modes"},
            delegated_choice_fields(
                action,
                decision_kind="priority",
                context={},
            ),
        )

    def test_persisted_fetch_schema_maps_to_executable_fields(self):
        action = {
            "id": "choose",
            "action": "choose",
            "choice_schema": {
                "search_candidates": ["A04", "A05"],
                "may_fail_to_find": True,
                "entry_pay_life": "boolean",
            },
        }

        form = build_action_form(
            action,
            decision_kind="search.fetch",
            context={
                "search_cards": [
                    {"id": "A04", "name": "Breeding Pool"},
                    {"id": "A05", "name": "Island"},
                ]
            },
        )

        self.assertEqual(
            ["search_card", "entry_pay_life"],
            [field["name"] for field in form["fields"]],
        )
        self.assertEqual(
            {"search_card", "entry_pay_life"},
            delegated_choice_fields(
                action,
                decision_kind="search.fetch",
                context={},
            ),
        )

    def test_special_decisions_have_scoped_forms(self):
        cases = [
            (
                {"id": "bottom", "action": "bottom"},
                "mulligan.bottom",
                {"count": 1, "hand": [{"id": "A01", "name": "Forest"}]},
                "cards",
            ),
            (
                {"id": "discard", "action": "discard"},
                "cleanup.discard",
                {"count": 1, "hand": [{"id": "A01", "name": "Forest"}]},
                "cards",
            ),
            (
                {"id": "order", "action": "order"},
                "trigger.order",
                {"triggers": [{"id": "S1", "label": "First"}]},
                "triggers",
            ),
            (
                {"id": "choose", "action": "choose"},
                "state.legend",
                {"keep_one": ["A10", "A11"]},
                "card",
            ),
        ]
        for action, kind, context, expected in cases:
            with self.subTest(kind=kind):
                form = build_action_form(
                    action,
                    decision_kind=kind,
                    context=context,
                )
                self.assertEqual(expected, form["fields"][0]["name"])
                self.assertEqual(
                    {expected},
                    delegated_choice_fields(
                        action,
                        decision_kind=kind,
                        context=context,
                    ),
                )

    def test_target_form_delegates_only_targets_and_modes(self):
        action = {
            "id": "cast:A01",
            "action": "cast",
            "target_schema": {
                "legal_modes": ["damage", "destroy"],
                "min_modes": 1,
                "max_modes": 1,
                "mode_schemas": {
                    "damage": {
                        "groups": [
                            {
                                "id": "creature",
                                "min": 1,
                                "max": 1,
                                "legal_refs": ["B01"],
                            }
                        ]
                    },
                    "destroy": {
                        "groups": [
                            {
                                "id": "artifact",
                                "min": 1,
                                "max": 1,
                                "legal_refs": ["C01"],
                            }
                        ]
                    },
                },
            },
        }

        form = build_action_form(
            action,
            decision_kind="priority",
            context={},
        )

        self.assertEqual("targets", form["fields"][0]["control"])
        self.assertEqual(
            {"targets", "modes"},
            delegated_choice_fields(
                action,
                decision_kind="priority",
                context={},
            ),
        )

    def test_object_map_preserves_numeric_required_count(self):
        form = build_action_form(
            {
                "id": "choose",
                "action": "choose",
                "choice_schema": {
                    "field": "decisions",
                    "shape": "object_map",
                    "legal_refs": ["A01", "A02", "A03"],
                    "required": 2,
                    "legal_values": ["pay_life", "top"],
                },
            },
            decision_kind="semantic.sylvan_library",
            context={},
        )

        self.assertEqual(2, form["fields"][0]["minimum"])

    def test_mana_modes_delegate_only_an_exact_server_issued_bundle(self):
        action = {
            "id": "activate:A01:ab1",
            "action": "activate",
            "choice_schema": {
                "mana_output": {
                    "type": "mana_bundle",
                    "label": "Mana to add",
                    "options": [
                        {"value": {"U": 1}, "label": "Add {U}"},
                        {"value": {"B": 1}, "label": "Add {B}"},
                    ],
                }
            },
        }

        form = build_action_form(
            action,
            decision_kind="priority",
            context={},
        )

        self.assertEqual("mana_modes", form["fields"][0]["control"])
        self.assertEqual({"U": 1}, form["fields"][0]["default"])
        self.assertEqual(
            {"mana_output"},
            delegated_choice_fields(
                action,
                decision_kind="priority",
                context={},
            ),
        )


if __name__ == "__main__":
    unittest.main()
