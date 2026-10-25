from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.static_player_restriction_model import (
    STATIC_PLAYER_RESTRICTION_CAPABILITY as CAPABILITY,
    StaticPlayerRestrictionKind as Kind, StaticPlayerRestrictionSpec as Spec,
    static_player_restriction_capabilities,
)
from quorune.compiler.static_player_restrictions import static_player_restriction_template
from quorune.compiler.static_cast_rule_templates import static_cast_rule_handler
from quorune.semantic_runtime.static_cast_rules import default_static_cast_rule_registry
from quorune.semantic_runtime.activation_restrictions import (
    ActivationRestrictionContext, FixedPublicActivationProhibitionHandler,
)
from scripts.build_test_database import build_fixture_database
import test_receiver_query_unions as controls
from quorune.rules.evidence_storage import read_evidence_document


NAMES = ('Dosan the Falling Leaf', 'Drannith Magistrate', 'City of Solitude', 'Grand Abolisher', 'Ethersworn Canonist')


class StaticPlayerRestrictionModelTests(unittest.TestCase):
    def test_closed_player_restriction_wire_preserves_actor_and_source_turn_roles(self):
        for kind in Kind:
            spec = Spec(kind)
            self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            for changes in ({'schema_version': True}, {'kind': 'future'}, {'extra': 1}):
                with self.assertRaises(ValueError):
                    Spec.from_dict({**spec.to_dict(), **changes})
        self.assertTrue(Spec(Kind.ACTOR_OTHER_TURN).applies(actor='B', active_player='A', source_controller='B'))
        self.assertFalse(Spec(Kind.ACTOR_OTHER_TURN).applies(actor='B', active_player='B', source_controller='A'))
        self.assertTrue(Spec(Kind.SOURCE_TURN_OPPONENTS).applies(actor='B', active_player='A', source_controller='A'))
        self.assertFalse(Spec(Kind.SOURCE_TURN_OPPONENTS).applies(actor='A', active_player='A', source_controller='A'))
        self.assertFalse(Spec(Kind.SOURCE_TURN_OPPONENTS).applies(actor='B', active_player='B', source_controller='A'))

    def test_original_surfaces_keep_cast_and_activation_halves_and_old_descriptor_wire(self):
        data = json.loads((ROOT/'tests/fixtures/static-player-restriction-cards.json').read_text(encoding='utf-8'))
        cards = {r['name']: r for r in data['cards']}
        registry = default_static_cast_rule_registry()
        for name in NAMES:
            result = static_player_restriction_template(cards[name]['oracle_text'])
            self.assertIsNotNone(result, name)
            self.assertEqual(2 if name in ('City of Solitude', 'Grand Abolisher') else 1, len(result[1]))
            for handler in result[1]:
                if handler['event'] == 'cast.static.rule':
                    registry.validate(handler)
                else:
                    FixedPublicActivationProhibitionHandler().validate(handler)
            self.assertEqual((CAPABILITY,), static_player_restriction_capabilities(result[1]))
        legacy = static_cast_rule_handler("Players can't cast spells during combat.", source_name='Generic Observer')[1]
        self.assertEqual(1, legacy['schema_version'])
        self.assertNotIn('player_restriction', legacy)
        registry.validate(legacy)
        for text in ('Players can cast spells only during your turn.',
                "Each player who has cast a red spell this turn can't cast additional nonartifact spells.",
                "Your opponents can't cast spells from anywhere other than your hand."):
            self.assertIsNone(static_player_restriction_template(text))

    def test_activation_actor_all_zones_and_abolisher_permanent_types_remain_distinct(self):
        handler = FixedPublicActivationProhibitionHandler()
        city = static_player_restriction_template('Players can cast spells and activate abilities only during their own turns.')[1][1]
        abolisher = static_player_restriction_template("During your turn, your opponents can't cast spells or activate abilities of artifacts, creatures, or enchantments.")[1][1]
        row = ObjectQueryResult(object_id='card', ref='card', printed_name='Generic source', owner='A', controller='A', zone='hand', types=('creature',))
        context = ActivationRestrictionContext(restriction_source_ref='observer', chosen_name='', candidate_source_name='Generic source',
            candidate_is_mana_ability=True, restriction_source_controller='C', restriction_attached_object_id=None,
            restriction_attached_player=None, candidate_row=row, candidate_is_loyalty_ability=False,
            candidate_actor='B', active_player='A')
        self.assertTrue(handler.lower(city, context))
        self.assertFalse(handler.lower(city, replace(context, active_player='B')))
        self.assertFalse(handler.lower(abolisher, replace(context, active_player='C')))
        on_field = replace(context, active_player='C', candidate_row=replace(row, zone='battlefield', controller='B'))
        self.assertTrue(handler.lower(abolisher, on_field))
        self.assertFalse(handler.lower(abolisher, replace(on_field, candidate_row=replace(on_field.candidate_row, types=('land',)))))
        self.assertFalse(handler.lower(abolisher, replace(on_field, candidate_row=replace(on_field.candidate_row, types=('planeswalker',)))))

    def test_versioned_handlers_reject_mixed_scope_and_schema_drift(self):
        templates = [static_player_restriction_template(text)[1] for text in (
            'Players can cast spells only during their own turns.',
            'Players can cast spells and activate abilities only during their own turns.',
            "During your turn, your opponents can't cast spells or activate abilities of artifacts, creatures, or enchantments.")]
        for handlers in templates:
            for descriptor in handlers:
                validator = default_static_cast_rule_registry() if descriptor['event'] == 'cast.static.rule' else FixedPublicActivationProhibitionHandler()
                for change in ({'schema_version': 2}, {'schema_version': True}, {'future': 1},
                        {'player_restriction': {'schema_version': 1, 'kind': 'future'}}):
                    with self.assertRaises(ValueError):
                        validator.validate({**descriptor, **change})
                changed = {**descriptor, 'player_restriction': Spec(Kind.NONARTIFACT_LIMIT).to_dict()}
                with self.assertRaises(ValueError):
                    validator.validate(changed)


