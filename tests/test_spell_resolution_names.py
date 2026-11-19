"""CR201.2a/702.192a: compare actual resolved names per current controller."""

from dataclasses import FrozenInstanceError, replace
import unittest

from quorune.spell_resolution_name_model import (
    SpellResolutionNameFact, SpellResolutionNameHistory, note_resolution_name,
)


class SpellResolutionNameModelTests(unittest.TestCase):
    def test_known_empty_history_records_first_resolution_then_distinct_copy_resolution_is_not_first(self):
        original = SpellResolutionNameHistory()
        after, first = note_resolution_name(original, stack_ref='S1', controller='A', names=('Generic Lesson',))
        after_copy, copied = note_resolution_name(after, stack_ref='S2', controller='A', names=('Generic Lesson',))
        self.assertTrue(first.require_known_first()); self.assertFalse(copied.require_known_first())
        self.assertFalse(original.has_same_name_resolution('A', ('Generic Lesson',)))
        self.assertEqual(after, after_copy); self.assertEqual(('S1', 'S2'), (first.stack_ref, copied.stack_ref))

    def test_each_controllers_resolution_history_is_independent_of_the_original_owner(self):
        history, _ = note_resolution_name(SpellResolutionNameHistory(), stack_ref='S1', controller='A', names=('Generic Lesson',))
        history, stolen = note_resolution_name(history, stack_ref='S2', controller='B', names=('Generic Lesson',))
        self.assertTrue(stolen.require_known_first()); self.assertEqual('B', stolen.controller)
        self.assertTrue(history.has_same_name_resolution('A', ('Generic Lesson',)))
        self.assertTrue(history.has_same_name_resolution('B', ('Generic Lesson',)))

    def test_multiname_comparison_uses_any_common_name_without_treating_unnamed_spells_as_named(self):
        history, first = note_resolution_name(SpellResolutionNameHistory(), stack_ref='S1', controller='A', names=('Alpha', 'Beta'))
        self.assertTrue(first.require_known_first())
        history, shared = note_resolution_name(history, stack_ref='S2', controller='A', names=('Beta', 'Gamma'))
        self.assertFalse(shared.require_known_first()); self.assertTrue(history.has_same_name_resolution('A', ('Gamma',)))
        unchanged, unnamed = note_resolution_name(history, stack_ref='S3', controller='A', names=())
        self.assertEqual(history, unchanged); self.assertFalse(unnamed.require_known_first())
        self.assertFalse(history.has_same_name_resolution('A', ()))

    def test_unavailable_legacy_history_cannot_be_replaced_with_known_empty_or_infer_first(self):
        history, unknown = note_resolution_name(None, stack_ref='S1', controller='A', names=('Generic Lesson',))
        self.assertIsNone(history); self.assertIsNone(unknown.first_named_resolution)
        with self.assertRaises(ValueError):unknown.require_known_first()
        history, unnamed = note_resolution_name(None, stack_ref='S2', controller='A', names=())
        self.assertIsNone(history); self.assertFalse(unnamed.require_known_first())

    def test_history_and_resolution_fact_roundtrip_without_losing_unknown_or_controller_scope(self):
        history, fact = note_resolution_name(SpellResolutionNameHistory(), stack_ref='S1', controller='B', names=('Generic Lesson',))
        self.assertEqual(history, SpellResolutionNameHistory.from_dict(history.to_dict()))
        self.assertEqual(fact, SpellResolutionNameFact.from_dict(fact.to_dict()))
        unknown = replace(fact, first_named_resolution=None)
        self.assertEqual(unknown, SpellResolutionNameFact.from_dict(unknown.to_dict()))
        with self.assertRaises(FrozenInstanceError):fact.controller = 'A'
        with self.assertRaises(TypeError):history.names_by_controller['B'] = ()

    def test_malformed_names_versions_and_extra_record_authority_are_rejected(self):
        for names in (('Beta', 'Alpha'), ('Alpha', 'Alpha'), ('',), (' Alpha',), (True,), ['Alpha']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                note_resolution_name(SpellResolutionNameHistory(), stack_ref='S1', controller='A', names=names)
        for changes in ({'schema_version': True}, {'schema_version': 1.0}, {'schema_version': 2}):
            with self.assertRaises(ValueError):replace(SpellResolutionNameHistory(), **changes)
        for first in (True, None, 1):
            with self.assertRaises(ValueError):SpellResolutionNameFact('S1', 'A', (), first)
        with self.assertRaises(ValueError):SpellResolutionNameHistory.from_dict({'schema_version': 1, 'names_by_controller': {}, 'owner': 'A'})
        with self.assertRaises(ValueError):note_resolution_name({}, stack_ref='S1', controller='A', names=('Alpha',))

    def test_resolution_facts_require_a_known_exact_first_flag_not_a_truthy_counter(self):
        with self.assertRaises(ValueError):SpellResolutionNameFact('S1', 'A', ('Alpha',), 1)
        with self.assertRaises(ValueError):SpellResolutionNameFact('', 'A', ('Alpha',), True)
        with self.assertRaises(ValueError):SpellResolutionNameFact('S1', '', ('Alpha',), True)
        fact = SpellResolutionNameFact('S1', 'A', ('Alpha',), False)
        with self.assertRaises(ValueError):SpellResolutionNameFact.from_dict({**fact.to_dict(), 'owner': 'B'})


class SpellResolutionNameLedgerTests(unittest.TestCase):
    def test_paused_resolution_keeps_its_first_flag_without_reclaiming_the_name(self):
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        ledger, fact = SpellResolutionNameLedger().begin(stack_ref='S1', controller='A', names=('Alpha',))
        resumed, same = ledger.begin(stack_ref='S1', controller='A', names=('Alpha',))
        self.assertIs(ledger, resumed); self.assertEqual(fact, same); self.assertTrue(same.require_known_first())
        self.assertEqual(1, len(resumed.pending))

    def test_completion_releases_only_active_claim_and_retains_lifetime_names(self):
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        ledger, _ = SpellResolutionNameLedger().begin(stack_ref='S1', controller='A', names=('Alpha',))
        completed = ledger.finish('S1'); self.assertFalse(completed.pending)
        self.assertTrue(completed.history.has_same_name_resolution('A', ('Alpha',)))
        later, fact = completed.begin(stack_ref='S2', controller='A', names=('Alpha',))
        self.assertFalse(fact.require_known_first()); self.assertEqual(('S2',), tuple(later.pending))
        self.assertIs(completed, completed.finish('S1'))

    def test_pending_rebinding_and_forged_claims_fail_closed_but_other_controller_is_independent(self):
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        ledger, _ = SpellResolutionNameLedger().begin(stack_ref='S1', controller='A', names=('Alpha',))
        for controller, names in (('B', ('Alpha',)), ('A', ('Beta',))):
            with self.assertRaises(ValueError):ledger.begin(stack_ref='S1', controller=controller, names=names)
        _, other = ledger.begin(stack_ref='S2', controller='B', names=('Alpha',))
        self.assertTrue(other.require_known_first())
        with self.assertRaises(ValueError):
            SpellResolutionNameLedger(pending={'S1': SpellResolutionNameFact('S1', 'A', ('Missing',), True).to_dict()})

    def test_pending_ledger_roundtrip_is_immutable_and_requires_known_history(self):
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        ledger, _ = SpellResolutionNameLedger().begin(stack_ref='S1', controller='A', names=('Alpha',))
        self.assertEqual(ledger, SpellResolutionNameLedger.from_dict(ledger.to_dict()))
        with self.assertRaises(TypeError):ledger.pending['S1'] = {}
        with self.assertRaises(ValueError):SpellResolutionNameLedger(history=None)
        with self.assertRaises(ValueError):SpellResolutionNameLedger.from_dict({**ledger.to_dict(), 'owner': 'B'})


class SpellResolutionNameStateCodecTests(unittest.TestCase):
    @staticmethod
    def state():
        from quorune.model import GameConfig, GameState
        return GameState(game_id='names', config=GameConfig(), players={}, cards={},
            deck_names={}, commander_oracle_ids={}, turn_order=[], current_turn=None,
            last_normal_turn_player=None)

    def test_absent_legacy_history_roundtrips_with_identical_checkpoint_and_hash(self):
        from quorune.model import GameState
        from quorune.util import stable_json
        state = self.state(); checkpoint = state.to_dict()
        self.assertNotIn('spell_resolution_name_ledger', checkpoint)
        restored = GameState.from_dict(checkpoint)
        self.assertIsNone(restored.spell_resolution_name_ledger)
        self.assertEqual(stable_json(checkpoint), stable_json(restored.to_dict()))

    def test_known_empty_and_paused_resolution_history_survive_roundtrip_distinct_from_absence(self):
        from quorune.model import GameState
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        state = self.state(); legacy = state.to_dict()
        state.spell_resolution_name_ledger = SpellResolutionNameLedger()
        known = state.to_dict(); self.assertNotEqual(legacy, known)
        self.assertEqual(state.spell_resolution_name_ledger,
            GameState.from_dict(known).spell_resolution_name_ledger)
        from quorune.model import StackItem
        state.stack = [StackItem(stack_id='1', ref='S1', kind='spell', controller='A', label='Alpha')]
        state.spell_resolution_name_ledger, first = state.spell_resolution_name_ledger.begin(
            stack_ref='S1', controller='A', names=('Alpha',))
        restored = GameState.from_dict(state.to_dict())
        _, resumed = restored.spell_resolution_name_ledger.begin(
            stack_ref='S1', controller='A', names=('Alpha',))
        self.assertEqual(first, resumed); self.assertTrue(resumed.require_known_first())

    def test_present_null_or_malformed_checkpoint_is_rejected_instead_of_becoming_absence(self):
        from quorune.model import GameState
        checkpoint = self.state().to_dict()
        for value in (None, {}, {'schema_version': True, 'history': {}, 'pending': {}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GameState.from_dict({**checkpoint, 'spell_resolution_name_ledger': value})

    def test_manifest_and_initial_checkpoint_must_agree_exactly_including_legacy_absence(self):
        from quorune.record_state_provenance import format_state_versions, validate_state_versions
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        field = 'spell_resolution_name_history_version'; state = self.state()
        legacy = {'format': format_state_versions(state)}
        self.assertNotIn(field, legacy['format']); validate_state_versions(legacy, state)
        state.spell_resolution_name_ledger = SpellResolutionNameLedger()
        current = {'format': format_state_versions(state)}
        self.assertEqual(1, current['format'][field]); validate_state_versions(current, state)
        with self.assertRaises(ValueError):validate_state_versions(legacy, state)
        for invalid in (None, 0, True, 1.0, 2):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_state_versions({'format': {**current['format'], field: invalid}}, state)
        state.spell_resolution_name_ledger = None
        with self.assertRaises(ValueError):validate_state_versions(current, state)


    def test_pending_checkpoint_requires_its_actual_spell_reference_and_controller(self):
        from quorune.model import GameState, StackItem
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        state = self.state()
        state.spell_resolution_name_ledger, _ = SpellResolutionNameLedger().begin(
            stack_ref='S1', controller='A', names=('Alpha',))
        state.stack = [StackItem(stack_id='1', ref='S1', kind='spell', controller='A', label='Alpha')]
        valid = state.to_dict(); self.assertEqual(valid, GameState.from_dict(valid).to_dict())
        for stack in ([], [{**valid['stack'][0], 'ref': 'S2'}],
                [{**valid['stack'][0], 'kind': 'triggered_ability'}],
                [{**valid['stack'][0], 'controller': 'B'}]):
            with self.subTest(stack=stack), self.assertRaises(ValueError):
                GameState.from_dict({**valid, 'stack': stack})


class SpellResolutionNameBoundaryTests(unittest.TestCase):
    def test_effective_names_include_fused_halves_and_preserve_unnamed_faces(self):
        from types import SimpleNamespace
        from quorune.spell_resolution_names import stack_spell_rules_names
        card = SimpleNamespace(face_down=False, zone='stack', printed_name='Ignored label')
        self.assertEqual(('Current',), stack_spell_rules_names(card, {'name': 'Current'}))
        self.assertEqual(('Alive', 'Well'), stack_spell_rules_names(card, {'name': 'Alive // Well'}))
        self.assertEqual((), stack_spell_rules_names(card, {'name': ''}))
        card.face_down = True
        self.assertEqual((), stack_spell_rules_names(card, {'name': 'Hidden identity'}))

    def test_current_controller_and_copy_names_retain_first_fact_only_until_completion(self):
        from types import SimpleNamespace
        from quorune.spell_resolution_names import begin_spell_resolution_names, finish_spell_resolution_names
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        card = SimpleNamespace(face_down=False, zone='stack', owner='B')
        host = SimpleNamespace(state=SimpleNamespace(spell_resolution_name_ledger=SpellResolutionNameLedger(),
            cards={'copy': card}), _effective_card_data=lambda c: {'name': 'Lesson'})
        item = SimpleNamespace(kind='spell_copy', ref='S1', controller='A', card_object_id='copy')
        first = begin_spell_resolution_names(host, item)
        self.assertTrue(first.require_known_first()); self.assertEqual('A', first.controller)
        self.assertEqual(first, begin_spell_resolution_names(host, item))
        finish_spell_resolution_names(host, item)
        self.assertFalse(host.state.spell_resolution_name_ledger.pending)
        item.ref = 'S2'
        self.assertFalse(begin_spell_resolution_names(host, item).require_known_first())
        item.ref = 'S3'; item.controller = 'B'
        self.assertTrue(begin_spell_resolution_names(host, item).require_known_first())

    def test_legacy_and_nonspell_boundaries_never_create_history(self):
        from types import SimpleNamespace
        from quorune.spell_resolution_names import begin_spell_resolution_names, finish_spell_resolution_names
        from quorune.spell_resolution_name_model import SpellResolutionNameLedger
        state = SimpleNamespace(spell_resolution_name_ledger=None)
        host = SimpleNamespace(state=state); spell = SimpleNamespace(kind='spell')
        self.assertIsNone(begin_spell_resolution_names(host, spell)); finish_spell_resolution_names(host, spell)
        self.assertIsNone(state.spell_resolution_name_ledger)
        ledger = SpellResolutionNameLedger(); state.spell_resolution_name_ledger = ledger
        ability = SimpleNamespace(kind='triggered')
        self.assertIsNone(begin_spell_resolution_names(host, ability)); finish_spell_resolution_names(host, ability)
        self.assertIs(ledger, state.spell_resolution_name_ledger)


from inherited_composition_fixture import InheritedNativeFixture


class SpellResolutionNameNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json, tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory(); root = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = root / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/paradigm-cards.json'], root / 'names.sqlite3')
        cls.db = CardDatabase(root / 'names.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Resolution names', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def names(self, s, seat='A'):
        self.assertFalse(s.state.spell_resolution_name_ledger.pending)
        return s.state.spell_resolution_name_ledger.history.names_by_controller.get(seat, ())

    def test_native_paid_spells_and_permanent_resolution_record_names_by_current_controller(self):
        s = self.session(702192100)
        bear = self.add(s.engine, 'Grizzly Bears', zone='hand')
        first = self.add(s.engine, 'Lightning Bolt', zone='hand', ref='name-bolt-first')
        second = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand', ref='name-bolt-B')
        self.prepare(s, mana={'G': 1, 'R': 2, 'C': 1}); s.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(s)
        self.cast_for(s, 'A', bear); self.assertIsNone(self.resolve(s))
        self.assertEqual(('Grizzly Bears',), self.names(s))
        self.cast_for(s, 'A', first, ['C']); self.assertIsNone(self.resolve(s))
        self.cast_for(s, 'B', second, ['D']); self.assertIsNone(self.resolve(s))
        self.assertEqual(('Grizzly Bears', 'Lightning Bolt'), self.names(s))
        self.assertEqual(('Lightning Bolt',), self.names(s, 'B'))
        self.replay(s, load=True)

    def test_native_all_targets_illegal_and_countered_spells_never_enter_resolution_history(self):
        s = self.session(702192110); bear = self.add(s.engine, 'Grizzly Bears', seat='B')
        bolt = self.add(s.engine, 'Lightning Bolt', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        countered = self.add(s.engine, 'Grizzly Bears', zone='hand', ref='countered-bear')
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        self.prepare(s, mana={'R': 1, 'G': 1, 'C': 1}); s.state.players['B'].mana_pool['U'] = 3
        self.checkpoint(s)
        self.cast_for(s, 'A', bolt, [bear.ref]); self.cast_for(s, 'B', bounce, [bear.ref])
        self.assertIsNone(self.resolve(s)); self.assertEqual((), self.names(s))
        self.assertEqual(('Unsummon',), self.names(s, 'B'))
        self.cast_for(s, 'A', countered); self.cast_for(s, 'B', counter, [s.state.stack[-1].ref])
        self.assertIsNone(self.resolve(s)); self.assertEqual((), self.names(s))
        self.assertEqual(('Counterspell', 'Unsummon'), self.names(s, 'B'))
        self.replay(s, load=True)

    def test_native_uncast_spell_copy_resolves_before_original_and_only_copy_is_first(self):
        from unittest.mock import patch
        import quorune.spell_resolution_names as names
        s = self.session(702192120); bolt = self.add(s.engine, 'Lightning Bolt', zone='hand')
        copy = self.add(s.engine, 'Twincast', zone='hand')
        self.prepare(s, mana={'R': 1, 'U': 2}); self.checkpoint(s)
        self.cast_for(s, 'A', bolt, ['B']); self.cast_for(s, 'A', copy, [s.state.stack[-1].ref])
        observed = []; original = names.begin_spell_resolution_names
        def capture(host, item):
            fact = original(host, item)
            if fact is not None:observed.append((item.kind, fact))
            return fact
        with patch.object(names, 'begin_spell_resolution_names', side_effect=capture):
            self.assertIsNotNone(self.resolve(s))
            result = s.act('pilot:A', {'action_id': 'choose', 'targets': ['B']})
            self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        bolts = [(kind, fact) for kind, fact in observed if fact.names == ('Lightning Bolt',)]
        self.assertEqual(['spell_copy', 'spell'], [kind for kind, _ in bolts])
        self.assertEqual([True, False], [fact.require_known_first() for _, fact in bolts])
        self.assertEqual(('Lightning Bolt', 'Twincast'), self.names(s))
        self.assertEqual(34, s.state.players['B'].life); self.replay(s, load=True)

    def test_native_choice_pause_keeps_original_first_fact_across_save_load_and_exact_replay(self):
        s = self.session(702192130); spell = self.add(s.engine, 'Opt', zone='hand')
        self.prepare(s, mana={'U': 1}); self.checkpoint(s); self.cast_for(s, 'A', spell)
        decision = self.resolve(s); self.assertIsNotNone(decision)
        ledger = s.state.spell_resolution_name_ledger
        self.assertEqual(('Opt',), ledger.history.names_by_controller['A'])
        self.assertEqual(1, len(ledger.pending))
        fact = next(iter(ledger.pending.values())); self.assertTrue(fact['first_named_resolution'])
        s = self.replay(s, load=True)
        self.assertEqual(ledger, s.state.spell_resolution_name_ledger)
        result = s.act('pilot:A', {'action_id': 'choose', 'cards': {'top': list(s.state.pending_decision.payload_by_actor['A']['legal_actions'][0]['choice_schema']['legal_refs']), 'bottom': []}})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual(('Opt',), self.names(s)); self.replay(s, load=True)


    def test_native_split_selected_and_fused_names_and_face_down_spell_have_rules_names(self):
        s = self.session(702192140)
        split = self.add(s.engine, 'Far // Away', zone='hand'); body = self.add(s.engine, 'Grizzly Bears', seat='B')
        fused = self.add(s.engine, 'Alive // Well', zone='hand')
        morph = self.add(s.engine, 'Daru Lancer', zone='hand')
        self.prepare(s, mana={'U': 1, 'G': 1, 'W': 1, 'C': 7}); self.checkpoint(s)
        for source, match, targets in ((split, {'face': 'Far'}, [body.ref]), (fused, {'cast_method': 'fuse'}, [])):
            self.priority(s, 'A')
            rows = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            row = next(row for row in rows if row.get('card') == source.ref
                and all(row.get(key) == value for key, value in match.items()))
            result = s.act('pilot:A', {'action_id': row['id'], 'targets': targets, 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual(('Alive', 'Far', 'Well'), self.names(s))
        result = s.act('pilot:A', {'action_id': 'cast-morph:' + morph.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[morph.object_id].face_down)
        self.assertEqual(('Alive', 'Far', 'Well'), self.names(s)); self.replay(s, load=True)

    def test_native_completion_fault_rolls_back_lifetime_name_and_effect_then_retries(self):
        from unittest.mock import patch
        import quorune.spell_resolution_names as names
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s = self.session(702192150); bolt = self.add(s.engine, 'Lightning Bolt', zone='hand')
        self.prepare(s, mana={'R': 1}); self.checkpoint(s); self.cast_for(s, 'A', bolt, ['B'])
        while len(s.state.priority_passes) < len(s.engine.active_seats) - 1:
            result = s.act(s.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        before = authoritative_state_hash(s.state); original = names.finish_spell_resolution_names; calls = []
        def fault(host, item):
            original(host, item); calls.append(item.ref)
            raise GameRuleError('Injected resolution name completion fault')
        principal = s.pending_principals()[0]
        with patch.object(names, 'finish_spell_resolution_names', side_effect=fault):
            result = s.act(principal, {'action_id': 'pass'})
            self.assertFalse(result.ok); self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertEqual(1, len(calls)); self.assertEqual(40, s.state.players['B'].life)
        self.assertFalse(s.state.spell_resolution_name_ledger.history.names_by_controller)
        result = s.act(principal, {'action_id': 'pass'}); self.assertTrue(result.ok, result.summary)
        self.assertEqual(37, s.state.players['B'].life); self.assertEqual(('Lightning Bolt',), self.names(s))
        self.replay(s, load=True)
