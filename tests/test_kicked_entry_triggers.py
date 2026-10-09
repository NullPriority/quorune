from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class KickedEntryCompilerTests(unittest.TestCase):
    def test_kicked_entry_condition_and_independent_targets_keep_source_spans(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for body in ('destroy target creature with flying.', 'create a 1/1 white Soldier creature token.', 'tap up to two target creatures.', 'you gain 10 life.'):
            text = 'When this creature enters, if it was kicked, ' + body
            with self.subTest(body=body):
                ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
                node = ir.faces[0].nodes[0]
                self.assertEqual('fixed-kicked-entry-trigger-v1', node.template_id)
                self.assertEqual('permanent.enter.self', node.event)
                self.assertEqual({'field': 'cast_option', 'op': 'eq', 'value': 'kicked'}, node.event_condition)
                self.assertEqual(text, ir.faces[0].oracle_text[node.span.start:node.span.end])
                self.assertIn('trigger.entry.fixed_kicked_result', node.capability_dependencies)
                self.assertEqual(registry.closure(('trigger.entry.fixed_kicked_result',), profile='commander_review').trusted, node.exact)

    def test_kicked_entry_unknown_payment_results_and_siblings_remain_residual(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for text in (
            'When this creature enters, if it was kicked twice, draw a card.',
            'When another creature enters, if it was kicked, draw a card.',
            'When this creature enters, if it was kicked, perform an unrepresented ritual.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(payment_record(text), capability_registry=registry)
                self.assertNotEqual('exact', ir.status)
        text = 'Kicker {G}\nWhen this creature enters, if it was kicked, draw a card.\nWhenever a player sneezes, draw a card.'
        ir = compile_oracle_card(payment_record(text), capability_registry=registry)
        self.assertNotEqual('exact', ir.status)

    def test_kicked_entry_condition_and_dependency_mutants_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        record = payment_record('When this creature enters, if it was kicked, draw a card.')
        for dependency in ('trigger.entry.fixed_kicked_result', 'casting.kicker.fixed_mana', 'trigger.event.normalized_zone_change', 'trigger.placement.apnap'):
            blocked = deepcopy(raw); row = next(r for r in blocked['capabilities'] if r['id'] == dependency)
            row.update(status='blocked', blockers=['Independent kicked entry dependency mutation'])
            with self.subTest(dependency=dependency):
                self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(blocked)).status)
        from quorune.compiler import kicked_entry_trigger_nodes as owner
        with mock.patch.object(owner, 'fixed_kicked_entry_trigger_node', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=load_default_capability_registry()).status)


