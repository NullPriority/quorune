from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectDuration, ContinuousObjectIdentity,
    ContinuousOperation, ContinuousEffectError, Layer,
)
from quorune.continuous_effects import CharacteristicState, evaluate_continuous_effects
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
    commit_continuous_effect,
)
from quorune.engine import TURN_STEPS
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.keyword_prohibitions import OpponentKeywordProhibitionHandler
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree


TEXT = "Creatures your opponents control lose flying and can't have or gain flying."
CAP = 'continuous.ability.opponent_keyword_prohibition'


class OpponentKeywordProhibitionCompilerTests(unittest.TestCase):
    def compile(self, text=TEXT):
        return compile_oracle_card(record(text), capability_registry=load_default_capability_registry(),
            capability_profile='commander_review')

    def test_exact_source_span_keyword_owner_and_nearest_unsupported_forms(self):
        for keyword in ('flying', 'first strike', 'hexproof', 'deathtouch', 'trample'):
            text = TEXT.replace('flying', keyword)
            ir = self.compile(text)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text, node.text)
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))
            self.assertIn(CAP, node.capability_dependencies)
        for text in (TEXT.replace('gain flying', 'gain reach'), TEXT.replace('flying', 'ward'),
                     TEXT.replace('Creatures', 'Cards'), TEXT+' Draw a card.'):
            self.assertNotEqual('exact', self.compile(text).status)
        with patch('quorune.compiler.keyword_prohibition_templates.opponent_keyword_prohibition_handler', return_value=None):
            self.assertNotEqual('exact', self.compile().status)

    def test_codec_preserves_old_effects_and_closes_malformed_exclusions(self):
        identity = ContinuousObjectIdentity('body', 'body@0')
        legacy = ContinuousEffect(effect_id='old', source_id='source', layer=Layer.ABILITY, sublayer='6', timestamp=1,
            operations=(ContinuousOperation('add_ability', 'Flying'),), origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, locked_objects=(identity,))
        old = legacy.to_dict()
        self.assertNotIn('denied_keyword_grants', old)
        self.assertEqual(old, ContinuousEffect.from_dict(old).to_dict())
        denied = replace(legacy, denied_keyword_grants=(('body', 'body@0', 'flying'),))
        self.assertEqual(denied.to_dict(), ContinuousEffect.from_dict(denied.to_dict()).to_dict())
        for value in ('flying', [('body', 'body@0')], [('other', 'other@0', 'flying')],
                      [('body', 'body@0', 'Flying')], [('body', 'body@0', 'reach')]):
            with self.subTest(value=value), self.assertRaises(ContinuousEffectError):
                replace(legacy, denied_keyword_grants=value)
        descriptor = {'handler_id': 'continuous.ability.opponent-keyword-prohibition.v1',
            'schema_version': 1, 'event': 'characteristics.evaluate', 'keyword': 'flying'}
        for changed in ({'schema_version': True}, {'keyword': 'ward'}, {'other': True}):
            with self.assertRaises(SemanticNodeError):
                OpponentKeywordProhibitionHandler().validate({**descriptor, **changed})

    def test_prohibition_differs_from_ordinary_removal_and_survives_later_grants(self):
        context = {'object_id': 'body', 'logical_object_id': 'body@0', 'controller': 'B', 'owner': 'B', 'zone': 'battlefield'}
        def effect(name, timestamp, operation):
            return ContinuousEffect(effect_id=name, source_id=name, layer=Layer.ABILITY, sublayer='6',
                timestamp=timestamp, operations=(operation,))
        for before, after in ((1, 2), (2, 1)):
            state = CharacteristicState('Body', controller='B', card_types={'creature'}, abilities=['Flying', 'Vigilance'])
            evaluated = evaluate_continuous_effects(state, [effect('deny', before, ContinuousOperation('prohibit_ability', 'flying')),
                effect('grant', after, ContinuousOperation('add_ability', 'Flying'))], context=context)
            self.assertNotIn('Flying', evaluated.characteristics['abilities'])
            self.assertIn('Vigilance', evaluated.characteristics['abilities'])
        state = CharacteristicState('Body', controller='B', card_types={'creature'}, abilities=['Flying'])
        evaluated = evaluate_continuous_effects(state, [effect('lose', 1, ContinuousOperation('remove_ability', 'Flying')),
            effect('grant', 2, ContinuousOperation('add_ability', 'Flying'))], context=context)
        self.assertIn('Flying', evaluated.characteristics['abilities'])

    def test_reused_characteristic_state_does_not_retain_an_expired_prohibition(self):
        state = CharacteristicState('Body', controller='B', card_types={'creature'})
        context = {'object_id':'body','logical_object_id':'body@0','controller':'B','owner':'B','zone':'battlefield'}
        deny = ContinuousEffect(effect_id='deny', source_id='deny', layer=Layer.ABILITY, sublayer='6', timestamp=1,
            operations=(ContinuousOperation('prohibit_ability','flying'),))
        grant = ContinuousEffect(effect_id='grant', source_id='grant', layer=Layer.ABILITY, sublayer='6', timestamp=2,
            operations=(ContinuousOperation('add_ability','Flying'),))
        self.assertNotIn('Flying', evaluate_continuous_effects(state,(deny,grant),context=context).characteristics['abilities'])
        self.assertIn('Flying', evaluate_continuous_effects(state,(grant,),context=context).characteristics['abilities'])

    def test_keyword_qualified_effect_depends_on_prohibition_before_its_scope_is_evaluated(self):
        from quorune.object_predicate import ObjectQuerySpec
        state = CharacteristicState('Body', controller='B', card_types={'creature'}, abilities=['Flying'])
        deny = ContinuousEffect(effect_id='late-deny', source_id='deny', layer=Layer.ABILITY, sublayer='6', timestamp=2,
            operations=(ContinuousOperation('prohibit_ability','flying'),))
        qualified = ContinuousEffect(effect_id='early-qualified', source_id='qualified', layer=Layer.ABILITY, sublayer='6',
            timestamp=1, operations=(ContinuousOperation('add_ability','Vigilance'),),
            applies=ObjectQuerySpec(keywords_all=('flying',)))
        result = evaluate_continuous_effects(state,(qualified,deny),
            context={'object_id':'body','logical_object_id':'body@0','controller':'B','owner':'B','zone':'battlefield'})
        self.assertNotIn('Vigilance', result.characteristics['abilities'])

    def test_historical_annotation_grant_cannot_bypass_current_prohibition(self):
        from types import SimpleNamespace
        from quorune.effect_runtime.objects_stack_and_tokens import _apply_grant_keyword_until_end_of_turn
        card = SimpleNamespace(ref='body', temporary_keywords=[])
        host = SimpleNamespace(state=SimpleNamespace(continuous_effects=None),
            _resolve_object=lambda *args,**kwargs:card,
            _effective_card_data=lambda *_args:{'prohibited_keywords':['flying']})
        result = _apply_grant_keyword_until_end_of_turn(host,{'card':'body','keyword':'Flying'},actor='A',
            operation='grant_keyword_until_end_of_turn',reason='legacy denial boundary')
        self.assertEqual('body', result)
        self.assertEqual([], card.temporary_keywords)


class OpponentKeywordProhibitionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'opponent-keyword-prohibition.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/token-entry-boundary-cards.json',
            ROOT/'tests/fixtures/opponent-keyword-prohibition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Keyword denial', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast(self, session, spell, targets=()):
        self.reject(session, 'cast:'+spell.ref, 'D', targets=list(targets), pay='auto')
        result = session.act('pilot:A', {'action_id': 'cast:'+spell.ref, 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

    def test_actual_denial_suppresses_counters_and_static_grants_then_static_effects_resume(self):
        session = self.session(11311001)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='denial-source')
        grant = self.add(session.engine, 'Archetype of Imagination', seat='B', ref='opposing-grant')
        one = self.add(session.engine, 'Generic Bound Body', seat='B', ref='counter-recipient')
        two = self.add(session.engine, 'Generic Bound Body', seat='C', ref='other-opponent')
        one.counters['flying'] = 1
        for card in (deny, grant): self.assert_closed(session, card)
        self.assertNotIn('flying', session.engine._combat_keywords(one))
        self.assertNotIn('flying', session.engine._combat_keywords(two))
        self.assertNotIn('flying', session.engine._combat_keywords(deny))
        session.engine.move_card(deny.object_id, 'graveyard', log=False)
        self.assertIn('flying', session.engine._combat_keywords(one))
        self.assertEqual({'flying': 1}, one.counters)

    def test_actual_denied_resolving_grant_does_not_appear_after_denial_source_leaves(self):
        session = self.session(11311002)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='resolution-denial-source')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='resolution-grant-recipient')
        jump = self.add(session.engine, 'Jump', zone='hand', ref='denied-jump')
        removal = self.add(session.engine, 'Doom Blade', zone='hand', ref='remove-denial')
        for card in (deny, jump, removal): self.assert_closed(session, card)
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana={'U': 1, 'B': 2})
        self.checkpoint(session)
        self.cast(session, jump, (body.ref,))
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.cast(session, removal, (deny.ref,))
        self.assertEqual('graveyard', session.state.cards[deny.object_id].zone)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.replay(session, load=True)

    def test_one_resolving_set_preserves_other_keywords_and_modifiers_for_each_recipient(self):
        session = self.session(11311003)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='set-denial')
        allowed = self.add(session.engine, 'Generic Bound Body', ref='allowed-set-recipient')
        denied = self.add(session.engine, 'Generic Bound Body', seat='B', ref='denied-set-recipient')
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-set-grant'), targets=(allowed, denied), components=(
                ResolutionContinuousComponent(Layer.ABILITY, '6', (
                    ContinuousOperation('add_ability', 'Flying'), ContinuousOperation('add_ability', 'Haste'))),
                ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, '7c', (
                    ContinuousOperation('modify_power_toughness', (2, 2)),))))
        self.assertIn('flying', session.engine._combat_keywords(allowed))
        self.assertNotIn('flying', session.engine._combat_keywords(denied))
        self.assertIn('haste', session.engine._combat_keywords(denied))
        self.assertEqual(4, session.engine._numeric_stat(denied.object_id, 'power'))
        session.engine.move_card(deny.object_id, 'graveyard', log=False)
        self.assertIn('flying', session.engine._combat_keywords(allowed))
        self.assertNotIn('flying', session.engine._combat_keywords(denied))
        self.assertIn('haste', session.engine._combat_keywords(denied))
        self.assertEqual(4, session.engine._numeric_stat(denied.object_id, 'power'))

    def test_preexisting_resolved_grant_resumes_when_a_later_prohibition_ends(self):
        session = self.session(11311004)
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='preexisting-grant-body')
        jump = self.add(session.engine, 'Jump', zone='hand', ref='preexisting-jump')
        self.prepare(session, mana={'U': 1})
        self.checkpoint(session)
        self.cast(session, jump, (body.ref,))
        self.assertIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        deny = self.add(session.engine, 'Archetype of Imagination', ref='later-prohibition')
        self.assert_closed(session, deny)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        session.engine.move_card(deny.object_id, 'graveyard', log=False)
        self.assertIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))

    def test_current_source_control_recomputes_opponents_and_ability_removal_ends_denial(self):
        session = self.session(11311005)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='control-denial-source')
        one = self.add(session.engine, 'Generic Bound Body', seat='A', ref='first-control-body')
        two = self.add(session.engine, 'Generic Bound Body', seat='B', ref='second-control-body')
        one.counters['flying'] = 1
        two.counters['flying'] = 1
        self.assertIn('flying', session.engine._combat_keywords(one))
        self.assertNotIn('flying', session.engine._combat_keywords(two))
        from quorune.control_effects import change_control
        change_control(session.engine, deny.object_id, 'B', reason='Keyword prohibition controller fixture')
        self.assertNotIn('flying', session.engine._combat_keywords(one))
        self.assertIn('flying', session.engine._combat_keywords(two))
        humility = self.add(session.engine, 'Humility', ref='remove-denial-abilities')
        self.assert_closed(session, humility)
        self.assertEqual((), session.engine._effective_static_component_keys(deny))
        self.assertNotIn('flying', session.engine._effective_card_data(one).get('prohibited_keywords', ()))

    def test_actual_hexproof_prohibition_changes_offered_and_accepted_target_legality(self):
        session = self.session(11311006)
        deny = self.add(session.engine, 'Archetype of Endurance', ref='hexproof-denier')
        target = self.add(session.engine, 'Generic Bound Body', seat='B', ref='hexproof-counter-target')
        target.counters['hexproof'] = 1
        spell = self.add(session.engine, 'Shock', zone='hand', ref='hexproof-denied-shock')
        self.assert_closed(session, deny)
        self.prepare(session, mana={'R': 1})
        self.checkpoint(session)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'] == 'cast:'+spell.ref)
        self.assertIn(target.ref, action['target_schema']['legal_refs'])
        self.cast(session, spell, (target.ref,))
        self.assertEqual(2, session.state.cards[target.object_id].marked_damage)
        self.replay(session, load=True)

    def test_actual_flying_prohibition_allows_a_grounded_blocker_and_replays(self):
        session = self.session(11311007)
        deny = self.add(session.engine, 'Archetype of Imagination', seat='B', ref='flying-denier')
        attacker = self.add(session.engine, 'Generic Bound Body', ref='flying-counter-attacker')
        blocker = self.add(session.engine, 'Generic Bound Body', seat='B', ref='ground-blocker')
        attacker.counters['flying'] = 1
        self.assert_closed(session, deny)
        self.assertNotIn('flying', session.engine._combat_keywords(attacker))
        # This blocker is grounded despite the source's own ordinary grant.
        commit_continuous_effect(session.state, ContinuousEffect(effect_id='fixed:ground-blocker',
            source_id='fixed:ground-blocker', layer=Layer.ABILITY, sublayer='6', timestamp=session.engine._next_zone_timestamp(),
            operations=(ContinuousOperation('remove_ability', 'Flying'),), origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(ContinuousObjectIdentity(blocker.object_id, blocker.logical_object_id),)))
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = 'A'
        engine.state.phase = 'combat'
        engine.state.step = 'declare_blockers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_blockers'))
        attacker.attacking = 'B'
        engine.state.combat = CombatState(attackers_declared=True,
            attackers={attacker.object_id:'B'}, defending_players=['B'])
        engine._begin_blocker_decisions()
        self.checkpoint(session)
        accepted = session.act('pilot:B', {'action_id':'block','blocks':{blocker.ref:attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(attacker.object_id, session.state.cards[blocker.object_id].blocking)
        self.replay(session, load=True)

    def _combat_keyword_denial(self, *, name, keyword, seed, power_bonus=0):
        session = self.session(seed)
        deny = self.add(session.engine, name, seat='B', ref='combat-denial-source')
        attacker = self.add(session.engine, 'Generic Bound Body', ref='combat-denied-attacker')
        blocker = self.add(session.engine, 'Generic Bound Body', seat='B', ref='combat-denied-blocker')
        attacker.counters[keyword] = 1
        if power_bonus:
            commit_continuous_effect(session.state, ContinuousEffect(effect_id='fixed:attacker-power',
                source_id='fixed:attacker-power', layer=Layer.POWER_TOUGHNESS, sublayer='7c',
                timestamp=session.engine._next_zone_timestamp(),
                operations=(ContinuousOperation('modify_power_toughness', (power_bonus, 0)),),
                origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(ContinuousObjectIdentity(attacker.object_id, attacker.logical_object_id),)))
        self.assert_closed(session, deny)
        self.assertNotIn(keyword, session.engine._combat_keywords(attacker))
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = 'A'
        engine.state.phase = 'combat'
        engine.state.step = 'declare_blockers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_blockers'))
        attacker.attacking = 'B'
        engine.state.combat = CombatState(attackers_declared=True,
            attackers={attacker.object_id:'B'}, defending_players=['B'])
        engine._begin_blocker_decisions()
        self.checkpoint(session)
        accepted = session.act('pilot:B', {'action_id':'block','blocks':{blocker.ref:attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        observations = []
        for _ in range(20):
            observations.append(session.state.cards[blocker.object_id].marked_damage)
            if session.state.cards[blocker.object_id].marked_damage or session.state.cards[blocker.object_id].zone != 'battlefield':
                break
            if session.state.pending_decision.kind == 'combat.damage':
                accepted = session.act('pilot:A', {'action_id':'assign_damage','assignments':[
                    {'source':attacker.ref,'target':blocker.ref,'amount':2+power_bonus}]})
            else:
                accepted = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        return session, attacker, blocker, observations

    def test_actual_deathtouch_prohibition_keeps_the_nonlethal_blocker_alive(self):
        session, attacker, blocker, _ = self._combat_keyword_denial(
            name='Archetype of Finality', keyword='deathtouch', seed=11311008)
        self.assertEqual('battlefield', session.state.cards[blocker.object_id].zone)
        self.assertEqual(2, session.state.cards[blocker.object_id].marked_damage)
        self.assertFalse(session.state.cards[blocker.object_id].deathtouch_damage)
        self.replay(session, load=True)

    def test_actual_first_strike_prohibition_removes_the_extra_damage_step(self):
        session, attacker, blocker, observations = self._combat_keyword_denial(
            name='Archetype of Courage', keyword='first strike', seed=11311009)
        # Only B's blocker has first strike; A's counter cannot add it.
        self.assertEqual(2, session.state.cards[attacker.object_id].marked_damage)
        self.assertEqual(2, session.state.cards[blocker.object_id].marked_damage)
        self.assertIn(0, observations)
        self.replay(session, load=True)

    def test_actual_trample_prohibition_keeps_all_blocked_damage_off_the_player(self):
        session, attacker, blocker, _ = self._combat_keyword_denial(
            name='Archetype of Aggression', keyword='trample', seed=11311010, power_bonus=6)
        self.assertEqual(40, session.state.players['B'].life)
        self.assertEqual('graveyard', session.state.cards[blocker.object_id].zone)
        self.replay(session, load=True)

    def test_omitting_prohibition_is_killed_by_current_native_keyword_consequences(self):
        with patch('quorune.semantic_runtime.keyword_prohibitions.OpponentKeywordProhibitionHandler.lower', return_value=()), \
                self.assertRaises(AssertionError):
            self.test_actual_denial_suppresses_counters_and_static_grants_then_static_effects_resume()

    def test_actual_denied_leap_keeps_its_private_draw_and_does_not_resume_flying(self):
        session = self.session(11311011)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='leap-denier')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='leap-target')
        leap = self.add(session.engine, 'Leap', zone='hand', ref='denied-leap')
        removal = self.add(session.engine, 'Doom Blade', zone='hand', ref='leap-remove-denier')
        hidden = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand', ref='leap-private-d')
        for card in (deny, leap, removal): self.assert_closed(session, card)
        self.prepare(session, mana={'U': 1, 'B': 2})
        self.checkpoint(session)
        hand = len(session.state.players['A'].zones['hand'])
        self.cast(session, leap, (body.ref,))
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.cast(session, removal, (deny.ref,))
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        for seat in 'ABC':
            self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.replay(session, load=True)

    def test_actual_copied_denial_component_keeps_working_after_the_original_leaves(self):
        session = self.session(11311012)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='copy-prohibition-original')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='copy-prohibition-recipient')
        body.counters['flying'] = 1
        copy = self.add(session.engine, 'Generic Entry One Copy', zone='hand', ref='copy-prohibition-spell')
        removal = self.add(session.engine, 'Doom Blade', zone='hand', ref='copy-remove-original')
        for card in (deny, copy, removal): self.assert_closed(session, card)
        self.prepare(session, mana={'W': 1, 'B': 2})
        self.checkpoint(session)
        self.cast(session, copy, (deny.ref,))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.assertEqual(1, len(tokens))
        self.cast(session, removal, (deny.ref,))
        self.assertEqual('graveyard', session.state.cards[deny.object_id].zone)
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.replay(session, load=True)

    def test_same_effect_type_changes_are_applied_before_keyword_grant_denial(self):
        session = self.session(11311013)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='animation-denier')
        land = self.add(session.engine, 'Generic Bound Plains', seat='B', ref='animated-denied-land')
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-animation'), targets=(land,), components=(
                ResolutionContinuousComponent(Layer.TYPE, '4', (
                    ContinuousOperation('add_types', ('creature',), field='card_types'),)),
                ResolutionContinuousComponent(Layer.ABILITY, '6', (ContinuousOperation('add_ability', 'Flying'),)),
                ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, '7b', (ContinuousOperation('set_power_toughness', (2, 2)),))))
        self.assertNotIn('flying', session.engine._combat_keywords(land))
        session.engine.move_card(deny.object_id, 'graveyard', log=False)
        self.assertNotIn('flying', session.engine._combat_keywords(land))

    def test_same_effect_losing_creature_type_allows_a_keyword_grant_before_source_departure(self):
        session = self.session(11311014)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='noncreature-denier')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='noncreature-grant-target')
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-noncreature'), targets=(body,), components=(
                ResolutionContinuousComponent(Layer.TYPE, '4', (ContinuousOperation('remove_creature_card_type'),)),
                ResolutionContinuousComponent(Layer.ABILITY, '6', (ContinuousOperation('add_ability', 'Flying'),))))
        self.assertIn('flying', session.engine._combat_keywords(body))
        session.engine.move_card(deny.object_id, 'graveyard', log=False)
        self.assertIn('flying', session.engine._combat_keywords(body))

    def test_same_effect_removing_the_prohibition_source_ability_allows_its_other_grant(self):
        session = self.session(11311015)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='same-effect-source-removal')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='same-effect-grant')
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-remove-and-grant'), targets=(deny, body), components=(
                ResolutionContinuousComponent(Layer.ABILITY, '6', (ContinuousOperation('remove_all_abilities'),
                    ContinuousOperation('add_ability','Flying'))),))
        self.assertEqual((), session.engine._effective_static_component_keys(deny))
        self.assertIn('flying', session.engine._combat_keywords(body))
        session.engine.move_card(deny.object_id, 'graveyard', log=False)
        self.assertIn('flying', session.engine._combat_keywords(body))

    def test_detached_resolution_proposal_query_does_not_mutate_live_state(self):
        from quorune.keyword_prohibition import prepare_resolution_keyword_exclusions
        from quorune.record import authoritative_state_hash
        session = self.session(11311016)
        self.add(session.engine, 'Archetype of Imagination', ref='pure-proposal-denier')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='pure-proposal-body')
        effect = ContinuousEffect(effect_id='pure:proposal', source_id='proposal', layer=Layer.ABILITY,
            sublayer='6', timestamp=session.state.event_sequence+100, operations=(ContinuousOperation('add_ability','Flying'),),
            origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(ContinuousObjectIdentity(body.object_id,body.logical_object_id),))
        before = authoritative_state_hash(session.state)
        prepared, = prepare_resolution_keyword_exclusions(session.engine,(body,),(effect,))
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual(((body.object_id,body.logical_object_id,'flying'),), prepared.denied_keyword_grants)

    def test_returned_recipient_does_not_inherit_its_old_resolution_grant_or_exclusion(self):
        session = self.session(11311017)
        deny = self.add(session.engine, 'Archetype of Imagination', ref='incarnation-denier')
        body = self.add(session.engine, 'Generic Bound Body', seat='B', ref='incarnation-recipient')
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-incarnation-grant'), targets=(body,), components=(
                ResolutionContinuousComponent(Layer.ABILITY,'6',(ContinuousOperation('add_ability','Flying'),)),))
        previous = body.logical_object_id
        session.engine.move_card(body.object_id,'graveyard',log=False,semantic_events=False)
        session.engine.move_card(body.object_id,'battlefield',controller='B',log=False,semantic_events=False)
        current = session.state.cards[body.object_id]
        self.assertNotEqual(previous,current.logical_object_id)
        session.engine.move_card(deny.object_id,'graveyard',log=False)
        self.assertNotIn('flying',session.engine._combat_keywords(current))
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-new-incarnation-grant'), targets=(current,), components=(
                ResolutionContinuousComponent(Layer.ABILITY,'6',(ContinuousOperation('add_ability','Flying'),)),))
        self.assertIn('flying',session.engine._combat_keywords(current))


if __name__ == '__main__':
    unittest.main()
