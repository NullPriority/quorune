from __future__ import annotations

"""CR 111/602/700.2: modal token definitions retain closed child programs."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.ability_fragments import ability_fragment_from_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


CAPABILITY = 'token.creation.typed_ability_definition'
ABILITY = '{T}: Target creature you control gets +1/+0 until end of turn. Activate only as a sorcery.'
CREATE = 'Create a 1/1 red Mercenary creature token with "' + ABILITY + '"'


def setup_database(cls):
    cls.temp = tempfile.TemporaryDirectory()
    path = Path(cls.temp.name) / 'modal-token-programs.sqlite3'
    build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
        ROOT / 'tests/fixtures/event-player-subject-cards.json',
        ROOT / 'tests/fixtures/conditional-query-declaration-cards.json',
        ROOT / 'tests/fixtures/modal-token-program-cards.json'], path)
    cls.db = CardDatabase(path)
    cls.registry = load_default_capability_registry()


class ModalTokenProgramCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_database(cls)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_modal_token_branches_retain_generated_children_and_distinct_program_keys(self):
        texts = [
            'Choose one or both \u2014\n\u2022 ' + CREATE + '\n\u2022 You gain 2 life.',
            'Choose one or both \u2014\n\u2022 ' + CREATE + '\n\u2022 ' + CREATE.replace('+1/+0', '+0/+1'),
        ]
        for text in texts:
            with self.subTest(text=text):
                card = record(text, 'Sorcery')
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                parent = next(node for node in ir.faces[0].nodes if node.kind == 'spell_ability')
                children = [node for node in ir.faces[0].nodes if node.kind == 'granted_activated_ability']
                self.assertEqual(2 if '+0/+1' in text else 1, len(children))
                references = set()
                for mode in parent.target_schema['modes'].values():
                    for effect in mode['effects']:
                        if effect['op'] == 'create_token':
                            fragment, = effect['characteristics']['ability_fragments']
                            references.add(ability_fragment_from_dict(fragment).semantic_key)
                programs = generated_programs(self.db, card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertTrue(references <= {program.key for program in programs})
                self.assertEqual(len(children), len(references))
                self.assertIn(CAPABILITY, parent.capability_dependencies)

    def test_original_spree_and_ordinary_modal_token_programs_close_normally(self):
        for name in ('Unfortunate Accident', 'Warping Wail'):
            card = self.db.lookup(name, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(any(node.kind.startswith('granted_') for node in ir.faces[0].nodes))
            if name == 'Unfortunate Accident':
                parent = next(node for node in ir.faces[0].nodes if node.kind == 'spell_ability')
                self.assertIn('casting.additional_cost.spree_fixed_modes', parent.capability_dependencies)

    def test_unknown_token_child_and_withdrawn_definition_keep_whole_modal_residual(self):
        text = 'Choose one or both \u2014\n\u2022 ' + CREATE + '\n\u2022 You gain 2 life.'
        for altered in (text.replace('+1/+0', '+X/+0'), text.replace('gets +1/+0', 'rewrites history'),
                        text.replace('You gain 2 life.', 'Rewrite history.')):
            self.assertNotEqual('exact', compile_oracle_card(record(altered, 'Sorcery'),
                capability_registry=self.registry, capability_profile='commander_review').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == CAPABILITY).update(status='blocked', blockers=['typed token owner withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(record(text, 'Sorcery'),
            capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)


class ModalTokenProgramRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        setup_database(cls)
        cls.deck = DeckDefinition('Modal token witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def reject(self, session, actor, response):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + actor, response).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def cast_token_mode(self, session, source):
        action = next(row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if row['id'] == 'cast:' + source.ref)
        result = session.act('pilot:A', {'action_id': action, 'modes': ['mode_2'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        token = tokens[0]
        data = session.engine._effective_card_data(token)
        self.assertEqual(('1', '1', ['R']), (data['power'], data['toughness'], list(data['colors'])))
        self.assertIn('mercenary', data['type_line'].casefold())
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        return session, token

    def token_action(self, session, token, actor, required=True):
        for _ in range(16):
            if session.state.priority_player == actor:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next((row for row in session.packet('pilot:' + actor, full=True)['decision']['ctx']['legal']['actions']
            if row.get('source') == token.ref and row['id'].startswith('activate:')), None)
        if required:
            self.assertIsNotNone(action, 'Missing independently registered token ability')
        return action

    def test_actual_spree_token_fresh_tap_guard_haste_activation_expiry_and_replay(self):
        session = self.session(417000)
        source = self.add(session.engine, 'Unfortunate Accident', zone='hand', ref='modal-token-original-spell')
        target = self.add(session.engine, 'Generic Conditional Detective', ref='modal-token-current-target')
        haste = self.add(session.engine, 'Generic Event Subject Control', zone='hand', ref='modal-token-real-haste')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='modal-token-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'B': 1, 'W': 2})
        self.checkpoint(session)
        session, token = self.cast_token_mode(session, source)
        self.assertIsNone(self.token_action(session, token, 'A', required=False))
        ability, = session.engine._activated_abilities(token)
        self.assertTrue(ability.tap_source)
        self.assertTrue(ability.sorcery_speed)
        self.reject(session, 'A', {'action_id': 'activate:' + token.ref + ':' + ability.ability_id, 'targets': [target.ref], 'pay': 'auto'})
        self.cast(session, 'A', haste, [token.ref])
        self.resolve(session)
        action = self.token_action(session, token, 'A')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[token.object_id].tapped)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('3', session.engine._effective_card_data(session.state.cards[target.object_id])['power'])
        turn = session.state.turn_sequence
        for _ in range(64):
            if session.state.turn_sequence != turn:
                break
            kind = session.state.pending_decision.kind
            if kind == 'priority':
                response = {'action_id': 'pass'}
            elif kind == 'combat.attackers':
                response = {'action_id': 'attack', 'attackers': {}}
            elif kind == 'combat.blockers':
                response = {'action_id': 'block', 'blocks': {}}
            else:
                self.fail('Unexpected decision before cleanup: ' + kind)
            self.assertTrue(session.act(session.pending_principals()[0], response).ok)
        else:
            self.fail('Cleanup did not complete')
        self.assertEqual('2', session.engine._effective_card_data(session.state.cards[target.object_id])['power'])
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_destroy_only_spree_mode_does_not_create_unselected_token(self):
        session = self.session(417020)
        source = self.add(session.engine, 'Unfortunate Accident', zone='hand', ref='modal-token-destroy-only')
        target = self.add(session.engine, 'Generic Conditional Detective', seat='B', ref='modal-token-destroy-target')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'B': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'modes': ['mode_1'],
            'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertFalse(any(card.is_token and card.zone == 'battlefield' for card in session.state.cards.values()))
        self.replay(session, load=True)

    def test_real_token_control_change_uses_current_activator_targets_and_child_program(self):
        session = self.session(417010)
        source = self.add(session.engine, 'Unfortunate Accident', zone='hand', ref='modal-token-control-spell')
        old = self.add(session.engine, 'Generic Conditional Detective', ref='modal-token-old-target')
        target = self.add(session.engine, 'Generic Conditional Detective', seat='D', ref='modal-token-new-target')
        control = self.add(session.engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='modal-token-real-control')
        self.assert_closed(session, source)
        self.prepare(session, active='D', mana={'C': 1, 'B': 1})
        session.state.players['D'].mana_pool.update(W=1)
        self.checkpoint(session)
        session, token = self.cast_token_mode(session, source)
        self.cast(session, 'D', control, [token.ref])
        self.resolve(session)
        token = session.state.cards[token.object_id]
        self.assertEqual('D', token.controller)
        action = self.token_action(session, token, 'D')
        self.reject(session, 'A', {'action_id': action['id'], 'targets': [old.ref], 'pay': 'auto'})
        self.reject(session, 'D', {'action_id': action['id'], 'targets': [old.ref], 'pay': 'auto'})
        result = session.act('pilot:D', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('3', session.engine._effective_card_data(session.state.cards[target.object_id])['power'])
        self.assertEqual('2', session.engine._effective_card_data(session.state.cards[old.object_id])['power'])
        self.replay(session, load=True)
