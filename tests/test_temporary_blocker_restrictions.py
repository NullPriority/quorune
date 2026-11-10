from __future__ import annotations

"""CR 509.1b/611.2c: resolved fixed blocker predicates use current blockers."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES = ('Tower of Coireall', "Joven's Tools", 'Firefright Mage', 'Ghirapur Guide')
SOURCE_AND_QUALIFIED_ORIGINALS = (
    'Gingerbrute', 'Resilient Roadrunner', 'Tin Street Dodger', 'Departed Deckhand',
    'Kumena, Tyrant of Orazca', 'Speed, Young Avenger', "Rhonas's Stalwart", 'Cephalid Inkshrouder',
    'Vectis Agents', 'Clockwork Droid', 'Kappa Cannoneer', 'Razzle-Dazzler', 'Creeping Tar Pit',
    'Dimir Keyrune', 'Chromium, the Mutable',
    'Tiger Shark, Abyssal Hunter', 'Ant-Man, Reformed Rogue',
)
CAPABILITY = 'combat.declaration.temporary_blocker_predicate'


class TemporaryBlockerRestrictionTests(unittest.TestCase):
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
    advance = controls.ProvokeTests.advance
    attack = controls.ProvokeTests.attack
    blockers = controls.ProvokeTests.blockers
    block = controls.ProvokeTests.block

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'temporary-blocker-restrictions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/temporary-blocker-restriction-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed blocker restrictions', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def legal(self, session, seat='A'):
        return session.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']

    def cast(self, session, seat, card, payment, targets=()):
        self.priority(session, seat)
        actions = [r for r in self.legal(session, seat) if r['id'] == 'cast:'+card.ref]
        self.assertEqual(1, len(actions), card.printed_name)
        result = session.act('pilot:'+seat, {'action_id': actions[0]['id'], 'pay': 'manual',
            'payment': payment, 'targets': list(targets)})
        self.assertTrue(result.ok, result.summary)

    def activate(self, session, source, target=None, payment=None, cost_cards=(), *, label_contains=None):
        self.priority(session, 'A')
        actions = [row for row in self.legal(session)
                   if row['id'].startswith('activate:'+source.ref+':')
                   and (label_contains is None or label_contains in row['label'])]
        self.assertEqual(1, len(actions), source.printed_name)
        command = {'action_id': actions[0]['id'], 'pay': 'manual' if payment else 'auto'}
        if target is not None:
            self.assertIn(target.ref, actions[0]['target_schema']['legal_refs'])
            command['targets'] = [target.ref]
        else:
            self.assertIsNone(actions[0].get('target_schema'))
        if payment: command['payment'] = payment
        if cost_cards: command['cost_cards'] = [c.ref for c in cost_cards]
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def scene(self, name, seed, mana=None):
        session = self.session(seed)
        source = self.add(session.engine, name)
        source.acquired_control_turn_count = -1
        target = self.add(session.engine, 'Generic Blocker Attacker')
        target.acquired_control_turn_count = -1
        wall = self.add(session.engine, 'Generic Blocker Wall', seat='B')
        ordinary = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        self.prepare(session, mana=mana)
        return session, source, target, wall, ordinary

    def test_complete_originals_bind_and_keep_nonempty_typed_activated_programs(self):
        for i, name in enumerate(NAMES):
            session = self.session(755010+i)
            source = self.add(session.engine, name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])
            programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(e.get('op') == 'grant_declaration_restriction_until_end_of_turn' for e in p.effects)]
            self.assertEqual(1, len(programs))
            self.assertIn(CAPABILITY, programs[0].capability_dependencies)

    def test_all_four_original_casts_preserve_their_complete_printed_permanents(self):
        for i, (name, payment) in enumerate(zip(NAMES, ({'C': 2}, {'C': 6}, {'R': 1}, {'G': 1, 'C': 2}))):
            session = self.session(755020+i)
            source = self.add(session.engine, name, zone='hand')
            self.prepare(session, mana=payment)
            self.checkpoint(session)
            self.cast(session, 'A', source, payment)
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            card = session.state.cards[source.object_id]
            self.assertEqual('battlefield', card.zone)
            self.assertTrue(self.binding(session, card)['strict_capability_ready'])
            record = self.db.lookup(name)
            if record.power is not None:
                self.assertEqual((int(record.power), int(record.toughness)),
                    (session.engine._numeric_stat(card.object_id, 'power'), session.engine._numeric_stat(card.object_id, 'toughness')))
            self.replay(session)

    def test_actual_tower_blocks_walls_but_accepts_an_ordinary_blocker(self):
        session, source, target, wall, ordinary = self.scene(NAMES[0], 755030)
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='tower-private-C')
        self.checkpoint(session)
        self.activate(session, source, target)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {wall.ref: [target.ref]}, valid=False)
        self.block(session, {ordinary.ref: [target.ref]})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual('graveyard', session.state.cards[ordinary.object_id].zone)
        self.assertEqual((2, 40), (session.state.cards[target.object_id].marked_damage, session.state.players['B'].life))
        self.assertEqual('battlefield', session.state.cards[wall.object_id].zone)
        self.private_replay(session, private)

    def test_actual_tools_pay_four_and_allow_only_the_wall_blocker(self):
        session, source, target, wall, ordinary = self.scene(NAMES[1], 755040, {'C': 4})
        self.checkpoint(session)
        self.activate(session, source, target, {'C': 4})
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {ordinary.ref: [target.ref]}, valid=False)
        self.block(session, {wall.ref: [target.ref]})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual('graveyard', session.state.cards[wall.object_id].zone)
        self.assertEqual((2, 40), (session.state.cards[target.object_id].marked_damage, session.state.players['B'].life))
        self.replay(session)

    def test_actual_firefright_pays_discard_and_permits_artifact_or_red_blockers(self):
        session, source, target, wall, ordinary = self.scene(NAMES[2], 755050, {'R': 1, 'C': 1})
        artifact = self.add(session.engine, 'Generic Blocker Artifact', seat='B')
        red = self.add(session.engine, 'Generic Blocker Red', seat='B')
        cost = self.add(session.engine, 'Generic Bound Growth', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='firefright-private-C')
        self.prepare(session, mana={'R': 1, 'C': 1})
        self.checkpoint(session)
        self.activate(session, source, target, {'R': 1, 'C': 1}, [cost])
        self.assertEqual('graveyard', session.state.cards[cost.object_id].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {ordinary.ref: [target.ref]}, valid=False)
        self.block(session, {wall.ref: [target.ref]}, valid=False)
        self.assertTrue(session.engine._can_block(target, session.state.cards[red.object_id])[0])
        self.block(session, {artifact.ref: [target.ref]})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual((2, 40), (session.state.cards[target.object_id].marked_damage, session.state.players['B'].life))
        self.assertEqual(1, sum(session.state.cards[c.object_id].zone == 'graveyard' for c in (artifact, red)))
        self.private_replay(session, private)

    def test_actual_guide_revalidates_own_target_and_reads_current_blocker_power(self):
        session, source, target, wall, ordinary = self.scene(NAMES[3], 755060, {'G': 1, 'C': 2})
        growth = self.add(session.engine, 'Giant Growth', seat='B', zone='hand')
        session.state.players['B'].mana_pool['G'] = 1
        self.checkpoint(session)
        action = next(r for r in self.legal(session) if r['id'].startswith('activate:'+source.ref+':'))
        self.assertNotIn(ordinary.ref, action['target_schema']['legal_refs'])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': action['id'], 'targets': [ordinary.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.activate(session, source, target, {'G': 1, 'C': 2})
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.engine._can_block(session.state.cards[target.object_id], session.state.cards[ordinary.object_id])[0])
        self.cast(session, 'B', growth, {'G': 1}, [ordinary.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {ordinary.ref: [target.ref]})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual((5, 40), (session.state.cards[target.object_id].marked_damage, session.state.players['B'].life))
        self.assertEqual('battlefield', session.state.cards[ordinary.object_id].zone)
        self.replay(session)

    def test_new_originals_bind_their_complete_programs(self):
        for index, name in enumerate(SOURCE_AND_QUALIFIED_ORIGINALS):
            with self.subTest(name=name):
                session = self.session(755090 + index)
                source = self.add(session.engine, name)
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                binding = self.binding(session, source)
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])

    def test_source_keyword_rules_read_live_blocker_abilities(self):
        for index, (name, payment) in enumerate((('Gingerbrute', {'C': 1}), ('Tin Street Dodger', {'R': 1}))):
            with self.subTest(name=name):
                session = self.session(755110 + index)
                source = self.add(session.engine, name)
                ordinary = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
                permitted = self.add(session.engine, 'Wall of Omens', seat='B')
                haste = self.add(session.engine, 'Expedite', seat='B', zone='hand')
                self.prepare(session, mana=payment)
                session.state.players['B'].mana_pool['R'] = 1
                self.checkpoint(session)
                self.activate(session, source, payment=payment, label_contains="can't be blocked")
                self.assertIsNone(self.resolve(session))
                if name == 'Gingerbrute':
                    self.cast(session, 'B', haste, {'R': 1}, [permitted.ref])
                    self.assertIsNone(self.resolve(session))
                self.attack(session, {source.ref: 'B'})
                self.blockers(session)
                self.block(session, {ordinary.ref: [source.ref]}, valid=False)
                self.block(session, {permitted.ref: [source.ref]})
                self.advance(session, lambda current: current.state.phase == 'postcombat_main')
                self.assertEqual(40, session.state.players['B'].life)
                self.replay(session)

    def test_new_keyword_rule_skips_the_returned_source(self):
        session = self.session(755112)
        source = self.add(session.engine, 'Gingerbrute')
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        blink = self.add(session.engine, 'Ephemerate', zone='hand')
        identity = source.logical_object_id
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        self.activate(session, source, payment={'C': 1}, label_contains="can't be blocked")
        self.cast(session, 'A', blink, {'W': 1}, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(identity, session.state.cards[source.object_id].logical_object_id)
        self.attack(session, {source.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]})
        self.replay(session)

    def test_deckhand_targets_another_creature_and_allows_the_spirit(self):
        session = self.session(755113)
        source = self.add(session.engine, 'Departed Deckhand')
        chosen = self.add(session.engine, 'Generic Blocker Attacker')
        chosen.acquired_control_turn_count = -1
        ordinary = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        spirit = self.add(session.engine, 'Spectral Sailor', seat='B')
        payment = {'C': 3, 'U': 1}
        self.prepare(session, mana=payment)
        self.checkpoint(session)
        action, = [row for row in self.legal(session) if row['id'].startswith('activate:'+source.ref+':')]
        self.assertNotIn(source.ref, action['target_schema']['legal_refs'])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A', {'action_id': action['id'], 'targets': [source.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.activate(session, source, chosen, payment)
        self.assertIsNone(self.resolve(session))
        self.attack(session, {chosen.ref: 'B'})
        self.blockers(session)
        self.block(session, {ordinary.ref: [chosen.ref]}, valid=False)
        self.block(session, {spirit.ref: [chosen.ref]})
        self.replay(session)

    def test_kumena_short_name_binds_the_source_after_a_real_merfolk_payment(self):
        session = self.session(755114)
        source = self.add(session.engine, 'Kumena, Tyrant of Orazca')
        source.acquired_control_turn_count = -1
        merfolk = self.add(session.engine, 'Merfolk of the Pearl Trident')
        other = self.add(session.engine, 'Generic Blocker Attacker')
        other.acquired_control_turn_count = -1
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, source, cost_cards=[merfolk], label_contains="can't be blocked")
        self.assertTrue(session.state.cards[merfolk.object_id].tapped)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(session))
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]}, valid=False)
        self.block(session, {blocker.ref: [other.ref]})
        self.replay(session)

    def test_tar_pit_retains_land_type_and_applies_its_declaration_rule(self):
        session = self.session(755115)
        source = self.add(session.engine, 'Creeping Tar Pit')
        source.acquired_control_turn_count = -1
        other = self.add(session.engine, 'Generic Blocker Attacker')
        other.acquired_control_turn_count = -1
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        payment = {'C': 1, 'U': 1, 'B': 1}
        self.prepare(session, mana=payment)
        self.checkpoint(session)
        self.activate(session, source, payment=payment, label_contains='Elemental creature')
        self.assertIsNone(self.resolve(session))
        effective = session.engine._effective_card_data(session.state.cards[source.object_id])
        types, subtypes = effective['type_line'].split(' — ', 1)
        self.assertEqual({'land', 'creature'}, set(types.casefold().split()))
        self.assertEqual('Elemental', subtypes)
        self.assertEqual((3, 2), tuple(session.engine._numeric_stat(source.object_id, key) for key in ('power', 'toughness')))
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]}, valid=False)
        self.block(session, {blocker.ref: [other.ref]})
        self.replay(session)

    def test_inkshrouder_pays_discard_and_resolves_both_source_effects(self):
        session = self.session(755116)
        source = self.add(session.engine, 'Cephalid Inkshrouder')
        source.acquired_control_turn_count = -1
        other = self.add(session.engine, 'Generic Blocker Attacker')
        other.acquired_control_turn_count = -1
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        discarded = self.add(session.engine, 'Generic Bound Growth', zone='hand')
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, source, cost_cards=[discarded])
        self.assertEqual('graveyard', session.state.cards[discarded.object_id].zone)
        self.assertIsNone(self.resolve(session))
        effective = session.engine._effective_card_data(session.state.cards[source.object_id])
        self.assertIn('shroud', {keyword.casefold() for keyword in effective['keywords']})
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]}, valid=False)
        self.block(session, {blocker.ref: [other.ref]})
        self.replay(session)

    def test_cannoneer_keeps_counter_placement_before_its_declaration_rule(self):
        session = self.session(755117)
        source = self.add(session.engine, 'Kappa Cannoneer')
        source.acquired_control_turn_count = -1
        other = self.add(session.engine, 'Generic Blocker Attacker')
        other.acquired_control_turn_count = -1
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        artifact = self.add(session.engine, 'Sol Ring', zone='hand')
        self.prepare(session, mana={'C': 1})
        self.checkpoint(session)
        self.cast(session, 'A', artifact, {'C': 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[source.object_id].counters.get('+1/+1'))
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]}, valid=False)
        self.block(session, {blocker.ref: [other.ref]})
        self.replay(session)

    def test_exert_scry_is_private_and_resolves_when_the_droid_leaves(self):
        session = self.session(755118)
        source = self.add(session.engine, 'Clockwork Droid')
        source.acquired_control_turn_count = -1
        blink = self.add(session.engine, 'Ephemerate', zone='hand')
        self.prepare(session)
        self.advance(session, lambda current: current.state.pending_decision.kind == 'combat.attackers')
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['exert']
        key, = [option['value'] for row in rows if row['value'] == source.ref for option in row['options']]
        session.state.players['A'].mana_pool['W'] = 1
        identity = source.logical_object_id
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'attack', 'attacks': {source.ref: 'B'},
                                         'exert': {source.ref: [key]}})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'A', blink, {'W': 1}, [source.ref])
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual(['A'], decision.actors)
        self.assertNotEqual(identity, session.state.cards[source.object_id].logical_object_id)
        top_id = session.state.players['A'].zones['library'][-1]
        top_ref = session.state.cards[top_id].ref
        self.assertIn(top_ref, json.dumps(session.packet('pilot:A', full=True)))
        for viewer in 'BCD':
            self.assertNotIn(top_ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
        response = {'action_id': 'choose', 'cards': {'top': [], 'bottom': [top_ref]}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', response).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', response)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(top_id, session.state.players['A'].zones['library'][0])
        self.assertIsNone(self.resolve(session))
        self.assertFalse(any(event.code == 'combat.declaration_restriction' for event in session.state.events))
        self.replay(session)

    def test_speed_qualifies_its_reflexive_target_at_announcement_and_resolution(self):
        session = self.session(755119)
        source = self.add(session.engine, 'Speed, Young Avenger')
        source.acquired_control_turn_count = -1
        ordinary = self.add(session.engine, 'Generic Blocker Ordinary', ref='speed-nonhaste-candidate')
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        haste = self.add(session.engine, 'Expedite', zone='hand')
        remove_haste = self.add(session.engine, 'Humble', seat='B', zone='hand')
        self.prepare(session, mana={'C': 1, 'R': 1})
        session.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
        self.checkpoint(session)
        self.cast(session, 'A', haste, {'R': 1}, [ordinary.ref])
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(session)
        self.assertEqual('semantic.target', decision.kind)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'targets': [ordinary.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'B', remove_haste, {'C': 1, 'W': 1}, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.attack(session, {source.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]})
        self.replay(session)

    def test_tiger_shark_resolves_its_paid_source_rule_and_attack_connive(self):
        session = self.session(755120)
        source = self.add(session.engine, 'Tiger Shark, Abyssal Hunter')
        source.acquired_control_turn_count = -1
        other = self.add(session.engine, 'Generic Blocker Attacker')
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='shark-private-discard')
        payment = {'C': 4, 'U': 1}
        self.prepare(session, mana=payment)
        self.checkpoint(session)
        self.activate(session, source, payment=payment)
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        decision = self.resolve(session)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual(['A'], decision.actors)
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': [private.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[private.object_id].zone)
        self.assertEqual(1, session.state.cards[source.object_id].counters.get('+1/+1'))
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]}, valid=False)
        self.block(session, {blocker.ref: [other.ref]})
        self.replay(session)

    def test_ant_man_cast_trigger_changes_power_then_limits_blocks(self):
        session = self.session(755121)
        source = self.add(session.engine, 'Ant-Man, Reformed Rogue')
        source.acquired_control_turn_count = -1
        other = self.add(session.engine, 'Generic Blocker Attacker')
        blocker = self.add(session.engine, 'Generic Blocker Ordinary', seat='B')
        target = self.add(session.engine, 'Generic Blocker Ordinary', seat='C', ref='ant-man-blue-spell-target')
        blue = self.add(session.engine, 'Unsummon', zone='hand')
        self.prepare(session, mana={'U': 1})
        self.checkpoint(session)
        self.cast(session, 'A', blue, {'U': 1}, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.engine._numeric_stat(source.object_id, 'power'))
        hand_before = len(session.state.players['A'].zones['hand'])
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        self.blockers(session)
        self.block(session, {blocker.ref: [source.ref]}, valid=False)
        self.block(session, {blocker.ref: [other.ref]})
        self.advance(session, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(39, session.state.players['B'].life)
        self.assertEqual(hand_before + 1, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_fixed_descriptor_and_sentence_boundaries_are_closed(self):
        from quorune.fixed_blocker_restrictions import FixedBlockerRestrictionSpec
        from quorune.compiler.fixed_blocker_restrictions import fixed_blocker_restriction_template
        for spec in (FixedBlockerRestrictionSpec('subtype', 'wall'), FixedBlockerRestrictionSpec('subtype', 'wall', True),
                FixedBlockerRestrictionSpec('artifact_or_color', 'R', True), FixedBlockerRestrictionSpec('power_at_most', 2),
                FixedBlockerRestrictionSpec('keyword', 'haste', True, schema_version=2)):
            self.assertEqual(spec, FixedBlockerRestrictionSpec.from_dict(spec.to_dict()))
        base = FixedBlockerRestrictionSpec('subtype', 'wall').to_dict()
        for change in ({'schema_version': True}, {'schema_version': 2}, {'kind': 'arbitrary'}, {'quality': 'unknown'},
                {'except_matching': 1}, {'extra': 'field'}):
            with self.assertRaises(ValueError): FixedBlockerRestrictionSpec.from_dict({**base, **change})
        for text in ("Target creature can't be blocked by Walls until your next turn.",
                "Target creature can't be blocked by creatures of the chosen color this turn.",
                "Target creature can't be blocked by creatures that player controls this turn.",
                "Target creature can't block this creature this turn.",
                "Target creature can't be blocked by Unknowns this turn.",
                "Target creature can't be blocked this turn except by creatures with flying.",
                "Another permanent can't be blocked this turn except by creatures with haste."):
            self.assertIsNone(fixed_blocker_restriction_template(text), text)

    def test_resolved_predicate_survives_ability_loss_and_source_departure_then_expires(self):
        session, source, target, wall, ordinary = self.scene(NAMES[0], 755065, {'W': 1, 'C': 1, 'U': 2})
        humble = self.add(session.engine, 'Humble', zone='hand')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='duration-private-C')
        self.checkpoint(session)
        self.activate(session, source, target)
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', humble, {'W': 1, 'C': 1}, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', bounce, {'U': 2}, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual((0, 1), (session.engine._numeric_stat(target.object_id, 'power'), session.engine._numeric_stat(target.object_id, 'toughness')))
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {wall.ref: [target.ref]}, valid=False)
        self.block(session, {})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual(40, session.state.players['B'].life)
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertTrue(session.engine._can_block(session.state.cards[target.object_id], session.state.cards[wall.object_id])[0])
        self.assertEqual((2, 6), (session.engine._numeric_stat(target.object_id, 'power'), session.engine._numeric_stat(target.object_id, 'toughness')))
        self.private_replay(session, private)

    def test_blink_creates_a_new_target_incarnation_without_the_old_blocker_rule(self):
        session, source, target, wall, ordinary = self.scene(NAMES[0], 755066, {'W': 1})
        blink = self.add(session.engine, 'Cloudshift', zone='hand')
        self.checkpoint(session)
        self.activate(session, source, target)
        self.assertIsNone(self.resolve(session))
        old = target.logical_object_id
        self.cast(session, 'A', blink, {'W': 1}, [target.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(old, session.state.cards[target.object_id].logical_object_id)
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {wall.ref: [target.ref]})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual((2, 40), (session.state.cards[target.object_id].marked_damage, session.state.players['B'].life))
        self.assertEqual('graveyard', session.state.cards[wall.object_id].zone)
        self.replay(session)

    def test_actual_tower_after_block_declaration_does_not_remove_the_existing_block(self):
        session, source, target, wall, ordinary = self.scene(NAMES[0], 755067)
        self.checkpoint(session)
        self.attack(session, {target.ref: 'B'})
        self.blockers(session)
        self.block(session, {wall.ref: [target.ref]})
        self.activate(session, source, target)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual((2, 40), (session.state.cards[target.object_id].marked_damage, session.state.players['B'].life))
        self.assertEqual('graveyard', session.state.cards[wall.object_id].zone)
        self.replay(session)

    def test_runtime_binding_requires_the_new_owner_independently(self):
        for i, name in enumerate((*NAMES, 'Gingerbrute')):
            session = self.session(755070+i)
            source = self.add(session.engine, name)
            programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies]
            self.assertEqual(1, len(programs))
            program = programs[0]
            dependencies = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
            stripped = replace(program, capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict() if dependencies else None)
            bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])
            bad_quality = deepcopy(program.effects[0])
            bad_quality['restriction']['quality'] = 'unknown'
            unsealed_source = {**program.effects[0], 'card': '$source'}
            for changed in (bad_quality, unsealed_source):
                malformed = replace(program, effects=(changed,))
                bound = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertTrue(any(b.startswith('runtime_fixed_blocker:invalid:') for b in bound['blockers']), bound['blockers'])

    def test_canonically_verified_owner_withdrawal_leaves_originals_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        changed = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in changed['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn blocker-predicate authority'])
        registry = CapabilityRegistry(changed)
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_native_producer_and_commit_omissions_are_killed(self):
        cases = (('quorune.compiler.temporary_declaration_templates.fixed_blocker_restriction_template',
                    'test_complete_originals_bind_and_keep_nonempty_typed_activated_programs'),
                 ('quorune.rules.temporary_declaration_restrictions.create_resolution_declaration_rule_effect',
                    'test_actual_tower_blocks_walls_but_accepts_an_ordinary_blocker'),
                 ('quorune.engine.CommanderEngine._matches_declaration_predicate',
                    'test_actual_tower_blocks_walls_but_accepts_an_ordinary_blocker'))
        for owner, method in cases:
            with patch(owner, return_value=None):
                with self.assertRaises(AssertionError): getattr(type(self)(method), method)()
