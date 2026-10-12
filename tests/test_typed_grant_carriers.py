from __future__ import annotations

"""Printed grants: CR 113.7a, 604.1, 611.3 and 613.1f establish expectations."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session, pass_current
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.conditional_continuous import (
    FixedPublicStateCharacteristicsHandler, FixedPublicStateGrantedAbilityHandler,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


CAPABILITY = "continuous.ability.fixed_public_state_grant"
FIXTURE = ROOT / "tests/fixtures/typed-grant-carrier-cards.json"


class TemporaryQuotedGrantCompilerTests(unittest.TestCase):
    def test_temporary_quoted_activation_retains_independent_child_and_duration(self):
        # CR 611.2a/c, 613.1f and 113.7: the recipient gains the stated
        # ability until cleanup; that recipient is the later ability source.
        from test_bound_effect_programs import record
        text = "Until end of turn, target creature gains \"{T}: Return target nonland permanent to its owner's hand.\""
        ir = compile_oracle_card(record(text, 'Instant'), capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        child = next(n for n in ir.faces[0].nodes if n.kind == 'granted_activated_ability')
        self.assertEqual('bounce', child.effects[0]['op'])
        self.assertEqual('$target.0', outer.effects[0]['card'])
        self.assertFalse(outer.effects[0]['printed_only'])
        self.assertFalse(outer.effects[0]['exclude_source'])
        self.assertEqual('granted_activated', outer.effects[0]['ability_fragments'][0]['kind'])


    def test_temporary_quote_rejects_open_child_and_duration_and_shape(self):
        from test_bound_effect_programs import record
        from quorune.rules.temporary_ability_grant import temporary_quoted_grant_node_capabilities
        registry = load_default_capability_registry()
        for text in (
            'Target creature gains "{T}: Rewrite history." until end of turn.',
            'Target creature gains "{T}: Draw a card." until your next turn.',
            'Target creature gains "When a player rewrites history, draw a card." until end of turn.',
            'Target creature gains "{T}: Draw a card."',
        ):
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Instant'), capability_registry=registry).status)
        ir = compile_oracle_card(record('Target creature gains "{T}: Draw a card." until end of turn.', 'Instant'), capability_registry=registry)
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        for change in ({'printed_only': True}, {'unknown': True}, {'card': '$target.1'}, {'exclude_source': True}):
            effect = {**outer.effects[0], **change}
            self.assertEqual((), temporary_quoted_grant_node_capabilities(effects=(effect,), target_schema=outer.target_schema, mechanic_ids=outer.mechanics))

    def test_temporary_quote_child_dependency_and_material_sibling_fail_closed(self):
        from test_bound_effect_programs import record
        text = "Until end of turn, target creature gains \"{T}: Return target nonland permanent to its owner's hand.\""
        registry = load_default_capability_registry()
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(c for c in raw['capabilities'] if c['id'] == 'permanent.return.owner_hand')
        row.update(status='blocked', blockers=['Independent quoted-child boundary mutation'])
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_oracle_card(record(text, 'Instant'), capability_registry=altered).status)
        self.assertNotEqual('exact', compile_oracle_card(record(text + '\nRewrite history.', 'Instant'), capability_registry=registry).status)

    def test_temporary_quoted_set_locks_one_public_query_and_child(self):
        from test_bound_effect_programs import record
        text = 'Until end of turn, lands you control gain "{T}: Add one mana of any color."'
        ir = compile_oracle_card(record(text, 'Instant'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        self.assertIsNone(outer.target_schema)
        self.assertEqual(8, outer.effects[0]['schema_version'])
        self.assertEqual('$controller', outer.effects[0]['predicate']['controller'])
        self.assertEqual(['land'], outer.effects[0]['predicate']['types_all'])
        self.assertEqual('granted_activated', outer.effects[0]['ability_fragments'][0]['kind'])

    def test_temporary_quoted_query_rejects_open_fields_hidden_zones_and_omission(self):
        from test_bound_effect_programs import record
        from quorune.rules.temporary_ability_grant import temporary_quoted_grant_node_capabilities
        from quorune.compiler import temporary_quoted_ability_nodes as owner
        text = 'Until end of turn, lands you control gain "{T}: Add one mana of any color."'
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(text, 'Instant'), capability_registry=registry)
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        for change in ({'unknown': True}, {'schema_version': True}, {'predicate': {**outer.effects[0]['predicate'], 'zones': ['hand']}}):
            self.assertEqual((), temporary_quoted_grant_node_capabilities(effects=({**outer.effects[0], **change},), target_schema=None, mechanic_ids=outer.mechanics))
        from quorune.effect_runtime.temporary_ability_bundle import apply_temporary_ability_bundle
        from quorune.errors import GameRuleError
        for change in ({'unknown': True}, {'predicate': {**outer.effects[0]['predicate'], 'zones': ['hand']}}):
            with self.assertRaises(GameRuleError):
                apply_temporary_ability_bundle(object(), {**outer.effects[0], **change}, actor='A', reason='Malformed query boundary')
        for subject in ('Cards in your hand', 'Lands with an unknown quality you control', 'Creatures target player controls'):
            self.assertNotEqual('exact', compile_oracle_card(record(text.replace('lands you control', subject), 'Instant'), capability_registry=registry).status)
        with mock.patch.object(owner, 'temporary_quoted_ability_nodes', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_temporary_quoted_set_locks_one_public_query_and_child()

    def test_temporary_quoted_death_trigger_is_a_recipient_child(self):
        from test_bound_effect_programs import record
        text = 'Until end of turn, target creature gains "When this creature dies, draw a card."'
        ir = compile_oracle_card(record(text, 'Instant'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        child = next(n for n in ir.faces[0].nodes if n.kind == 'granted_triggered_ability')
        self.assertIn(child.event, {'creature.dies.self', 'creature.dies'})
        self.assertEqual('granted_triggered', outer.effects[0]['ability_fragments'][0]['kind'])

    def test_temporary_trigger_child_omission_mutant_is_killed(self):
        from quorune.compiler import temporary_quoted_ability_nodes as owner
        with mock.patch.object(owner, 'temporary_quoted_ability_nodes', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_temporary_quoted_death_trigger_is_a_recipient_child()

    def test_fixed_stats_and_quoted_trigger_keep_one_recipient_timestamp(self):
        from test_bound_effect_programs import record
        ir = compile_oracle_card(record('Until end of turn, target creature gets +2/+0 and gains "When this creature dies, draw a card."', 'Instant'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        self.assertEqual(9, outer.effects[0]['schema_version'])
        self.assertEqual((2, 0), (outer.effects[0]['power'], outer.effects[0]['toughness']))
        self.assertEqual('$target.0', outer.effects[0]['card'])
        self.assertEqual('granted_triggered', outer.effects[0]['ability_fragments'][0]['kind'])

    def test_coupled_stat_quote_rejects_boolean_stats_unknown_fields_and_omission(self):
        from test_bound_effect_programs import record
        from quorune.rules.temporary_ability_grant import temporary_quoted_grant_node_capabilities
        from quorune.effect_runtime.temporary_ability_bundle import apply_temporary_ability_bundle
        from quorune.errors import GameRuleError
        from quorune.compiler import temporary_quoted_ability_nodes as owner
        ir = compile_oracle_card(record('Until end of turn, target creature gets +2/+0 and gains "When this creature dies, draw a card."', 'Instant'), capability_registry=load_default_capability_registry())
        outer = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        for change in ({'power': True}, {'unknown': True}):
            altered = {**outer.effects[0], **change}
            self.assertEqual((), temporary_quoted_grant_node_capabilities(effects=(altered,), target_schema=outer.target_schema, mechanic_ids=outer.mechanics))
            with self.assertRaises(GameRuleError):
                apply_temporary_ability_bundle(object(), altered, actor='A', reason='Malformed coupled grant')
        with mock.patch.object(owner, 'temporary_quoted_ability_nodes', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_fixed_stats_and_quoted_trigger_keep_one_recipient_timestamp()

    def test_temporary_quoted_compiler_omission_mutant_is_killed(self):
        from quorune.compiler import temporary_quoted_ability_nodes as owner
        with mock.patch.object(owner, 'temporary_quoted_ability_nodes', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_temporary_quoted_activation_retains_independent_child_and_duration()


class TemporaryQuotedGrantRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'temporary-grant.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Temporary grant', [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _witnesses
    session = _witnesses.session
    add = _witnesses.add
    ready = _witnesses.ready
    checkpoint = _witnesses.checkpoint
    resolve = _witnesses.resolve
    replay = _witnesses.replay

    def test_original_retraction_helix_grants_recipient_activation_authority_and_replay(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        from quorune.card_programs.adapters import compile_best_available_card_program
        session = self.session(357101)
        engine = session.engine
        spell = self.add(engine, 'Retraction Helix', zone='hand')
        recipient = self.add(engine, 'Generic Bound Body', seat='B', ref='helix-recipient')
        recipient.acquired_control_turn_count = -1
        victim = self.add(engine, 'Generic Bound Body', seat='C', ref='helix-victim')
        program = compile_best_available_card_program(self.db, self.db.lookup('Retraction Helix'), semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        action = self.ready(session, spell, {'U': 1})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        session = self.replay(session, load=True)
        engine = session.engine
        for _ in range(8):
            if session.pending_principals()[0] == 'pilot:B':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        activation = next(a for a in actions if a['id'].startswith('activate:' + recipient.ref + ':'))
        self.assertIn(victim.ref, activation['target_schema']['legal_refs'])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A', {'action_id': activation['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {'action_id': activation['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(session.state.cards[recipient.object_id].tapped)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[victim.object_id].zone)
        projected = json.dumps(session.packet('pilot:D', full=True)['state'])
        self.assertNotIn(victim.object_id, projected)
        self.assertIn(victim.ref, projected)  # The bounced public object remains known.
        self.assertNotIn('hand', session.packet('pilot:D', full=True)['state']['players']['C'])
        for oid in session.state.players['C'].zones['hand']:
            if oid != victim.object_id:
                self.assertNotIn(session.state.cards[oid].ref, projected)
        self.replay(session, load=True)
        expire_end_of_turn_continuous_effects(session.state)
        self.assertFalse(engine._effective_ability_fragments(session.state.cards[recipient.object_id]))


    def test_original_banishing_knack_recipient_blink_and_ability_removal_controls(self):
        from quorune.continuous_effect_model import ContinuousOperation, Layer
        from quorune.continuous_effect_state import create_resolution_continuous_effect, resolution_effect_source
        session = self.session(357102)
        engine = session.engine
        spell = self.add(engine, 'Banishing Knack', zone='hand')
        recipient = self.add(engine, 'Generic Bound Body', ref='knack-recipient')
        recipient.acquired_control_turn_count = -1
        action = self.ready(session, spell, {'U': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        session = self.replay(session, load=True)
        engine = session.engine
        current = session.state.cards[recipient.object_id]
        self.assertTrue(engine._effective_ability_fragments(current))
        # A later layer-six removal suppresses the gained activation; restoring
        # that independent effect exposes the still-live earlier grant.
        previous = len(session.state.continuous_effects)
        create_resolution_continuous_effect(engine, source=resolution_effect_source(engine, {}),
            targets=(current,), layer=Layer.ABILITY, sublayer='6',
            operations=(ContinuousOperation('remove_all_abilities'),))
        self.assertFalse(engine._effective_ability_fragments(current))
        del session.state.continuous_effects[previous:]
        self.assertTrue(engine._effective_ability_fragments(current))
        old = current.logical_object_id
        engine.move_card(current.object_id, 'hand', log=False)
        engine.move_card(current.object_id, 'battlefield', controller='A', log=False)
        self.assertNotEqual(old, current.logical_object_id)
        self.assertFalse(engine._effective_ability_fragments(current))


    def test_original_divergent_growth_locks_lands_and_uses_current_recipient_controller(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        session = self.session(357103)
        engine = session.engine
        spell = self.add(engine, 'Divergent Growth', zone='hand')
        land = self.add(engine, 'Generic Bound Plains', ref='growth-land')
        foreign = self.add(engine, 'Generic Bound Plains', seat='C', ref='foreign-growth-land')
        action = self.ready(session, spell, {'G': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        session = self.replay(session, load=True)
        engine = session.engine
        self.assertTrue(engine._effective_ability_fragments(session.state.cards[land.object_id]))
        self.assertFalse(engine._effective_ability_fragments(session.state.cards[foreign.object_id]))
        late = self.add(engine, 'Generic Bound Plains', ref='late-growth-land')
        self.assertFalse(engine._effective_ability_fragments(late))
        engine.change_control(land.object_id, 'B', reason='Independent controller boundary')
        for _ in range(8):
            if session.pending_principals()[0] == 'pilot:B':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        activation = next(a for a in actions if a['id'].startswith('activate:' + land.ref + ':ability:granted:'))
        amount = session.state.players['B'].mana_pool.get('U', 0)
        accepted = session.act('pilot:B', {'action_id': activation['id'], 'mana_choice': 'U', 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(amount + 1, session.state.players['B'].mana_pool.get('U', 0))
        self.assertTrue(session.state.cards[land.object_id].tapped)
        expire_end_of_turn_continuous_effects(session.state)
        self.assertFalse(engine._effective_ability_fragments(session.state.cards[land.object_id]))


    def test_temporary_death_grant_uses_departing_recipient_controller_private_draw_and_replay(self):
        session = self.session(357104)
        engine = session.engine
        spell = self.add(engine, 'Generic Temporary Death Draw', zone='hand')
        removal = self.add(engine, 'Murder', zone='hand')
        recipient = self.add(engine, 'Generic Bound Body', seat='B', ref='granted-death-recipient')
        action = self.ready(session, spell, {'U': 1, 'B': 2, 'C': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        session = self.replay(session, load=True)
        engine = session.engine
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == removal.ref)
        before_draw = len(session.state.players['B'].zones['hand'])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[recipient.object_id].zone)
        self.assertEqual(before_draw + 1, len(session.state.players['B'].zones['hand']))
        projected = json.dumps(session.packet('pilot:D', full=True)['state'])
        for oid in session.state.players['B'].zones['hand']:
            self.assertNotIn(session.state.cards[oid].ref, projected)
        self.replay(session, load=True)
        # Departure and reentry remove the original grant, so a second death
        # of the new incarnation cannot publish another granted draw.
        current = session.state.cards[recipient.object_id]
        engine.move_card(current.object_id, 'battlefield', controller='B', log=False)
        after = len(session.state.players['B'].zones['hand'])
        engine.apply_effect({'op': 'destroy', 'card': current.ref, 'reason': 'New-incarnation negative control'}, actor='A')
        self.resolve(session)
        self.assertEqual(after, len(session.state.players['B'].zones['hand']))


    def test_coupled_stat_quote_actual_cast_shared_timestamp_death_and_replay(self):
        session = self.session(357106)
        engine = session.engine
        spell = self.add(engine, 'Generic Coupled Death Draw', zone='hand')
        removal = self.add(engine, 'Murder', zone='hand')
        recipient = self.add(engine, 'Generic Bound Body', seat='B', ref='granted-death-recipient')
        action = self.ready(session, spell, {'U': 1, 'B': 2, 'C': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(4, engine._numeric_stat(recipient.object_id, 'power'))
        self.assertEqual(2, len(session.state.continuous_effects))
        self.assertEqual(1, len({effect.timestamp for effect in session.state.continuous_effects}))
        session = self.replay(session, load=True)
        engine = session.engine
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == removal.ref)
        before_draw = len(session.state.players['B'].zones['hand'])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[recipient.object_id].zone)
        self.assertEqual(before_draw + 1, len(session.state.players['B'].zones['hand']))
        projected = json.dumps(session.packet('pilot:D', full=True)['state'])
        for oid in session.state.players['B'].zones['hand']:
            self.assertNotIn(session.state.cards[oid].ref, projected)
        self.replay(session, load=True)
        # Departure and reentry remove the original grant, so a second death
        # of the new incarnation cannot publish another granted draw.
        current = session.state.cards[recipient.object_id]
        engine.move_card(current.object_id, 'battlefield', controller='B', log=False)
        after = len(session.state.players['B'].zones['hand'])
        engine.apply_effect({'op': 'destroy', 'card': current.ref, 'reason': 'New-incarnation negative control'}, actor='A')
        self.resolve(session)
        self.assertEqual(after, len(session.state.players['B'].zones['hand']))


    def test_original_verdant_rebirth_returns_granted_recipient_after_real_death_and_replays(self):
        session = self.session(357105)
        engine = session.engine
        spell = self.add(engine, 'Verdant Rebirth', zone='hand')
        removal = self.add(engine, 'Murder', zone='hand')
        recipient = self.add(engine, 'Generic Bound Body', seat='B', ref='rebirth-recipient')
        action = self.ready(session, spell, {'G': 1, 'B': 2, 'C': 2})
        self.checkpoint(session)
        a_hand = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(a_hand, len(session.state.players['A'].zones['hand']))
        session = self.replay(session, load=True)
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == removal.ref)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [recipient.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[recipient.object_id].zone)
        self.assertIn(recipient.object_id, session.state.players['B'].zones['hand'])
        self.assertEqual('graveyard', session.state.cards[spell.object_id].zone)
        self.replay(session, load=True)


class TypedGrantCarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "typed-grants.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/scryfall-exact-lists.json", FIXTURE], path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / "examples/mishra-eminent-one.txt", commander="Mishra, Eminent One")
        cls.zimone = loader.load(ROOT / "examples/zimone-and-dina.txt", commander="Zimone and Dina")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=seed, auto_pass_empty=False)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        for seat in engine.seats:
            engine.state.players[seat].mana_pool.update(C=20, W=20, U=20, B=20, R=20, G=20)
        return session

    def card(self, session, name, zone="hand", seat="A"):
        engine = session.engine
        record = self.db.lookup(name, fuzzy=False)
        ref = engine._next_ref("P")
        card = CardInstance(
            object_id=f"typed-grant:{ref}", ref=ref, oracle_id=record.oracle_id,
            printed_name=record.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats) if zone in {"battlefield", "graveyard"} else [seat],
            revealed_to=list(engine.seats) if zone == "battlefield" else [],
            entered_battlefield_turn_sequence=engine.state.turn_sequence - 1,
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db, engine.semantics, (record,), capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return card

    def priority(self, session, seat):
        engine = session.engine
        if engine.state.pending_decision is None:
            engine._grant_priority(engine.state.priority_player or engine.state.active_player)
            engine.pump()
        for _ in range(12):
            decision = engine.state.pending_decision
            if decision is not None and seat in decision.actors:
                break
            pass_current(session)
        else:
            self.fail(f"No supported priority window reached {seat}")
        return session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]

    def cast(self, session, card, *, targets=()):
        actions = self.priority(session, card.controller)
        action_id = f"cast:{card.ref}"
        self.assertTrue(any(a["id"] == action_id for a in actions), actions)
        result = session.act(f"pilot:{card.controller}", {"action_id": action_id, "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def resolve(self, session):
        for _ in range(40):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            if session.state.pending_decision and session.state.pending_decision.role == "arbiter":
                top = session.state.stack[-1]
                program = session.engine.semantics.get(top.semantic_key) if top.semantic_key else None
                self.fail(str({
                    "untrusted_stack_key": top.semantic_key,
                    "program_trust": program.trust_level if program else None,
                    "current_trusted": session.engine.semantic_program_is_current_trusted(program),
                    "capabilities": program.capability_dependencies if program else None,
                }))
            pass_current(session)
        self.fail("The offered action did not finish resolving")

    @staticmethod
    def checkpoint(session):
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

    def assert_replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            result = replay_record(directory, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(expected, result["final_state_hash"])

    def test_original_grant_carriers_are_capability_bound(self):
        registry = load_default_capability_registry()
        for name in ("Squirrel Nest", "Orochi Merge-Keeper", "Flaring Flame-Kin", "Relic Vial", "Jaheira, Friend of the Forest", "Underworld Connections", "Hunting Grounds"):
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)
                if name in {"Orochi Merge-Keeper", "Flaring Flame-Kin", "Relic Vial", "Hunting Grounds"}:
                    self.assertTrue(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes))

    def test_conditional_grant_schema_and_dependencies_fail_closed(self):
        registry = load_default_capability_registry()
        record = self.db.lookup("Flaring Flame-Kin")
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        descriptor = next(h for f in ir.faces for n in f.nodes for h in n.handlers if h.get("schema_version") == 2)
        handler = FixedPublicStateGrantedAbilityHandler()
        handler.validate(descriptor)
        with self.assertRaises(SemanticNodeError):
            FixedPublicStateCharacteristicsHandler().validate(descriptor)
        old_header = copy.deepcopy(descriptor)
        old_header.update(handler_id="continuous.characteristics.fixed-public-state.v1", schema_version=1)
        with self.assertRaises(SemanticNodeError):
            FixedPublicStateCharacteristicsHandler().validate(old_header)
        for field, value in (("schema_version", True), ("event", "arbitrary"), ("unknown", True)):
            bad = copy.deepcopy(descriptor)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(SemanticNodeError):
                handler.validate(bad)
        for fragments in ([], [descriptor["modifier"]["add_ability_fragments"][0]] * 2, [dict(kind="arbitrary")]):
            bad = copy.deepcopy(descriptor)
            bad["modifier"]["add_ability_fragments"] = fragments
            with self.assertRaises(SemanticNodeError):
                handler.validate(bad)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        row = next(r for r in raw["capabilities"] if r["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Deliberately unavailable owner"])
        blocked = compile_oracle_card(record, capability_registry=CapabilityRegistry(raw), capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        for text in (
            'As long as this creature is enchanted, it has "{X}: Draw X cards."',
            'As long as a creature has flying, this creature has "{T}: Draw a card."',
            'As long as this creature is enchanted, it has "{T}: Draw a card." and "{T}: You gain 1 life."',
        ):
            rejected = compile_oracle_card(replace(record, oracle_text=text), capability_registry=registry, capability_profile="commander_review")
            self.assertNotEqual("exact", rejected.status, text)
        rune = compile_oracle_card(self.db.lookup("Rune of Might"), capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", rune.status)

    def test_printed_land_grant_uses_recipient_and_replays(self):
        session = self.session(61130001)
        land = self.card(session, "Forest", "battlefield")
        aura = self.card(session, "Squirrel Nest")
        destroy = self.card(session, "Disenchant", seat="B")
        self.priority(session, "A")
        self.checkpoint(session)
        self.cast(session, aura, targets=(land.ref,))
        self.resolve(session)
        self.assertEqual(land.object_id, aura.attached_to)
        ability = next(a for a in session.engine._activated_abilities(land) if a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key)
        actions = self.priority(session, "A")
        action_id = f"activate:{land.ref}:{ability.ability_id}"
        self.assertTrue(any(a["id"] == action_id for a in actions))
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:B", {"action_id": action_id})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        activated = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(activated.ok, activated.summary)
        self.assertTrue(land.tapped)
        self.assertFalse(aura.tapped)
        self.cast(session, destroy, targets=(aura.ref,))
        self.resolve(session)
        self.assertEqual("graveyard", aura.zone)
        self.assertFalse(any(a.builtin_semantic_key == ability.builtin_semantic_key for a in session.engine._activated_abilities(land)))
        squirrels = [c for c in session.state.cards.values() if c.zone == "battlefield" and c.printed_name == "Squirrel"]
        self.assertEqual(1, len(squirrels))
        self.assertEqual("A", squirrels[0].controller)
        private = session.state.players["A"].zones["hand"]
        projected = json.dumps(session.packet("pilot:B", full=True))
        for object_id in private:
            self.assertNotIn(object_id, projected)
        self.assert_replay(session)

    def test_conditional_grant_compiler_mutation_is_killed(self):
        record = self.db.lookup("Flaring Flame-Kin")
        registry = load_default_capability_registry()
        with mock.patch(
            "quorune.compiler.attached_granted_ability_nodes.conditional_quoted_ability_text",
            return_value=None,
        ):
            result = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", result.status)

    def test_printed_conditional_activation_ends_without_erasing_stack(self):
        session = self.session(61130002)
        source = self.card(session, "Flaring Flame-Kin", "battlefield")
        aura = self.card(session, "Holy Strength")
        destroy = self.card(session, "Disenchant", seat="B")
        self.assertFalse(any(a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key for a in session.engine._activated_abilities(source)))
        self.priority(session, "A")
        self.checkpoint(session)
        self.cast(session, aura, targets=(source.ref,))
        self.resolve(session)
        data = session.engine._effective_card_data(source)
        self.assertEqual((5, 6), (int(data["power"]), int(data["toughness"])))
        self.assertIn("Trample", data["keywords"])
        ability = next(a for a in session.engine._activated_abilities(source) if a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertTrue(any(a["id"] == action_id for a in self.priority(session, "A")))
        result = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, destroy, targets=(aura.ref,))
        self.resolve(session)
        self.assertFalse(any(a.builtin_semantic_key == ability.builtin_semantic_key for a in session.engine._activated_abilities(source)))
        data = session.engine._effective_card_data(source)
        self.assertEqual((3, 2), (int(data["power"]), int(data["toughness"])))
        self.assertNotIn("Trample", data["keywords"])
        self.assert_replay(session)

    def test_printed_conditional_granted_death_trigger_uses_event_time_state(self):
        session = self.session(61130003)
        vial = self.card(session, "Relic Vial", "battlefield")
        cleric = self.card(session, "Soul Warden", "battlefield")
        other = self.card(session, "Runeclaw Bear", "battlefield")
        murder = self.card(session, "Murder", seat="B")
        second_murder = self.card(session, "Murder", seat="B")
        life = {seat: session.state.players[seat].life for seat in session.engine.seats}
        self.priority(session, "B")
        self.checkpoint(session)
        self.cast(session, murder, targets=(cleric.ref,))
        self.resolve(session)
        self.assertEqual("graveyard", cleric.zone)
        self.assertEqual(life["A"] + 1, session.state.players["A"].life)
        for seat in ("B", "C", "D"):
            self.assertEqual(life[seat] - 1, session.state.players[seat].life)
        self.assertFalse(any(f.get("kind") == "granted_triggered_ability" for f in session.engine._effective_card_data(vial)["ability_fragments"]))
        after_last_cleric = {seat: session.state.players[seat].life for seat in session.engine.seats}
        self.cast(session, second_murder, targets=(other.ref,))
        self.resolve(session)
        self.assertEqual("graveyard", other.zone)
        self.assertEqual(after_last_cleric, {seat: session.state.players[seat].life for seat in session.engine.seats})
        self.assert_replay(session)

    def test_printed_conditional_granted_hand_choice_is_private_and_persists(self):
        session = self.session(61130004)
        self.card(session, "Hunting Grounds", "battlefield")
        victim = self.card(session, "Runeclaw Bear", "battlefield")
        candidate = self.card(session, "Runeclaw Bear")
        spell = self.card(session, "Murder", seat="B")
        for _ in range(7):
            self.card(session, "Forest", "graveyard")
        self.priority(session, "B")
        self.checkpoint(session)
        self.cast(session, spell, targets=(victim.ref,))
        for _ in range(28):
            pending = session.state.pending_decision
            if pending is not None and pending.kind == "semantic.choice":
                break
            pass_current(session)
        else:
            self.fail("Printed Hunting Grounds did not offer its hand choice")
        decision = session.packet("pilot:A", full=True)["decision"]
        self.assertIn(candidate.ref, json.dumps(decision))
        for seat in ("B", "C", "D"):
            self.assertNotIn(candidate.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:B", {"action_id": "choose", "card": candidate.ref})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        accepted = session.act("pilot:A", {"action_id": "choose", "card": candidate.ref})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        moved = session.state.cards[candidate.object_id]
        self.assertEqual("battlefield", moved.zone)
        self.assertEqual("A", moved.controller)
        self.assertFalse(moved.tapped)
        self.assert_replay(session)

    def test_printed_land_grant_control_uses_recipient_cost_and_replays(self):
        session = self.session(61130005)
        land = self.card(session, "Forest", "battlefield")
        aura = self.card(session, "Chamber of Manipulation")
        fodder = self.card(session, "Forest")
        target = self.card(session, "Runeclaw Bear", "battlefield", seat="B")
        self.priority(session, "A")
        self.checkpoint(session)
        self.cast(session, aura, targets=(land.ref,))
        self.resolve(session)
        ability = next(a for a in session.engine._activated_abilities(land) if a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key)
        action_id = f"activate:{land.ref}:{ability.ability_id}"
        actions = self.priority(session, "A")
        action = next(a for a in actions if a["id"] == action_id)
        self.assertIn(fodder.ref, action["cost_summary"]["choose_cost"][0]["legal_refs"])
        activated = session.act("pilot:A", {"action_id": action_id, "cost_cards": [fodder.ref], "targets": [target.ref]})
        self.assertTrue(activated.ok, activated.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertFalse(session.state.cards[aura.object_id].tapped)
        self.assertEqual("graveyard", session.state.cards[fodder.object_id].zone)
        self.resolve(session)
        changed = session.state.cards[target.object_id]
        self.assertEqual("B", changed.owner)
        self.assertEqual("A", changed.controller)
        self.assert_replay(session)
