from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.public_zone_conditions import public_zone_condition
from quorune.public_zone_condition_model import public_zone_fact_value, public_zone_condition_capabilities, PUBLIC_ZONE_CONDITION_CAPABILITY as CAPABILITY
from quorune.continuous_conditions import FixedPublicStateConditionSpec
from scripts.build_test_database import build_fixture_database
import test_receiver_query_unions as controls
from quorune.rules.evidence_storage import read_evidence_document


class PublicZoneConditionModelTests(unittest.TestCase):
    def state(self):
        players = {s: SimpleNamespace(in_game=True, zones={'hand': [s+'hand'], 'library': list(range(21))}) for s in 'ABCD'}
        return SimpleNamespace(turn_order=list('ABCD'), players=players, cards={})

    def test_zone_facts_preserve_player_owner_and_single_library_quantifiers(self):
        state = self.state()
        state.players['A'].zones['hand'] = []
        self.assertEqual(0, public_zone_fact_value(state, 'A', 'opponent_hand_empty', None))
        state.players['C'].zones['hand'] = []
        self.assertEqual(1, public_zone_fact_value(state, 'A', 'opponent_hand_empty', None))
        state.players['C'].in_game = False
        self.assertEqual(0, public_zone_fact_value(state, 'A', 'opponent_hand_empty', None))
        state.players['A'].zones['library'] = list(range(20))
        self.assertEqual(1, public_zone_fact_value(state, 'A', 'any_library_count_at_most', '20'))
        state.players['A'].zones['library'] = list(range(21))
        self.assertEqual(0, public_zone_fact_value(state, 'A', 'any_library_count_at_most', '20'))
        state.cards['hidden'] = SimpleNamespace(zone='exile', owner='B', controller='A', is_token=False)
        self.assertEqual(1, public_zone_fact_value(state, 'A', 'opponent_owns_exiled_card', None))
        state.cards['hidden'].is_token = True
        self.assertEqual(0, public_zone_fact_value(state, 'A', 'opponent_owns_exiled_card', None))
        state.cards['hidden'].is_token = False
        self.assertEqual(0, public_zone_fact_value(state, 'B', 'opponent_owns_exiled_card', None))

    def test_closed_fact_wire_rejects_identity_and_threshold_scope_drift(self):
        for text in ('an opponent has no cards in hand', 'an opponent owns a card in exile', 'a library has twenty or fewer cards in it'):
            condition = public_zone_condition(text)
            self.assertEqual(condition, FixedPublicStateConditionSpec.from_dict(json.loads(json.dumps(condition.to_dict()))))
            self.assertEqual((CAPABILITY,), public_zone_condition_capabilities(condition.to_dict()))
            for change in ({'amount': 2}, {'amount': True}, {'schema_version': 3}, {'fact_parameter': 'private identity'}):
                with self.assertRaises(ValueError):
                    FixedPublicStateConditionSpec.from_dict({**condition.to_dict(), **change})
        library = public_zone_condition('a library has twenty or fewer cards in it')
        for value in (None, '020', '-1', '2147483648', True, '20.0'):
            with self.assertRaises(ValueError):
                FixedPublicStateConditionSpec.from_dict({**library.to_dict(), 'fact_parameter': value})
        for text in ('all libraries have twenty or fewer cards in them',
                'libraries have twenty or fewer cards total', 'an opponent has a red card in hand',
                'you own a named card in exile', 'a library has X or fewer cards in it'):
            self.assertIsNone(public_zone_condition(text), text)


NAMES = ('Warden of the Beyond', 'Guul Draz Specter', 'Isleback Spawn')


class PublicZoneConditionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ReceiverQueryUnionRuntimeTests.session
    add = controls.ReceiverQueryUnionRuntimeTests.add
    prepare = controls.ReceiverQueryUnionRuntimeTests.prepare
    checkpoint = controls.ReceiverQueryUnionRuntimeTests.checkpoint
    resolve = controls.ReceiverQueryUnionRuntimeTests.resolve
    replay = controls.ReceiverQueryUnionRuntimeTests.replay
    binding = controls.ReceiverQueryUnionRuntimeTests.binding
    priority = controls.ReceiverQueryUnionRuntimeTests.priority
    private_replay = controls.ReceiverQueryUnionRuntimeTests.private_replay
    cast = controls.ReceiverQueryUnionRuntimeTests.cast
    stats = controls.ReceiverQueryUnionRuntimeTests.stats
    advance = controls.ReceiverQueryUnionRuntimeTests.advance
    attack = controls.ReceiverQueryUnionRuntimeTests.attack
    blockers = controls.ReceiverQueryUnionRuntimeTests.blockers
    block = controls.ReceiverQueryUnionRuntimeTests.block

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'public-zone-conditions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-zone-condition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public zone conditions', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 40)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def hand_size(self, s, seat, count):
        player = s.state.players[seat]
        while len(player.zones['hand']) > count:
            s.engine.move_card(player.zones['hand'][-1], 'library', reason='public count fixture boundary', log=False)
        while len(player.zones['hand']) < count:
            s.engine.move_card(player.zones['library'][-1], 'hand', reason='public count fixture boundary', log=False)

    def library_size(self, s, seat, count):
        player = s.state.players[seat]
        while len(player.zones['library']) > count:
            s.engine.move_card(player.zones['library'][-1], 'graveyard', reason='public library count fixture boundary', log=False)
        while len(player.zones['library']) < count:
            s.engine.move_card(player.zones['graveyard'][-1], 'library', reason='public library count fixture boundary', log=False)

    def test_three_originals_have_complete_admission_and_independent_zone_fact_minimum(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i, name in enumerate(NAMES):
            s = self.session(754100+i)
            source = self.add(s.engine, name, zone='hand')
            self.assertTrue(self.binding(s, source)['strict_capability_ready'], name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
            deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
            missing = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])

    def test_actual_warden_cast_exile_and_return_recompute_bonus_once(self):
        s = self.session(754110)
        source = self.add(s.engine, 'Warden of the Beyond', zone='hand')
        victim = self.add(s.engine, 'Grizzly Bears', seat='B')
        second = self.add(s.engine, 'Grizzly Bears', seat='C', ref='second-exiled-owner')
        swords = self.add(s.engine, 'Generic Zone Condition Exile', zone='hand')
        swords2 = self.add(s.engine, 'Generic Zone Condition Exile', zone='hand', ref='second-exile-spell')
        back = self.add(s.engine, 'Pull from Eternity', zone='hand')
        back2 = self.add(s.engine, 'Pull from Eternity', zone='hand', ref='second-return-spell')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'W': 5})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 2, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 2), self.stats(s, source))
        self.cast(s, swords, [victim.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[victim.object_id].zone)
        self.assertEqual((4, 4), self.stats(s, source))
        self.assertEqual(40, s.state.players['B'].life)
        self.cast(s, swords2, [second.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 4), self.stats(s, source))
        self.cast(s, back, [victim.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 4), self.stats(s, source))
        self.cast(s, back2, [second.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 2), self.stats(s, source))
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertIn('vigilance', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.private_replay(s, private)

    def test_actual_specter_combat_discard_opens_bonus_and_private_draw_closes_it(self):
        s = self.session(754111)
        source = self.add(s.engine, 'Guul Draz Specter', zone='hand')
        draw = self.add(s.engine, 'Sign in Blood', zone='hand')
        blocker = self.add(s.engine, 'Grizzly Bears', seat='B')
        for seat in 'BCD':
            self.hand_size(s, seat, 1)
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'B': 4})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 2, 'B': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 2), self.stats(s, source))
        source = s.state.cards[source.object_id]
        source.acquired_control_turn_count = -1
        self.prepare(s)
        self.checkpoint(s)
        self.attack(s, {source.ref: 'B'})
        for _ in range(40):
            decision = s.state.pending_decision
            if decision is not None and decision.kind == 'choice.apnap':
                break
            self.assertIsNotNone(decision)
            self.assertEqual('priority', decision.kind)
            result = s.act(s.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual('choice.apnap', s.state.pending_decision.kind)
        selected = s.state.cards[s.state.players['B'].zones['hand'][0]].ref
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', {'action_id': 'choose', 'cards': [selected]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act('pilot:B', {'action_id': 'choose', 'cards': [selected]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((5, 5), self.stats(s, source))
        self.advance(s, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(38, s.state.players['B'].life)
        self.prepare(s, mana={'B': 2})
        self.checkpoint(s)
        self.cast(s, draw, ['B'], payment={'B': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 2), self.stats(s, source))
        self.assertEqual(36, s.state.players['B'].life)
        self.private_replay(s, private)

    def test_face_down_exile_counts_owner_without_revealing_identity_or_counting_tokens(self):
        s = self.session(754113)
        source = self.add(s.engine, 'Warden of the Beyond')
        hidden = self.add(s.engine, 'Generic Bound Growth', seat='B', zone='exile', ref='private-facedown-exile')
        hidden.face_down = True
        hidden.controller = 'A'
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s)
        self.checkpoint(s)
        self.assertEqual((4, 4), self.stats(s, source))
        for seat in 'CD':
            packet = s.packet('pilot:'+seat, full=True)
            view = next(card for card in packet['state']['players']['B']['ex'] if card['id'] == hidden.ref)
            self.assertEqual('?', view['n'])
            self.assertNotIn('cid', view)
        for seat in 'AB':
            packet = s.packet('pilot:'+seat, full=True)
            view = next(card for card in packet['state']['players']['B']['ex'] if card['id'] == hidden.ref)
            self.assertEqual(hidden.printed_name, view['n'])
        s.engine.change_control(source.object_id, 'B', reason='exile condition current controller boundary')
        self.assertEqual((2, 2), self.stats(s, source))
        s.engine.change_control(source.object_id, 'A', reason='exile condition restored controller')
        self.assertEqual((4, 4), self.stats(s, source))
        s.engine.move_card(hidden.object_id, 'graveyard', reason='exile condition departure')
        token_ref = s.engine.create_token('B', name='Public token control',
            characteristics={'type_line': 'Token Creature — Bear', 'power': '2', 'toughness': '2'}, reason='exile token boundary')[0]
        token = next(c for c in s.state.cards.values() if c.ref == token_ref)
        s.engine.move_card(token.object_id, 'exile', reason='token is not a card')
        self.assertEqual((2, 2), self.stats(s, source))
        self.prepare(s)
        self.checkpoint(s)
        self.private_replay(s, private)

    def test_library_threshold_and_empty_hand_use_current_seats_without_private_identity(self):
        s = self.session(754114)
        source = self.add(s.engine, 'Isleback Spawn')
        specter = self.add(s.engine, 'Guul Draz Specter')
        for seat in 'ABCD':
            self.library_size(s, seat, 21)
            self.hand_size(s, seat, 1)
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertEqual((4, 8), self.stats(s, source))
        self.library_size(s, 'A', 20)
        self.assertEqual((8, 16), self.stats(s, source))
        self.library_size(s, 'B', 20)
        self.assertEqual((8, 16), self.stats(s, source))
        self.library_size(s, 'A', 21)
        self.assertEqual((8, 16), self.stats(s, source))
        self.library_size(s, 'B', 21)
        self.assertEqual((4, 8), self.stats(s, source))
        self.hand_size(s, 'A', 0)
        self.assertEqual((2, 2), self.stats(s, specter))
        self.hand_size(s, 'D', 0)
        self.assertEqual((5, 5), self.stats(s, specter))
        s.engine.change_control(specter.object_id, 'D', reason='empty hand relation current controller boundary')
        self.assertEqual((5, 5), self.stats(s, specter))
        self.hand_size(s, 'A', 1)
        self.assertEqual((2, 2), self.stats(s, specter))
        self.prepare(s)
        self.checkpoint(s)
        self.private_replay(s, private)

    def test_actual_source_ability_loss_and_bounce_recompute_zone_bonus(self):
        s = self.session(754115)
        source = self.add(s.engine, 'Warden of the Beyond')
        self.add(s.engine, 'Grizzly Bears', seat='B', zone='exile')
        humble = self.add(s.engine, 'Humble', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1, 'W': 1, 'U': 1})
        self.checkpoint(s)
        self.assertEqual((4, 4), self.stats(s, source))
        self.cast(s, humble, [source.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0, 1), self.stats(s, source))
        self.cast(s, bounce, [source.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.private_replay(s, private)

    def test_canonically_verified_zone_condition_withdrawal_blocks_all_new_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        value = deepcopy(raw)
        next(c for c in value['capabilities'] if c['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn public zone condition'])
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, name)
            blockers = [b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers), blockers)

    def test_fresh_original_witnesses_kill_public_zone_producer_and_fact_omissions(self):
        method = 'test_three_originals_have_complete_admission_and_independent_zone_fact_minimum'
        with patch('quorune.compiler.public_zone_conditions.public_zone_condition', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        method = 'test_actual_warden_cast_exile_and_return_recompute_bonus_once'
        with patch('quorune.public_zone_condition_model.public_zone_fact_value', return_value=0):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_spawn_cast_and_mill_cross_a_single_library_boundary(self):
        s = self.session(754112)
        source = self.add(s.engine, 'Isleback Spawn', zone='hand')
        mill = self.add(s.engine, 'Tome Scour', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        for seat in 'ABCD':
            self.library_size(s, seat, 21 if seat == 'B' else 26)
        self.prepare(s, mana={'C': 5, 'U': 3})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 5, 'U': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 8), self.stats(s, source))
        self.cast(s, mill, ['B'])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(16, len(s.state.players['B'].zones['library']))
        self.assertEqual((8, 16), self.stats(s, source))
        self.assertIn('shroud', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.private_replay(s, private)

    def test_existing_avatar_retains_full_support_and_unowned_carriers_remain_residual(self):
        from quorune.oracle_ir import compile_oracle_card
        s = self.session(754116)
        avatar = self.add(s.engine, 'Avatar of Will', zone='hand')
        self.assertTrue(self.binding(s, avatar)['strict_capability_ready'])
        for name in ('Rekindled Flame', 'Shelldock Isle'):
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, name)
            self.assertTrue(ir.material_residuals)
