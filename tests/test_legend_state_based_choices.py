from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.replacement.immutable import FrozenMap
from quorune.state_based_legend_model import (
    LegendCandidateSnapshot, LegendChoicePlan, LegendConflictSnapshot,
    StateBasedLegendError, legend_rule_choice_version,
)
from quorune.state_based_legend_choices import current_legend_choices


def plan_fixture(*, second_a_group=False, departed_anchor=False):
    groups = [LegendConflictSnapshot(seat, name, tuple(
        LegendCandidateSnapshot(key, key + ':0', key) for key in ids))
        for seat, name, ids in (
            ('A', 'First Legend', ('a1', 'a2')),
            ('C', 'Second Legend', ('c1', 'c2')),
        )]
    if second_a_group:
        groups.append(LegendConflictSnapshot('A', 'Other Legend', tuple(
            LegendCandidateSnapshot(key, key + ':0', key) for key in ('a3', 'a4'))))
    frame = FrozenMap({
        'active_player': 'B', 'phase': 'precombat_main', 'step': 'main', 'turn_sequence': 3,
        'stack_refs': [], 'turn_order': ['A', 'B', 'C', 'D'],
        'active_seats': ['A', 'C', 'D'] if departed_anchor else ['A', 'B', 'C', 'D'],
        'battlefield': [{'object_id': candidate.object_id, 'logical_object_id': candidate.logical_object_id,
                        'controller': group.controller, 'name': group.name, 'phased_out': False,
                        'type_line': 'Legendary Creature — Human'}
                       for group in groups for candidate in group.candidates],
    })
    return LegendChoicePlan(frame, ('C', 'D', 'A') if departed_anchor else ('B', 'C', 'D', 'A'), tuple(groups))


