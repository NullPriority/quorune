from __future__ import annotations

"""CR 702.118: printed and granted Skulk use the holder's current power."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions


def record(text, kind='Enchantment'):
    return CardRecord(oracle_id='fixture:granted-skulk', name='Generic Skulk Compiler', mana_cost='{0}',
        mana_value=0, type_line=kind, oracle_text=text, power='2' if 'Creature' in kind else None,
        toughness='4' if 'Creature' in kind else None, loyalty=None, defense=None, colors=(), color_identity=(),
        keywords=('Equip',) if 'Equip {' in text else ('Enchant',) if text.startswith('Enchant ') else (),
        produced_mana=(), layout='normal', released_at='2026-01-01',
        legalities={'commander': 'legal'}, faces=(), raw={})


class GrantedSkulkCompilerTests(unittest.TestCase):
    def test_all_existing_grant_scopes_declare_the_live_skulk_owner(self):
        registry = load_default_capability_registry()
        cases = (
            ('Enchant creature\nEnchanted creature has skulk.', 'Enchantment — Aura'),
            ('Equipped creature gets +1/+1 and has skulk.\nEquip {0}', 'Artifact — Equipment'),
            ('Creatures you control have skulk.', 'Enchantment'),
            ('During your turn, this creature has skulk.', 'Creature — Soldier'),
        )
        for text, kind in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, kind), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertIn('combat.block.skulk', {cap for face in ir.faces for node in face.nodes for cap in node.capability_dependencies})
        from quorune.keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
        from quorune.compiler.continuous_templates import _ATTACHED_SUPPORTED_ABILITIES
        self.assertEqual({word.casefold() for word in FIXED_CHARACTERISTIC_KEYWORDS} | {'flash'},
            _ATTACHED_SUPPORTED_ABILITIES)

    def test_unknown_siblings_and_withdrawn_skulk_authority_remain_residual(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record('Creatures you control have skulk and an unrepresented ability.'),
            capability_registry=registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'combat.block.skulk').update(status='blocked', blockers=['withdrawn'])
        ir = compile_oracle_card(record('Creatures you control have skulk.'),
            capability_registry=CapabilityRegistry(raw), capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(ir.material_residuals)


class GrantedSkulkRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name).resolve() / 'granted-skulk.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/granted-skulk-cards.json', ROOT / 'tests/fixtures/event-player-subject-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Granted Skulk witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def assert_closed(self, session, card):
        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding)
        self.assertEqual('capability_closed', binding['trust_basis'])

    def ready(self, session, card, mana):
        # Admission belongs to the complete canonical program. Equipment also
        # retains catalog metadata that does not execute its intrinsic Equip.
        self.assert_closed(session, card)
        engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.active_player = 'A'; engine.state.started = True
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine._grant_priority('A'); engine.pump()
        return next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('card') == card.ref)

    def cast(self, session, seat, card, targets=()):
        for _ in range(16):
            session.engine.pump()
            self.assertEqual('priority', session.state.pending_decision.kind)
            if session.state.priority_player == seat:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Casting principal did not receive priority')
        current = session.state.cards[card.object_id]
        offered = next(row for row in session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + current.ref)
        accepted = session.act('pilot:' + seat, {'action_id': offered['id'], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_original_key_cast_equip_control_change_and_departure_preserve_holder_semantics(self):
        session = self.session(395001); engine = session.engine
        key = self.add(engine, 'Skeleton Key', zone='hand', ref='original-skulk-key')
        body = self.add(engine, 'Generic Skulk Body', ref='skulk-key-holder')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='skulk-steal-holder')
        self.add(engine, 'Generic Bound Plains', seat='D', ref='skulk-control-payment')
        bounce = self.add(engine, 'Generic Skulk Bounce', zone='hand', ref='skulk-key-return')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='skulk-private')
        offered = self.ready(session, key, {'C': 3}); self.assert_closed(session, key)
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok); self.resolve(session)
        equip = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('source') == key.ref and row['id'].startswith('activate:'))
        self.assertTrue(session.act('pilot:A', {'action_id': equip['id'], 'targets': [body.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        session = self.replay(session, load=True)
        self.assertIn('skulk', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.cast(session, 'D', control, [body.ref]); self.resolve(session)
        self.assertEqual('D', session.state.cards[body.object_id].controller)
        self.assertIn('skulk', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.cast(session, 'A', bounce, [key.ref]); self.resolve(session)
        self.assertEqual('hand', session.state.cards[key.object_id].zone)
        self.assertNotIn('skulk', session.engine._combat_keywords(session.state.cards[body.object_id]))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        self.replay(session, load=True)

    def test_actual_granted_skulk_blocks_use_holder_current_power_not_grant_controller(self):
        for grow in (False, True):
            with self.subTest(grow=grow):
                session = self.session(395010 + grow); engine = session.engine
                attacker = self.add(engine, 'Generic Skulk Body', seat='B', ref='foreign-skulk-attacker')
                attacker.acquired_control_turn_count = -1
                aura = self.add(engine, 'Generic Skulk Aura', ref='foreign-skulk-grant')
                aura.attached_to = attacker.object_id; attacker.attachments.append(aura.object_id)
                greater = self.add(engine, 'Generic Skulk Greater', seat='C', ref='greater-power-blocker')
                equal = self.add(engine, 'Generic Bound Body', seat='C', ref='equal-power-blocker')
                growth = self.add(engine, 'Generic Skulk Growth', seat='B', zone='hand', ref='skulk-current-power')
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='skulk-combat-private')
                self.assert_closed(session, aura)
                engine.permissions.invalidate_current(); engine.state.pending_decision = None
                engine.state.started = True; engine.state.active_player = 'B'; engine.state.phase = 'combat'
                engine.state.step = 'declare_attackers'; engine.state.phase_index = 5; engine.state.combat = CombatState()
                engine._issue_attackers()
                self.checkpoint(session)
                life = {seat: player.life for seat, player in session.state.players.items()}
                accepted = session.act('pilot:B', {'action_id': 'attack', 'attackers': {attacker.ref: 'C'}})
                self.assertTrue(accepted.ok, accepted.summary)
                if grow:
                    self.cast(session, 'B', growth, [attacker.ref]); self.resolve(session)
                for _ in range(24):
                    session.engine.pump()
                    if session.state.pending_decision.kind == 'combat.blockers':
                        break
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                else:
                    self.fail('Blocker declaration did not arrive')
                if not grow:
                    before = authoritative_state_hash(session.state)
                    denied = session.act('pilot:C', {'action_id': 'block', 'blocks': {greater.ref: attacker.ref}})
                    self.assertFalse(denied.ok); self.assertEqual(before, authoritative_state_hash(session.state))
                blocker = greater if grow else equal
                accepted = session.act('pilot:C', {'action_id': 'block', 'blocks': {blocker.ref: attacker.ref}})
                self.assertTrue(accepted.ok, accepted.summary)
                session = self.replay(session, load=True)
                for _ in range(32):
                    session.engine.pump()
                    if session.state.phase != 'combat':
                        break
                    self.assertEqual('priority', session.state.pending_decision.kind)
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                else:
                    self.fail('Combat did not finish')
                self.assertEqual(life, {seat: player.life for seat, player in session.state.players.items()})
                self.assertEqual('battlefield', session.state.cards[attacker.object_id].zone)
                self.assertEqual('graveyard' if grow else 'battlefield', session.state.cards[greater.object_id].zone)
                self.assertEqual('battlefield', session.state.cards[equal.object_id].zone)
                self.assertEqual(3 if grow else 2, session.state.cards[attacker.object_id].marked_damage)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)
