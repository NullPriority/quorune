"""Execution evidence follows actual resolutions, including command-born spells."""

from types import SimpleNamespace, MappingProxyType
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from quorune.stack_resolution_inputs import (
    StackResolutionInputs, capture_stack_resolutions, observe_stack_resolution,
)
from common import ROOT, keep_all
from quorune import GameConfig
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class ResolutionCaptureModelTests(unittest.TestCase):
    def empty(self):
        return StackResolutionInputs(MappingProxyType({}), MappingProxyType({}))

    def test_new_stack_object_is_observed_without_changing_the_initial_snapshot(self):
        owner, card = object(), object()
        item = SimpleNamespace(ref='new-spell', semantic_key='source:spell:front')
        initial = self.empty()
        with capture_stack_resolutions(owner, initial) as capture:
            observe_stack_resolution(owner, item, card)
            observed = capture.snapshot()
        self.assertFalse(initial)
        self.assertIs(item, observed[item.ref])
        self.assertIs(card, observed.physical_cards[item.ref])
        with self.assertRaises(TypeError):observed.stack_items['other'] = item

    def test_current_stack_object_keeps_its_captured_detached_physical_source(self):
        owner, card = object(), object()
        before = SimpleNamespace(ref='spell', semantic_key=None)
        current = SimpleNamespace(ref='spell', semantic_key='actual:spell:front')
        initial = StackResolutionInputs({'spell': before}, {'spell': card})
        with capture_stack_resolutions(owner, initial) as capture:
            observe_stack_resolution(owner, current, None)
        observed = capture.snapshot()
        self.assertIs(before, initial['spell'])
        self.assertIs(current, observed['spell'])
        self.assertIs(card, observed.physical_cards['spell'])

    def test_nested_owners_and_exception_exit_never_reuse_another_command_capture(self):
        first, second = object(), object()
        item = SimpleNamespace(ref='spell')
        with capture_stack_resolutions(first, self.empty()) as outer:
            observe_stack_resolution(second, item, None)
            self.assertFalse(outer.snapshot())
            with self.assertRaises(RuntimeError):
                with capture_stack_resolutions(second, self.empty()) as inner:
                    observe_stack_resolution(first, item, None)
                    self.assertFalse(inner.snapshot())
                    observe_stack_resolution(second, item, None)
                    raise RuntimeError('aborted command')
            observe_stack_resolution(first, item, None)
            self.assertIn(item.ref, outer.snapshot())
        observe_stack_resolution(first, item, None)
        with capture_stack_resolutions(first, self.empty()) as fresh:
            self.assertFalse(fresh.snapshot())

    def test_replay_rejects_missing_spurious_and_reordered_resolution_rows(self):
        from quorune.record_trust import validate_resolution_provenance
        items = [SimpleNamespace(ref='first', semantic_key='builtin:first'),
                 SimpleNamespace(ref='second', semantic_key='builtin:second')]
        captured = StackResolutionInputs({item.ref:item for item in items}, {})
        events = [SimpleNamespace(event_id=i+1, code='stack.resolve', details={'stack':item.ref})
            for i, item in enumerate(items)]
        rows = [{'key':item.semantic_key, 'oracle_id':None, 'builtin':True, 'version':1} for item in items]
        semantics = SimpleNamespace(get=lambda key:None)
        values = dict(semantics=semantics, engine=object(), events=events,
            before_event_sequence=0, captured=captured, profile='commander_review', sequence=1)
        validate_resolution_provenance(rows, **values)
        for invalid in ([], rows[:1], rows+rows[:1], list(reversed(rows))):
            with self.subTest(rows=invalid), self.assertRaisesRegex(ValueError, 'incomplete or spurious'):
                validate_resolution_provenance(invalid, **values)
        for field, invalid in (('version', True), ('version', 1.0), ('builtin', 1)):
            malformed = [{**rows[0], field:invalid}, rows[1]]
            with self.assertRaisesRegex(ValueError, 'Malformed resolved semantic provenance'):
                validate_resolution_provenance(malformed, **values)
        with self.assertRaisesRegex(ValueError, 'no command-local observation'):
            validate_resolution_provenance([], **{**values, 'captured':self.empty()})

    def test_repeated_implicit_keys_bind_each_actual_spell_and_copy_occurrence(self):
        from quorune.record_trust import validate_resolution_provenance
        key = 'same:spell:front'
        items = [SimpleNamespace(ref='original', semantic_key=key, kind='spell'),
                 SimpleNamespace(ref='copy', semantic_key=key, kind='spell_copy')]
        cards = {item.ref:object() for item in items}
        captured = StackResolutionInputs({item.ref:item for item in items}, cards)
        events = [SimpleNamespace(event_id=i+1, code='stack.resolve', details={'stack':item.ref})
            for i, item in enumerate(items)]
        def implicit(engine, semantic_key, *, stack_item, physical_card, include_spell_copies):
            self.assertEqual(key, semantic_key)
            self.assertIs(cards[stack_item.ref], physical_card)
            self.assertTrue(include_spell_copies)
            return {'oracle_id':'same', 'version':1, 'builtin':False,
                'implicit_fallback':'copy' if stack_item.kind=='spell_copy' else 'original'}
        rows = [{'key':key, 'oracle_id':'same', 'version':1, 'builtin':False, 'implicit_fallback':kind}
            for kind in ('original', 'copy')]
        with patch('quorune.record_trust.implicit_semantic_execution_provenance', side_effect=implicit) as witness:
            validate_resolution_provenance(rows, SimpleNamespace(get=lambda key:None),
                engine=object(), events=events, before_event_sequence=0, captured=captured,
                profile='commander_review', sequence=2)
            self.assertEqual(2, witness.call_count)

    def test_runtime_schema_is_current_strict_and_historical_absence_keeps_its_contract(self):
        from quorune.record_trust import runtime_trust_provenance, _validate_runtime_trust_provenance
        current = runtime_trust_provenance()
        self.assertEqual(2, current['schema_version'])
        _validate_runtime_trust_provenance(None, context='legacy absence')
        _validate_runtime_trust_provenance(current, context='current')
        for invalid in (True, 2.0, '2', 1):
            with self.subTest(version=invalid), self.assertRaisesRegex(ValueError, 'Runtime trust provenance mismatch'):
                _validate_runtime_trust_provenance({**current, 'schema_version':invalid}, context='altered')


class ResolutionCaptureRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] in {
            'Generic Bound Commander', 'Generic Bound Plains', 'Generic Bound Growth'}]
        base['rulings'] = []
        base_path = Path(cls.temporary.name)/'synthetic-deck-and-private-control.json'
        base_path.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8', newline='\n')
        path = Path(cls.temporary.name)/'resolution-capture.sqlite3'
        build_fixture_database([base_path, ROOT/'tests/fixtures/command-resolution-provenance-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Command resolution provenance', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def run_case(self, name, automatic, seed):
        session = CommanderSession.create(self.db, {seat:self.deck for seat in 'ABCD'},
            first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=automatic))
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.priority_player = None
        session.state.priority_passes = []
        source = self.add(session.engine, name, zone='hand')
        target = self.add(session.engine, 'Grizzly Bears', seat='B') if name=='Giant Growth' else None
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        before = len(session.commands)
        self.assertFalse(session.state.stack)
        self.cast(session, source, (target.ref,) if target is not None else ())
        if automatic:
            self.assertFalse(session.state.stack)
            self.assertEqual(before+1, len(session.commands))
        else:
            self.assertTrue(session.state.stack)
            self.assertIsNone(self.settle(session))
        rows = [row for command in session.commands for row in command['semantics']['programs_used']
            if row.get('oracle_id')==source.oracle_id]
        self.assertEqual(1, len(rows), 'Every actual resolution needs its own source-bound evidence')
        if target is None:
            self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
            self.assertEqual('trusted_generic_permanent_spell', rows[0]['implicit_fallback'])
        else:
            self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
            self.assertEqual(5, session.engine._numeric_stat(target.object_id, 'power'))
            self.assertNotIn('implicit_fallback', rows[0])
            card_program = session.engine.semantics.card_program_for_oracle(source.oracle_id)
            self.assertEqual(card_program.fingerprint, rows[0]['card_program_fingerprint'])
        return session, source, private

    def test_original_generic_and_registered_resolutions_record_actual_sources_and_replay(self):
        for index, (name, automatic) in enumerate((('Elves of Deep Shadow', True),
                                                 ('Elves of Deep Shadow', False), ('Giant Growth', True))):
            with self.subTest(original=name, automatic=automatic):
                session, source, private = self.run_case(name, automatic, 977_700+index)
                self.private_replay(session, private)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)/'record'
                    session.save(path)
                    commands = deepcopy(session.commands)
                    position = next(i for i, command in enumerate(commands) if any(
                        row.get('oracle_id')==source.oracle_id for row in command['semantics']['programs_used']))
                    for duplicate in (False, True):
                        changed = deepcopy(commands)
                        if duplicate:
                            changed[position]['semantics']['programs_used'] *= 2
                        else:
                            changed[position]['semantics']['programs_used'] = []
                            changed[position]['semantics']['card_programs_used'] = {}
                        (path/'commands.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in changed),
                            encoding='utf-8', newline='\n')
                        with self.assertRaisesRegex(ValueError, 'incomplete or spurious'):
                            replay_record(path, self.db, verify=True)

    def test_resolution_observation_omission_is_killed(self):
        with patch('quorune.stack_resolution_inputs.observe_stack_resolution', return_value=None):
            with self.assertRaises(AssertionError):
                self.run_case('Elves of Deep Shadow', True, 977_799)
