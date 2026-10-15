from __future__ import annotations

import unittest

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


def role_creator(text: str) -> CardRecord:
    return CardRecord(oracle_id='00000000-0000-4000-8000-000000311060', name='Generic Role Creator',
        mana_cost='{G}', mana_value=1, type_line='Sorcery', oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=('G',), color_identity=('G',),
        keywords=(), produced_mana=(), layout='normal', released_at='2026-01-01',
        legalities={'commander': 'legal'}, faces=(), raw={})


class PredefinedRoleCompilerTests(unittest.TestCase):
    def test_complete_role_definitions_keep_source_spans_and_child_authority(self):
        from quorune.compiler.role_token_nodes import role_definition_children
        from quorune.compiler.ir_model import SourceSpan
        registry = load_default_capability_registry()
        for role in ('Cursed', 'Monster', 'Royal', 'Sorcerer', 'Virtuous', 'Wicked', 'Young Hero'):
            text = 'Create a ' + role + ' Role token attached to target creature you control.'
            with self.subTest(role=role):
                definition = role_definition_children(role=role, record=role_creator(text), face_id='front',
                    node_id='n1', line=text, span=SourceSpan(0, len(text), 1), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertIsNotNone(definition)
                fragments, nodes = definition
                self.assertTrue(all(n.text == text and n.span.start == 0 and n.span.end == len(text) for n in nodes))
                self.assertTrue(fragments)
                self.assertTrue(all(n.kind.startswith('granted_') for n in nodes))
                from quorune.compiler.generated_program_identity import generated_ability_id
                from quorune.semantic_runtime.ability_fragments import fragments_from_descriptors
                from quorune.ability_fragments import granted_triggered_specs
                child_keys = {'00000000-0000-4000-8000-000000311060:' + generated_ability_id(
                    kind=n.kind, face_id='front', line=1, static_declaration=bool(n.handlers), node_id=n.node_id)
                    for n in nodes}
                for node in nodes:
                    for descriptor in node.handlers:
                        raw = descriptor.get('modifier', {}).get('add_ability_fragments', [])
                        for fragment in granted_triggered_specs(raw):
                            self.assertIn(fragment.semantic_key, child_keys)
                            self.assertTrue(fragment.semantic_key.endswith(':' + fragment.ability_id))

    def test_role_definition_withdrawal_and_creator_fragment_isolation(self):
        from unittest.mock import patch
        from quorune.compiler.role_token_nodes import role_definition_children, role_creation_parts
        from quorune.compiler.ir_model import SourceSpan
        text = 'Create a Monster Role token attached to target creature you control.'
        args = dict(role='Monster', record=role_creator(text), face_id='front', node_id='n1', line=text,
            span=SourceSpan(0, len(text), 1), capability_registry=load_default_capability_registry(),
            capability_profile='commander_review')
        with patch('quorune.oracle_ir.compile_oracle_card') as compiler:
            compiler.return_value.status = 'unresolved'
            self.assertIsNone(role_definition_children(**args))
        for text in ('Create a Unknown Role token attached to target creature you control.',
            'Create a Monster Role token attached to that creature.',
            'Create two Monster Role tokens attached to target creature you control.'):
            self.assertIsNone(role_creation_parts(text))
        from quorune.compiled_ability_fragments import compiled_static_ability_fragments
        from types import SimpleNamespace
        program = SimpleNamespace(key='fixture:static:front:n1:role:1', provenance={'granted_only': True})
        registry = SimpleNamespace(runtime_handler_programs_for_oracle=lambda *_args, **_kwargs: (),
            programs_for_oracle=lambda *_args, **_kwargs: (program,))
        host = SimpleNamespace(semantics=registry, card_record=lambda _card: role_creator('Draw a card.'),
            semantic_program_is_current_trusted=lambda _program: self.fail('Granted-only creator child was consulted'))
        program.handlers = ({'handler_id': 'continuous.attached.fixed-characteristics.v1'},)
        card = SimpleNamespace(active_face=None)
        self.assertEqual((), compiled_static_ability_fragments(host, card))

    def test_role_static_children_lower_through_existing_program_identity(self):
        from quorune.compiler.role_token_nodes import role_definition_children
        from quorune.compiler.ir_model import SourceSpan, OracleFaceIR, OracleCardIR
        from quorune.compiler.program_generation import _generated_programs_from_ir
        from types import SimpleNamespace
        text = 'Create a Sorcerer Role token attached to target creature you control.'
        registry = load_default_capability_registry()
        record = role_creator(text)
        fragments, nodes = role_definition_children(role='Sorcerer', record=record, face_id='front', node_id='n1',
            line=text, span=SourceSpan(0, len(text), 1), capability_registry=registry, capability_profile='commander_review')
        ir = OracleCardIR(oracle_id=record.oracle_id, card_name=record.name, schema_version=1,
            compiler_version='oracle-ir-v365', oracle_hash='0' * 64, semantic_hash='0' * 64,
            faces=(OracleFaceIR(face_id='front', face_name=record.name, oracle_text=text, nodes=nodes, residuals=()),))
        db = SimpleNamespace(rulings=lambda _card: ())
        programs = _generated_programs_from_ir(db, record, ir, trust_level='provisional',
            capability_registry=registry, capability_profile='commander_review', has_rules_derived_trust_carrier=False)
        self.assertEqual(2, len(programs))
        self.assertTrue(all(program.provenance['granted_only'] for program in programs))
        self.assertTrue(any(program.ability_id == 'static:front:n1:role:1' for program in programs))
        self.assertTrue(any(program.ability_id == 'trigger:front:n1:granted:token:2' for program in programs))


class RoleTimestampChoiceTests(unittest.TestCase):
    def test_replacement_adjusted_timestamp_choice_requires_exact_permutation(self):
        from types import SimpleNamespace
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.role_tokens import RoleTokenChoiceHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        target = SimpleNamespace(logical_object_id='body@1', phased_out=False, types=('creature',))
        preview = (FrozenMap({'name': 'Monster', 'quantity': 2,
            'characteristics': {'type_line': 'Token Enchantment — Aura Role'}}),)
        query = SimpleNamespace(object=lambda *_args, **_kwargs: target, token_creation_preview=lambda: preview)
        context = SimpleNamespace(query=query, actor='A', stack_ref='S1', stack_label='Role creation')
        effect = {'op': 'create_role_token', 'name': 'Monster', 'quantity': 1, 'controller': 'A',
            'aura_target_ref': 'body', 'characteristics': {'type_line': 'Token Enchantment — Aura Role'}}
        handler = RoleTokenChoiceHandler()
        preparation = handler.prepare(effect, context)
        self.assertEqual(('role:0', 'role:1'), preparation.request.choice.legal_refs)
        continuation = SimpleNamespace(effect=preparation.continuation_effect, note='Role creation')
        completion = handler.complete(continuation, {'order': ['role:1', 'role:0']}, query)
        self.assertEqual((1, 0), tuple(completion.intents[0].effect['_role_order']))
        for response in ({'order': ['role:0']}, {'order': ['role:0', 'role:0']}, {'order': ['role:0', 'foreign']}):
            with self.assertRaises(SemanticChoiceError):
                handler.complete(continuation, response, query)
        target.logical_object_id = 'body@2'
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {'order': ['role:0', 'role:1']}, query)

    def test_ordered_plans_assign_relative_timestamps_without_guessing(self):
        from dataclasses import dataclass
        from quorune.replacement.immutable import FrozenMap
        from quorune.role_token_creation import ordered_role_plans
        from quorune.token_creation import TokenCreationError
        @dataclass(frozen=True)
        class Plan:
            ref: str
            zone_timestamp: int = 10
            aura_target_ref: str = 'body'
            annotations: object = FrozenMap({'token_characteristics': {'type_line': 'Token Enchantment — Aura Role'}})
        first, second = Plan('first'), Plan('second')
        ordered = ordered_role_plans((first, second), (1, 0), 10)
        self.assertEqual([('first', 11), ('second', 10)], [(plan.ref, plan.zone_timestamp) for plan in ordered])
        for order in ((0,), (0, 0), (True, 0), (0, 2)):
            with self.assertRaises(TokenCreationError):
                ordered_role_plans((first, second), order, 10)


