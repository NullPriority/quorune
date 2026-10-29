"""Native designation action fixture; deliberately not a TestCase."""
from pathlib import Path
import tempfile
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DesignationEventFixture(actions.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'designation-events.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/designation-event-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Designation events', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def activate(self, s, seat, source, targets=(), *, reserve=None):
        self.priority(s, seat)
        current = s.state.cards[source.object_id]
        offered = next(a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
                       if a['id'].startswith('activate:' + current.ref + ':'))
        r = s.act('pilot:' + seat, {'action_id': offered['id'], 'targets': list(targets),
                                  'pay': 'auto', 'reserve': reserve or {}})
        self.assertTrue(r.ok, r.summary)
