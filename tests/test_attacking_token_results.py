from __future__ import annotations

import unittest

from quorune.oracle_ir import _reviewed_effect_template
from dataclasses import replace
from types import SimpleNamespace
from quorune.replacement.immutable import FrozenMap
from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
from quorune.semantic_choices.fixed_attacking_tokens import FixedAttackingTokenDestinationHandler
from quorune.semantic_choices.model import SemanticChoiceError


class AttackingTokenResultCompilerTests(unittest.TestCase):
    def test_fixed_attacking_results_keep_printed_definition_and_entry_state(self):
        cases = (
            ("Create two 1/1 white Soldier creature tokens that are tapped and attacking.", 2, True),
            ("Create a 1/1 white Human creature token that's tapped and attacking.", 1, True),
            ("Create two 4/4 white Angel creature tokens with flying and vigilance that are attacking.", 2, False),
        )
        for text, quantity, tapped in cases:
            with self.subTest(text=text):
                template, effects, target, mechanics = _reviewed_effect_template(text, card_name='Entry Result Fixture')
                self.assertIsNotNone(template)
                self.assertIsNone(target)
                self.assertEqual(len(effects), 1)
                self.assertEqual(effects[0]['op'], 'choose_fixed_attacking_token_destinations')
                self.assertEqual(effects[0]['quantity'], quantity)
                self.assertIs(effects[0]['tapped'], tapped)
                self.assertEqual(effects[0]['token_entry_version'], 1)
                self.assertIn('fixed-attacking-token-result', mechanics)

    def test_new_result_does_not_consume_specified_recipients_copies_or_delayed_riders(self):
        _,effects,_,_=_reviewed_effect_template(
            "Create a 1/1 white Human creature token that's tapped and attacking. Sacrifice it at end of combat.",
            card_name='Entry Result Fixture')
        effect,=effects
        self.assertEqual('choose_fixed_attacking_token_destinations',effect['op'])
        self.assertEqual({'schema_version':1,'kind':'created_token_cleanup','action':'sacrifice','timing':'end_of_combat'},effect['token_cleanup'])
        for text in (
            "Create a 1/1 white Human creature token that's tapped and attacking that player.",
            "Create a token that's a copy of this creature that's tapped and attacking.",
            "Create a Treasure token that's tapped and attacking.",
            "Create a 1/1 white Human creature token that's tapped and attacking. Sacrifice it at end of combat if it dealt damage to a player this turn.",
        ):
            with self.subTest(text=text):
                _, effects, _, _ = _reviewed_effect_template(text, card_name='Entry Result Fixture')
                self.assertFalse(any(e.get('op') == 'choose_fixed_attacking_token_destinations' for e in effects))

    def test_withdrawn_attacking_owner_does_not_withdraw_ordinary_fixed_tokens(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id'] == 'token.creation.fixed_attacking_definition').update(
            status='blocked', blockers=['Attacking entry owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for text, exact in (("Whenever this creature attacks, create a 1/1 white Soldier creature token that's tapped and attacking.", False),
                ("Whenever this creature attacks, create a 1/1 white Soldier creature token.", True)):
            with self.subTest(text=text):
                record = replace(query_record(text), type_line='Creature — Soldier')
                self.assertEqual(exact, compile_oracle_card(record, capability_registry=registry,
                    capability_profile='commander_review').status == 'exact')


class AttackingTokenDestinationModelTests(unittest.TestCase):
    def battle_cry_host(self, version=1):
        from quorune.ability_fragments import CombatKeywordTriggerKind
        from quorune.attack_transition_model import AttackObjectIdentity, AttackKeywordTriggerOccurrence
        source = AttackObjectIdentity('source', 'source@0', 'Source')
        declared = AttackObjectIdentity('declared', 'declared@0', 'Declared')
        occurrence = AttackKeywordTriggerOccurrence.create(transition_id='test-transition',
            kind=CombatKeywordTriggerKind.BATTLE_CRY, controller='A', source=source,
            affected=(declared,), amount=1, instance_index=0)
        cards = {oid: SimpleNamespace(object_id=oid, logical_object_id=oid+'@0',
            ref=oid.title(), zone='battlefield') for oid in ('source', 'declared', 'entered')}
        state = SimpleNamespace(cards=cards, combat=SimpleNamespace(attackers={
            'source': 'B', 'declared': 'B', 'entered': 'C'}), battle_cry_resolution_version=version)
        return SimpleNamespace(state=state, _log=lambda *args, **kwargs: None), occurrence

    def test_battle_cry_resolution_uses_current_attacking_set(self):
        from unittest.mock import patch
        from quorune import attack_transition_resolution as owner
        host, occurrence = self.battle_cry_host()
        with patch.object(owner, 'create_resolution_continuous_effect', return_value=object()) as create:
            self.assertEqual({'declared', 'entered'}, set(owner.resolve_attack_keyword_trigger(host, occurrence, stack_ref='S1')))
        self.assertEqual({'declared', 'entered'}, {c.object_id for c in create.call_args.kwargs['targets']})

    def test_battle_cry_marker_codec_preserves_legacy_absence_and_rejects_mismatch(self):
        from quorune.model import GameState, GameConfig
        from quorune.battle_cry_resolution_model import battle_cry_resolution_version
        from quorune.record_state_provenance import format_state_versions, validate_state_versions
        state = GameState(game_id='battle-cry-codec', config=GameConfig(), players={}, cards={},
            deck_names={}, commander_oracle_ids={}, turn_order=[], current_turn=0, last_normal_turn_player=None)
        self.assertEqual(1, state.to_dict()['battle_cry_resolution_version'])
        legacy = state.to_dict(); legacy.pop('battle_cry_resolution_version')
        restored = GameState.from_dict(legacy)
        self.assertIsNone(restored.battle_cry_resolution_version)
        self.assertNotIn('battle_cry_resolution_version', restored.to_dict())
        validate_state_versions({'format': format_state_versions(state)}, state)
        validate_state_versions({'format': format_state_versions(restored)}, restored)
        for value in (True, 0, 2, '1'):
            with self.assertRaises(ValueError): battle_cry_resolution_version(value)
            with self.assertRaises(ValueError): validate_state_versions(
                {'format': {**format_state_versions(state), 'battle_cry_resolution_version': value}}, state)

    def test_battle_cry_legacy_boundary_and_current_source_incarnations_are_distinct(self):
        from unittest.mock import patch
        from quorune import attack_transition_resolution as owner
        from quorune.attack_transition_model import AttackTransitionError
        host, occurrence = self.battle_cry_host(None)
        with self.assertRaises(AttackTransitionError):
            owner.resolve_attack_keyword_trigger(host, occurrence, stack_ref='S1')
        host.state.combat.attackers.pop('entered')
        with patch.object(owner, 'create_resolution_continuous_effect', return_value=object()):
            self.assertEqual(('declared',), owner.resolve_attack_keyword_trigger(host, occurrence, stack_ref='S1'))
            host.state.battle_cry_resolution_version = 1
            host.state.cards['source'].logical_object_id = 'source@1'
            host.state.combat.attackers.pop('declared')
            self.assertEqual(('source',), owner.resolve_attack_keyword_trigger(host, occurrence, stack_ref='S1'))

    def test_reusing_only_declared_battle_cry_recipients_is_killed(self):
        from unittest.mock import patch
        from quorune import battle_cry_resolution as owner
        baseline = unittest.TestResult()
        case = type(self)('test_battle_cry_resolution_uses_current_attacking_set'); case.run(baseline)
        self.assertTrue(baseline.wasSuccessful())
        with patch.object(owner, '_current_battle_cry_identities', lambda state, occurrence: occurrence.affected):
            mutant = unittest.TestResult(); case.run(mutant)
        self.assertFalse(mutant.wasSuccessful()); self.assertFalse(mutant.errors)

    def setup_choice(self, quantity=2, *, tapped=True, allowed=True, multiplier=1):
        text = f"Create {quantity} 1/1 white Soldier creature tokens that are {'tapped and ' if tapped else ''}attacking."
        effect = dict(_reviewed_effect_template(text, card_name='Entry Result Fixture')[1][0])
        effect['player'] = 'A'
        query = SnapshotSemanticChoiceQuery(seat_order=tuple('ABCD'), active_order=tuple('ABCD'),
            attacking_token_entry_allowed=allowed, materialized_token_creation_preview=(FrozenMap({
                'quantity': quantity*multiplier, '_attacking_group': 'any'}),))
        context = SemanticChoiceContext(actor='A', stack_ref='S1', stack_controller='A',
            stack_label='Original printed token result', source_ref='source', card_ref=None,
            semantic_program_id='test', semantic_program_version=1, query=query)
        handler = FixedAttackingTokenDestinationHandler()
        prepared = handler.prepare(effect, context)
        return handler, effect, query, context, prepared

    def test_each_replacement_expanded_token_has_independent_destinations_without_unprinted_sacrifice(self):
        for quantity in range(1, 5):
            for tapped in (False, True):
                with self.subTest(quantity=quantity, tapped=tapped):
                    handler, _, query, _, prepared = self.setup_choice(quantity, tapped=tapped, multiplier=2)
                    effect = prepared.continuation_effect
                    refs = tuple(effect['_token_ids'])
                    self.assertEqual(quantity*2, len(refs))
                    destinations = {ref: 'BCD'[index % 3] for index, ref in enumerate(refs)}
                    result = handler.complete(SimpleNamespace(effect=effect), {'attacking': destinations}, query)
                    intent = result.intents[0]
                    self.assertEqual(tuple(destinations.values()), intent.attacking_assignments)
                    self.assertEqual(quantity, intent.quantity)
                    self.assertIs(intent.tapped, tapped)
                    self.assertFalse(intent.sacrifice_at_end_step)
                    self.assertFalse(intent.exile_at_end_of_combat)

    def test_malformed_definitions_and_stale_or_incomplete_assignments_are_rejected(self):
        handler, effect, query, context, prepared = self.setup_choice()
        for changed in ({'quantity': True}, {'quantity': 0}, {'token_entry_version': True},
                {'token_entry_version': 2}, {'tapped': 1}, {'unknown': 1}, {'player': 'D'},
                {'name': 'Unknown token'}, {'characteristics': {'type_line': 'Artifact — Treasure'}}):
            with self.subTest(changed=changed), self.assertRaises(SemanticChoiceError):
                handler.prepare({**effect, **changed}, context)
        continuation = SimpleNamespace(effect=prepared.continuation_effect)
        for assignments in ({}, {'attacking:0': 'B'}, {'attacking:0': 'A', 'attacking:1': 'B'},
                {'attacking:0': 'B', 'attacking:1': 'C', 'attacking:2': 'D'}):
            with self.subTest(assignments=assignments), self.assertRaises(SemanticChoiceError):
                handler.complete(continuation, {'attacking': assignments}, query)
        valid = {'attacking': {'attacking:0': 'B', 'attacking:1': 'C'}}
        for stale in (replace(query, active_order=('A', 'B', 'C')),
                replace(query, attacking_token_entry_allowed=False),
                replace(query, materialized_token_creation_preview=())):
            with self.assertRaises(SemanticChoiceError):
                handler.complete(continuation, valid, stale)

    def test_nonattacking_controller_creates_printed_tokens_without_combat_assignments(self):
        handler, _, query, _, prepared = self.setup_choice(2, allowed=False)
        self.assertIsNone(prepared.request)
        result = handler.complete(SimpleNamespace(effect=prepared.continuation_effect), {}, query)
        self.assertEqual(2, result.intents[0].quantity)
        self.assertTrue(result.intents[0].tapped)
        self.assertEqual((), result.intents[0].attacking_assignments)

    def test_inheriting_mobilize_sacrifice_is_killed(self):
        from unittest.mock import patch
        from quorune.semantic_choices import fixed_attacking_tokens as owner
        original = owner.complete_attacking_destinations
        def mutate(*args, **kwargs):
            return original(*args, **{**kwargs, 'sacrifice': True})
        baseline = unittest.TestResult()
        case = type(self)('test_each_replacement_expanded_token_has_independent_destinations_without_unprinted_sacrifice')
        case.run(baseline)
        self.assertTrue(baseline.wasSuccessful())
        with patch.object(owner, 'complete_attacking_destinations', mutate):
            mutant = unittest.TestResult(); case.run(mutant)
        self.assertFalse(mutant.wasSuccessful()); self.assertFalse(mutant.errors)


import json
import tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AttackingTokenResultNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'attacking-token-results.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/attacking-token-result-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Attacking token results', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Isamaru, Hound of Konda'])

    def trim_opening_hand(self, s):
        for oid in list(s.state.players['A'].zones['hand']):
            s.engine.move_card(oid, 'library', log=False)

    def choose_destinations(self, s, destinations):
        self.until(s, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'semantic.choice')
        refs = s.state.pending_decision.payload_by_actor['A']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual(len(destinations), len(refs))
        command = {'action_id': 'choose', 'attacking': dict(zip(refs, destinations))}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        incomplete = {'action_id': 'choose', 'attacking': {refs[0]: destinations[0]}}
        self.assertFalse(s.act('pilot:A', incomplete).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertTrue(s.act('pilot:A', command).ok)

    def test_actual_hero_entry_before_battle_cry_uses_live_attackers_without_attack_events(self):
        s = self.session(508400301); self.trim_opening_hand(s)
        hero = self.add(s.engine, 'Hero of Bladehold')
        observer = self.add(s.engine, 'Beastmaster Ascension')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        self.assert_closed(s, hero); self.assert_closed(s, observer)
        self.prepare(s); self.checkpoint(s)
        self.until(s, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'combat.attackers')
        self.assertTrue(s.act('pilot:A', {'action_id': 'attack', 'attackers': {hero.ref: 'B'}}).ok)
        self.assertEqual('trigger.order', s.state.pending_decision.kind)
        items = [item for batch in s.state.pending_trigger_batches for item in batch.items]
        token_items = [item for item in items if item.get('source_object_id') == hero.object_id
            and item.get('context', {}).get('event') == 'creature.attacks']
        self.assertEqual(1, len(token_items))
        order = [item.ref for item in items if item is not token_items[0]] + [token_items[0].ref]
        self.assertTrue(s.act('pilot:A', {'action_id': 'order', 'triggers': order}).ok)
        self.until(s, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'semantic.choice')
        self.assertFalse(any(c.zone == 'battlefield' and c.printed_name == 'Soldier' for c in s.state.cards.values()))
        self.choose_destinations(s, ('B', 'C'))
        decision = s.state.pending_decision
        self.assertNotEqual('semantic.choice', decision.kind, repr(decision.payload_by_actor.get('A'))[:1400])
        optional = self.resolve(s)
        self.assertEqual('semantic.choice', optional.kind)
        self.assertIn('put', optional.payload_by_actor['A']['legal_actions'][0]['choice_schema']['legal_values'])
        result = s.act('pilot:A', {'action_id': 'choose', 'choice': 'put'})
        self.assertTrue(result.ok, result.summary)
        self.settle(s)
        soldiers = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.printed_name == 'Soldier']
        self.assertEqual(2, len(soldiers)); self.assertTrue(all(c.tapped for c in soldiers))
        self.assertEqual({'B', 'C'}, {c.attacking for c in soldiers})
        self.assertTrue(all(str(s.engine._effective_card_data(c)['power']) == '2' for c in soldiers))
        self.assertEqual(1, s.state.cards[observer.object_id].counters['quest'])
        self.assertFalse(s.state.delayed_triggers)
        for seat in 'ABD': self.assertNotIn(private.ref, json.dumps(s.packet('pilot:'+seat, full=True)))
        self.until(s, lambda game: game.state.phase == 'postcombat_main')
        self.assertEqual([35, 38, 40], [s.state.players[seat].life for seat in 'BCD'])
        self.replay(s, load=True)

    def test_actual_parhelion_crew_creates_untapped_attacking_angels_that_survive_end_step(self):
        s = self.session(508400302); self.trim_opening_hand(s)
        vehicle = self.add(s.engine, 'Parhelion II'); crew = self.add(s.engine, 'Loxodon Smiter')
        self.assert_closed(s, vehicle); self.prepare(s); self.checkpoint(s)
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(r for r in actions if vehicle.ref in r['id'] and 'crew' in repr(r).casefold())
        self.assertTrue(s.act('pilot:A', {'action_id': action['id'], 'cost_cards': [crew.ref]}).ok)
        self.assertTrue(crew.tapped); self.resolve_top(s)
        self.attack(s, [(vehicle, 'B')])
        self.until(s, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'semantic.choice')
        s = self.replay(s, load=True); self.choose_destinations(s, ('C', 'D')); self.settle(s)
        angels = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.printed_name == 'Angel']
        self.assertEqual(2, len(angels)); self.assertTrue(all(not c.tapped for c in angels))
        self.assertEqual({'C', 'D'}, {c.attacking for c in angels})
        self.assertFalse(s.state.delayed_triggers)
        self.until(s, lambda game: game.state.phase == 'postcombat_main')
        self.assertEqual([35, 36, 36], [s.state.players[seat].life for seat in 'BCD'])
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.step == 'upkeep')
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in angels))
        self.replay(s, load=True)


if __name__ == '__main__':
    unittest.main()
