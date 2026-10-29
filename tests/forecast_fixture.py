"""Ordinary priority and Forecast actions, without TestCase ancestry."""
from pathlib import Path
import tempfile
import optional_result_followup_fixture as fixtures
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ForecastFixture(fixtures.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'forecast.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/forecast-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Forecast', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def forecast_actions(self, s, source, actor='A'):
        decision = s.packet('pilot:' + actor, full=True)['decision']
        if decision is None or s.state.pending_decision.kind != 'priority':return []
        return [row for row in decision['ctx']['legal']['actions'] if row['id'].startswith('activate:' + source.ref + ':')]

    def tap_for_mana(self, s, source, actor='A'):
        self.priority(s, actor)
        row = next(a for a in s.packet('pilot:' + actor, full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':'))
        result = s.act('pilot:' + actor, {'action_id': row['id']})
        self.assertTrue(result.ok, result.summary)

    def activate_forecast(self, s, source, targets=(), **fields):
        self.priority(s, 'A')
        action = self.forecast_actions(s, source)[0]
        command = {'action_id': action['id'], 'targets': list(targets), 'pay': 'auto', **fields}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        return action['id']

    def prepare_forecast(self, s):
        self.ready(s, {})
        self.step(s, 'A', 'beginning', 'upkeep')

    def choose_as(self, s, actor, **fields):
        result = s.act('pilot:' + actor, {'action_id': 'choose', **fields});self.assertTrue(result.ok, result.summary)
