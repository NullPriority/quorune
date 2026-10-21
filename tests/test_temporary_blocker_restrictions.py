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

NAMES = ('Tower of Coireall', "Joven's Tools", 'Firefright Mage', 'Ghirapur Guide')
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

    def activate(self, session, source, target, payment=None, cost_cards=()):
        self.priority(session, 'A')
        actions = [r for r in self.legal(session) if r['id'].startswith('activate:'+source.ref+':')]
        self.assertEqual(1, len(actions), source.printed_name)
        self.assertIn(target.ref, actions[0]['target_schema']['legal_refs'])
        command = {'action_id': actions[0]['id'], 'targets': [target.ref], 'pay': 'manual' if payment else 'auto'}
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

    def test_fixed_descriptor_and_sentence_boundaries_are_closed(self):
        from quorune.fixed_blocker_restrictions import FixedBlockerRestrictionSpec
        from quorune.compiler.fixed_blocker_restrictions import fixed_blocker_restriction_template
        for spec in (FixedBlockerRestrictionSpec('subtype', 'wall'), FixedBlockerRestrictionSpec('subtype', 'wall', True),
                FixedBlockerRestrictionSpec('artifact_or_color', 'R', True), FixedBlockerRestrictionSpec('power_at_most', 2)):
            self.assertEqual(spec, FixedBlockerRestrictionSpec.from_dict(spec.to_dict()))
        base = FixedBlockerRestrictionSpec('subtype', 'wall').to_dict()
        for change in ({'schema_version': True}, {'schema_version': 2}, {'kind': 'arbitrary'}, {'quality': 'unknown'},
                {'except_matching': 1}, {'extra': 'field'}):
            with self.assertRaises(ValueError): FixedBlockerRestrictionSpec.from_dict({**base, **change})
        for text in ("Target creature can't be blocked by Walls until your next turn.",
                "Target creature can't be blocked by creatures of the chosen color this turn.",
                "Target creature can't be blocked by creatures that player controls this turn.",
                "Target creature can't block this creature this turn.",
                "Target creature can't be blocked by Unknowns this turn."):
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
        for i, name in enumerate(NAMES):
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
            changed = deepcopy(program.effects[0])
            changed['restriction']['quality'] = 'unknown'
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
        declarations = json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
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
