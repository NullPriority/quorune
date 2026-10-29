"""Original Forecast hand activations obey owner-upkeep and reveal lifetimes."""
import unittest
import forecast_fixture as fixtures
from quorune.record import authoritative_state_hash
from quorune.forecast_model import FORECAST_REVEAL_FIELD


class ForecastNativeTests(fixtures.ForecastFixture, unittest.TestCase):
    def test_actual_skyscribing_forecast_is_owner_upkeep_once_and_disclosure_ends_at_draw(self):
        s = self.session(702057001)
        source = self.add(s.engine, 'Skyscribing', zone='hand', ref='private-forecast-subject')
        ring = self.add(s.engine, 'Sol Ring');island = self.add(s.engine, 'Island')
        more_islands = [self.add(s.engine, 'Island', ref='normal-skyscribing-blue-' + str(i)) for i in range(2)]
        wastes = [self.add(s.engine, 'Wastes', ref='normal-skyscribing-generic-' + str(i)) for i in range(2)]
        self.assert_closed(s, source);self.ready(s, {})
        self.assertFalse(self.forecast_actions(s, source))
        self.assertNotIn(source.ref, str(s.packet('pilot:C', full=True)))
        self.step(s, 'A', 'beginning', 'upkeep')
        self.tap_for_mana(s, ring);self.tap_for_mana(s, island)
        prior_revealed_to = list(s.state.cards[source.object_id].revealed_to)
        action = self.activate_forecast(s, source)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual(set('ABCD'), set(s.state.cards[source.object_id].revealed_to))
        self.assertIn(source.ref, str(s.packet('pilot:C', full=True)))
        self.private(s)
        before = {p:len(s.state.players[p].zones['hand']) for p in 'ABCD'}
        s = self.replay(s, load=True);self.settle(s)
        self.assertEqual({p:n+1 for p,n in before.items()}, {p:len(s.state.players[p].zones['hand']) for p in 'ABCD'})
        self.assertFalse(self.forecast_actions(s, s.state.cards[source.object_id]))
        fingerprint = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': action, 'pay': 'auto'}).ok)
        self.assertEqual(fingerprint, authoritative_state_hash(s.state))
        self.until(s, lambda s:s.state.step == 'draw')
        current = s.state.cards[source.object_id]
        self.assertNotIn(FORECAST_REVEAL_FIELD, current.annotations)
        self.assertEqual(prior_revealed_to, current.revealed_to)
        self.assertEqual(set('ABCD'), set(current.known_to))
        self.until(s, lambda s:s.state.phase == 'precombat_main')
        for land in [*more_islands, *wastes]:self.tap_for_mana(s, s.state.cards[land.object_id])
        before = {p:len(s.state.players[p].zones['hand']) - int(p == 'A') for p in 'ABCD'}
        offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'] == 'cast:' + source.ref)
        result = s.act('pilot:A', {'action_id': offered['id'], 'x': 2, 'pay': 'auto'});self.assertTrue(result.ok, result.summary)
        self.settle(s)
        self.assertEqual({p:n+2 for p,n in before.items()}, {p:len(s.state.players[p].zones['hand']) for p in 'ABCD'})
        self.private(s);self.replay(s, load=True)

    def test_actual_steeling_stance_leaves_revealed_hand_and_new_upkeep_incarnation_can_forecast_again(self):
        s = self.session(702057002)
        source = self.add(s.engine, 'Steeling Stance', zone='hand')
        regrowth = self.add(s.engine, 'Regrowth', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears')
        self.add(s.engine, 'Vedalken Orrery')
        ring = self.add(s.engine, 'Sol Ring')
        plains = [self.add(s.engine, 'Plains', ref='forecast-white-' + str(i)) for i in range(4)]
        forest = self.add(s.engine, 'Forest')
        self.assert_closed(s, source);self.prepare_forecast(s)
        for land in [ring, *plains, forest]:self.tap_for_mana(s, land)
        original = s.state.cards[source.object_id].logical_object_id
        self.activate_forecast(s, source, [body.ref], reserve={'C':2,'W':3,'G':1});self.settle(s)
        self.assertEqual(3, s.engine._numeric_stat(body.object_id, 'power'))
        self.assertIn(FORECAST_REVEAL_FIELD, s.state.cards[source.object_id].annotations)
        self.cast_for(s, 'A', s.state.cards[source.object_id], reserve={'C':1,'W':1,'G':1})
        self.assertNotIn(FORECAST_REVEAL_FIELD, s.state.cards[source.object_id].annotations)
        self.settle(s)
        self.cast_for(s, 'A', regrowth, [source.ref], reserve={'W':1});self.settle(s)
        current = s.state.cards[source.object_id]
        self.assertEqual('hand', current.zone);self.assertNotEqual(original, current.logical_object_id)
        self.assertTrue(self.forecast_actions(s, current))
        self.activate_forecast(s, current, [body.ref]);self.settle(s)
        self.assertEqual(5, s.engine._numeric_stat(body.object_id, 'power'))
        self.private(s);self.replay(s, load=True)
