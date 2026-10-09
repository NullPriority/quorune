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

from quorune.compiler.kicked_spell_conditions import kicked_spell_condition_template
from quorune.oracle_ir import _reviewed_effect_template
from quorune.resolution_conditions import validate_resolution_condition_instruction
from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
from quorune.semantic_choices import SemanticChoiceContext, SnapshotSemanticChoiceQuery, SemanticChoiceError


class KickedSpellConditionTests(unittest.TestCase):
    def test_kicked_result_is_separate_from_mandatory_prefix(self):
        result = kicked_spell_condition_template(
            "Return target nonland permanent to its owner's hand. If this spell was kicked, draw a card.",
            compile_component=lambda text: _reviewed_effect_template(text, card_name='Witness'),
        )
        self.assertIsNotNone(result)
        effects = result[1]
        self.assertEqual('bounce', effects[0]['op'])
        self.assertEqual('$context.kicked', effects[1]['cast_fact'])
        self.assertEqual('draw', effects[1]['effects'][0]['op'])
        self.assertIsNotNone(result[2])

    def test_runtime_cast_fact_accepts_only_sealed_boolean_and_locked_controller(self):
        result = kicked_spell_condition_template('If this spell was kicked, draw a card.', compile_component=lambda text: _reviewed_effect_template(text, card_name='Witness'))
        effect = {**result[1][0], 'player': 'A', 'effects': [{'op': 'draw', 'player': 'A', 'count': 1, 'private': True}]}
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'))
        context = SemanticChoiceContext(actor='A', stack_ref='STACK', stack_controller='A', stack_label='Witness', source_ref=None, card_ref=None, semantic_program_id='Witness', semantic_program_version=1, query=query)
        handler = PublicResolutionConditionHandler()
        for paid in (False, True):
            prepared = handler.prepare({**effect, 'cast_fact': paid}, context)
            self.assertEqual(1 if paid else 0, len(prepared.auto_continue.prepend_effects))
        for value in (None, 1, 'yes', '$context.kicked'):
            with self.subTest(value=value), self.assertRaises((ValueError, SemanticChoiceError)):
                handler.prepare({**effect, 'cast_fact': value}, context)
        with self.assertRaises(SemanticChoiceError):
            handler.prepare({**effect, 'player': 'B', 'cast_fact': True}, context)

    def test_cast_condition_rejects_self_replacement_open_targets_and_unknown_fields(self):
        for text in ('If this spell was kicked, instead draw two cards.', 'If this spell was kicked, destroy target creature.', 'If this spell was kicked, draw X cards.'):
            with self.subTest(text=text):
                self.assertIsNone(kicked_spell_condition_template(text, compile_component=lambda body: _reviewed_effect_template(body, card_name='Witness')))
        result = kicked_spell_condition_template('If this spell was kicked, draw a card.', compile_component=lambda text: _reviewed_effect_template(text, card_name='Witness'))
        with self.assertRaises(ValueError):
            validate_resolution_condition_instruction({**result[1][0], 'unknown': True})

    def test_kicked_cast_fact_dependency_mutants_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        record = payment_record('Kicker {1}{U}\nDraw two cards. If this spell was kicked, you gain 3 life.', type_line='Sorcery')
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for id in ('resolution.effect.fixed_cast_fact', 'resolution.effect.public_condition', 'casting.kicker.fixed_mana'):
            value = deepcopy(raw); cap = next(r for r in value['capabilities'] if r['id'] == id)
            cap.update(status='blocked', blockers=['Independent cast-fact mutation'])
            with self.subTest(id=id):
                self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(value)).status)


class KickedSpellActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'kicked-spell.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json', ROOT / 'tests/fixtures/kicked-spell-conditions.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Kicked spell deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

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
        card = CardInstance(object_id='kicked-spell:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=seat, controller=seat, zone=zone, zone_timestamp=engine._next_zone_timestamp(), known_to=[seat] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card; engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self, session, source, mana):
        engine = session.engine; engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('A'); engine.pump()
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in engine.semantics.programs_for_oracle(source.oracle_id)))
        return next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state); session.commands.clear(); session.decisions.clear()

    def resolve(self, session):
        for _ in range(28):
            if session.state.pending_decision and session.state.pending_decision.kind != 'priority': return
            if not session.state.stack: return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Kicked spell did not finish')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kicked-spell-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_real_roil_normal_and_kicked_cast_keep_prefix_and_optional_result_scope(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                session = self.session(7023320 + int(kicked)); engine = session.engine
                spell = self.add(engine, 'Into the Roil', ref='ROIL')
                target = self.add(engine, 'Generic Payment Commander', ref='TARGET', zone='battlefield', seat='B')
                action = self.ready(session, spell, {'U': 2, 'C': 2})
                hand_before = len(session.state.players['A'].zones['hand']); self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'cost_option': 'kicked' if kicked else 'normal', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertIs(session.state.stack[-1].context['kicked'], kicked)
                self.resolve(session)
                self.assertEqual('hand', session.state.cards[target.object_id].zone)
                self.assertEqual(hand_before if kicked else hand_before - 1, len(session.state.players['A'].zones['hand']))
                self.replay(session)

    def test_illegal_roil_target_skips_kicked_draw_even_though_fact_is_paid(self):
        session = self.session(7023322); engine = session.engine
        spell = self.add(engine, 'Into the Roil', ref='ROIL')
        target = self.add(engine, 'Generic Payment Commander', ref='TARGET', zone='battlefield', seat='B')
        action = self.ready(session, spell, {'U': 2, 'C': 2})
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        engine.move_card(target.object_id, 'graveyard', reason='Independent invalid-target boundary')
        hand_before = len(session.state.players['A'].zones['hand']); self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(hand_before, len(session.state.players['A'].zones['hand'])); self.replay(session)

    def test_real_espionage_private_discard_after_prefix_pending_load_and_replay(self):
        session = self.session(7023323); engine = session.engine
        spell = self.add(engine, 'Phyrexian Espionage', ref='ESPIONAGE')
        action = self.ready(session, spell, {'U': 1, 'B': 1, 'C': 3})
        before = len(session.state.players['A'].zones['hand']); self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual(before + 1, len(session.state.players['A'].zones['hand']))
        self.assertEqual(['B'], session.state.pending_decision.actors)
        for seat in 'ACD': self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-espionage'; session.save(path)
            session = CommanderSession.load(self.db, path)
        for _ in range(4):
            if not session.state.pending_decision or session.state.pending_decision.kind == 'priority': break
            principal = session.pending_principals()[0]; seat = principal.rsplit(':', 1)[1]
            card = session.state.cards[session.state.players[seat].zones['hand'][0]]
            result = session.act(principal, {'action_id': 'choose', 'cards': [card.ref]})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(before + 1, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_missing_cast_fact_is_rejected_before_conditional_result(self):
        session = self.session(7023324); engine = session.engine
        spell = self.add(engine, 'Phyrexian Espionage', ref='ESPIONAGE')
        action = self.ready(session, spell, {'U': 1, 'C': 2})
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'normal', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        item = session.state.stack[-1]; item.context.pop('kicked')
        # The missing-fact behavior is verified directly at the registered
        # owner, without representing absence as an unpaid choice.
        handler = PublicResolutionConditionHandler()
        context = engine._semantic_choice_context(item, 'A', {})
        wrapper = {'op': 'apply_if_public_condition', 'schema_version': 2, 'player': 'A', 'condition': {'schema_version': 1, 'kind': 'kicked_cast'}, 'cast_fact': None, 'effects': [{'op': 'draw', 'player': 'A', 'count': 1, 'private': True}], 'mechanic_ids': ['cr-121-drawing-a-card'], 'prefix_mechanic_ids': []}
        with self.assertRaises(SemanticChoiceError): handler.prepare(wrapper, context)

    def test_actual_kicked_counter_result_keeps_source_attribution_after_prefix(self):
        session = self.session(7023325); engine = session.engine
        spell = self.add(engine, 'Strength of the Coalition', ref='COALITION')
        creature = self.add(engine, 'Generic Payment Commander', ref='CREATURE', zone='battlefield')
        other = self.add(engine, 'Generic Payment Commander', ref='OTHER', zone='battlefield', seat='B')
        action = self.ready(session, spell, {'G': 1, 'W': 1, 'C': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [creature.ref], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual(1, session.state.cards[creature.object_id].counters.get('+1/+1'))
        self.assertFalse(session.state.cards[other.object_id].counters)
        self.replay(session)

    def test_copy_retains_kicked_fact_without_repaying_cost_owner(self):
        session = self.session(7023326); engine = session.engine
        spell = self.add(engine, 'Into the Roil', ref='ROIL')
        first = self.add(engine, 'Generic Payment Commander', ref='FIRST', zone='battlefield', seat='B')
        second = self.add(engine, 'Generic Payment Commander', ref='SECOND', zone='battlefield', seat='C')
        action = self.ready(session, spell, {'U': 2, 'C': 2})
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [first.ref], 'cost_option': 'kicked', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        original = session.state.stack[-1]
        copied = engine._copy_stack_item(controller='A', target=original, targets=[second.ref], target_groups={}, reason='Independent copied Kicker choice witness')
        self.assertIs(True, copied.context['kicked'])
        self.checkpoint(session); hand_before = len(session.state.players['A'].zones['hand'])
        self.resolve(session)
        self.assertEqual(hand_before + 2, len(session.state.players['A'].zones['hand']))
        self.assertEqual('hand', session.state.cards[first.object_id].zone)
        self.assertEqual('hand', session.state.cards[second.object_id].zone)
        self.replay(session)

    def test_unconditional_cast_result_mutant_is_killed_by_actual_unpaid_cast(self):
        original = PublicResolutionConditionHandler.prepare
        def unconditional(handler, effect, context):
            return original(handler, {**effect, 'cast_fact': True} if effect.get('schema_version') == 2 else effect, context)
        with mock.patch.object(PublicResolutionConditionHandler, 'prepare', unconditional):
            session = self.session(7023327); engine = session.engine
            spell = self.add(engine, 'Into the Roil', ref='ROIL')
            target = self.add(engine, 'Generic Payment Commander', ref='TARGET', zone='battlefield', seat='B')
            action = self.ready(session, spell, {'U': 1, 'C': 1})
            before = len(session.state.players['A'].zones['hand'])
            result = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'cost_option': 'normal', 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary); self.resolve(session)
            with self.assertRaises(AssertionError):
                self.assertEqual(before - 1, len(session.state.players['A'].zones['hand']))

    def test_actual_scalar_counter_quantity_reads_current_power_and_replays(self):
        session = self.session(1070301); engine = session.engine
        spell = self.add(engine, "Soul's Might", ref='MIGHT')
        target = self.add(engine, 'Generic Payment Commander', ref='TARGET', zone='battlefield')
        target.counters['+1/+1'] = 1
        action = self.ready(session, spell, {'G': 1, 'C': 4})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual(4, session.state.cards[target.object_id].counters['+1/+1'], 'Current power 3 adds three counters')
        self.replay(session)

    def test_zero_scalar_counter_quantity_commits_price_and_places_no_counters(self):
        session = self.session(1070302); engine = session.engine
        spell = self.add(engine, "Soul's Might", ref='ZERO-MIGHT')
        target = self.add(engine, 'Generic Payment Commander', ref='ZERO-TARGET', zone='battlefield')
        target.annotations['continuous_power'] = 0
        action = self.ready(session, spell, {'G': 1, 'C': 4})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertFalse(session.state.cards[target.object_id].counters)
        self.assertEqual(0, session.state.players['A'].mana_pool['G']); self.replay(session)
