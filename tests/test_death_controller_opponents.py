"""Printed named-counter death results distinguish owner, last controller, and observer."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.death_controller_opponents import (
    DEATH_OPPONENTS_CAPABILITY, death_opponent_node_capabilities, death_opponents_program_dependencies,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record


BOARD = 'Bounty Board'
PROFILE = 'commander_review'
BODY = "each of its controller's opponents draws a card and gains 2 life."
DEATH = 'Whenever a creature with a bounty counter on it dies, '


class DeathControllerOpponentsCompilerTests(unittest.TestCase):
    def test_constructed_singular_named_counter_shape_and_unsupported_neighbors(self):
        registry = load_default_capability_registry()

        def compile_text(text):
            return compile_oracle_card(
                query_record(text), capability_registry=registry, capability_profile=PROFILE,
            )

        alternate = DEATH.replace('bounty', '+1/+1') + BODY.replace('a card', 'two cards')
        for text, counter in ((DEATH + BODY, 'bounty'), (alternate, '+1/+1')):
            with self.subTest(text=text):
                ir = compile_text(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual('creature.dies', node.event)
                self.assertEqual([counter], node.event_condition['all'][1]['value'])
                self.assertIn(DEATH_OPPONENTS_CAPABILITY, node.capability_dependencies)
                draw, gain = node.effects
                self.assertEqual(('draw_each_player', 'life'), (draw['op'], gain['op']))
                self.assertEqual(draw['players'], gain['players'])
                self.assertIs(draw['private'], True)
                for replacement in ({'private': 1}, {'players': {'kind': 'death_controller_opponents', 'schema_version': True}},
                                    {'players': ['$controller']}, {'count': 0}, {'source': '$source'}):
                    self.assertFalse(death_opponent_node_capabilities((dict(draw) | replacement, gain), None))
                self.assertFalse(death_opponent_node_capabilities((gain, draw), None))
                self.assertFalse(death_opponent_node_capabilities((draw, gain), {'count': 1}))
        for text in (
            'Whenever a creature dies, ' + BODY,
            DEATH.replace('dies', 'leaves the battlefield') + BODY,
            DEATH.replace('a creature', 'one or more creatures') + BODY,
            DEATH + BODY.replace("controller's", "owner's"),
            DEATH + BODY.replace('draws a card and gains 2 life', 'may draw a card and gain 2 life'),
            DEATH + BODY.replace('gains 2 life', 'gains X life'),
            DEATH + BODY + ' Proliferate.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_text(text).status)


class BountyBoardNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'bounty-board.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bounty-board-cards.json',
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/event-creature-controller-result-cards.json',
            ROOT / 'tests/fixtures/counter-qualified-zone-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Bounty death and private draw', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def test_full_card_is_exact_and_runtime_binding_requires_each_death_result_owner(self):
        record = self.db.lookup(BOARD)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertEqual(3, len(ir.faces[0].nodes))
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile=PROFILE,
        )
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        session = self.session(841000)
        board = self.add(session.engine, BOARD)
        trigger = next(p for p in session.engine.semantics.programs_for_oracle(board.oracle_id) if p.event == 'creature.dies')
        required = death_opponents_program_dependencies(trigger)
        self.assertIn(DEATH_OPPONENTS_CAPABILITY, required)
        for missing in required:
            declared = tuple(value for value in trigger.capability_dependencies if value != missing)
            altered = replace(
                trigger, coverage=(), capability_dependencies=declared,
                capability_closure=self.registry.closure(declared, profile=PROFILE).to_dict(),
            )
            result = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result['strict'], missing)
            self.assertIn('capability:undeclared_runtime_dependency:' + missing, result['blockers'])
        for altered in (
            replace(trigger, event='permanent.leave'),
            replace(trigger, effects=(*trigger.effects, {'op': 'draw', 'player': '$controller', 'count': 1})),
        ):
            with self.assertRaises(ValueError):
                death_opponents_program_dependencies(altered)

    def _actions(self, session, seat):
        return session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']

    def _fixture(self, seed):
        session = self.session(seed)
        board = self.add(session.engine, BOARD, zone='hand', ref='bounty-board-a')
        other_board = self.add(session.engine, BOARD, seat='B', ref='bounty-board-b')
        borrowed = self.add(session.engine, 'Grizzly Bears', seat='D', ref='bounty-borrowed')
        marked = self.add(session.engine, 'Grizzly Bears', seat='C', ref='bounty-marked')
        marked.counters['bounty'] = 2
        wrong_counter = self.add(session.engine, 'Grizzly Bears', seat='B', ref='wrong-counter')
        wrong_counter.counters['+1/+1'] = 1
        steal = self.add(session.engine, 'Control Magic', seat='B', zone='hand', ref='bounty-control')
        self.add(session.engine, 'Vedalken Orrery', seat='B', ref='bounty-flash')
        wipe = self.add(session.engine, 'Day of Judgment', zone='hand', ref='bounty-destroy')
        for item in (board, other_board, steal, wipe):
            self.assert_closed(session, item)
        self.prepare(session, mana={'C': 9, 'W': 2})
        session.state.players['B'].mana_pool.update({'C': 2, 'U': 2})
        self.checkpoint(session)
        self.cast(session, 'A', board)
        self.assertIsNone(self.resolve(session))
        combat_action, = [
            row for row in self._actions(session, 'A') if row['id'].startswith('activate:' + board.ref + ':')
            and row.get('target_schema')
        ]
        before_pool = sum(session.state.players['A'].mana_pool.values())
        command = {'action_id': combat_action['id'], 'targets': [borrowed.ref], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', command).ok)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(board.tapped)
        self.assertEqual(before_pool - 1, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual({'bounty': 1}, borrowed.counters)
        self.cast(session, 'B', steal, [borrowed.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('D', 'B'), (borrowed.owner, borrowed.controller))
        return session, (board, other_board), (borrowed, marked, wrong_counter), wipe

    def _overlapping_deaths(self, seed, *, replay):
        session, boards, victims, wipe = self._fixture(seed)
        self.cast(session, 'A', wipe)
        hands = {seat: set(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        lives = {seat: session.state.players[seat].life for seat in 'ABCD'}
        self.resolve_top(session)
        self.assertTrue(all(victim.zone == 'graveyard' and not victim.counters for victim in victims))
        self.assertIn(victims[0].object_id, session.state.players['D'].zones['graveyard'])
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        if replay:
            session = self.replay(session, load=True)
        for seat in 'AB':
            self.assertEqual(('pilot:' + seat,), tuple(session.pending_principals()))
            projected = session.packet('pilot:' + seat, full=True)['decision']['ctx']['triggers']
            self.assertEqual(2, len(projected))
            response = session.act('pilot:' + seat, {'action_id': 'order', 'triggers': [row['id'] for row in projected]})
            self.assertTrue(response.ok, response.summary)
        self.assertEqual(
            [('A', boards[0].object_id), ('A', boards[0].object_id),
             ('B', boards[1].object_id), ('B', boards[1].object_id)],
            [(item.controller, item.source_object_id) for item in session.state.stack],
        )
        self.assertEqual(['B', 'B', 'C', 'C'], sorted(item.context['previous_controller'] for item in session.state.stack))
        self.assertIsNone(self.resolve(session))
        for seat, count in zip('ABCD', (4, 2, 2, 4), strict=True):
            player = session.state.players[seat]
            drawn = set(player.zones['hand']) - hands[seat]
            self.assertEqual(count, len(drawn), seat)
            self.assertEqual(lives[seat] + 2 * count, player.life, seat)
            if replay:
                own = json.dumps(session.packet('pilot:' + seat, full=True))
                for object_id in drawn:
                    ref = session.state.cards[object_id].ref
                    self.assertIn(ref, own)
                    for other in 'ABCD'.replace(seat, ''):
                        self.assertNotIn(ref, json.dumps(session.packet('pilot:' + other, full=True)))
        if replay:
            self.replay(session, load=True)

    def test_four_seat_printed_placement_theft_overlapping_deaths_private_draw_and_replay(self):
        self._overlapping_deaths(841001, replay=True)

    def test_source_controller_substitution_is_killed_by_actual_borrowed_death(self):
        from quorune.rules.death_controller_opponents import bind_death_opponent_result

        def wrong_anchor(host, item, effect):
            if isinstance(effect.get('players'), dict) and effect['players'].get('kind') == 'death_controller_opponents':
                altered = deepcopy(item)
                altered.context['previous_controller'] = item.controller
                return bind_death_opponent_result(host, altered, effect)
            return bind_death_opponent_result(host, item, effect)

        with patch('quorune.rules.death_controller_opponents.bind_death_opponent_result', side_effect=wrong_anchor):
            with self.assertRaisesRegex(AssertionError, r'4 != 2 : A'):
                self._overlapping_deaths(841002, replay=False)

    def test_printed_mana_and_marked_exile_or_noncreature_death_have_no_reward(self):
        session = self.session(841003)
        board = self.add(session.engine, BOARD, ref='bounty-mana')
        alien = self.add(session.engine, 'Grizzly Bears', seat='B', ref='bounty-exiled')
        alien.counters['bounty'] = 1
        artifact = self.add(session.engine, 'Vedalken Orrery', seat='C', ref='bounty-noncreature')
        artifact.counters['bounty'] = 1
        exile = self.add(session.engine, 'Swords to Plowshares', zone='hand', ref='bounty-exile')
        destroy = self.add(session.engine, 'Disenchant', zone='hand', ref='bounty-artifact-destroy')
        for item in (board, exile, destroy):
            self.assert_closed(session, item)
        self.prepare(session, mana={'W': 2})
        self.checkpoint(session)
        mana, = [
            row for row in self._actions(session, 'A')
            if row.get('source') == board.ref and row.get('mana_ability') is True
        ]
        before_pool = dict(session.state.players['A'].mana_pool)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': mana['id'], 'mana_output': {'C': 1}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': mana['id'], 'mana_output': {'G': 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before_pool.get('G', 0) + 1, session.state.players['A'].mana_pool.get('G', 0))
        self.assertTrue(board.tapped)
        self.assertFalse(session.state.stack)
        for spell, target, destination in ((exile, alien, 'exile'), (destroy, artifact, 'graveyard')):
            self.cast(session, 'A', spell, [target.ref])
            hands = {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
            lives = {seat: session.state.players[seat].life for seat in 'ABCD'}
            self.resolve_top(session)
            self.assertEqual(destination, session.state.cards[target.object_id].zone)
            self.assertFalse(session.state.stack)
            self.assertEqual(hands, {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'})
            if spell is exile:
                lives['B'] += 2
            self.assertEqual(lives, {seat: session.state.players[seat].life for seat in 'ABCD'})
        self.assertEqual(0, session.state.players['A'].mana_pool.get('G', 0))
        self.replay(session, load=True)

    def test_actual_reward_skips_a_conceded_opponent_and_replays(self):
        session = self.session(841004)
        board = self.add(session.engine, BOARD)
        marked = self.add(session.engine, "Grizzly Bears", seat="B")
        marked.counters["bounty"] = 1
        wipe = self.add(session.engine, "Day of Judgment", zone="hand")
        self.assert_closed(session, board)
        self.assert_closed(session, wipe)
        self.prepare(session, mana={"C": 2, "W": 2})
        self.checkpoint(session)

        self.cast(session, "A", wipe)
        self.resolve_top(session)
        trigger, = session.state.stack
        self.assertEqual("B", trigger.context["previous_controller"])
        self.priority(session, "D")
        accepted = session.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.players["D"].in_game)
        self.assertEqual([trigger.ref], [item.ref for item in session.state.stack])

        session = self.replay(session, load=True)
        hands = {seat: len(session.state.players[seat].zones["hand"]) for seat in "ABCD"}
        lives = {seat: session.state.players[seat].life for seat in "ABCD"}
        self.resolve_top(session)
        for seat in "ABCD":
            player = session.state.players[seat]
            gained = int(seat in "AC")
            self.assertEqual(hands[seat] + gained, len(player.zones["hand"]))
            self.assertEqual(lives[seat] + 2 * gained, player.life)
        self.replay(session, load=True)
