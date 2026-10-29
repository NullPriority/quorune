"""Independent CR702.57/302.6 owner-hand, upkeep, usage and reveal contracts."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from quorune.abilities import ActivatedAbility, parse_activated_abilities
from quorune.activation_condition_model import ActivationConditionKind
from quorune.activation_usage import ActivationLimit, activation_usage_verdict, commit_activation_usage
from quorune.forecast_model import ForecastRevealFrame, FORECAST_REVEAL_FIELD
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.compiler.activated_tap_costs import fixed_activated_tap_cost


def forecast_ability(cost='{2}{U}', body='Draw a card.'):
    return parse_activated_abilities(card_name='Forecast Source', oracle_text=f'Forecast — {cost}, Reveal this card from your hand: {body}', keywords=('Forecast',))[0]


class ForecastModelTests(unittest.TestCase):
    def test_forecast_descriptor_is_hand_upkeep_once_and_legacy_descriptors_omit_marker(self):
        ability = forecast_ability()
        self.assertTrue(ability.forecast);self.assertTrue(ability.compiled_cost)
        self.assertEqual(('hand',), ability.zones)
        self.assertEqual(ActivationLimit.ONCE_PER_TURN, ability.activation_limit)
        self.assertIn(ActivationConditionKind.CONTROLLERS_UPKEEP, {c.kind for c in ability.activation_conditions})
        self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
        for changes in ({'forecast': 1}, {'zones': ('battlefield',)}, {'activation_limit': None}, {'activation_conditions': ()}):
            with self.assertRaises(ValueError):replace(ability, **changes)
        with self.assertRaises(ValueError):ActivatedAbility.from_dict({**ability.to_dict(), 'forecast': False})
        ordinary = parse_activated_abilities(card_name='Source', oracle_text='{U}: Draw a card.', keywords=())[0]
        self.assertNotIn('forecast', ordinary.to_dict())
        malformed = parse_activated_abilities(card_name='Source', oracle_text='Forecast — {U}: Draw a card.', keywords=('Forecast',))[0]
        self.assertFalse(malformed.compiled_cost)

    def test_forecast_reveal_frame_is_versioned_and_original_incarnation_bound(self):
        frame = ForecastRevealFrame(ContinuousObjectIdentity('source', 'source@1'), 'A', 3, ('A',))
        self.assertEqual(frame, ForecastRevealFrame.from_dict(frame.to_dict()))
        for change in ({'schema_version': True}, {'turn_sequence': -1}, {'prior_revealed_to': ['A', 'A']}, {'unknown': 0}, {'owner': ''}):
            with self.assertRaises(ValueError):ForecastRevealFrame.from_dict({**frame.to_dict(), **change})
        source = SimpleNamespace(annotations={})
        ability = forecast_ability()
        self.assertTrue(activation_usage_verdict(source, ability_id=ability.ability_id, limit=ability.activation_limit, turn_sequence=3).available)
        commit_activation_usage(source, ability_id=ability.ability_id, limit=ability.activation_limit, turn_sequence=3)
        self.assertFalse(activation_usage_verdict(source, ability_id=ability.ability_id, limit=ability.activation_limit, turn_sequence=3).available)
        self.assertTrue(activation_usage_verdict(source, ability_id=ability.ability_id, limit=ability.activation_limit, turn_sequence=4).available)

    def test_forecast_disclosure_retains_knowledge_and_ends_only_outside_upkeep_or_on_departure(self):
        from quorune.forecast import begin_forecast_reveal, end_forecast_reveal, synchronize_forecast_reveals
        source = SimpleNamespace(object_id='source', logical_object_id='source@1', ref='source', owner='A', zone='hand',
            oracle_id='oracle', annotations={}, known_to=['A'], revealed_to=['A'])
        state = SimpleNamespace(cards={'source': source}, players={'A': SimpleNamespace(zones={'hand': ['source']})},
            active_player='A', phase='beginning', step='upkeep', turn_sequence=3)
        host = SimpleNamespace(state=state, seats=list('ABCD'), active_seats=list('ABCD'),
            card_record=lambda card:SimpleNamespace(oracle_id='oracle', name='Forecast Source'), _log=lambda *args, **kwargs:None)
        begin_forecast_reveal(host, 'A', source)
        self.assertEqual(set('ABCD'), set(source.revealed_to));self.assertEqual(set('ABCD'), set(source.known_to))
        synchronize_forecast_reveals(host)
        self.assertIn(FORECAST_REVEAL_FIELD, source.annotations)
        begin_forecast_reveal(host, 'A', source)
        self.assertEqual(['A'], source.annotations[FORECAST_REVEAL_FIELD]['prior_revealed_to'])
        state.step = 'draw';synchronize_forecast_reveals(host)
        self.assertEqual(['A'], source.revealed_to);self.assertEqual(set('ABCD'), set(source.known_to))
        state.step = 'upkeep';begin_forecast_reveal(host, 'A', source);end_forecast_reveal(host, source)
        self.assertEqual(['A'], source.revealed_to);self.assertNotIn(FORECAST_REVEAL_FIELD, source.annotations)
        from quorune.errors import GameRuleError
        with self.assertRaises(GameRuleError):begin_forecast_reveal(host, 'B', source)

    def test_forecast_colored_tap_cost_admits_each_white_or_blue_untapped_controlled_creature(self):
        ability = fixed_activated_tap_cost(forecast_ability('Tap two untapped white and/or blue creatures you control'))
        self.assertTrue(ability.compiled_cost, ability.uncompiled_costs)
        cost = ability.choices[0].fixed_tap_cost();self.assertEqual(2, cost.schema_version)
        self.assertEqual(2, cost.count)
        def row(ref, colors, **changes):
            return replace(ObjectQueryResult(ref, ref, ref, 'A', 'A', 'battlefield', types=('creature',), colors=colors), **changes)
        rows = (row('white', ('W',)), row('blue', ('U',)), row('both', ('W', 'U')), row('other', ('G',)),
            row('foreign', ('W',), controller='B', owner='B'), row('tapped', ('U',), tapped=True), row('artifact', ('W',), types=('artifact',)), row('phased', ('U',), phased_out=True))
        self.assertEqual({'white', 'blue', 'both'}, {r.ref for r in query_objects(rows, cost.bound_predicate('A', 'source'))})
        from quorune.rules.activation_costs import FixedTapActivationCost, FixedTapActivationCostError
        with self.assertRaises(FixedTapActivationCostError):replace(cost, predicate=replace(cost.predicate, colors_any=(), colors_all=('W','U')))
        mutant = replace(cost.bound_predicate('A', 'source'), colors_any=(), colors_all=('W','U'))
        with self.assertRaises(AssertionError):self.assertEqual({'white', 'blue', 'both'}, {r.ref for r in query_objects(rows, mutant)})
