"""Real source-counter cards through the existing native action fixture."""
from pathlib import Path
import tempfile
import optional_result_followup_fixture as actions
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SourceCounterPronounFixture(actions.OptionalResultFollowupFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'source-counter-pronouns.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/source-counter-pronoun-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source counter pronouns', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def tap_mana(self, s, source, actor='A'):
        self.priority(s, actor)
        row = next(a for a in s.packet('pilot:' + actor, full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':'))
        result = s.act('pilot:' + actor, {'action_id': row['id']});self.assertTrue(result.ok, result.summary)

    def activate_counter_source(self, s, source, targets=(), *, number=None, resume=False):
        self.priority(s, 'A')
        rows = [a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':') and (number is None or ':ab' + str(number) in a['id'])]
        self.assertEqual(1, len(rows), rows);row = rows[0]
        command = {'action_id': row['id'], 'targets': list(targets), 'pay': 'auto'}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        return self.replay(s, load=True) if resume else s

    def upkeep_choice(self, s, *, pay):
        self.until(s, lambda s: getattr(s.state.pending_decision, 'kind', None) == 'semantic.choice')
        command = {'action_id': 'choose', 'pay': pay, 'plan': 'KEEP_PERMANENT' if pay else 'DECLINE_UPKEEP'}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
