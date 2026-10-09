from __future__ import annotations

from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quorune.compiler.target_announcement_bindings import target_announcement_binding_spec
from quorune.rules.target_announcements import TargetAnnouncement, dispatch_target_announcements

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class TargetAnnouncementTests(unittest.TestCase):
    def test_target_announcement_compiler_dependency_and_implementation_mutants_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        record = payment_record('Whenever this creature becomes the target of a spell or ability, you lose 1 life.')
        for id in ('trigger.event.normalized_target_announcement', 'trigger.placement.apnap', 'target.revalidate_resolution'):
            value = deepcopy(raw); cap = next(r for r in value['capabilities'] if r['id'] == id)
            cap.update(status='blocked', blockers=['Independent target occurrence mutation'])
            with self.subTest(dependency=id):
                self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(value)).status)
        from quorune.compiler import target_announcement_bindings as owner
        with mock.patch.object(owner, 'target_announcement_binding_spec', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=load_default_capability_registry()).status)
    def test_target_occurrence_pins_stack_controller_and_each_current_object_once(self):
        rows = [SimpleNamespace(ref=ref, zone='battlefield', phased_out=False, controller='B', logical_object_id=ref + ':1') for ref in ('FIRST', 'SECOND')]
        published = []
        host = SimpleNamespace(
            state=SimpleNamespace(cards={row.ref: row for row in rows}),
            _type_parts=lambda _line: ({'creature'}, set(), set()),
            _effective_card_data=lambda _card: {'type_line': 'Creature'},
            _dispatch_semantic_event=lambda event, ctx, **kwargs: published.append((event, ctx)),
        )
        item = SimpleNamespace(ref='STACK', controller='A', kind='spell', targets=['FIRST', 'FIRST', 'SECOND'])
        dispatch_target_announcements(host, item)
        self.assertEqual(['FIRST', 'SECOND'], [ctx['card'] for _, ctx in published])
        self.assertEqual(['A', 'A'], [ctx['stack_controller'] for _, ctx in published])
        self.assertEqual(['B', 'B'], [ctx['controller'] for _, ctx in published])
        self.assertEqual(['FIRST:1', 'SECOND:1'], [ctx['card_object_identity'] for _, ctx in published])
        published.clear()
        dispatch_target_announcements(host, item, previous_targets=('FIRST',))
        self.assertEqual(['SECOND'], [ctx['card'] for _, ctx in published])

    def test_current_phased_or_departed_target_creates_no_occurrence(self):
        published = []
        host = SimpleNamespace(state=SimpleNamespace(cards={'ref': SimpleNamespace(ref='FIRST', zone='graveyard', phased_out=False)}), _dispatch_semantic_event=lambda *args, **kwargs: published.append(args))
        item = SimpleNamespace(ref='STACK', controller='A', kind='spell_copy', targets=['FIRST'])
        dispatch_target_announcements(host, item)
        self.assertFalse(published)
        with self.assertRaises(ValueError):
            TargetAnnouncement('STACK', 'A', 'unknown', 'FIRST', 'B', 'FIRST:1', ('creature',))

    def test_target_subscription_separates_target_controller_from_stack_controller(self):
        spec = target_announcement_binding_spec('Whenever a creature you control becomes the target of a spell or ability an opponent controls, draw a card.', card_name='Witness')
        self.assertEqual('object.became_target', spec.event)
        self.assertIn({'field': 'controller', 'op': 'eq', 'value': '$source.controller'}, spec.condition['all'])
        self.assertIn({'field': 'stack_controller', 'op': 'ne', 'value': '$source.controller'}, spec.condition['all'])
        self.assertIsNone(target_announcement_binding_spec('Whenever this creature becomes the target of a spell for the first time each turn, draw a card.', card_name='Witness'))
        self.assertIsNone(target_announcement_binding_spec('Whenever a creature you control becomes the target of a spell, put a +1/+1 counter on it.', card_name='Witness'))