class PredefinedRoleNativeTests(unittest.TestCase):
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Actions
    session = _Actions.session
    add = _Actions.add
    ready = _Actions.ready
    checkpoint = _Actions.checkpoint
    replay = _Actions.replay
    resolve = _Actions.resolve
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'roles.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/predefined-role-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Roles', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def assert_strict(self, session, source):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def test_original_cursed_courtier_creates_attached_role_with_complete_characteristics(self):
        session = self.session(311060)
        engine = session.engine
        source = self.add(engine, 'Cursed Courtier', zone='hand')
        self.assert_strict(session, source)
        offered = self.ready(session, source, {'W': 1, 'C': 2})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        tokens = [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        self.assertEqual(source.object_id, tokens[0].attached_to)
        self.assertEqual((1, 1), (engine._numeric_stat(source.object_id, 'power'), engine._numeric_stat(source.object_id, 'toughness')))
        self.assertIn('Lifelink', engine._effective_card_data(source)['keywords'])
        self.replay(session, load=True)

    def test_original_royal_creation_orders_doubled_roles_before_state_based_uniqueness(self):
        from quorune.record import authoritative_state_hash
        session = self.session(311061)
        engine = session.engine
        source = self.add(engine, 'Redtooth Genealogist', zone='hand')
        body = self.add(engine, 'Generic Bound Body', ref='role-subject')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='role-foreign-subject')
        self.add(engine, 'Anointed Procession', ref='role-doubler')
        self.assert_strict(session, source)
        offered = self.ready(session, source, {'G': 1, 'C': 2})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {'action_id': 'choose', 'targets': [foreign.ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {'action_id': 'choose', 'targets': [source.ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': 'choose', 'targets': [body.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        for principal, order in (('pilot:B', ['role:0', 'role:1']), ('pilot:A', ['role:0', 'role:0'])):
            before = authoritative_state_hash(session.state)
            denied = session.act(principal, {'action_id': 'choose', 'order': order})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': 'choose', 'order': ['role:1', 'role:0']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        engine = session.engine
        roles = [card for card in engine.state.cards.values() if card.is_token]
        self.assertEqual(2, len(roles))
        alive = [card for card in roles if card.zone == 'battlefield']
        self.assertEqual(1, len(alive))
        self.assertEqual(min(card.ref for card in roles), alive[0].ref)
        self.assertEqual(body.object_id, alive[0].attached_to)
        self.assertEqual((3, 7), (engine._numeric_stat(body.object_id, 'power'), engine._numeric_stat(body.object_id, 'toughness')))
        self.assertIn('Ward {1}', engine._effective_card_data(body)['keywords'])
        self.replay(session)

    def test_typed_role_spells_execute_monster_virtuous_and_young_hero_children(self):
        from test_source_pronoun_results import SourcePronounRuntimeTests
        for role in ('Monster', 'Virtuous', 'Young Hero'):
            with self.subTest(role=role):
                session = self.session(311070 + ('Monster', 'Virtuous', 'Young Hero').index(role))
                engine = session.engine
                body = self.add(engine, 'Generic Role Small Creature', ref='role-body')
                spell = self.add(engine, 'Generic ' + role + ' Role Spell', zone='hand')
                self.assert_strict(session, spell)
                offered = self.ready(session, spell, {'G': 1})
                self.checkpoint(session)
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                expected = 2 if role == 'Young Hero' else 3
                self.assertEqual(expected, engine._numeric_stat(body.object_id, 'power'))
                if role == 'Monster':
                    self.assertIn('Trample', engine._effective_card_data(body)['keywords'])
                if role == 'Young Hero':
                    SourcePronounRuntimeTests.start_source_combat(self, session)
                    self.checkpoint(session)
                    accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {body.ref: 'B'}})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertEqual(1, len(engine.state.stack))
                    session = self.replay(session, load=True)
                    self.resolve(session)
                    self.assertEqual(1, session.state.cards[body.object_id].counters.get('+1/+1', 0))
                self.replay(session)

    def test_wicked_replaced_role_loses_each_opponents_life_with_lki_and_replay(self):
        session = self.session(311073)
        engine = session.engine
        body = self.add(engine, 'Generic Role Small Creature', ref='wicked-body')
        wicked = self.add(engine, 'Generic Wicked Role Spell', zone='hand')
        monster = self.add(engine, 'Generic Monster Role Spell', zone='hand')
        offered = self.ready(session, wicked, {'G': 2})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.replay(session)
        offered = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == monster.ref)
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(40, engine.state.players['A'].life)
        for seat in 'BCD':
            self.assertEqual(39, engine.state.players[seat].life)
        self.assertEqual(1, sum(card.is_token and card.zone == 'battlefield' for card in engine.state.cards.values()))
        self.replay(session, load=True)

    def test_sorcerer_grant_attacks_for_private_scry_and_other_controller_role_coexists(self):
        from test_source_pronoun_results import SourcePronounRuntimeTests
        session = self.session(311080)
        engine = session.engine
        body = self.add(engine, 'Generic Role Small Creature', ref='sorcerer-body')
        spell = self.add(engine, 'Generic Sorcerer Role Spell', zone='hand')
        other = self.add(engine, 'Generic Public Monster Role Spell', seat='B', zone='hand', ref='other-controller-role')
        offered = self.ready(session, spell, {'G': 1})
        engine.state.players['B'].mana_pool['G'] = 1
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.replay(session)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = 'B'
        engine._grant_priority('B')
        engine.pump()
        self.checkpoint(session)
        offered = next(a for a in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == other.ref)
        accepted = session.act('pilot:B', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        roles = [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual({'A', 'B'}, {card.controller for card in roles})
        self.assertEqual(4, engine._numeric_stat(body.object_id, 'power'))
        self.replay(session)
        SourcePronounRuntimeTests.start_source_combat(self, session)
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {body.ref: 'C'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, len(engine.state.stack))
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        session = self.replay(session, load=True)
        packet = session.packet('pilot:A', full=True)
        looked = packet['decision']['ctx']['objects'][0]['id']
        for seat in 'BCD':
            self.assertNotIn(looked, str(session.packet('pilot:' + seat, full=True)))
        accepted = session.act('pilot:A', {'action_id': 'choose', 'cards': {'top': [looked], 'bottom': []}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.replay(session)

    def test_original_target_blink_prevents_role_attachment_to_new_incarnation(self):
        session = self.session(311081)
        engine = session.engine
        source = self.add(engine, 'Redtooth Genealogist', zone='hand')
        body = self.add(engine, 'Generic Role Small Creature', ref='role-blink-subject')
        blink = self.add(engine, 'Cloudshift', zone='hand')
        offered = self.ready(session, source, {'G': 1, 'C': 2, 'W': 3})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'targets': [body.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(8):
            if session.pending_principals()[0] == 'pilot:A':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        offered = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == blink.ref)
        old = body.logical_object_id
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertNotEqual(old, session.state.cards[body.object_id].logical_object_id)
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))
        self.replay(session)

    def test_original_embereth_veteran_pays_sacrifice_and_grants_young_hero_attack(self):
        from test_source_pronoun_results import SourcePronounRuntimeTests
        session = self.session(311085)
        engine = session.engine
        veteran = self.add(engine, 'Embereth Veteran')
        body = self.add(engine, 'Generic Role Small Creature', ref='hero-body')
        self.assert_strict(session, veteran)
        offered = self.ready(session, veteran, {'C': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', engine.state.cards[veteran.object_id].zone)
        self.assertEqual(0, sum(engine.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.resolve(session)
        roles = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(roles))
        self.assertEqual(body.object_id, roles[0].attached_to)
        self.replay(session)
        SourcePronounRuntimeTests.start_source_combat(self, session)
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {body.ref: 'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[body.object_id].counters.get('+1/+1', 0))
        self.replay(session)


if __name__ == '__main__':
    unittest.main()
