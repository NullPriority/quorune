from __future__ import annotations

"""CR702.102/709.4: hand-only paired casting is one ordered split spell."""

from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fuse_model import FuseHalf, FuseSpec, FUSE_ANNOTATION
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls

NAMES = ('Alive // Well', 'Double Jump // Flying Kick', 'Down // Dirty', 'Far // Away',
    'Profit // Loss', 'Protect // Serve', 'Toil // Trouble', 'Turn // Burn', 'Wear // Tear')


class FuseTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ProvokeTests.session
    add = controls.ProvokeTests.add
    prepare = controls.ProvokeTests.prepare
    checkpoint = controls.ProvokeTests.checkpoint
    resolve = controls.ProvokeTests.resolve
    replay = controls.ProvokeTests.replay
    binding = controls.ProvokeTests.binding
    private_replay = controls.ProvokeTests.private_replay
    priority = controls.ProvokeTests.priority
    actions = controls.ProvokeTests.actions
    advance = controls.ProvokeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'fuse.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fuse-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Ordered Fuse', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_complete_originals_have_normal_admission(self):
        for index, name in enumerate(NAMES):
            session = self.session(762010 + index)
            source = self.add(session.engine, name, zone='hand')
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            self.assertEqual(2, len(ir.faces))
            for face in ir.faces:
                for node in face.nodes:
                    self.assertEqual(node.text, face.oracle_text[node.span.start:node.span.end])
            bound = self.binding(session, source)
            self.assertTrue(bound['strict_capability_ready'], (name, bound['blockers']))
            from quorune.rules.fuse import compiled_fuse_spec
            spec = compiled_fuse_spec(session.engine, source)
            self.assertIsNotNone(spec, name)
            for ability in ('spell:' + spec.left.face_id, 'spell:' + spec.right.face_id, spec.ability_id):
                program = session.engine.semantics.get(f'{source.oracle_id}:{ability}')
                self.assertIsNotNone(program, ability)
                self.assertTrue(session.engine.semantic_program_is_current_trusted(program))

    def test_closed_fuse_model_and_unsupported_sources(self):
        half = FuseHalf('left', 'Left', '{1}{U}', 'Instant', ('U',))
        spec = FuseSpec(half, FuseHalf('right', 'Right', '{R}', 'Sorcery', ('R',)))
        self.assertEqual(spec, FuseSpec.from_dict(spec.to_dict()))
        self.assertEqual({'name': 'Left // Right', 'mana_cost': '{1}{U}{R}', 'mana_value': 3,
            'type_line': 'Instant Sorcery', 'colors': ['U', 'R']}, spec.characteristic_values())
        for raw in ({**spec.to_dict(), 'schema_version': True}, {**spec.to_dict(), 'origin': 'exile'},
                {**spec.to_dict(), 'right': spec.left.to_dict()}):
            with self.assertRaises(ValueError):
                FuseSpec.from_dict(raw)
        original = self.db.lookup('Alive // Well')
        for faces in (original.faces[:1], tuple({**f, 'oracle_text': f['oracle_text'] + '\nInvent unknown results.'}
                if i else f for i, f in enumerate(original.faces))):
            ir = compile_oracle_card(replace(original, faces=faces), capability_registry=self.registry,
                capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)

    def cast_row(self, session, source, *, fused=True, face=None, seat='A'):
        self.priority(session, seat)
        rows=session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        return next((row for row in rows if row.get('card') == source.ref
            and (row.get('cast_method') == 'fuse' if fused else row.get('cast_method') is None
                and (face is None or row.get('face') == face))), None)

    def reject(self, session, command, *, principal='pilot:A'):
        before = authoritative_state_hash(session.state)
        result = session.act(principal, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def cast(self, session, source, payment, targets=(), *, fused=True, face=None, option=None, seat='A'):
        row = self.cast_row(session, source, fused=fused, face=face, seat=seat)
        self.assertIsNotNone(row, (source.printed_name, fused, face))
        command = {'action_id': row['id'], 'pay': 'manual', 'payment': payment,
            'targets': targets if isinstance(targets, dict) else list(targets)}
        if option is not None:
            command['cost_option'] = option
        self.reject(session, command, principal='pilot:D')
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_alive_well_observes_created_token(self):
        session = self.session(762100)
        source = self.add(session.engine, 'Alive // Well', zone='hand')
        self.add(session.engine, 'Generic Bound Body')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='private-c-growth')
        self.prepare(session, mana={'C': 3, 'G': 1, 'W': 1})
        self.checkpoint(session)
        row = self.cast_row(session, source)
        self.assertIsNotNone(row)
        self.assertEqual('{3}{G}{W}', row['cost'])
        self.cast(session, source, {'C': 3, 'G': 1, 'W': 1})
        self.assertEqual(1, len(session.state.stack))
        data = session.engine._effective_card_data(session.state.cards[source.object_id])
        self.assertEqual(('Alive // Well', 5, ['W', 'G']), (data['name'], data['mana_value'], data['colors']))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(44, session.state.players['A'].life)
        tokens = [c for c in session.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        self.assertEqual((3, 3), (session.engine._numeric_stat(tokens[0].object_id, 'power'),
            session.engine._numeric_stat(tokens[0].object_id, 'toughness')))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertNotIn(FUSE_ANNOTATION, session.state.cards[source.object_id].annotations)
        self.private_replay(session, private)

    def test_actual_toil_trouble_draws_privately_before_damage(self):
        session = self.session(762101)
        source = self.add(session.engine, 'Toil // Trouble', zone='hand')
        self.add(session.engine, 'Generic Bound Growth', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='private-c-growth')
        self.prepare(session, mana={'C': 4, 'B': 1, 'R': 1})
        self.checkpoint(session)
        before = len(session.state.players['B'].zones['hand'])
        self.cast(session, source, {'C': 4, 'B': 1, 'R': 1}, ['B', 'B'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before + 2, len(session.state.players['B'].zones['hand']))
        self.assertEqual(40 - 2 - (before + 2), session.state.players['B'].life)
        b_cards=[session.state.cards[oid] for oid in session.state.players['B'].zones['hand']]
        for seat in 'ACD':
            packet=json.dumps(session.packet('pilot:'+seat,full=True))
            for card in b_cards:self.assertNotIn(card.ref,packet)
        own_packet=json.dumps(session.packet('pilot:B',full=True))
        for card in b_cards:self.assertIn(card.ref,own_packet)
        self.private_replay(session, private)

    def test_actual_toil_and_trouble_keep_different_player_roles(self):
        session = self.session(762102)
        source = self.add(session.engine, 'Toil // Trouble', zone='hand')
        for index in range(4):
            self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='c-hand-' + str(index))
        self.add(session.engine, 'Generic Bound Growth', seat='B', zone='hand', ref='b-hand')
        self.prepare(session, mana={'C': 4, 'B': 1, 'R': 1})
        self.checkpoint(session)
        before_b = len(session.state.players['B'].zones['hand'])
        before_c = len(session.state.players['C'].zones['hand'])
        self.cast(session, source, {'C': 4, 'B': 1, 'R': 1}, ['B', 'C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before_b + 2, len(session.state.players['B'].zones['hand']))
        self.assertEqual(before_c, len(session.state.players['C'].zones['hand']))
        self.assertEqual((38, 40 - before_c), (session.state.players['B'].life, session.state.players['C'].life))
        self.replay(session)

    def test_fresh_fuse_source_omission_is_killed(self):
        method = 'test_actual_alive_well_observes_created_token'
        with patch('quorune.compiler.fuse_faces.compile_fuse_faces', side_effect=lambda record, faces, **kw: faces):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_double_jump_then_flying_kick_uses_three_independent_roles(self):
        session = self.session(762103)
        source = self.add(session.engine, 'Double Jump // Flying Kick', zone='hand')
        own = self.add(session.engine, 'Generic Bound Body')
        enemy = self.add(session.engine, 'Generic Bound Body', seat='B', ref='kick-recipient')
        self.prepare(session, mana={'C': 2, 'U': 1, 'R': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 2, 'U': 1, 'R': 1}, [own.ref, own.ref, enemy.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        own, enemy = session.state.cards[own.object_id], session.state.cards[enemy.object_id]
        self.assertEqual((5, 5), (session.engine._numeric_stat(own.object_id, 'power'),
            session.engine._numeric_stat(own.object_id, 'toughness')))
        self.assertEqual(1, own.counters.get('flying'))
        self.assertIn('flying', session.engine._combat_keywords(own))
        self.assertEqual(('battlefield', 5), (enemy.zone, enemy.marked_damage))
        self.replay(session)

    def test_actual_down_discard_resumes_before_dirty_returns_own_card(self):
        session = self.session(762104)
        source = self.add(session.engine, 'Down // Dirty', zone='hand')
        returned = self.add(session.engine, 'Generic Bound Growth', zone='graveyard', ref='dirty-own-card')
        first = self.add(session.engine, 'Grizzly Bears', seat='B', zone='hand', ref='down-first')
        second = self.add(session.engine, 'Wind Drake', seat='B', zone='hand', ref='down-second')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='down-private-c')
        self.prepare(session, mana={'C': 5, 'B': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 5, 'B': 1, 'G': 1}, ['B', returned.ref])
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('graveyard', session.state.cards[returned.object_id].zone)
        self.assertEqual(['pilot:B'], session.pending_principals())
        for seat in 'ACD':
            packet=json.dumps(session.packet('pilot:'+seat,full=True))
            for card in (first,second):self.assertNotIn(card.ref,packet)
        self.reject(session, {'action_id': 'choose', 'cards': [first.ref, second.ref]})
        session = self.replay(session, load=True)
        result = session.act('pilot:B', {'action_id': 'choose', 'cards': [first.ref, second.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard', 'graveyard', 'hand'), tuple(session.state.cards[c.object_id].zone
            for c in (first, second, returned)))
        self.private_replay(session, private)

    def test_actual_far_bounces_before_away_selects_remaining_creature(self):
        session = self.session(762105)
        source = self.add(session.engine, 'Far // Away', zone='hand')
        bounced = self.add(session.engine, 'Grizzly Bears', seat='B', ref='far-returned')
        sacrificed = self.add(session.engine, 'Wind Drake', seat='B', ref='away-choice')
        self.prepare(session, mana={'C': 3, 'U': 1, 'B': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 3, 'U': 1, 'B': 1}, [bounced.ref, 'B'])
        decision = self.resolve(session)
        if decision is not None:
            self.assertEqual(['pilot:B'], session.pending_principals())
            self.assertEqual('hand', session.state.cards[bounced.object_id].zone)
            session = self.replay(session, load=True)
            result = session.act('pilot:B', {'action_id': 'choose', 'cards': [sacrificed.ref]})
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'graveyard'), (session.state.cards[bounced.object_id].zone,
            session.state.cards[sacrificed.object_id].zone))
        self.replay(session)

    def test_actual_profit_loss_applies_both_controller_scopes(self):
        session = self.session(762106)
        source = self.add(session.engine, 'Profit // Loss', zone='hand')
        own = self.add(session.engine, 'Generic Bound Body')
        enemy = self.add(session.engine, 'Grizzly Bears', seat='B')
        other = self.add(session.engine, 'Wind Drake', seat='C')
        self.prepare(session, mana={'C': 3, 'W': 1, 'B': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 3, 'W': 1, 'B': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        for card, expected in ((own, (3, 7)), (enemy, (1, 1)), (other, (1, 1))):
            self.assertEqual(expected, (session.engine._numeric_stat(card.object_id, 'power'),
                session.engine._numeric_stat(card.object_id, 'toughness')))
        self.replay(session)

    def test_actual_protect_serve_allows_same_creature_for_both_halves(self):
        session = self.session(762107)
        source = self.add(session.engine, 'Protect // Serve', zone='hand')
        own = self.add(session.engine, 'Generic Bound Body')
        self.prepare(session, mana={'C': 3, 'W': 1, 'U': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 3, 'W': 1, 'U': 1}, [own.ref, own.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((-2, 10), (session.engine._numeric_stat(own.object_id, 'power'),
            session.engine._numeric_stat(own.object_id, 'toughness')))
        self.replay(session)

    def test_actual_turn_removes_flying_before_burn_kills_one_toughness(self):
        session = self.session(762108)
        source = self.add(session.engine, 'Turn // Burn', zone='hand')
        enemy = self.add(session.engine, 'Wind Drake', seat='B')
        self.prepare(session, mana={'C': 3, 'U': 1, 'R': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 3, 'U': 1, 'R': 1}, [enemy.ref, enemy.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[enemy.object_id].zone)
        self.assertEqual(40, session.state.players['B'].life)
        self.replay(session)

    def test_actual_wear_tear_destroys_independent_artifact_and_enchantment(self):
        session = self.session(762109)
        source = self.add(session.engine, 'Wear // Tear', zone='hand')
        artifact = self.add(session.engine, 'Fist of Suns', seat='B')
        enchantment = self.add(session.engine, 'Omniscience', seat='C')
        self.prepare(session, mana={'C': 1, 'R': 1, 'W': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 1, 'R': 1, 'W': 1}, [artifact.ref, enchantment.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard', 'graveyard'), (session.state.cards[artifact.object_id].zone,
            session.state.cards[enchantment.object_id].zone))
        self.replay(session)

    def test_actual_free_and_wubrg_prices_combine_with_fuse_and_one_tax(self):
        for index, (grant, payment) in enumerate((('Omniscience', {'C': 1}),
                ('Fist of Suns', {'C': 1, 'W': 1, 'U': 1, 'B': 1, 'R': 1, 'G': 1}))):
            session = self.session(762110 + index)
            source = self.add(session.engine, 'Alive // Well', zone='hand')
            self.add(session.engine, grant)
            self.add(session.engine, 'Sphere of Resistance')
            self.add(session.engine, 'Generic Bound Body')
            self.prepare(session, mana=payment)
            self.checkpoint(session)
            row = self.cast_row(session, source)
            self.assertIsNotNone(row)
            options = [o for o in row['cost_options'] if o['id'].startswith('battlefield-alternative:')]
            self.assertEqual(1, len(options))
            self.assertEqual(1, options[0]['requirements']['GENERIC'])
            self.cast(session, source, payment, option=options[0]['id'])
            self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(44, session.state.players['A'].life)
            self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
            self.replay(session)

    def test_actual_reduction_applies_once_to_combined_printed_cost(self):
        session = self.session(762112)
        source = self.add(session.engine, 'Alive // Well', zone='hand')
        self.add(session.engine, 'Goblin Electromancer')
        self.prepare(session, mana={'C': 2, 'W': 1, 'G': 1})
        self.checkpoint(session)
        row = self.cast_row(session, source)
        self.assertIsNotNone(row)
        self.assertEqual(2, row['cost_options'][0]['requirements']['GENERIC'])
        self.cast(session, source, {'C': 2, 'W': 1, 'G': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(44, session.state.players['A'].life)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_actual_fused_copy_retains_combined_characteristics_and_target_roles(self):
        session = self.session(762113)
        source = self.add(session.engine, 'Toil // Trouble', zone='hand')
        copy_spell = self.add(session.engine, 'Reverberate', zone='hand')
        self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='copy-c-hand')
        self.prepare(session, mana={'C': 4, 'B': 1, 'R': 3})
        self.checkpoint(session)
        before_b = len(session.state.players['B'].zones['hand'])
        before_c = len(session.state.players['C'].zones['hand'])
        self.cast(session, source, {'C': 4, 'B': 1, 'R': 1}, ['B', 'C'])
        original = session.state.stack[-1]
        self.cast(session, copy_spell, {'R': 2}, [original.ref], fused=False)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose'})
        self.assertTrue(result.ok, result.summary)
        copied = next(item for item in session.state.stack if item.kind == 'spell_copy')
        self.assertEqual((original.semantic_key, ['B', 'C']), (copied.semantic_key, copied.targets))
        data = session.engine._effective_card_data(session.state.cards[copied.card_object_id])
        self.assertEqual(('Toil // Trouble', 6, ['B', 'R']), (data['name'], data['mana_value'], data['colors']))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before_b + 4, len(session.state.players['B'].zones['hand']))
        self.assertEqual((36, 40 - before_c * 2), (session.state.players['B'].life, session.state.players['C'].life))
        self.replay(session)

    def test_actual_fused_halves_have_no_state_based_actions_between_them(self):
        session = self.session(762114)
        source = self.add(session.engine, 'Generic Bound Fused Survival', zone='hand')
        body = self.add(session.engine, 'Generic Bound Body')
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.prepare(session, mana={'U': 1, 'W': 1})
        self.checkpoint(session)
        self.cast(session, source, {'U': 1, 'W': 1}, [body.ref, body.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[body.object_id].zone)
        self.assertEqual(6, session.engine._numeric_stat(body.object_id, 'toughness'))
        self.assertFalse(session.engine._current_turn_history('creature_died'))
        self.replay(session)

    def test_actual_target_revalidation_preserves_legal_half_and_fizzles_all_illegal(self):
        for index, same in enumerate((False, True)):
            session = self.session(762115 + index)
            source = self.add(session.engine, 'Protect // Serve', zone='hand')
            own = self.add(session.engine, 'Generic Bound Body')
            enemy = self.add(session.engine, 'Grizzly Bears', seat='B')
            bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
            session.state.players['B'].mana_pool['U'] = 1
            self.prepare(session, mana={'C': 3, 'W': 1, 'U': 1})
            self.checkpoint(session)
            self.cast(session, source, {'C': 3, 'W': 1, 'U': 1},
                [enemy.ref if same else own.ref, enemy.ref])
            self.cast(session, bounce, {'U': 1}, [enemy.ref], fused=False, seat='B')
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual('hand', session.state.cards[enemy.object_id].zone)
            self.assertEqual((2, 6) if same else (4, 10),
                (session.engine._numeric_stat(own.object_id, 'power'), session.engine._numeric_stat(own.object_id, 'toughness')))
            self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
            self.replay(session)

    def test_actual_left_destruction_does_not_reuse_departed_right_target(self):
        session = self.session(762117)
        source = self.add(session.engine, 'Wear // Tear', zone='hand')
        target = self.add(session.engine, 'Whip of Erebos', seat='B')
        self.prepare(session, mana={'C': 1, 'R': 1, 'W': 1})
        self.checkpoint(session)
        self.cast(session, source, {'C': 1, 'R': 1, 'W': 1}, [target.ref, target.ref])
        old_identity = target.logical_object_id
        old_counter = target.zone_change_counter
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertEqual('graveyard', current.zone)
        self.assertNotEqual(old_identity, current.logical_object_id)
        self.assertEqual(old_counter + 1, current.zone_change_counter)
        self.replay(session)

    def test_fuse_method_rejects_nonhand_origins_single_faces_and_unrelated_cards(self):
        from quorune.rules.fuse import fuse_cast_view
        from quorune.rules.casting.proposal import build_cast_offer, build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest, CastProposalError
        session = self.session(762120)
        hand = self.add(session.engine, 'Alive // Well', zone='hand')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        foreign = [self.add(session.engine, 'Alive // Well', zone=zone, ref='fuse-' + zone)
            for zone in ('exile', 'graveyard', 'command')]
        self.prepare(session, mana={'C': 3, 'W': 1, 'G': 1, 'R': 1})
        self.checkpoint(session)
        for card in (*foreign, bolt):
            before = authoritative_state_hash(session.state)
            with self.assertRaises(CastProposalError):
                fuse_cast_view(session.engine, card, self.db.lookup(card.printed_name), 'fuse')
            self.assertIsNone(build_cast_offer(session.engine, 'A', card, cast_method='fuse').offer)
            self.assertEqual(before, authoritative_state_hash(session.state))
        request = CastProposalRequest.from_submission('A', {'card': hand.ref, 'from': 'hand',
            'cast_method': 'fuse', 'face': 'Alive', 'pay': 'auto'})
        before = authoritative_state_hash(session.state)
        with self.assertRaises(CastProposalError):
            build_cast_proposal(session.engine, request)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.reject(session, {'action_id': 'cast-fuse:' + hand.ref, 'pay': 'manual', 'payment': {'G': 1, 'C': 3}})
        self.replay(session)

    def test_composition_and_independent_runtime_minima_reject_tampering(self):
        from quorune.rules.fuse_shapes import closed_fused_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(762121)
        source = self.add(session.engine, 'Toil // Trouble', zone='hand')
        programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)]
        fused = next(p for p in programs if 'fused_spell_resolution' in p.coverage)
        self.assertTrue(closed_fused_program(fused))
        for changed in (replace(fused, effects=list(reversed(fused.effects))),
                replace(fused, target_schema={'groups': list(reversed(fused.target_schema['groups']))}),
                replace(fused, provenance={**fused.provenance, 'fuse_components': fused.provenance['fuse_components'][:1]}),
                replace(fused, provenance={**fused.provenance, 'fuse': {**fused.provenance['fuse'], 'schema_version': True}})):
            self.assertFalse(closed_fused_program(changed))
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile='commander_review')['strict'])
        for program in programs:
            deps = [c for c in program.capability_dependencies if c != 'casting.method.fuse']
            stripped = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict() if deps else None)
            bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:casting.method.fuse', bound['blockers'])

    def test_verified_fuse_dependency_withdrawal_is_the_actual_blocker(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == 'casting.method.fuse').update(
            status='blocked', blockers=['withdrawn hand-only paired casting owner'])
        registry = CapabilityRegistry(raw)
        decl = json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index = build_index(registry_value=raw, declaration_source=decl,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:casting.method.fuse:blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_scoped_hand_count_preserves_legacy_wire_and_rejects_unknown_groups(self):
        from types import SimpleNamespace
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin
        from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
        legacy = ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT, schema_version=4)
        self.assertEqual({'kind': 'scalar_effect_amount', 'schema_version': 4,
            'origin': 'target_player_hand_count', 'characteristic': None, 'history_fact': None,
            'coefficient': 1, 'binding_id': None}, legacy.to_dict())
        scoped = replace(legacy, schema_version=7, target_group='target_1')
        self.assertEqual(scoped, ScalarEffectAmountSpec.from_dict(scoped.to_dict()))
        host = SimpleNamespace(state=SimpleNamespace(players={seat: SimpleNamespace(in_game=True,
            zones={'hand': list(range(count))}) for seat, count in (('B', 2), ('C', 5))}))
        item = SimpleNamespace(controller='A', targets=['B', 'C'],
            context={'target_groups': {'target_0': ['B'], 'target_1': ['C']}})
        self.assertEqual(5, resolve_scalar_effect_amount(host, scoped.to_dict(), item))
        for raw in ({**scoped.to_dict(), 'schema_version': True}, {**scoped.to_dict(), 'target_group': 'unknown'},
                {**legacy.to_dict(), 'target_group': 'target_1'}):
            with self.assertRaises(ValueError):
                ScalarEffectAmountSpec.from_dict(raw)
        item.context['target_groups_current'] = {'target_0': ['B'], 'target_1': [None]}
        with self.assertRaises(ValueError):
            resolve_scalar_effect_amount(host, scoped.to_dict(), item)

    def test_fresh_method_target_and_order_omissions_are_killed(self):
        import quorune.rules.fuse_shapes as shapes
        original = shapes.fused_body
        def backwards(halves):
            effects, schema = original(halves)
            return list(reversed(effects)), schema
        for path, replacement, method in (
                ('quorune.rules.fuse.compiled_fuse_spec', lambda *a: None, 'test_actual_alive_well_observes_created_token'),
                ('quorune.scalar_effect_amounts._hand_count_target', lambda item, spec: item.targets[0],
                    'test_actual_toil_and_trouble_keep_different_player_roles')):
            with patch(path, replacement):
                with self.assertRaises(AssertionError, msg=path):
                    getattr(type(self)(method), method)()
        method = 'test_actual_alive_well_observes_created_token'
        with patch('quorune.rules.fuse_shapes.fused_body', backwards), patch('quorune.compiler.fuse_programs.fused_body', backwards):
            with self.assertRaises(AssertionError, msg='fused instruction order'):
                getattr(type(self)(method), method)()

    def half_witnesses(self, name):
        prices = {
            'Alive // Well': ({'C': 3, 'G': 1}, {'W': 1}),
            'Double Jump // Flying Kick': ({'C': 1, 'U': 1}, {'C': 1, 'R': 1}),
            'Down // Dirty': ({'C': 3, 'B': 1}, {'C': 2, 'G': 1}),
            'Far // Away': ({'C': 1, 'U': 1}, {'C': 2, 'B': 1}),
            'Profit // Loss': ({'C': 1, 'W': 1}, {'C': 2, 'B': 1}),
            'Protect // Serve': ({'C': 2, 'W': 1}, {'C': 1, 'U': 1}),
            'Toil // Trouble': ({'C': 2, 'B': 1}, {'C': 2, 'R': 1}),
            'Turn // Burn': ({'C': 2, 'U': 1}, {'C': 1, 'R': 1}),
            'Wear // Tear': ({'C': 1, 'R': 1}, {'W': 1}),
        }
        for index, payment in enumerate(prices[name]):
            session = self.session(762200 + NAMES.index(name) * 2 + index)
            source = self.add(session.engine, name, zone='hand')
            own = self.add(session.engine, 'Generic Bound Body')
            enemy = self.add(session.engine, 'Generic Bound Body', seat='B', ref='half-enemy')
            artifact = self.add(session.engine, 'Fist of Suns', seat='B')
            enchantment = self.add(session.engine, 'Omniscience', seat='C')
            returned = self.add(session.engine, 'Generic Bound Growth', zone='graveyard', ref='half-return')
            discarded = [self.add(session.engine, n, seat='B', zone='hand', ref='half-discard-' + str(i))
                for i, n in enumerate(('Grizzly Bears', 'Wind Drake'))]
            face = self.db.lookup(name).faces[index]['name']
            targets = {'Double Jump': [own.ref], 'Flying Kick': [own.ref, enemy.ref], 'Down': ['B'],
                'Dirty': [returned.ref], 'Far': [enemy.ref], 'Away': ['B'], 'Protect': [own.ref],
                'Serve': [enemy.ref], 'Toil': ['B'], 'Trouble': ['B'], 'Turn': [enemy.ref],
                'Burn': ['B'], 'Wear': [artifact.ref], 'Tear': [enchantment.ref]}.get(face, [])
            self.prepare(session, mana=payment)
            self.checkpoint(session)
            before_b = len(session.state.players['B'].zones['hand'])
            self.cast(session, source, payment, targets, fused=False, face=face)
            data = session.engine._effective_card_data(session.state.cards[source.object_id])
            self.assertEqual((face, sum(payment.values()), [c for c in 'WUBRG' if payment.get(c)]),
                (data['name'], data['mana_value'], data['colors']))
            self.assertNotIn(FUSE_ANNOTATION, session.state.cards[source.object_id].annotations)
            pending = self.resolve(session)
            if pending is not None:
                self.assertIn(face, ('Down', 'Away'))
                self.assertEqual(['pilot:B'], session.pending_principals())
                refs = [c.ref for c in discarded] if face == 'Down' else [enemy.ref]
                result = session.act('pilot:B', {'action_id': 'choose', 'cards': refs})
                self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(session))
            power = lambda c: session.engine._numeric_stat(c.object_id, 'power')
            toughness = lambda c: session.engine._numeric_stat(c.object_id, 'toughness')
            if face == 'Alive':
                self.assertEqual(1, len([c for c in session.state.cards.values() if c.is_token and c.zone == 'battlefield']))
                self.assertEqual(40, session.state.players['A'].life)
            elif face == 'Well':
                self.assertEqual(42, session.state.players['A'].life)
                self.assertFalse(any(c.is_token for c in session.state.cards.values()))
            elif face == 'Double Jump':
                self.assertEqual((5, 5, 1, 0), (power(own), toughness(own), own.counters.get('flying'), enemy.marked_damage))
            elif face == 'Flying Kick':
                self.assertEqual((2, 6, 0, 2), (power(own), toughness(own), own.counters.get('flying', 0), enemy.marked_damage))
            elif face == 'Down':
                self.assertTrue(all(c.zone == 'graveyard' for c in discarded))
                self.assertEqual(('graveyard', before_b - 2), (returned.zone, len(session.state.players['B'].zones['hand'])))
            elif face == 'Dirty':
                self.assertEqual(('hand', before_b), (returned.zone, len(session.state.players['B'].zones['hand'])))
            elif face in {'Far', 'Away'}:
                self.assertEqual('hand' if face == 'Far' else 'graveyard', enemy.zone)
            elif face in {'Profit', 'Loss', 'Protect', 'Serve'}:
                expected = {'Profit': (3, 7, 2, 6), 'Loss': (2, 6, 1, 5),
                    'Protect': (4, 10, 2, 6), 'Serve': (2, 6, -4, 6)}[face]
                self.assertEqual(expected, (power(own), toughness(own), power(enemy), toughness(enemy)))
            elif face in {'Toil', 'Trouble', 'Burn'}:
                self.assertEqual(38 if face != 'Trouble' else 40 - before_b, session.state.players['B'].life)
                self.assertEqual(before_b + (2 if face == 'Toil' else 0), len(session.state.players['B'].zones['hand']))
            elif face == 'Turn':
                self.assertEqual((0, 1, ['R']), (power(enemy), toughness(enemy), session.engine._effective_card_data(enemy)['colors']))
                self.assertEqual(40, session.state.players['B'].life)
            elif face in {'Wear', 'Tear'}:
                self.assertEqual(('graveyard', 'battlefield') if face == 'Wear' else ('battlefield', 'graveyard'),
                    (artifact.zone, enchantment.zone))
            self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
            self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
            self.replay(session)

    def test_actual_alive_and_well_can_each_be_cast_alone(self):
        self.half_witnesses('Alive // Well')

    def test_actual_double_jump_and_flying_kick_can_each_be_cast_alone(self):
        self.half_witnesses('Double Jump // Flying Kick')

    def test_actual_down_and_dirty_can_each_be_cast_alone(self):
        self.half_witnesses('Down // Dirty')

    def test_actual_far_and_away_can_each_be_cast_alone(self):
        self.half_witnesses('Far // Away')

    def test_actual_profit_and_loss_can_each_be_cast_alone(self):
        self.half_witnesses('Profit // Loss')

    def test_actual_protect_and_serve_can_each_be_cast_alone(self):
        self.half_witnesses('Protect // Serve')

    def test_actual_toil_and_trouble_can_each_be_cast_alone(self):
        self.half_witnesses('Toil // Trouble')

    def test_actual_turn_and_burn_can_each_be_cast_alone(self):
        self.half_witnesses('Turn // Burn')

    def test_actual_wear_and_tear_can_each_be_cast_alone(self):
        self.half_witnesses('Wear // Tear')

    def test_actual_impulse_permission_allows_each_half_from_exile_but_cannot_fuse(self):
        session=self.session(762230)
        impulse=self.add(session.engine,'Reckless Impulse',zone='hand')
        one=self.add(session.engine,'Alive // Well',zone='library',ref='exile-alive')
        two=self.add(session.engine,'Alive // Well',zone='library',ref='exile-well')
        self.add(session.engine,'Generic Bound Body')
        self.prepare(session,mana={'C':4,'R':1,'G':1,'W':1})
        self.checkpoint(session)
        self.cast(session,impulse,{'C':1,'R':1},fused=False)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile','exile'),(one.zone,two.zone))
        for source in (one,two):
            self.assertIsNone(self.cast_row(session,source))
            self.reject(session,{'action_id':'cast-fuse:'+source.ref,'pay':'auto'})
        self.cast(session,session.state.cards[one.object_id],{'C':3,'G':1},fused=False,face='Alive')
        self.assertIsNone(self.resolve(session))
        self.cast(session,session.state.cards[two.object_id],{'W':1},fused=False,face='Well')
        session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(44,session.state.players['A'].life)
        self.assertEqual(3,len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_missing_fused_executable_keeps_all_three_casts_unavailable(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        with patch('quorune.compiler.fuse_programs.with_fused_programs', side_effect=lambda programs, **kw: list(programs)):
            session=self.session(762240)
            source=self.add(session.engine,'Alive // Well',zone='hand')
            self.assertFalse(self.binding(session,source)['strict_capability_ready'])
            card_program=compile_best_available_card_program(self.db,self.db.lookup('Alive // Well'),
                semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
            self.assertTrue(any(r.get('kind')=='program_lowering' for r in card_program.residuals))
            self.prepare(session,mana={'C':3,'G':1,'W':1})
            self.checkpoint(session)
            self.assertIsNone(self.cast_row(session,source))
            for face in ('Alive','Well'):
                self.assertIsNone(self.cast_row(session,source,fused=False,face=face))
            self.reject(session,{'action_id':'cast:'+source.ref,'pay':'auto'})