class KickedEntryActionTests(unittest.TestCase):
    # CR 702.33e/f, 603.4 and 400.7: paid kicker is an entry cast fact;
    # returning the card creates a new object without that payment history.
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'kicked-entry.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json',
            ROOT / 'tests/fixtures/kicked-entry-triggers.json',
        ], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Kicked entry deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in 'ABCD'}, first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session); engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        records = tuple(record for record in self.db.iter_cards() if compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review').status == 'exact')
        register_generated_programs(self.db, engine.semantics, records, trust_level='trusted', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='hand', seat='A'):
        record = self.db.lookup(name)
        card = CardInstance(object_id='kicked:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=seat, controller=seat, zone=zone, zone_timestamp=engine._next_zone_timestamp(), known_to=[seat] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card; engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self, session, source, mana):
        engine = session.engine; engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('A'); engine.pump()
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs); self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        return next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state); session.commands.clear(); session.decisions.clear()

    def advance_to(self, session, predicate):
        for _ in range(24):
            if predicate(session): return
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Kicked entry did not reach its expected boundary')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kicked-entry-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_actual_kicked_and_unkicked_druid_casts_pay_exact_cost_and_replay(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                session = self.session(7023301 + int(kicked)); engine = session.engine
                source = self.add(engine, 'Krosan Druid', ref='DRUID')
                action = self.ready(session, source, {'G': 2, 'C': 6})
                self.assertEqual({'normal', 'kicked'}, {option['id'] for option in action['cost_options']})
                self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked' if kicked else 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(0 if kicked else 1, engine.state.players['A'].mana_pool['G'])
                self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
                self.assertEqual(1 if kicked else 0, len(session.state.stack))
                if kicked:
                    self.assertEqual('kicked', session.state.stack[-1].context['cast_option'])
                    self.advance_to(session, lambda s: not s.state.stack)
                self.assertEqual(50 if kicked else 40, session.state.players['A'].life)
                self.replay(session)

    def test_actual_kicked_entry_trigger_survives_blink_and_new_entry_has_no_kicker(self):
        session = self.session(7023303); engine = session.engine
        source = self.add(engine, 'Krosan Druid', ref='DRUID')
        spell = self.add(engine, 'Cloudshift', ref='BLINK')
        action = self.ready(session, source, {'G': 2, 'C': 8, 'W': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack)); old_identity = source.logical_object_id
        self.advance_to(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'priority' and s.state.priority_player == 'A')
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:BLINK')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].logical_object_id != old_identity)
        self.assertEqual(1, len(session.state.stack), 'A returned new object must not trigger another kicked ability')
        self.advance_to(session, lambda s: not s.state.stack)
        self.assertEqual(50, session.state.players['A'].life); self.replay(session)

    def test_actual_kicked_private_library_choice_rolls_back_and_loads_pending(self):
        session = self.session(7023304); engine = session.engine
        source = self.add(engine, 'Vineshaper Prodigy', ref='PRODIGY')
        action = self.ready(session, source, {'G': 1, 'U': 1, 'C': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind != 'priority')
        packet = session.packet('pilot:A', full=True)['decision']
        for seat in 'BCD': self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
        before = authoritative_state_hash(session.state)
        bad = session.act('pilot:B', {'action_id': 'choose'})
        self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-kicked-library'; session.save(path)
            session = CommanderSession.load(self.db, path)
        packet = session.packet('pilot:A', full=True)['decision']
        ctx = packet['ctx']
        self.assertEqual(3, len(ctx['objects']))
        refs = [card['id'] for card in ctx['objects']]
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': {'hand': [refs[0]], 'bottom': refs[1:]}})
        self.assertTrue(result.ok, result.summary)
        self.replay(session)

    def test_kicked_targets_are_chosen_only_on_paid_entry_and_illegal_choices_roll_back(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                session = self.session(7023305 + int(kicked)); engine = session.engine
                source = self.add(engine, 'Oran-Rief Recluse', ref='RECLUSE')
                flying = self.add(engine, 'Air Elemental', ref='FLYING', zone='battlefield', seat='B')
                ground = self.add(engine, 'Llanowar Elves', ref='GROUND', zone='battlefield', seat='C')
                action = self.ready(session, source, {'G': 2, 'C': 4})
                self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked' if kicked else 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
                if not kicked:
                    self.assertFalse(session.state.stack)
                    self.assertEqual('priority', session.state.pending_decision.kind)
                else:
                    self.assertEqual('semantic.target', session.state.pending_decision.kind)
                    before = authoritative_state_hash(session.state)
                    for principal, ref in (('pilot:B', flying.ref), ('pilot:A', ground.ref)):
                        bad = session.act(principal, {'action_id': 'choose', 'targets': [ref]})
                        self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(session.state))
                    result = session.act('pilot:A', {'action_id': 'choose', 'targets': [flying.ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.advance_to(session, lambda s: not s.state.stack)
                    self.assertEqual('graveyard', session.state.cards[flying.object_id].zone)
                self.assertEqual('battlefield', session.state.cards[ground.object_id].zone)
                self.replay(session)

    def test_kicked_optional_targets_allow_zero_without_hiding_legal_targets(self):
        session = self.session(7023307); engine = session.engine
        source = self.add(engine, 'Kitesail Cleric', ref='CLERIC')
        target = self.add(engine, 'Air Elemental', ref='LEGAL', zone='battlefield', seat='B')
        action = self.ready(session, source, {'W': 2, 'C': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
        self.assertIn(target.ref, str(session.packet('pilot:A', full=True)['decision']))
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': []})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: not s.state.stack)
        self.assertFalse(session.state.cards[target.object_id].tapped); self.replay(session)

    def test_kicked_condition_omission_mutant_is_killed_by_actual_unkicked_cast(self):
        from dataclasses import replace
        from quorune.compiler import kicked_entry_trigger_nodes as owner
        original = owner.fixed_kicked_entry_trigger_node
        def omit(**kwargs):
            node = original(**kwargs)
            return replace(node, event_condition=None) if node else None
        with mock.patch.object(owner, 'fixed_kicked_entry_trigger_node', omit):
            with self.assertRaises(AssertionError):
                session = self.session(7023309); source = self.add(session.engine, 'Krosan Druid', ref='UNPAID')
                action = self.ready(session, source, {'G': 1, 'C': 2})
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
                self.assertFalse(session.state.stack, 'An unpaid entry must not trigger the kicked result')

    def test_actual_kicked_relic_copies_do_not_inherit_kicker_and_create_no_loop(self):
        session = self.session(7023308); engine = session.engine
        source = self.add(engine, 'Skyclave Relic', ref='RELIC')
        action = self.ready(session, source, {'C': 6})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack))
        self.advance_to(session, lambda s: not s.state.stack)
        copies = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(2, len(copies))
        self.assertTrue(all(card.tapped for card in copies))
        self.assertTrue(all(card.annotations.get('kicker_paid') is not True for card in copies))
        self.replay(session)

    def test_actual_kicked_warhorse_creates_one_token_after_paid_entry(self):
        session = self.session(7023310); engine = session.engine
        source = self.add(engine, 'Phyrexian Warhorse', ref='WARHORSE')
        action = self.ready(session, source, {'B': 1, 'W': 1, 'C': 4})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.advance_to(session, lambda s: s.state.cards[source.object_id].zone == 'battlefield')
        self.assertEqual(1, len(session.state.stack))
        self.advance_to(session, lambda s: not s.state.stack)
        soldiers = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(1, len(soldiers)); self.assertEqual('Soldier', soldiers[0].printed_name)
        self.replay(session)
