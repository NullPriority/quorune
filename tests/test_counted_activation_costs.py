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


class CountedActivationCompilerTests(unittest.TestCase):
    def test_fixed_counted_costs_preserve_query_count_and_siblings(self):
        from test_fixed_activated_zone_change_costs import fixture_card
        registry = load_default_capability_registry()
        for cost, count, operation, subtype in (
            ('Sacrifice three Treasures', 3, 'sacrifice_one', 'treasure'),
            ('Sacrifice two other Goblins', 2, 'sacrifice_one', 'goblin'),
            ('Discard two cards', 2, 'discard_one', None),
            ('Exile two creature cards from your graveyard', 2, 'exile_one_from_graveyard', None),
            ("Return three lands you control to their owner's hand", 3, 'return_one_to_owner_hand', None),
        ):
            with self.subTest(cost=cost):
                record = fixture_card('Counted cost witness', cost + ': Draw a card.')
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status)
                choice = ir.faces[0].nodes[0].cost['choices'][0]
                self.assertEqual(count, choice['n']); self.assertEqual(operation, choice['k'])
                self.assertEqual([subtype] if subtype else [], choice['q']['subtypes_all'])
                self.assertEqual(cost + ': Draw a card.', ir.faces[0].nodes[0].text)
        ir = compile_oracle_card(fixture_card('Sibling witness', 'Sacrifice three Treasures: Draw a card.\nWhenever a player sneezes, draw a card.'), capability_registry=registry)
        self.assertTrue(ir.faces[0].nodes[0].exact); self.assertNotEqual('exact', ir.status)

    def test_counted_cost_rejects_open_qualities_partial_groups_and_unknown_counts(self):
        from test_fixed_activated_zone_change_costs import fixture_card
        registry = load_default_capability_registry()
        for cost in (
            'Sacrifice eleven Goblins', 'Sacrifice any number of Goblins',
            'Discard two cards at random', 'Sacrifice two creatures and an artifact',
            'Sacrifice two creatures with total power 4', 'Sacrifice two Boguses',
            'Exile two other cards from your graveyard', 'Return two target lands to their owners hands',
        ):
            with self.subTest(cost=cost):
                ir = compile_oracle_card(fixture_card('Boundary witness', cost + ': Draw a card.'), capability_registry=registry)
                self.assertNotEqual('exact', ir.status)

    def test_counted_cost_compiler_and_dependency_mutants_fail_closed(self):
        from test_fixed_activated_zone_change_costs import fixture_card
        import json
        from quorune.rules.capabilities import CapabilityRegistry
        registry = load_default_capability_registry()
        record = fixture_card('Counted cost witness', 'Sacrifice three Treasures: Draw a card.')
        from quorune.compiler import spell_additional_cost_templates as owner
        with mock.patch.object(owner, 'fixed_counted_zone_change_cost_clause', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        capability = next(row for row in raw['capabilities'] if row['id'] == 'activation.selected_zone_change.fixed')
        capability['status'] = 'blocked'; capability['blockers'] = ['Independent counted-cost dependency mutation']
        self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(raw)).status)

    def test_counted_replacement_sequence_rejects_unselected_objects_and_open_fields(self):
        from quorune.rules.activation_zone_change_costs import counted_activation_replacement_selections
        from quorune.replacement.model import ReplacementEffectError
        for row in (
            {'object_ref': 'OTHER', 'selection': 'replacement'},
            {'object_ref': 'FIRST', 'selection': {'effect_id': 'replacement', 'event_id': 'zone.change:1:1:OTHER'}},
            {'object_ref': 'FIRST', 'selection': 'replacement', 'unknown': True},
            {'object_ref': 'FIRST', 'selection': {'effect_id': '', 'event_id': 'zone.change:1:1:FIRST'}},
        ):
            with self.subTest(row=row), self.assertRaises(ReplacementEffectError):
                counted_activation_replacement_selections({'_counted_cost_replacement_sequence': [row]}, ('FIRST', 'SECOND'))