class StaticPlayerRestrictionRuntimeTests(unittest.TestCase):
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
    advance = controls.ReceiverQueryUnionRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'static-player-restrictions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/static-player-restriction-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Static player restriction', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def actions(self, s, seat='A'):
        return s.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']

    def cast(self, s, card, targets=(), *, seat='A', payment=None):
        self.priority(s, seat)
        row = next((r for r in self.actions(s, seat) if r['id'] == 'cast:'+card.ref), None)
        self.assertIsNotNone(row, card.printed_name)
        command = {'action_id': row['id'], 'targets': list(targets), 'pay': 'auto'}
        if s.state.cards[card.object_id].zone != 'hand':
            options = row.get('cost_options', [])
            self.assertEqual(1, len(options))
            command['cost_option'] = options[0]['id']
        if payment is not None:
            command.update(pay='manual', payment=payment)
        self.reject(s, command, seat='D' if seat != 'D' else 'C')
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, s, command, *, seat='A'):
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def activate(self, s, card, *, seat='A'):
        self.priority(s, seat)
        row = next((r for r in self.actions(s, seat) if r['id'].startswith('activate:'+card.ref+':')), None)
        self.assertIsNotNone(row, card.printed_name)
        result = s.act('pilot:'+seat, {'action_id': row['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        return row

    def test_five_originals_have_complete_native_programs_and_independent_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        for i, name in enumerate(NAMES):
            s = self.session(752100+i)
            source = self.add(s.engine, name, zone='hand')
            self.assertTrue(self.binding(s, source)['strict_capability_ready'], name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            programs = [p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies]
            self.assertEqual(2 if name in ('City of Solitude', 'Grand Abolisher') else 1, len(programs))
            for program in programs:
                deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
                stripped = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])

    def test_actual_dosan_cast_restricts_each_actor_turn_but_keeps_mana_abilities(self):
        s = self.session(752110)
        source = self.add(s.engine, 'Dosan the Falling Leaf', zone='hand')
        own = self.add(s.engine, 'Shock', zone='hand')
        enemy = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='dosan-enemy-shock')
        forest = self.add(s.engine, 'Forest', seat='B')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool['R'] = 1
        self.prepare(s, mana={'C': 1, 'G': 2, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1, 'G': 2})
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.cast(s, own, ['D'])
        self.priority(s, 'B')
        self.assertFalse(any(r['id'] == 'cast:'+enemy.ref for r in self.actions(s, 'B')))
        self.reject(s, {'action_id': 'cast:'+enemy.ref, 'targets': ['C'], 'pay': 'auto'}, seat='B')
        self.activate(s, forest, seat='B')
        self.assertEqual(1, s.state.players['B'].mana_pool['G'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['D'].life)
        self.prepare(s, actor='B', active='B', mana={'R': 1})
        self.checkpoint(s)
        self.cast(s, enemy, ['C'], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['C'].life)
        self.private_replay(s, private)

    def test_actual_source_control_and_ability_loss_recompute_both_abolisher_halves(self):
        s = self.session(752115)
        source = self.add(s.engine, 'Grand Abolisher')
        enemy = self.add(s.engine, 'Shock', seat='B', zone='hand')
        artifact = self.add(s.engine, 'Sol Ring', seat='B')
        humble = self.add(s.engine, 'Humble', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, actor='A', active='B', mana={'C': 3, 'U': 2, 'W': 1})
        s.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(s)
        # A controls the source, but it is B's turn: both B actions remain legal.
        from quorune.rules.casting.proposal import build_cast_offer
        self.assertEqual('payable', build_cast_offer(s.engine, 'B', s.state.cards[enemy.object_id]).status)
        from quorune.rules.activation.availability import activation_availability
        ability = s.engine._activated_abilities(artifact)[0]
        self.assertEqual('payable', activation_availability(s.engine, 'B', artifact, ability)[0])
        # Control Magic is sorcery-speed, so first use a canonical control boundary.
        s.engine.change_control(source.object_id, 'B', reason='static player source-controller boundary')
        self.assertEqual('payable', activation_availability(s.engine, 'B', artifact, ability)[0])
        own = self.add(s.engine, 'Shock', zone='hand', ref='controlled-abolisher-A-shock')
        self.prepare(s, mana={'R': 1, 'C': 1, 'W': 1}, active='B')
        self.checkpoint(s)
        self.assertEqual('static_cast_prohibition', build_cast_offer(s.engine, 'A', s.state.cards[own.object_id]).reason)
        # On A's turn the B-controlled source permits A to remove its ability.
        self.prepare(s, mana={'R': 1, 'C': 1, 'W': 1}, active='A')
        self.checkpoint(s)
        self.cast(s, humble, [source.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.prepare(s, mana={'R': 1}, active='B')
        self.checkpoint(s)
        self.assertEqual('payable', build_cast_offer(s.engine, 'A', s.state.cards[own.object_id]).status)
        self.prepare(s, mana={'R': 1}, active='B')
        self.checkpoint(s)
        self.cast(s, own, ['D'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['D'].life)
        self.private_replay(s, private)

    def test_drannith_command_and_exile_permissions_recheck_source_departure(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        from quorune.rules.casting.proposal import build_cast_offer
        s = self.session(752117)
        source = self.add(s.engine, 'Drannith Magistrate')
        exile = self.add(s.engine, 'Shock', seat='B', zone='exile')
        grant_temporary_cast_permission(s.engine, card=exile,
            grant=TemporaryCastPermissionGrant('B', ImpulseAccessDuration.END_OF_TURN, None, False, 'static restriction exile permission'),
            reason='static player permission boundary')
        commander = next(s.state.cards[oid] for oid in s.state.players['B'].zones['command'])
        # Ensure the fixture commander takes the same normal complete registration path.
        self.add(s.engine, 'Generic Bound Commander', seat='C', ref='registered-commander-control')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'U': 1}, active='B')
        s.state.players['B'].mana_pool.update({'W': 1, 'R': 1})
        self.checkpoint(s)
        for card in (exile, commander):
            self.assertEqual('static_cast_prohibition', build_cast_offer(s.engine, 'B', card).reason)
        self.cast(s, bounce, [source.ref])
        self.assertIsNone(self.resolve(s))
        self.cast(s, exile, ['D'], seat='B')
        self.assertIsNone(self.resolve(s))
        self.cast(s, commander, seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[commander.object_id].zone)
        self.assertEqual(38, s.state.players['D'].life)
        self.private_replay(s, private)

    def test_canonist_counts_countered_nonartifact_casts_and_resets_on_next_turn(self):
        s = self.session(752118)
        first = self.add(s.engine, 'Shock', zone='hand')
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        source = self.add(s.engine, 'Ethersworn Canonist', zone='hand')
        second = self.add(s.engine, 'Shock', zone='hand', ref='countered-canonist-second')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'R': 2, 'C': 1, 'W': 1})
        s.state.players['B'].mana_pool['U'] = 2
        self.checkpoint(s)
        self.cast(s, first, ['D'])
        target = s.state.stack[-1].ref
        self.cast(s, counter, [target], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual(40, s.state.players['D'].life)
        self.cast(s, source, payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertFalse(any(r['id'] == 'cast:'+second.ref for r in self.actions(s)))
        self.reject(s, {'action_id': 'cast:'+second.ref, 'targets': ['D'], 'pay': 'auto'})
        turn = s.state.turn_sequence
        self.advance(s, lambda current: current.state.turn_sequence > turn)
        self.prepare(s, actor='A', active='B', mana={'R': 1})
        self.checkpoint(s)
        self.cast(s, second, ['D'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['D'].life)
        self.private_replay(s, private)

    def test_canonically_verified_player_restriction_withdrawal_blocks_all_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        value = deepcopy(raw)
        next(c for c in value['capabilities'] if c['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn static player restriction'])
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

    def test_fresh_native_witnesses_kill_player_producer_and_cast_activation_omissions(self):
        method = 'test_five_originals_have_complete_native_programs_and_independent_minimum'
        with patch('quorune.compiler.static_player_restrictions.static_player_restriction_template', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        method = 'test_actual_dosan_cast_restricts_each_actor_turn_but_keeps_mana_abilities'
        with patch('quorune.rules.casting.proposal.static_cast_prohibition_reason', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        method = 'test_actual_city_cast_blocks_off_turn_mana_and_cycling_but_not_own_turn'
        with patch('quorune.rules.activation.availability.current_activation_prohibitions', return_value=()):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_player_restrictions_bind_auto_mana_sources_and_stale_payment_plans(self):
        from quorune.mana_source_discovery import available_mana_sources
        from quorune.mana_activation import complete_mana_plan_activations
        from quorune.errors import GameRuleError
        s = self.session(752116)
        source = self.add(s.engine, 'Grand Abolisher')
        ring = self.add(s.engine, 'Sol Ring', seat='B')
        land = self.add(s.engine, 'Forest', seat='B')
        token_ref = s.engine.create_token('B', name='', copy_of=ring.ref, reason='player restriction token mana boundary')[0]
        token = next(c for c in s.state.cards.values() if c.ref == token_ref)
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, actor='B', active='A')
        self.checkpoint(s)
        refs = {r.ref for r in available_mana_sources(s.engine, 'B')}
        self.assertIn(land.ref, refs)
        self.assertNotIn(ring.ref, refs)
        self.assertNotIn(token.ref, refs)
        before = authoritative_state_hash(s.state)
        for card in (ring, token):
            with self.assertRaises(GameRuleError):
                complete_mana_plan_activations(s.engine, 'B', [{'source': card.ref, 'bundle': {'C': 2}}])
            self.assertEqual(before, authoritative_state_hash(s.state))
        # A current source ability loss permits the very same typed mana plans.
        source.phased_out = True
        refs = {r.ref for r in available_mana_sources(s.engine, 'B')}
        self.assertTrue({ring.ref, token.ref, land.ref}.issubset(refs))
        source.phased_out = False
        self.prepare(s, actor='B', active='B')
        self.checkpoint(s)
        self.activate(s, token, seat='B')
        self.assertEqual(2, s.state.players['B'].mana_pool['C'])
        self.assertTrue(s.state.cards[token.object_id].tapped)
        self.private_replay(s, private)

    def test_actual_city_cast_blocks_off_turn_mana_and_cycling_but_not_own_turn(self):
        s = self.session(752111)
        source = self.add(s.engine, 'City of Solitude', zone='hand')
        enemy = self.add(s.engine, 'Shock', seat='B', zone='hand')
        forest = self.add(s.engine, 'Forest', seat='B')
        cycle = self.add(s.engine, 'Barren Moor', seat='B', zone='hand')
        own = self.add(s.engine, 'Shock', zone='hand', ref='city-own-shock')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'G': 1, 'R': 1})
        s.state.players['B'].mana_pool.update({'B': 1, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 2, 'G': 1})
        self.assertIsNone(self.resolve(s))
        self.cast(s, own, ['D'])
        self.priority(s, 'B')
        for card, prefix in ((enemy, 'cast:'), (forest, 'activate:'), (cycle, 'activate:')):
            self.assertFalse(any(r['id'].startswith(prefix+card.ref) for r in self.actions(s, 'B')))
        self.reject(s, {'action_id': 'cast:'+enemy.ref, 'targets': ['C'], 'pay': 'auto'}, seat='B')
        from quorune.rules.activation.availability import activation_availability
        for card in (forest, cycle):
            ability = s.engine._activated_abilities(s.state.cards[card.object_id])[0]
            self.assertEqual('unavailable', activation_availability(s.engine, 'B', s.state.cards[card.object_id], ability)[0])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.prepare(s, actor='B', active='B', mana={'B': 1, 'R': 1})
        self.checkpoint(s)
        self.activate(s, forest, seat='B')
        self.activate(s, cycle, seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[cycle.object_id].zone)
        self.cast(s, enemy, ['C'], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['C'].life)
        self.private_replay(s, private)

    def test_actual_abolisher_cast_keeps_land_mana_and_hand_cycling_available(self):
        s = self.session(752112)
        source = self.add(s.engine, 'Grand Abolisher', zone='hand')
        own = self.add(s.engine, 'Shock', zone='hand')
        enemy = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='abolisher-enemy-shock')
        land = self.add(s.engine, 'Forest', seat='B')
        artifact = self.add(s.engine, 'Sol Ring', seat='B')
        creature = self.add(s.engine, 'Llanowar Elves', seat='B')
        creature.acquired_control_turn_count = -1
        cycle = self.add(s.engine, 'Barren Moor', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'W': 2, 'R': 1})
        s.state.players['B'].mana_pool.update({'B': 1, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'W': 2})
        self.assertIsNone(self.resolve(s))
        self.cast(s, own, ['D'])
        self.priority(s, 'B')
        self.assertFalse(any(r['id'] == 'cast:'+enemy.ref for r in self.actions(s, 'B')))
        for card in (artifact, creature):
            self.assertFalse(any(r['id'].startswith('activate:'+card.ref+':') for r in self.actions(s, 'B')))
        self.activate(s, land, seat='B')
        self.activate(s, cycle, seat='B')
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[cycle.object_id].zone)
        self.prepare(s, actor='B', active='B', mana={'R': 1})
        self.checkpoint(s)
        self.activate(s, artifact, seat='B')
        self.assertEqual(2, s.state.players['B'].mana_pool['C'])
        self.cast(s, enemy, ['C'], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['C'].life)
        self.private_replay(s, private)

    def test_actual_canonist_cast_counts_prior_nonartifact_and_allows_artifact_creatures(self):
        s = self.session(752113)
        first = self.add(s.engine, 'Shock', zone='hand')
        source = self.add(s.engine, 'Ethersworn Canonist', zone='hand')
        second = self.add(s.engine, 'Shock', zone='hand', ref='canonist-second')
        artifact = self.add(s.engine, 'Ornithopter', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'R': 2, 'C': 1, 'W': 1})
        s.state.players['B'].mana_pool['U'] = 1
        self.checkpoint(s)
        self.cast(s, first, ['D'])
        self.assertIsNone(self.resolve(s))
        self.cast(s, source, payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertFalse(any(r['id'] == 'cast:'+second.ref for r in self.actions(s)))
        self.reject(s, {'action_id': 'cast:'+second.ref, 'targets': ['D'], 'pay': 'auto'})
        self.cast(s, artifact)
        self.cast(s, bounce, [source.ref], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.cast(s, second, ['D'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(36, s.state.players['D'].life)
        self.private_replay(s, private)

    def test_actual_drannith_cast_blocks_opponent_nonhand_methods_and_keeps_hand_casts(self):
        s = self.session(752114)
        source = self.add(s.engine, 'Drannith Magistrate', zone='hand')
        grave = self.add(s.engine, "Raffine's Guidance", seat='B', zone='graveyard')
        target = self.add(s.engine, 'Generic Bound Body', seat='B')
        own_grave = self.add(s.engine, "Raffine's Guidance", zone='graveyard', ref='drannith-own-grave')
        own_target = self.add(s.engine, 'Generic Bound Body', ref='drannith-own-target')
        enemy = self.add(s.engine, 'Shock', seat='B', zone='hand')
        own = self.add(s.engine, 'Shock', zone='hand', ref='drannith-own-shock')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 3, 'W': 2, 'R': 1})
        s.state.players['B'].mana_pool.update({'C': 2, 'W': 1, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.cast(s, own_grave, [own_target.ref], payment={'C': 2, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.cast(s, own, ['D'])
        self.cast(s, enemy, ['C'], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual((38, 38), (s.state.players['C'].life, s.state.players['D'].life))
        self.prepare(s, actor='B', active='B', mana={'C': 2, 'W': 1})
        self.checkpoint(s)
        self.assertFalse(any(r['id'] == 'cast:'+grave.ref for r in self.actions(s, 'B')))
        self.reject(s, {'action_id': 'cast:'+grave.ref, 'targets': [target.ref], 'pay': 'auto'}, seat='B')
        self.private_replay(s, private)
