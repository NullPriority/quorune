"""Independent CR702.166/607/601.2/400.7 declaration and price boundaries."""
from dataclasses import replace
import unittest
from unittest.mock import patch
import test_zone_trigger_events as zone_witnesses
from quorune.bargain_model import FixedBargainSpec
from quorune.declared_cast_cost_model import DeclaredCastCostFacts, DeclaredCastCostChoice, DeclaredCastCostKind
from quorune.declared_cost_conditions import DeclaredCastCostCondition
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.rules.casting.bargain_costs import bargain_sacrifice_cost
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost, AdditionalCostError
from quorune.sacrifice_union_model import SacrificePredicateUnion, controlled_permanent_predicate


class BargainModelTests(unittest.TestCase):
    def test_bargain_declared_instances_roundtrip_and_link_only_their_own_choice(self):
        spec = FixedBargainSpec()
        self.assertEqual(spec, FixedBargainSpec.from_dict(spec.to_dict()))
        condition = DeclaredCastCostCondition(DeclaredCastCostKind.BARGAIN, spec.instance_id)
        self.assertTrue(condition.matches(spec.declaration(True).to_dict()))
        self.assertFalse(condition.matches(spec.declaration(False).to_dict()))
        facts = DeclaredCastCostFacts((DeclaredCastCostChoice(DeclaredCastCostKind.BARGAIN, 'other', True),
            DeclaredCastCostChoice(DeclaredCastCostKind.BARGAIN, spec.instance_id, False)))
        self.assertFalse(condition.matches(facts.to_dict()))
        self.assertEqual(('other',), facts.chosen_instances())
        with self.assertRaises(ValueError):condition.matches(FixedBargainSpec('unrelated').declaration(True).to_dict())
        for raw in ({'schema_version': True, 'choices': []}, {'schema_version': 1, 'choices': [spec.declaration(True).to_dict()]},
                    {'schema_version': 1, 'choices': [spec.declaration(True).to_dict()['choices'][0]] * 2}):
            with self.assertRaises(ValueError):DeclaredCastCostFacts.from_dict(raw)
        for value in (-1, 'true', 1):
            with self.assertRaises(ValueError):spec.declaration(value)
        from quorune.declared_cast_cost_model import declared_cost_copy_fields
        self.assertEqual({}, declared_cost_copy_fields({}))
        copied = declared_cost_copy_fields({'declared_cast_costs': spec.declaration(True).to_dict()})
        self.assertTrue(condition.matches(copied['declared_cast_costs']))
        from types import SimpleNamespace
        from quorune.storm import spell_copy_template
        parent = SimpleNamespace(label='Spell', ref='S1', controller='A', card_object_id='spell', semantic_key='test:spell',
            targets=[], modes=[], x_value=None, default_destination='graveyard', referred_object_ids=[], context=copied)
        host = SimpleNamespace(_stack_target_schema=lambda *args:None)
        template = spell_copy_template(host, parent, None)
        copied['declared_cast_costs']['choices'][0]['chosen'] = False
        self.assertTrue(condition.matches(template['declared_cast_costs']))
        parent.context = {}
        self.assertNotIn('declared_cast_costs', spell_copy_template(host, parent, None))

    def test_bargain_union_admits_artifact_or_enchantment_or_token_not_a_conjunction(self):
        def row(ref, types, token=False, **changes):
            return replace(ObjectQueryResult(ref, ref, ref, 'A', 'A', 'battlefield', types=types, token=token), **changes)
        rows = (row('artifact', ('artifact',)), row('enchantment', ('enchantment',)), row('creature-token', ('creature',), True),
            row('land-token', ('land',), True), row('ordinary-creature', ('creature',)), row('ordinary-land', ('land',)),
            row('foreign', ('artifact',), owner='B', controller='B'), row('borrowed', ('artifact',), owner='B'),
            row('phased', ('enchantment',), phased_out=True), row('tapped', ('artifact',), tapped=True))
        def admitted(union):
            return {r.ref for predicate in union.bound_predicates('A') for r in query_objects(rows, predicate)}
        expected = {'artifact', 'enchantment', 'creature-token', 'land-token', 'borrowed', 'tapped'}
        union = FixedBargainSpec().price_union()
        self.assertEqual(expected, admitted(union))
        self.assertEqual(union, SacrificePredicateUnion.from_dict(union.to_dict()))
        mutant = SacrificePredicateUnion((controlled_permanent_predicate(types_any=('artifact', 'enchantment'), token=True),
            controlled_permanent_predicate(types_all=('artifact',), token=True)))
        with self.assertRaises(AssertionError):self.assertEqual(expected, admitted(mutant))

    def test_bargain_zone_cost_five_is_closed_and_preserves_earlier_descriptor_shapes(self):
        cost = bargain_sacrifice_cost(FixedBargainSpec())
        self.assertEqual(cost, FixedZoneChangeAdditionalCost.from_descriptor(cost.to_descriptor()))
        for changes in ({'schema_version': 4}, {'count': 2}, {'operation': 'discard_one', 'choice_field': 'discard_cards'},
                        {'predicate_union': None}, {'predicate': controlled_permanent_predicate(types_all=('artifact',))}):
            with self.assertRaises(AdditionalCostError):replace(cost, **changes)
        legacy = replace(cost, schema_version=1, predicate_union=None)
        self.assertNotIn('predicate_union', legacy.to_descriptor())
        with self.assertRaises(AdditionalCostError):
            FixedZoneChangeAdditionalCost.from_descriptor({**legacy.to_descriptor(), 'predicate_union': None})
        for raw in ({**cost.to_descriptor(), 'predicate_union': {'schema_version': 1, 'predicates': []}},
                    {**cost.to_descriptor(), 'unknown': 0}):
            with self.assertRaises(AdditionalCostError):FixedZoneChangeAdditionalCost.from_descriptor(raw)

    def test_bargain_entry_occurrence_omits_legacy_fields_and_seals_declared_instance(self):
        from quorune.zone_trigger_events import normalized_zone_trigger_events, ZoneTriggerEventError
        old = zone_witnesses.occurrence()
        self.assertNotIn('declared_cast_costs', old.to_dict())
        self.assertTrue(all('declared_cast_cost_instances' not in row.context for row in normalized_zone_trigger_events(old)))
        spec = FixedBargainSpec()
        for chosen in (True, False):
            value = spec.declaration(chosen).to_dict()
            event = zone_witnesses.occurrence(origin='stack', declared_cast_costs=value)
            value['choices'][0]['chosen'] = not chosen
            self.assertEqual(chosen, event.to_dict()['declared_cast_costs']['choices'][0]['chosen'])
            entered = next(row for row in normalized_zone_trigger_events(event) if row.kind == 'permanent.enter')
            self.assertEqual((spec.instance_id,) if chosen else (), entered.context['declared_cast_cost_instances'])
        with self.assertRaises(ZoneTriggerEventError):zone_witnesses.occurrence(origin='hand', declared_cast_costs=spec.declaration(True).to_dict())