class CountedActivationActionTests(unittest.TestCase):
    # Independent CR 118.3/601.2h/602.2b contract: the complete fixed cost is
    # paid before the stack ability; 701.21a sacrifices controlled objects to
    # their owners' graveyards. One instruction moves the set simultaneously.
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'counted-cost.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json',
            ROOT / 'tests/fixtures/scryfall-exact-lists.json',
            ROOT / 'tests/fixtures/counted-activation-costs.json',
        ], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Counted payment deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in 'ABCD'}, first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session); engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        records = tuple(record for record in self.db.iter_cards() if compile_oracle_card(
            record, capability_registry=self.registry, capability_profile='commander_review',
        ).status == 'exact')
        register_generated_programs(self.db, engine.semantics, records, trust_level='provisional', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='battlefield', owner='A', controller=None):
        record = self.db.lookup(name); controller = controller or owner
        card = CardInstance(object_id='counted:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=owner, controller=controller, zone=zone, zone_timestamp=engine._next_zone_timestamp(), acquired_control_turn_count=-1, known_to=[owner] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card
        engine.state.players[controller if zone == 'battlefield' else owner].zones[zone].append(card.object_id)
        return card

    def ready(self, session, source, mana=None):
        engine = session.engine; engine.state.started = True
        engine.state.active_player = 'A'; engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana or {'C': 8, 'R': 1})
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('A'); engine.pump()
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs); self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        return session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear(); session.decisions.clear()

    def resolve(self, session):
        for _ in range(28):
            if session.state.pending_decision and session.state.pending_decision.kind != 'priority': return
            if not session.state.stack: return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Counted cost stack did not resolve')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'counted-cost-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_real_lavamancer_full_graveyard_payment_and_action_rollback_replay(self):
        session = self.session(6020201); engine = session.engine
        source = self.add(engine, 'Grim Lavamancer', ref='LAVAMANCER')
        cards = [self.add(engine, 'Generic Payment Plains', ref=ref, zone='graveyard') for ref in ('FIRST', 'SECOND')]
        other = self.add(engine, 'Generic Payment Plains', ref='OTHER', zone='graveyard', owner='B')
        action = next(row for row in self.ready(session, source) if row['id'].startswith('activate:LAVAMANCER:'))
        self.assertEqual(2, action['cost_summary']['choose_cost'][0]['n'])
        self.assertEqual({'FIRST', 'SECOND'}, set(action['cost_summary']['choose_cost'][0]['legal_refs']))
        self.checkpoint(session); before = authoritative_state_hash(engine.state)
        for principal, refs in (('pilot:A', ['FIRST']), ('pilot:A', ['FIRST', 'FIRST']), ('pilot:A', ['FIRST', 'OTHER']), ('pilot:B', ['FIRST', 'SECOND'])):
            bad = session.act(principal, {'action_id': action['id'], 'cost_cards': refs, 'targets': ['B'], 'pay': 'auto'})
            self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(engine.state))
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [card.ref for card in cards], 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(session.state.cards[card.object_id].zone == 'exile' for card in cards))
        self.assertEqual('graveyard', session.state.cards[other.object_id].zone)
        self.resolve(session); self.assertEqual(38, session.state.players['B'].life)
        self.replay(session)

    def test_real_goblin_warrens_sacrifices_one_simultaneous_controlled_set(self):
        session = self.session(6020202); engine = session.engine
        source = self.add(engine, 'Goblin Warrens', ref='WARRENS')
        self.add(engine, 'Ruthless Knave', ref='GOBLIN-SEED')
        # Independently authored generic creature tokens are cost state, while
        # the printed Warrens ability is admitted through real registration.
        engine.create_token('A', name='Goblin', characteristics={'type_line': 'Creature — Goblin', 'power': '1', 'toughness': '1', 'colors': ['R']}, quantity=2)
        tokens = [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield']
        action = next(row for row in self.ready(session, source) if row['id'].startswith('activate:WARRENS:'))
        self.assertEqual(2, action['cost_summary']['choose_cost'][0]['n'])
        self.checkpoint(session)
        with mock.patch.object(engine, '_move_cards_simultaneously', wraps=engine._move_cards_simultaneously) as movement:
            result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [card.ref for card in tokens], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(any({object_id for object_id, _ in call.args[0]} == {card.object_id for card in tokens} for call in movement.call_args_list))
        self.resolve(session)
        current = [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(3, len(current)); self.replay(session)

    def test_counted_sacrifice_replacements_suspend_whole_cost_load_and_resume_once(self):
        session = self.session(6020203); engine = session.engine
        source = self.add(engine, 'Eater of Hope', ref='EATER')
        cards = [self.add(engine, 'Llanowar Elves', ref=ref) for ref in ('FIRST', 'SECOND')]
        target = self.add(engine, 'Llanowar Elves', owner='B', ref='TARGET')
        self.add(engine, 'Generic Payment Destination Replacement', owner='B', ref='EXILE-ONE')
        self.add(engine, 'Generic Payment Competing Replacement', owner='C', ref='EXILE-TWO')
        action = next(row for row in self.ready(session, source, {'B': 1, 'C': 2}) if row['id'].startswith('activate:EATER:') and row['cost_summary'].get('choose_cost', [{}])[0].get('n') == 2)
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [card.ref for card in cards], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIn('replacement', session.state.pending_decision.kind)
        self.assertTrue(all(session.state.cards[card.object_id].zone == 'battlefield' for card in cards))
        self.assertFalse(session.state.stack)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-counted-cost'; session.save(path)
            session = CommanderSession.load(self.db, path)
        for _ in range(8):
            decision = session.state.pending_decision
            if decision is None or 'replacement' not in decision.kind: break
            principal = session.pending_principals()[0]; packet = session.packet(principal, full=True)['decision']
            result = session.act(principal, {'action_id': 'choose', 'replacement': packet['ctx']['options'][0]['id']})
            self.assertTrue(result.ok, result.summary)
        for card in cards:
            current = session.state.cards[card.object_id]
            self.assertEqual('exile', current.zone); self.assertEqual(1, sum(current.counters.values()))
        self.assertEqual(1, len(session.state.stack)); self.resolve(session)
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.replay(session)

    def test_counted_discard_activation_is_private_complete_and_replays(self):
        session = self.session(6020204); engine = session.engine
        source = self.add(engine, 'Zombie Infestation', ref='INFESTATION')
        cards = [self.add(engine, 'Generic Payment Plains', ref=ref, zone='hand') for ref in ('FIRST', 'SECOND')]
        self.add(engine, 'Generic Payment Plains', ref='OTHER', zone='hand', owner='B')
        action = next(row for row in self.ready(session, source) if row['id'].startswith('activate:INFESTATION:'))
        choice = action['cost_summary']['choose_cost'][0]
        self.assertEqual(2, choice['n']); self.assertNotIn('OTHER', choice['legal_refs'])
        for seat in 'BCD':
            self.assertNotIn('FIRST', str(session.packet('pilot:' + seat, full=True)))
            self.assertNotIn('SECOND', str(session.packet('pilot:' + seat, full=True)))
        self.checkpoint(session); before = authoritative_state_hash(engine.state)
        for refs in (['FIRST'], ['FIRST', 'FIRST'], ['FIRST', 'OTHER']):
            result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': refs})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
        with mock.patch.object(engine, '_move_cards_simultaneously', wraps=engine._move_cards_simultaneously) as movement:
            result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, movement.call_count)
        self.assertTrue(all(session.state.cards[card.object_id].zone == 'graveyard' for card in cards))
        self.resolve(session)
        zombies = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(1, len(zombies)); self.replay(session)

    def test_counted_cost_sequential_movement_mutant_is_killed(self):
        from quorune.engine import CommanderEngine
        def sequential(host, changes, *, reason, transition_kinds=None, **kwargs):
            result = []
            for object_id, destination in changes:
                host.move_card(object_id, destination, reason=reason, transition_kind=transition_kinds[object_id])
                result.append(host.state.cards[object_id])
            return result
        with mock.patch.object(CommanderEngine, '_move_cards_simultaneously', sequential):
            with self.assertRaises(AssertionError):
                self.test_simultaneous_sacrifice_seals_each_departure_observers_before_either_leaves()

    def test_simultaneous_sacrifice_seals_each_departure_observers_before_either_leaves(self):
        session = self.session(6020205); engine = session.engine
        source = self.add(engine, 'Goblin Warrens', ref='WARRENS')
        first = self.add(engine, 'Pashalik Mons', ref='FIRST')
        second = self.add(engine, 'Pashalik Mons', ref='SECOND')
        # Two same-name legendary objects are initially present only until this
        # paid cost; no priority is granted before they are sacrificed together.
        ability = next(a for a in engine._activated_abilities(source) if a.choices and a.choices[0].count == 2)
        from quorune.rules.activation_costs import pay_counted_zone_change_activation_cost
        paid = pay_counted_zone_change_activation_cost(engine, actor='A', source=source, choice=ability.choices[0], response={'cost_cards': [first.ref, second.ref]})
        self.assertEqual([first.object_id, second.object_id], paid)
        triggers = [item for batch in engine.state.pending_trigger_batches for item in batch.items]
        triggers.extend(engine.state.stack)
        self.assertEqual(4, len(triggers), 'Each departing source must observe both simultaneous Goblin deaths')

    def test_real_counted_return_cost_uses_current_control_and_owner_hand(self):
        session = self.session(6020206); engine = session.engine
        source = self.add(engine, 'Soratami Mirror-Mage', ref='MIRROR-MAGE')
        lands = [self.add(engine, 'Island', ref=ref, owner='B' if index == 0 else 'A', controller='A') for index, ref in enumerate(('FIRST', 'SECOND', 'THIRD'))]
        target = self.add(engine, 'Llanowar Elves', ref='TARGET', owner='B')
        opposing = self.add(engine, 'Island', ref='OPPONENT', owner='B')
        action = next(row for row in self.ready(session, source, {'C': 3}) if row['id'].startswith('activate:MIRROR-MAGE:'))
        legal = action['cost_summary']['choose_cost'][0]['legal_refs']
        self.assertIn('FIRST', legal); self.assertNotIn(opposing.ref, legal)
        self.checkpoint(session); before = authoritative_state_hash(engine.state)
        bad = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': ['FIRST', 'SECOND', 'OPPONENT'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertFalse(bad.ok); self.assertEqual(before, authoritative_state_hash(engine.state))
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [card.ref for card in lands], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        for card in lands:
            current = session.state.cards[card.object_id]
            self.assertEqual('hand', current.zone)
            self.assertIn(card.object_id, session.state.players[card.owner].zones['hand'])
        self.resolve(session); self.assertEqual('hand', session.state.cards[target.object_id].zone)
        self.replay(session)