class TargetAnnouncementActionTests(unittest.TestCase):
    # CR 115.7 and 603.2: one stack object newly targeting an object creates
    # one occurrence; copying/retargeting and casting remain distinct events.
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'target-announcements.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json',
            ROOT / 'tests/fixtures/kicked-entry-triggers.json',
            ROOT / 'tests/fixtures/scryfall-exact-lists.json',
            ROOT / 'tests/fixtures/library-search-qualifier-cards.json',
            ROOT / 'tests/fixtures/target-announcements.json',
        ], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Target occurrence deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in 'ABCD'}, first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session); engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        names = {'Tethered Skirge', "Shapers' Sanctuary", 'Giant Growth', 'Shock', 'Prodigal Sorcerer',
            'Triton Fortune Hunter', 'Lagonna-Band Trailblazer', 'Tar Pit Warrior', 'Cloudshift',
            'Air Elemental', 'Llanowar Elves', 'Oran-Rief Recluse', 'Generic Payment Commander', 'Generic Payment Plains'}
        records = tuple(record for record in self.db.iter_cards() if record.name in names and compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review').status == 'exact')
        register_generated_programs(self.db, engine.semantics, records, trust_level='trusted', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='battlefield', seat='A'):
        record = self.db.lookup(name)
        card = CardInstance(object_id='targeting:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=seat, controller=seat, zone=zone, zone_timestamp=engine._next_zone_timestamp(), acquired_control_turn_count=-1, known_to=[seat] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card; engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def priority(self, session, seat='A', mana=None):
        engine = session.engine; engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players[seat].mana_pool.update(mana or {'G': 4, 'R': 4, 'U': 4, 'C': 4})
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority(seat); engine.pump()
        return session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state); session.commands.clear(); session.decisions.clear()

    def resolve(self, session):
        for _ in range(24):
            if session.state.pending_decision and session.state.pending_decision.kind != 'priority': return
            if not session.state.stack: return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Target occurrence stack did not resolve')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target-occurrence-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_actual_targeted_cast_and_ability_each_trigger_once_and_replay(self):
        for mode in ('spell', 'ability'):
            with self.subTest(mode=mode):
                session = self.session(1150201 + int(mode == 'ability')); engine = session.engine
                victim = self.add(engine, 'Tethered Skirge', ref='SKIRGE')
                source = self.add(engine, 'Giant Growth' if mode == 'spell' else 'Prodigal Sorcerer', ref='ACTION', zone='hand' if mode == 'spell' else 'battlefield', seat='B')
                actions = self.priority(session, 'B')
                action = next(row for row in actions if row['id'] == 'cast:ACTION' or row['id'].startswith('activate:ACTION:'))
                self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in engine.semantics.programs_for_oracle(victim.oracle_id)))
                self.checkpoint(session); before = authoritative_state_hash(engine.state)
                bad = session.act('pilot:C', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
                self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(engine.state))
                result = session.act('pilot:B', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(2, len(session.state.stack))
                self.assertEqual('A', session.state.stack[-1].controller)
                self.resolve(session); self.assertEqual(39, session.state.players['A'].life)
                self.replay(session)

    def test_opposing_stack_controller_and_target_controller_publish_private_optional_draw(self):
        for caster in ('A', 'B'):
            with self.subTest(caster=caster):
                session = self.session(1150203 + int(caster == 'B')); engine = session.engine
                source = self.add(engine, "Shapers' Sanctuary", ref='SANCTUARY')
                victim = self.add(engine, 'Air Elemental', ref='VICTIM')
                spell = self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat=caster)
                action = next(row for row in self.priority(session, caster) if row['id'] == 'cast:GROWTH')
                self.checkpoint(session)
                result = session.act('pilot:' + caster, {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(2 if caster == 'B' else 1, len(session.state.stack))
                hand_before = len(session.state.players['A'].zones['hand'])
                self.resolve(session)
                if caster == 'B':
                    self.assertEqual(['A'], session.state.pending_decision.actors)
                    for seat in 'BCD': self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
                    before = authoritative_state_hash(session.state)
                    bad = session.act('pilot:B', {'action_id': 'choose', 'apply': True})
                    self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(session.state))
                    result = session.act('pilot:A', {'action_id': 'choose', 'choice': 'draw'})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual(hand_before + 1, len(session.state.players['A'].zones['hand']))
                    self.resolve(session)
                self.replay(session)

    def test_cast_target_occurrence_never_appears_after_invalid_target_rollback(self):
        session = self.session(1150205); engine = session.engine
        victim = self.add(engine, 'Tethered Skirge', ref='SKIRGE')
        land = self.add(engine, 'Generic Payment Plains', ref='LAND')
        self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat='B')
        action = next(row for row in self.priority(session, 'B') if row['id'] == 'cast:GROWTH')
        self.checkpoint(session); before = authoritative_state_hash(engine.state)
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [land.ref], 'pay': 'auto'})
        self.assertFalse(result.ok); self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(session.state.stack); self.assertEqual(40, session.state.players['A'].life)
        self.replay(session)

    def test_copy_and_retarget_owner_publish_new_targets_without_creating_casts(self):
        from quorune.semantic_runtime.intents import RetargetStackItemIntent
        from quorune.replacement.immutable import FrozenMap
        session = self.session(1150206); engine = session.engine
        first = self.add(engine, 'Tethered Skirge', ref='FIRST')
        second = self.add(engine, 'Tethered Skirge', ref='SECOND')
        heroic = self.add(engine, 'Triton Fortune Hunter', ref='HEROIC', seat='B')
        self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat='B')
        action = next(row for row in self.priority(session, 'B') if row['id'] == 'cast:GROWTH')
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [first.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        spell = next(item for item in session.state.stack if item.kind == 'spell')
        copied = engine._copy_stack_item(controller='B', target=spell, targets=[second.ref], target_groups={}, reason='Independent normalized copy owner witness')
        def trigger_count():
            return sum(item.kind == 'triggered_ability' for item in engine.state.stack) + sum(len(batch.items) for batch in engine.state.pending_trigger_batches)
        self.assertEqual(2, trigger_count())
        engine.retarget_stack_item_intent(RetargetStackItemIntent(actor='B', target_stack_ref=copied.ref, targets=(heroic.ref,), target_groups=FrozenMap(), source_stack_ref=spell.ref))
        self.assertEqual(2, trigger_count(), 'Retargeting cannot create a Heroic cast trigger')
        engine.retarget_stack_item_intent(RetargetStackItemIntent(actor='B', target_stack_ref=copied.ref, targets=(second.ref,), target_groups=FrozenMap(), source_stack_ref=spell.ref))
        self.assertEqual(3, trigger_count())
        engine.retarget_stack_item_intent(RetargetStackItemIntent(actor='B', target_stack_ref=copied.ref, targets=(second.ref,), target_groups=FrozenMap(), source_stack_ref=spell.ref))
        self.assertEqual(3, trigger_count(), 'Keeping the same target is no new target occurrence')

    def test_pending_target_draw_choice_loads_and_keeps_actor_private(self):
        session = self.session(1150207); engine = session.engine
        self.add(engine, "Shapers' Sanctuary", ref='SANCTUARY')
        victim = self.add(engine, 'Air Elemental', ref='VICTIM')
        self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat='B')
        action = next(row for row in self.priority(session, 'B') if row['id'] == 'cast:GROWTH')
        self.checkpoint(session)
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-target-draw'; session.save(path)
            session = CommanderSession.load(self.db, path)
        for seat in 'BCD': self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
        result = session.act('pilot:A', {'action_id': 'choose', 'choice': 'draw'})
        self.assertTrue(result.ok, result.summary); self.resolve(session); self.replay(session)

    def test_target_dispatch_omission_mutant_is_killed_by_actual_cast(self):
        from quorune.rules import target_announcements as owner
        with mock.patch.object(owner, 'dispatch_target_announcements', return_value=None):
            session = self.session(1150208); engine = session.engine
            victim = self.add(engine, 'Tethered Skirge', ref='SKIRGE')
            self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat='B')
            action = next(row for row in self.priority(session, 'B') if row['id'] == 'cast:GROWTH')
            result = session.act('pilot:B', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary)
            with self.assertRaises(AssertionError):
                self.assertEqual(2, len(session.state.stack), 'Actual target occurrence must create its printed trigger')

    def test_actual_targeted_illusion_sacrifices_before_spell_and_replays(self):
        session = self.session(1150209); engine = session.engine
        victim = self.add(engine, 'Tar Pit Warrior', ref='WARRIOR')
        self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat='B')
        action = next(row for row in self.priority(session, 'B') if row['id'] == 'cast:GROWTH')
        self.checkpoint(session)
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.assertEqual(2, len(session.state.stack))
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertFalse(session.state.stack); self.replay(session)

    def test_targeted_blink_spell_creates_its_own_illusion_sacrifice_trigger(self):
        session = self.session(1150210); engine = session.engine
        victim = self.add(engine, 'Tar Pit Warrior', ref='WARRIOR')
        self.add(engine, 'Giant Growth', ref='GROWTH', zone='hand', seat='B')
        self.add(engine, 'Cloudshift', ref='BLINK', zone='hand')
        engine.state.players['A'].mana_pool.update({'W': 1})
        action = next(row for row in self.priority(session, 'B') if row['id'] == 'cast:GROWTH')
        self.checkpoint(session)
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        for _ in range(4):
            if session.state.priority_player == 'A': break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:BLINK')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, len(session.state.stack))
        # Cloudshift itself targets the Warrior and therefore creates another
        # sacrifice trigger above the blink. Both must be allowed to resolve.
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.replay(session)

    def test_actual_triggered_target_choice_announces_only_committed_target(self):
        session = self.session(1150211); engine = session.engine
        victim = self.add(engine, 'Tethered Skirge', ref='SKIRGE')
        self.add(engine, 'Oran-Rief Recluse', ref='RECLUSE', zone='hand', seat='B')
        self.priority(session, 'B', {'G': 2, 'C': 4})
        engine.state.active_player = 'B'
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('B'); engine.pump()
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'] == 'cast:RECLUSE')
        self.checkpoint(session)
        result = session.act('pilot:B', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertEqual(40, session.state.players['A'].life)
        result = session.act('pilot:B', {'action_id': 'choose', 'targets': [victim.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, len(session.state.stack))
        self.resolve(session)
        self.assertEqual(39, session.state.players['A'].life)
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.replay(session)
