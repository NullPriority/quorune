from __future__ import annotations

"""CR 702.27a/601.2h: optional typed nonmana prices precede Buyback resolution."""

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from unittest.mock import patch
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls

NAMES = ('Constant Mists', 'Demonic Collusion', 'Flowstone Flood', 'Forbid', 'Pegasus Stampede',
    'Reaping the Rewards', 'Slaughter', 'Walk the Aeons')
CAPABILITY = 'casting.buyback.fixed_nonmana'


class NonmanaBuybackCostTests(unittest.TestCase):
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
        path = Path(cls.temp.name)/'nonmana-buyback.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/nonmana-buyback-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Nonmana Buyback', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def legal(self, session, seat='A'):
        return session.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']

    def cast(self, session, seat, card, payment, targets=(), *, buyback=False, **choices):
        self.priority(session, seat)
        rows = [r for r in self.legal(session, seat) if r['id'] == 'cast:'+card.ref]
        self.assertEqual(1, len(rows), card.printed_name)
        if buyback:
            self.assertIn('buyback', {o['id'] for o in rows[0]['cost_options']})
        command = {'action_id': rows[0]['id'], 'pay': 'manual', 'payment': payment,
            'targets': list(targets), **choices}
        if buyback:command['cost_option'] = 'buyback'
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_all_eight_originals_have_complete_normal_admission(self):
        for i, name in enumerate(NAMES):
            session = self.session(757010+i)
            source = self.add(session.engine, name, zone='hand')
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])

    def test_actual_rewards_pays_land_before_return_and_normal_recast_does_not_keep_buyback(self):
        session = self.session(757030)
        source = self.add(session.engine, 'Reaping the Rewards', zone='hand')
        land = self.add(session.engine, 'Forest')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='buyback-private-C')
        self.prepare(session, mana={'W': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'W': 1}, buyback=True, sacrifice_cards=[land.ref])
        self.assertEqual(('stack', 'graveyard', 40), (session.state.cards[source.object_id].zone,
            session.state.cards[land.object_id].zone, session.state.players['A'].life))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 42), (session.state.cards[source.object_id].zone, session.state.players['A'].life))
        self.cast(session, 'A', source, {'W': 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard', 44), (session.state.cards[source.object_id].zone, session.state.players['A'].life))
        self.private_replay(session, private)

    def test_actual_forbid_discards_two_before_countering_and_returns_to_hand(self):
        session = self.session(757040)
        source = self.add(session.engine, 'Forbid', zone='hand')
        costs = [self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='forbid-cost-'+str(i)) for i in range(2)]
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='forbid-private-C')
        session.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'U': 2, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'B', bolt, {'R': 1}, ['A'])
        spell = session.state.stack[-1].ref
        self.cast(session, 'A', source, {'U': 2, 'C': 1}, [spell], buyback=True,
            discard_cards=[c.ref for c in costs])
        self.assertTrue(all(session.state.cards[c.object_id].zone == 'graveyard' for c in costs))
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'graveyard', 40), (session.state.cards[source.object_id].zone,
            session.state.cards[bolt.object_id].zone, session.state.players['A'].life))
        self.private_replay(session, private)

    def test_actual_collusion_keeps_search_private_after_selected_discard(self):
        session = self.session(757050)
        source = self.add(session.engine, 'Demonic Collusion', zone='hand')
        costs = [self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='collusion-cost-'+str(i)) for i in range(2)]
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='collusion-private-C')
        self.prepare(session, mana={'B': 2, 'C': 3})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'B': 2, 'C': 3}, buyback=True, discard_cards=[c.ref for c in costs])
        self.assertTrue(all(session.state.cards[c.object_id].zone == 'graveyard' for c in costs))
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        oid = session.state.players['A'].zones['library'][0]
        ref = session.state.cards[oid].ref
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': [ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'hand'), (session.state.cards[source.object_id].zone, session.state.cards[oid].zone))
        for viewer in 'BCD':self.assertNotIn(ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
        self.private_replay(session, private)

    def test_actual_flowstone_pays_life_then_random_hand_card_and_destroys_land(self):
        session = self.session(757060)
        source = self.add(session.engine, 'Flowstone Flood', zone='hand')
        self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='flowstone-random-cost')
        target = self.add(session.engine, 'Forest', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='flowstone-private-C')
        self.prepare(session, mana={'R': 1, 'C': 3})
        self.checkpoint(session)
        hand = set(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', source, {'R': 1, 'C': 3}, [target.ref], buyback=True)
        discarded = hand-set(session.state.players['A'].zones['hand'])-{source.object_id}
        self.assertEqual(1, len(discarded))
        self.assertEqual(37, session.state.players['A'].life)
        self.assertTrue(all(session.state.cards[oid].zone == 'graveyard' for oid in discarded))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'graveyard', 37), (session.state.cards[source.object_id].zone,
            session.state.cards[target.object_id].zone, session.state.players['A'].life))
        self.private_replay(session, private)

    def test_actual_slaughter_pays_four_life_and_retains_nonblack_destruction(self):
        session = self.session(757070)
        source = self.add(session.engine, 'Slaughter', zone='hand')
        target = self.add(session.engine, 'Generic Bound Body', seat='B')
        self.prepare(session, mana={'B': 2, 'C': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'B': 2, 'C': 2}, [target.ref], buyback=True)
        self.assertEqual(36, session.state.players['A'].life)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'graveyard', 36), (session.state.cards[source.object_id].zone,
            session.state.cards[target.object_id].zone, session.state.players['A'].life))
        self.replay(session)

    def test_actual_pegasus_pays_land_and_creates_the_complete_flying_token(self):
        session = self.session(757080)
        source = self.add(session.engine, 'Pegasus Stampede', zone='hand')
        land = self.add(session.engine, 'Forest')
        self.prepare(session, mana={'W': 1, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'W': 1, 'C': 1}, buyback=True, sacrifice_cards=[land.ref])
        self.assertEqual('graveyard', session.state.cards[land.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = [c for c in session.state.cards.values() if c.zone == 'battlefield' and c.is_token]
        self.assertEqual(1, len(tokens))
        token = tokens[0];data = session.engine._effective_card_data(token)
        self.assertEqual(('1', '1', ['W']), (data['power'], data['toughness'], data['colors']))
        self.assertIn('pegasus', session.engine._type_parts(data['type_line'])[1])
        self.assertIn('flying', session.engine._combat_keywords(token))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_actual_mists_pays_land_and_prevents_real_combat_damage(self):
        session = self.session(757090)
        source = self.add(session.engine, 'Constant Mists', zone='hand')
        land = self.add(session.engine, 'Forest')
        attacker = self.add(session.engine, 'Generic Bound Body')
        attacker.acquired_control_turn_count = -1
        self.add(session.engine, 'Generic Bound Body', seat='B', ref='mists-blocker-B')
        self.prepare(session, mana={'G': 1, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'G': 1, 'C': 1}, buyback=True, sacrifice_cards=[land.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'graveyard'), (session.state.cards[source.object_id].zone, session.state.cards[land.object_id].zone))
        self.attack(session, {attacker.ref: 'B'})
        self.blockers(session)
        self.block(session, {})
        self.advance(session, lambda s: s.state.phase == 'postcombat_main')
        self.assertEqual(40, session.state.players['B'].life)
        self.replay(session, load=True)

    def test_actual_walk_sacrifices_three_islands_including_nonbasic_and_adds_targeted_turn(self):
        session = self.session(757100)
        source = self.add(session.engine, 'Walk the Aeons', zone='hand')
        lands = [self.add(session.engine, 'Island', ref='walk-island-'+str(i)) for i in range(2)]
        lands.append(self.add(session.engine, 'Tropical Island'))
        self.add(session.engine, 'Forest')
        self.prepare(session, mana={'U': 2, 'C': 4})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'U': 2, 'C': 4}, ['D'], buyback=True, sacrifice_cards=[c.ref for c in lands])
        self.assertTrue(all(session.state.cards[c.object_id].zone == 'graveyard' for c in lands))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertEqual('D', session.state.active_player)
        self.replay(session)

    def test_countered_nonmana_buyback_does_not_return_or_refund_its_cost(self):
        session = self.session(757110)
        source = self.add(session.engine, 'Reaping the Rewards', zone='hand')
        land = self.add(session.engine, 'Forest')
        counter = self.add(session.engine, 'Counterspell', seat='B', zone='hand')
        session.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'W': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'W': 1}, buyback=True, sacrifice_cards=[land.ref])
        ref = session.state.stack[-1].ref
        self.cast(session, 'B', counter, {'U': 2}, [ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard', 'graveyard', 40), (session.state.cards[source.object_id].zone,
            session.state.cards[land.object_id].zone, session.state.players['A'].life))
        self.replay(session)

    def test_all_targets_illegal_does_not_return_the_paid_buyback_spell(self):
        session = self.session(757120)
        source = self.add(session.engine, 'Slaughter', zone='hand')
        target = self.add(session.engine, 'Generic Bound Body', seat='B')
        blink = self.add(session.engine, 'Cloudshift', seat='B', zone='hand')
        session.state.players['B'].mana_pool['W'] = 1
        self.prepare(session, mana={'B': 2, 'C': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'B': 2, 'C': 2}, [target.ref], buyback=True)
        self.cast(session, 'B', blink, {'W': 1}, [target.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard', 'battlefield', 36), (session.state.cards[source.object_id].zone,
            session.state.cards[target.object_id].zone, session.state.players['A'].life))
        self.replay(session)

    def test_copy_resolves_without_moving_the_original_card_or_paying_again(self):
        session = self.session(757130)
        source = self.add(session.engine, 'Reaping the Rewards', zone='hand')
        copy = self.add(session.engine, 'Twincast', zone='hand')
        land = self.add(session.engine, 'Forest')
        self.prepare(session, mana={'W': 1, 'U': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source, {'W': 1}, buyback=True, sacrifice_cards=[land.ref])
        target = session.state.stack[-1].ref
        self.cast(session, 'A', copy, {'U': 2}, [target])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand', 'graveyard', 44), (session.state.cards[source.object_id].zone,
            session.state.cards[land.object_id].zone, session.state.players['A'].life))
        self.assertEqual(1, sum(c.zone == 'hand' and c.printed_name == source.printed_name for c in session.state.cards.values()))
        self.replay(session)

    def test_optional_buyback_invalid_or_unpayable_choices_reject_before_any_payment(self):
        session = self.session(757140)
        source = self.add(session.engine, 'Forbid', zone='hand')
        costs = [self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='invalid-forbid-'+str(i)) for i in range(2)]
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        foreign = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='invalid-forbid-foreign')
        session.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'U': 2, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'B', bolt, {'R': 1}, ['A'])
        self.priority(session, 'A')
        target = session.state.stack[-1].ref
        for refs in ([costs[0].ref], [costs[0].ref, costs[0].ref], [costs[0].ref, source.ref], [costs[0].ref, foreign.ref]):
            before = authoritative_state_hash(session.state)
            result = session.act('pilot:A', {'action_id': 'cast:'+source.ref, 'cost_option': 'buyback',
                'pay': 'manual', 'payment': {'U': 2, 'C': 1}, 'targets': [target], 'discard_cards': refs})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source, {'U': 2, 'C': 1}, [target], buyback=True, discard_cards=[c.ref for c in costs])
        self.assertIsNone(self.resolve(session))
        self.replay(session)

    def test_typed_schema_roundtrip_and_legacy_wire_contract_remain_closed(self):
        from quorune.cast_lifecycles import FixedCastLifecycleSpec, compile_fixed_cast_lifecycle
        from quorune.compiler.nonmana_buyback_costs import compile_nonmana_buyback
        legacy = compile_fixed_cast_lifecycle(material_line='Buyback {3}', oracle_line='Buyback {3}', line_index=0)
        self.assertIsNotNone(legacy)
        self.assertNotIn('additional_costs', legacy.to_dict())
        self.assertEqual(legacy, FixedCastLifecycleSpec.from_dict(legacy.to_dict()))
        for name in NAMES:
            line = self.db.lookup(name).oracle_text.splitlines()[0]
            spec = compile_nonmana_buyback(material_line=line, oracle_line=line, line_index=0)
            self.assertIsNotNone(spec, name)
            self.assertEqual(spec, FixedCastLifecycleSpec.from_dict(spec.to_dict()))
            for change in ({'schema_version': True}, {'schema_version': 99}, {'additional_costs': []},
                    {'additional_costs': {}}, {'additional_costs': ['arbitrary']}, {'extra': 1}):
                with self.assertRaises((ValueError, TypeError)):FixedCastLifecycleSpec.from_dict({**spec.to_dict(), **change})
        for text in ('Buyback—Discard X cards.', 'Buyback—Sacrifice a creature.', 'Buyback—Pay half your life.',
                'Buyback—Sacrifice three lands and draw a card.'):
            self.assertIsNone(compile_nonmana_buyback(material_line=text, oracle_line=text, line_index=0), text)

    def test_optional_cost_metadata_preserves_existing_base_price(self):
        from types import SimpleNamespace
        from quorune.rules.casting.option_expansion import expand_optional_mana_costs
        from quorune.cast_lifecycles import escape_other_card_cost_descriptor
        from quorune.compiler.nonmana_buyback_costs import compile_nonmana_buyback
        spec = compile_nonmana_buyback(material_line='Buyback—Discard two cards.',
            oracle_line='Buyback—Discard two cards.', line_index=0)
        self.assertIsNotNone(spec)
        base_cost = escape_other_card_cost_descriptor(3)
        base = {'id': 'escape', 'requirements': {'U': 2, 'GENERIC': 1}, '_additional_option_costs': [base_cost]}
        host = SimpleNamespace(_mana_vector=lambda value: {k: int((value or {}).get(k, 0)) for k in ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')})
        rows = expand_optional_mana_costs(host, SimpleNamespace(zone='graveyard'), [base], {'optional_costs': [spec.fixed_cost_option()]})
        selected = [r for r in rows if r['id'] == 'buyback']
        self.assertEqual(1, len(selected))
        self.assertEqual([base_cost, *spec.fixed_cost_option()['_additional_option_costs']], selected[0]['_additional_option_costs'])
        self.assertEqual([base_cost], base['_additional_option_costs'])

    def test_new_buyback_and_composed_random_owners_are_independent_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i, name in enumerate(NAMES):
            session = self.session(757150+i)
            source = self.add(session.engine, name, zone='hand')
            programs = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(h.get('handler_id') == 'casting.lifecycle.fixed-public.v1' for h in p.handlers)]
            self.assertEqual(1, len(programs))
            program = programs[0]
            for cap in (CAPABILITY, *(('casting.additional_cost.fixed_life_random',) if name == 'Flowstone Flood' else ())):
                deps = tuple(d for d in program.capability_dependencies if d != cap)
                stripped = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict() if deps else None)
                bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+cap, bound['blockers'])

    def test_canonically_verified_nonmana_owner_withdrawal_blocks_all_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn nonmana Buyback authority'])
        registry = CapabilityRegistry(raw)
        decl = json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index = build_index(registry_value=raw, declaration_source=decl, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_fresh_producer_price_and_return_omissions_are_killed(self):
        from quorune.cast_lifecycles import FixedCastLifecycleSpec
        original = FixedCastLifecycleSpec.fixed_cost_option
        def no_price(spec):
            option = original(spec)
            if spec.schema_version == 5:option.pop('_additional_option_costs', None)
            return option
        cases = (('quorune.compiler.nonmana_buyback_costs.compile_nonmana_buyback', lambda **kwargs: None,
                    'test_all_eight_originals_have_complete_normal_admission'),
                 ('quorune.cast_lifecycles.FixedCastLifecycleSpec.fixed_cost_option', no_price,
                    'test_actual_rewards_pays_land_before_return_and_normal_recast_does_not_keep_buyback'))
        for owner, replacement, method in cases:
            with patch(owner, replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
        import quorune.rules.casting.commit as commit
        original_fields = commit.fixed_cast_lifecycle_stack_fields
        def no_return(option, destination, **kwargs):
            if option.get('fixed_cast_lifecycle', {}).get('schema_version') == 5:return destination, {}
            return original_fields(option, destination, **kwargs)
        method = 'test_actual_rewards_pays_land_before_return_and_normal_recast_does_not_keep_buyback'
        with patch.object(commit, 'fixed_cast_lifecycle_stack_fields', no_return):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