class LegendStateBasedChoiceModelTests(unittest.TestCase):
    def test_apnap_collects_all_keeps_without_a_zone_mutation(self):
        initial = plan_fixture()
        self.assertEqual('C', initial.actor)
        chosen = initial.choose('C', 'c1')
        self.assertEqual('A', chosen.actor)
        complete = chosen.choose('A', 'a2')
        self.assertTrue(complete.complete)
        self.assertEqual(('c1', 'a2'), complete.kept)
        self.assertEqual((), initial.kept)
        self.assertEqual(initial.frame, complete.frame)
        self.assertEqual(complete, LegendChoicePlan.from_dict(complete.to_dict()))

    def test_each_player_may_choose_the_order_of_their_own_conflicts(self):
        plan = plan_fixture(second_a_group=True).choose('C', 'c2')
        self.assertEqual({'a1', 'a2', 'a3', 'a4'}, {c.ref for c in plan.legal_candidates()})
        plan = plan.choose('A', 'a3')
        self.assertEqual({'a1', 'a2'}, {c.ref for c in plan.legal_candidates()})
        self.assertTrue(plan.choose('A', 'a1').complete)

    def test_departed_turn_anchor_is_known_but_is_not_a_choice_principal(self):
        plan = plan_fixture(departed_anchor=True)
        self.assertEqual('B', plan.frame['active_player'])
        self.assertEqual('C', plan.actor)
        with self.assertRaises(StateBasedLegendError):
            plan.choose('B', 'c1')

    def test_wrong_principals_repeated_groups_and_malformed_saved_choices_reject(self):
        plan = plan_fixture()
        for actor, ref in (('A', 'a1'), ('D', 'c1'), ('C', 'a1'), ('C', 'unknown')):
            with self.subTest(actor=actor, ref=ref):
                with self.assertRaises(StateBasedLegendError):
                    plan.choose(actor, ref)
        for changed in (
            {'kept': ['a1', 'c1']}, {'kept': ['c1', 'c2']}, {'kept': 'c1'},
            {'schema_version': True}, {'extra': True}, {'player_order': ['A', 'B', 'C', 'D']},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    LegendChoicePlan.from_dict({**plan.to_dict(), **changed})
        with self.assertRaises(ValueError):
            replace(plan, groups=(*plan.groups, plan.groups[0]))

    def test_versions_preserve_absence_and_reject_unknown_or_boolean_modes(self):
        self.assertIsNone(legend_rule_choice_version(None))
        self.assertEqual(1, legend_rule_choice_version(1))
        for value in (True, False, 0, 2, '1'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    legend_rule_choice_version(value)

    def test_checkpoint_mode_and_manifest_provenance_must_agree(self):
        from quorune.model import GameConfig, GameState
        from quorune.record_state_provenance import format_state_versions, validate_state_versions

        state = GameState('legend-codec', GameConfig(), {}, {}, {}, {}, [], None, None)
        current = state.to_dict()
        self.assertEqual(1, GameState.from_dict(current).legend_rule_choice_version)
        provenance = format_state_versions(state)
        validate_state_versions({'format': provenance}, state)
        for value in (None, 0, True, 2):
            changed = {**provenance, 'legend_rule_choice_version': value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_state_versions({'format': changed}, state)
        for value in (0, True, 2):
            with self.subTest(checkpoint=value), self.assertRaises(ValueError):
                GameState.from_dict({**current, 'legend_rule_choice_version': value})
        legacy = dict(current)
        legacy.pop('legend_rule_choice_version')
        historical = GameState.from_dict(legacy)
        self.assertIsNone(historical.legend_rule_choice_version)
        self.assertNotIn('legend_rule_choice_version', historical.to_dict())
        validate_state_versions({'format': format_state_versions(historical)}, historical)

    def test_saved_batch_extension_requires_complete_matching_legend_choices(self):
        from quorune.state_based_legend_model import legend_choices_from_frame
        from quorune.replacement.immutable import thaw_value
        initial = plan_fixture()
        complete = initial.choose('C', 'c1').choose('A', 'a1')
        frame = thaw_value(initial.frame)
        self.assertIsNone(legend_choices_from_frame(frame))
        current = {**frame, 'legend_choices': complete.to_dict()}
        self.assertEqual(complete, legend_choices_from_frame(current))
        for changed in (
            {**frame, 'legend_choices': initial.to_dict()},
            {**current, 'legend_choices': None},
            {**current, 'turn_sequence': frame['turn_sequence'] + 1},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                legend_choices_from_frame(changed)


from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


class LegendStateBasedChoiceNativeTests(InheritedNativeFixture, unittest.TestCase):
    """Post-resolution owner snapshots; setup does not claim a producing spell."""
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'legend-state-choices.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/per-player-target-cards.json',
            ROOT / 'tests/fixtures/state-based-destination-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Legend owner controls', [
            DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 14),
        ], ['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def scene(self, seed, *, legacy=False, zero_toughness=False, destination_replacements=False, composed=False):
        s = self.session(seed)
        engine = s.engine
        if legacy:
            s.state.legend_rule_choice_version = None
        engine.permissions.invalidate_current()
        s.state.started = True
        s.state.active_player = 'B'
        s.state.last_normal_turn_player = 'B'
        if s.state.current_turn is not None:
            s.state.current_turn = replace(s.state.current_turn, player='B')
        s.state.phase, s.state.step = 'precombat_main', 'main'
        s.state.phase_index = TURN_STEPS.index(('precombat_main', 'main'))
        incoming = None
        if composed:
            incoming = self.add(engine, 'Phyrexian Walker', seat='B', zone='hand', ref='legend-physical-part')
            engine._grant_priority('B')
            engine.pump()
            actions = s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
            offer = next(row for row in actions if row.get('card') == incoming.ref)
            result = s.act('pilot:B', {'action_id': offer['id'], 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary)
        commanders = {}
        for owner, controller in (('A', 'A'), ('B', 'A'), ('C', 'C'), ('D', 'C')):
            card = next(card for card in s.state.cards.values() if card.owner == owner and card.is_commander)
            self.assert_closed(s, card)
            commanders[owner] = card.object_id
            engine.move_card(card.object_id, 'battlefield', controller=controller, log=False)
        if incoming is not None:
            from quorune.continuous_effect_model import ContinuousObjectIdentity
            from quorune.permanent_composition import MergePosition
            from quorune.rules.composition_merge import prepare_composition_merge
            from quorune.zone_transitions import ZoneTransitionOwner
            root = s.state.cards[commanders['B']]
            spell = next(item for item in s.state.stack if item.card_object_id == incoming.object_id)
            # Canonical merge-owner snapshot, not an ordinary Walker cast effect.
            ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(
                engine, target=ContinuousObjectIdentity(root.object_id, root.logical_object_id),
                source=ContinuousObjectIdentity(incoming.object_id, incoming.logical_object_id),
                stack_ref=spell.ref, position=MergePosition.BOTTOM))
        private = self.add(engine, 'Counterspell', seat='C', zone='hand', ref='legend-private-control')
        if destination_replacements:
            # Existing reviewed replacement controls; no strict whole-card claim.
            for index in range(2):
                self.add(engine, 'Dauthi Voidwalker', ref='legend-destination-source-' + str(index))
        if zero_toughness:
            engine.state.cards[commanders['A']].annotations.setdefault('copy_overrides', {}).update(
                {'type_line': 'Legendary Creature — Dog', 'power': '2', 'toughness': '0'})
        engine.permissions.invalidate_current()
        s.state.priority_player = None
        s.state.priority_passes = []
        engine.pump()
        self.checkpoint(s)
        return s, commanders, private

    def choose(self, s, actor, ref):
        result = s.act('pilot:' + actor, {'action_id': 'choose', 'card': ref})
        self.assertTrue(result.ok, result.summary)

    def test_current_apnap_keeps_precede_all_departures_and_commander_choices(self):
        s, ids, private = self.scene(704500201)
        self.assertEqual(('state.legend', ['C']), (s.state.pending_decision.kind, s.state.pending_decision.actors))
        first = s.state.cards[ids['C']].ref
        for ref in (s.state.cards[ids['A']].ref, 'unknown'):
            before = authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:C', {'action_id': 'choose', 'card': ref}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        original_phase = s.state.phase
        s.state.phase = 'postcombat_main'
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C', {'action_id': 'choose', 'card': first}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s.state.phase = original_phase
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', {'action_id': 'choose', 'card': first}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.choose(s, 'C', first)
        self.assertTrue(all(s.state.cards[object_id].zone == 'battlefield' for object_id in ids.values()))
        self.assertEqual(('state.legend', ['A']), (s.state.pending_decision.kind, s.state.pending_decision.actors))
        context = s.packet('pilot:A', full=True)['decision']['ctx']
        self.assertEqual('C', context['earlier_keeps'][0]['player'])
        s = self.replay(s, load=True)
        self.choose(s, 'A', s.state.cards[ids['A']].ref)
        self.assertEqual(('battlefield', 'graveyard', 'battlefield', 'graveyard'), tuple(
            s.state.cards[ids[seat]].zone for seat in 'ABCD'))
        self.assertIsNone(current_legend_choices())
        self.assertEqual(('state.commander_zone', ['B']),
            (s.state.pending_decision.kind, s.state.pending_decision.actors))
        for owner in ('B', 'D'):
            self.assertEqual([owner], s.state.pending_decision.actors)
            result = s.act('pilot:' + owner, {'action_id': 'remain'})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(('priority', ['B']), (s.state.pending_decision.kind, s.state.pending_decision.actors))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        self.replay(s, load=True)

    def test_legacy_absence_retains_the_original_offered_choice_and_replay(self):
        s, ids, _private = self.scene(704500202, legacy=True)
        self.assertEqual(['A'], s.state.pending_decision.actors)
        self.assertNotIn('legend_rule_choice_version', s.state.to_dict())
        self.choose(s, 'A', s.state.cards[ids['A']].ref)
        self.assertEqual('graveyard', s.state.cards[ids['B']].zone)
        self.assertEqual('battlefield', s.state.cards[ids['D']].zone)
        self.assertEqual(('state.commander_zone', ['B']),
            (s.state.pending_decision.kind, s.state.pending_decision.actors))
        self.replay(s, load=True)

    def test_zero_toughness_legend_remains_a_keep_choice_before_the_shared_sba_commit(self):
        s, ids, _private = self.scene(704500203, zero_toughness=True)
        self.assertEqual(['C'], s.state.pending_decision.actors)
        self.assertEqual('battlefield', s.state.cards[ids['A']].zone)
        self.choose(s, 'C', s.state.cards[ids['C']].ref)
        self.assertTrue(all(s.state.cards[object_id].zone == 'battlefield' for object_id in ids.values()))
        self.assertIn(s.state.cards[ids['A']].ref,
            s.state.pending_decision.payload_by_actor['A']['keep_one'])
        s = self.replay(s, load=True)
        self.choose(s, 'A', s.state.cards[ids['A']].ref)
        self.assertEqual(('graveyard', 'graveyard', 'battlefield', 'graveyard'), tuple(
            s.state.cards[ids[seat]].zone for seat in 'ABCD'))
        self.assertIsNone(current_legend_choices())
        self.replay(s, load=True)

    def test_departed_active_player_owner_cleanup_restarts_from_surviving_current_facts(self):
        s, ids, _private = self.scene(704500204)
        self.choose(s, 'C', s.state.cards[ids['C']].ref)
        self.assertEqual(['A'], s.state.pending_decision.actors)
        # B has no command capability during A's choice. Preserve that authority
        # boundary, and exercise departure through the existing elimination owner.
        # Replay each side separately: this owner mutation is not a player command.
        s = self.replay(s, load=True)
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s.engine._eliminate_players(['B'], reason='legend continuation owner cleanup control')
        self.assertEqual('B', s.state.active_player)
        self.assertFalse(s.state.players['B'].in_game)
        self.assertEqual(['C'], s.state.pending_decision.actors)
        self.assertEqual('battlefield', s.state.cards[ids['D']].zone)
        self.assertEqual('battlefield', s.state.cards[ids['A']].zone)
        self.checkpoint(s)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B', {'action_id': 'choose', 'card': s.state.cards[ids['C']].ref}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        self.choose(s, 'C', s.state.cards[ids['C']].ref)
        self.assertEqual('graveyard', s.state.cards[ids['D']].zone)
        self.assertEqual(('state.commander_zone', ['D']),
            (s.state.pending_decision.kind, s.state.pending_decision.actors))
        result = s.act('pilot:D', {'action_id': 'remain'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(('priority', ['C']), (s.state.pending_decision.kind, s.state.pending_decision.actors))
        self.replay(s, load=True)

    def test_game_end_clears_pending_legend_choices_without_a_decision_participant(self):
        for draw in (False, True):
            with self.subTest(draw=draw):
                s, _ids, _private = self.scene(704500205 + int(draw))
                s.engine._eliminate_players(list('ABCD' if draw else 'BCD'), reason='owner cleanup control')
                self.assertTrue(s.state.game_over)
                self.assertIsNone(s.state.pending_decision)
                self.assertIsNone(current_legend_choices())
                self.assertEqual(draw, s.state.draw)
                self.assertEqual(None if draw else 'A', s.state.winner)

    def test_completed_keeps_survive_destination_choices_without_partial_movement(self):
        s, ids, _private = self.scene(704500207, destination_replacements=True)
        self.choose(s, 'C', s.state.cards[ids['C']].ref)
        self.choose(s, 'A', s.state.cards[ids['A']].ref)
        self.assertIsNone(current_legend_choices())
        self.assertTrue(LegendChoicePlan.from_dict(
            s.state.pending_decision.continuation['state_based_frame']['legend_choices']).complete)
        for _ in range(4):
            if s.state.pending_decision.kind != 'replacement.order':
                break
            self.assertTrue(all(s.state.cards[object_id].zone == 'battlefield' for object_id in ids.values()))
            self.assertIn('legend_choices', s.state.pending_decision.continuation['state_based_frame'])
            s = self.replay(s, load=True)
            actor = s.state.pending_decision.actors[0]
            options = s.packet('pilot:' + actor, full=True)['decision']['ctx']['options']
            choice = {'action_id': 'choose', 'replacement': options[0]['id']}
            original = deepcopy(s.state.pending_decision.continuation)
            s.state.pending_decision.continuation['state_based_frame']['legend_choices']['kept'].reverse()
            before = authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:' + actor, choice).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
            s.state.pending_decision.continuation = original
            result = s.act('pilot:' + actor, choice)
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(current_legend_choices())
        self.assertEqual(('battlefield', 'exile', 'battlefield', 'exile'), tuple(
            s.state.cards[ids[seat]].zone for seat in 'ABCD'))
        self.assertEqual(1, s.state.cards[ids['B']].counters.get('void', 0))
        self.assertEqual(1, s.state.cards[ids['D']].counters.get('void', 0))
        self.replay(s, load=True)

    def test_completed_keeps_survive_physical_ordering_before_the_shared_commit(self):
        s, ids, _private = self.scene(704500208, composed=True)
        self.choose(s, 'C', s.state.cards[ids['C']].ref)
        self.choose(s, 'A', s.state.cards[ids['A']].ref)
        self.assertEqual(('permanent.composition_order', ['B']),
            (s.state.pending_decision.kind, s.state.pending_decision.actors))
        self.assertTrue(all(s.state.cards[object_id].zone == 'battlefield' for object_id in ids.values()))
        frame = s.state.pending_decision.continuation['composition_state_based_order']['state_based_frame']
        self.assertTrue(LegendChoicePlan.from_dict(frame['legend_choices']).complete)
        self.assertIsNone(current_legend_choices())
        s = self.replay(s, load=True)
        root = s.state.cards[ids['B']]
        result = s.act('pilot:B', {'action_id': 'order', 'cards': ['legend-physical-part', root.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(('battlefield', 'graveyard', 'battlefield', 'graveyard'), tuple(
            s.state.cards[ids[seat]].zone for seat in 'ABCD'))
        part = next(card for card in s.state.cards.values() if card.ref == 'legend-physical-part')
        self.assertEqual('graveyard', part.zone)
        self.assertLess(s.state.players['B'].zones['graveyard'].index(part.object_id),
            s.state.players['B'].zones['graveyard'].index(root.object_id))
        self.assertIsNone(current_legend_choices())
        self.replay(s, load=True)

    def test_failed_batch_commit_rolls_back_and_does_not_leak_scoped_keeps(self):
        from quorune.errors import GameRuleError
        import quorune.state_based_execution as owner
        s, ids, _private = self.scene(704500209)
        self.choose(s, 'C', s.state.cards[ids['C']].ref)
        before = authoritative_state_hash(s.state)
        commit = owner._commit_state_based_zone_changes
        def fault(host, plan):
            commit(host, plan)
            raise GameRuleError('Injected failure after the simultaneous zone commit')
        with patch.object(owner, '_commit_state_based_zone_changes', side_effect=fault):
            result = s.act('pilot:A', {'action_id': 'choose', 'card': s.state.cards[ids['A']].ref})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertIsNone(current_legend_choices())
        self.assertTrue(all(s.state.cards[object_id].zone == 'battlefield' for object_id in ids.values()))
        self.choose(s, 'A', s.state.cards[ids['A']].ref)
        self.assertEqual(('battlefield', 'graveyard', 'battlefield', 'graveyard'), tuple(
            s.state.cards[ids[seat]].zone for seat in 'ABCD'))
        self.assertIsNone(current_legend_choices())
        self.replay(s, load=True)


if __name__ == '__main__':
    unittest.main()
