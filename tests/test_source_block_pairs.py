from __future__ import annotations

"""CR 509.1b/400.7: one blocker is restricted against one source incarnation."""

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

NAMES = ('Duct Crawler', "Kozilek's Pathfinder", 'Screeching Griffin', 'Spin Engine')
CAPABILITY = 'combat.declaration.source_block_pair'


class SourceBlockPairTests(unittest.TestCase):
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
        path = Path(cls.temp.name)/'source-block-pairs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-specific-blocking-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source-specific blockers', [DeckEntry('Generic Bound Commander', 1, 'commander'),
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

    def activate(self, session, source, target, payment, *, valid=True):
        self.priority(session, 'A')
        actions = [r for r in self.legal(session) if r['id'].startswith('activate:'+source.ref+':')]
        self.assertEqual(1, len(actions), source.printed_name)
        self.assertIn(target.ref, actions[0]['target_schema']['legal_refs'])
        command = {'action_id': actions[0]['id'], 'targets': [target.ref], 'pay': 'manual', 'payment': payment}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:self.assertEqual(before, authoritative_state_hash(session.state))

    def scene(self, name, seed, *, zone='battlefield'):
        session = self.session(seed)
        source = self.add(session.engine, name, zone=zone)
        source.acquired_control_turn_count = -1
        chosen = self.add(session.engine, 'Generic Pair Blocker', seat='B')
        unchosen = self.add(session.engine, 'Generic Pair Other Blocker', seat='B')
        other = self.add(session.engine, 'Generic Pair Other Attacker')
        other.acquired_control_turn_count = -1
        return session, source, chosen, unchosen, other

    def top(self, session, remaining=1):
        for _ in range(24):
            if len(session.state.stack) <= remaining:return
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Top response did not resolve')

    def pairs(self, session):
        from quorune.source_block_pair_model import ResolvedSourceBlockPair
        return [r for r in session.state.continuous_effects if isinstance(r, ResolvedSourceBlockPair)]

    def test_complete_originals_bind_the_source_specific_pair_program(self):
        for i, name in enumerate(NAMES):
            session = self.session(756010+i)
            source = self.add(session.engine, name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])
            programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies]
            self.assertEqual(1, len(programs))
            self.assertEqual({'schema_version': 1, 'kind': 'source_block_pair'}, programs[0].effects[0]['restriction'])

    def test_all_four_actual_casts_and_pairs_restrict_only_the_selected_match(self):
        cases = ((NAMES[0], {'R': 1}, {'R': 1, 'C': 1}),
            (NAMES[1], {'C': 6}, {'C': 1}), (NAMES[2], {'W': 1, 'C': 3}, {'R': 1}),
            (NAMES[3], {'C': 3}, {'R': 1}))
        for i, (name, cast_price, activation_price) in enumerate(cases):
            session, source, chosen, unchosen, other = self.scene(name, 756020+i, zone='hand')
            haste = self.add(session.engine, 'Expedite', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='pair-private-C')
            mana = {c: cast_price.get(c, 0)+activation_price.get(c, 0)+int(c == 'R') for c in 'WUBRGC'}
            self.prepare(session, mana=mana)
            self.checkpoint(session)
            self.cast(session, 'A', source, cast_price)
            self.assertIsNone(self.resolve(session))
            self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
            record = self.db.lookup(name)
            self.assertEqual((int(record.power), int(record.toughness)),
                (session.engine._numeric_stat(source.object_id, 'power'), session.engine._numeric_stat(source.object_id, 'toughness')))
            hand = set(session.state.players['A'].zones['hand'])
            self.cast(session, 'A', haste, {'R': 1}, [source.ref])
            self.assertIsNone(self.resolve(session))
            drawn = set(session.state.players['A'].zones['hand'])-hand
            self.assertEqual(1, len(drawn))
            for oid in drawn:
                for viewer in 'BCD':self.assertNotIn(session.state.cards[oid].ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
            self.activate(session, source, chosen, activation_price)
            self.assertFalse(any(session.state.players['A'].mana_pool.values()))
            if i == 0:session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(1, len(self.pairs(session)))
            self.attack(session, {source.ref: 'B', other.ref: 'B'})
            self.blockers(session)
            self.block(session, {chosen.ref: [source.ref]}, valid=False)
            self.assertTrue(session.engine._can_block(session.state.cards[source.object_id], session.state.cards[unchosen.object_id])[0])
            self.block(session, {chosen.ref: [other.ref]})
            self.advance(session, lambda s: s.state.phase == 'postcombat_main')
            self.assertEqual(40-int(record.power), session.state.players['B'].life)
            self.assertEqual(('battlefield', 2, 2), (session.state.cards[source.object_id].zone,
                session.state.cards[chosen.object_id].marked_damage, session.state.cards[other.object_id].marked_damage))
            for viewer in 'ABD':self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
            self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
            if i == 0:self.replay(session, load=True)

    def test_pathfinder_requires_colorless_and_self_targeting_remains_legal(self):
        session, source, chosen, unchosen, other = self.scene(NAMES[1], 756030)
        self.prepare(session, mana={'R': 1, 'C': 1})
        self.checkpoint(session)
        self.activate(session, source, source, {'R': 1}, valid=False)
        self.activate(session, source, source, {'C': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((0, 1), (session.state.players['A'].mana_pool['C'], session.state.players['A'].mana_pool['R']))
        pairs = self.pairs(session)
        self.assertEqual(1, len(pairs))
        self.assertEqual(pairs[0].attacker, pairs[0].blocker)
        self.assertTrue(session.engine._can_block(session.state.cards[source.object_id], session.state.cards[chosen.object_id])[0])
        self.replay(session)

    def test_source_blink_before_or_after_resolution_does_not_follow_the_returned_source(self):
        for i, before in enumerate((True, False)):
            session, source, chosen, unchosen, other = self.scene(NAMES[0], 756040+i)
            blink = self.add(session.engine, 'Cloudshift', zone='hand')
            haste = self.add(session.engine, 'Expedite', zone='hand')
            self.prepare(session, mana={'R': 2, 'C': 1, 'W': 1})
            self.checkpoint(session)
            old = source.logical_object_id
            self.activate(session, source, chosen, {'R': 1, 'C': 1})
            if not before:self.assertIsNone(self.resolve(session))
            self.cast(session, 'A', blink, {'W': 1}, [source.ref])
            if before:self.top(session)
            else:self.assertIsNone(self.resolve(session))
            self.assertNotEqual(old, session.state.cards[source.object_id].logical_object_id)
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            pairs = self.pairs(session)
            self.assertEqual(1, len(pairs))
            self.assertEqual(old, pairs[0].attacker.logical_object_id)
            self.cast(session, 'A', haste, {'R': 1}, [source.ref])
            self.assertIsNone(self.resolve(session))
            self.attack(session, {source.ref: 'B'})
            self.blockers(session)
            self.block(session, {chosen.ref: [source.ref]})
            self.advance(session, lambda s: s.state.phase == 'postcombat_main')
            self.assertEqual(('graveyard', 40), (session.state.cards[source.object_id].zone, session.state.players['B'].life))
            self.replay(session)

    def test_selected_blocker_blink_before_or_after_resolution_never_restricts_the_new_blocker(self):
        for i, before in enumerate((True, False)):
            session, source, chosen, unchosen, other = self.scene(NAMES[0], 756050+i)
            blink = self.add(session.engine, 'Cloudshift', seat='B', zone='hand')
            session.state.players['B'].mana_pool['W'] = 1
            self.prepare(session, mana={'R': 1, 'C': 1})
            self.checkpoint(session)
            old = chosen.logical_object_id
            self.activate(session, source, chosen, {'R': 1, 'C': 1})
            if not before:self.assertIsNone(self.resolve(session))
            self.cast(session, 'B', blink, {'W': 1}, [chosen.ref])
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertNotEqual(old, session.state.cards[chosen.object_id].logical_object_id)
            self.assertEqual(0 if before else 1, len(self.pairs(session)))
            self.attack(session, {source.ref: 'B'})
            self.blockers(session)
            self.block(session, {chosen.ref: [source.ref]})
            self.advance(session, lambda s: s.state.phase == 'postcombat_main')
            self.assertEqual(('graveyard', 40), (session.state.cards[source.object_id].zone, session.state.players['B'].life))
            self.replay(session)

    def test_pair_survives_ability_loss_and_blocker_control_changes_then_expires(self):
        session, source, chosen, unchosen, other = self.scene(NAMES[0], 756060)
        humble = self.add(session.engine, 'Humble', zone='hand')
        steal_a = self.add(session.engine, 'Word of Seizing', zone='hand', ref='pair-steal-A')
        steal_b = self.add(session.engine, 'Word of Seizing', seat='B', zone='hand', ref='pair-steal-B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='pair-control-private-C')
        session.state.players['B'].mana_pool.update({'R': 2, 'C': 3})
        self.prepare(session, mana={'R': 3, 'C': 5, 'W': 1})
        self.checkpoint(session)
        old = chosen.logical_object_id
        self.activate(session, source, chosen, {'R': 1, 'C': 1})
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', humble, {'W': 1, 'C': 1}, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', steal_a, {'R': 2, 'C': 3}, [chosen.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('A', session.state.cards[chosen.object_id].controller)
        session = self.replay(session, load=True)
        self.cast(session, 'B', steal_b, {'R': 2, 'C': 3}, [chosen.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('B', old), (session.state.cards[chosen.object_id].controller, session.state.cards[chosen.object_id].logical_object_id))
        self.attack(session, {source.ref: 'B', other.ref: 'B'})
        self.blockers(session)
        self.block(session, {chosen.ref: [source.ref]}, valid=False)
        self.block(session, {chosen.ref: [other.ref]})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual(40, session.state.players['B'].life)
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertEqual(0, len(self.pairs(session)))
        self.assertTrue(session.engine._can_block(session.state.cards[source.object_id], session.state.cards[chosen.object_id])[0])
        self.assertEqual(1, session.engine._numeric_stat(source.object_id, 'power'))
        self.private_replay(session, private)

    def test_pair_created_after_blocks_does_not_remove_the_existing_block(self):
        session, source, chosen, unchosen, other = self.scene(NAMES[3], 756070)
        land = self.add(session.engine, 'Mountain')
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, {source.ref: 'B'})
        self.blockers(session)
        self.block(session, {chosen.ref: [source.ref]})
        self.priority(session, 'A')
        mana = [r for r in self.legal(session) if r['id'].startswith('activate:'+land.ref+':')]
        self.assertEqual(1, len(mana))
        result = session.act('pilot:A', {'action_id': mana[0]['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players['A'].mana_pool['R'])
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.activate(session, source, chosen, {'R': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual(('graveyard', 40), (session.state.cards[source.object_id].zone, session.state.players['B'].life))
        self.assertEqual(3, session.state.cards[chosen.object_id].marked_damage)
        self.replay(session)

    def test_pair_descriptor_and_journal_have_closed_immutable_schemas(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.source_block_pair_model import SourceBlockPairSpec, ResolvedSourceBlockPair
        spec = SourceBlockPairSpec()
        self.assertEqual(spec, SourceBlockPairSpec.from_dict(spec.to_dict()))
        for change in ({'schema_version': True}, {'schema_version': 2}, {'kind': 'global_cant_block'}, {'attacker': 'client-picked'}):
            with self.assertRaises(ValueError):SourceBlockPairSpec.from_dict({**spec.to_dict(), **change})
        attacker = ContinuousObjectIdentity('source', 'source@0')
        blocker = ContinuousObjectIdentity('blocker', 'blocker@0')
        rule = ResolvedSourceBlockPair('pair', 'source', 1, attacker, blocker)
        self.assertEqual(rule, ResolvedSourceBlockPair.from_dict(rule.to_dict()))
        for change in ({'schema_version': True}, {'duration': 'permanent'}, {'timestamp': True}, {'source_id': 'other'}, {'extra': 1}):
            with self.assertRaises(ValueError):ResolvedSourceBlockPair.from_dict({**rule.to_dict(), **change})
        from quorune.compiler.source_block_pairs import source_block_pair_template
        text = "Target creature can't block this creature this turn."
        self.assertIsNone(source_block_pair_template(text, allow_source=False))
        for text in ("Target creature can't block another creature this turn.",
                "Target creature can't block this creature until your next turn.",
                "Up to two target creatures can't block this creature this turn."):
            self.assertIsNone(source_block_pair_template(text, allow_source=True))

    def test_missing_runtime_source_rejects_before_any_mutation(self):
        from quorune.errors import GameRuleError
        from quorune.rules.source_block_pairs import apply_source_block_pair
        session, source, chosen, unchosen, other = self.scene(NAMES[0], 756080)
        self.prepare(session)
        before = authoritative_state_hash(session.state)
        with self.assertRaises(GameRuleError):
            apply_source_block_pair(session.engine, {'op': 'grant_declaration_restriction_until_end_of_turn',
                'card': chosen.ref, 'restriction': {'schema_version': 1, 'kind': 'source_block_pair'}}, actor='A', reason='Missing source control')
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_runtime_minimum_cannot_be_stripped_and_malformed_pairs_reject(self):
        for i, name in enumerate(NAMES):
            session = self.session(756090+i)
            source = self.add(session.engine, name)
            programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies]
            self.assertEqual(1, len(programs))
            program = programs[0]
            deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
            stripped = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict() if deps else None)
            bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])
            effect = deepcopy(program.effects[0])
            effect['restriction']['schema_version'] = True
            bound = bind_semantic_program_runtime(replace(program, effects=(effect,)), capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertTrue(any(b.startswith('runtime_source_block_pair:invalid:') for b in bound['blockers']), bound['blockers'])

    def test_canonically_verified_pair_owner_withdrawal_leaves_all_originals_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn source-pair authority'])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_fresh_producer_commit_and_logical_incarnation_omissions_are_killed(self):
        cases = (('quorune.compiler.temporary_declaration_templates.source_block_pair_template', None,
                    'test_complete_originals_bind_the_source_specific_pair_program'),
                 ('quorune.rules.source_block_pairs.commit_continuous_effect', None,
                    'test_all_four_actual_casts_and_pairs_restrict_only_the_selected_match'))
        for owner, value, method in cases:
            with patch(owner, return_value=value):
                with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
        method = 'test_source_blink_before_or_after_resolution_does_not_follow_the_returned_source'
        with patch('quorune.rules.source_block_pairs.pair_identity_matches', side_effect=lambda identity,card: identity.object_id == card.object_id):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
