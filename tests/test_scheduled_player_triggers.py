from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import json
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.rules.trigger_source_information import (
    TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY, tapped_draw_damage_condition,
)
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class ScheduledPlayerTriggerCompilerTests(unittest.TestCase):
    def test_departure_mode_requires_a_closed_scheduled_step_and_recipient(self):
        from quorune.rules.scheduled_player_shapes import (
            SCHEDULED_PLAYER_CAPABILITY, SCHEDULED_PLAYER_DEPARTURE_COVERAGE,
            SCHEDULED_PLAYER_MECHANIC, scheduled_player_departure_runtime_dependencies,
        )
        text = "At the beginning of each player's upkeep, that player loses 2 life."
        ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(SCHEDULED_PLAYER_DEPARTURE_COVERAGE, node.runtime_coverage)
        program = dict(coverage=(SCHEDULED_PLAYER_MECHANIC, SCHEDULED_PLAYER_DEPARTURE_COVERAGE),
                       effects=node.effects, capability_dependencies=node.capability_dependencies,
                       target_schema=node.target_schema, event=node.event, event_condition=node.event_condition)
        self.assertEqual((SCHEDULED_PLAYER_CAPABILITY,),
                         scheduled_player_departure_runtime_dependencies(SimpleNamespace(**program)))
        for change in (
            {'event': 'permanent.enter'},
            {'event_condition': {'any': [node.event_condition, {'field': 'step', 'op': 'eq', 'value': 'end_step'}]}},
            {'target_schema': {'count': 1, 'zones': ['player']}},
            {'capability_dependencies': ('life.change.effect',)},
            {'effects': ({'op': 'lose_life', 'player': '$controller', 'amount': 2},)},
        ):
            with self.assertRaisesRegex(ValueError, 'closed upkeep or draw result'):
                scheduled_player_departure_runtime_dependencies(SimpleNamespace(**{**program, **change}))

    def test_additional_draw_step_missing_authority_stays_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry

        record=query_record("At the beginning of each player's draw step, that player draws an additional card.")
        for capability in ('trigger.event.scheduled_player_result','zone.draw.library_to_hand'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==capability).update(
                status='blocked',blockers=['draw-step authority omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record,
                capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)

    def test_draw_step_schedules_keep_context_and_controller_recipients_distinct(self):
        registry=load_default_capability_registry()
        for text,step,recipient in (
            ("At the beginning of each player's draw step, that player draws an additional card.",'draw','$context.player'),
            ("At the beginning of each other player's draw step, that player draws an additional card.",'draw','$context.player'),
            ("At the beginning of your draw step, draw two additional cards.",'draw','$controller'),
        ):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,
                                       capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertEqual(recipient,node.effects[0]['player'])
                self.assertIn(step,str(node.event_condition))

    def test_mixed_scheduled_result_preserves_each_original_recipient(self):
        text = "At the beginning of each opponent's upkeep, that player draws a card and you gain 1 life."
        ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertEqual(({'op': 'draw', 'player': '$context.player', 'count': 1, 'private': True},
            {'op': 'life', 'player': '$controller', 'delta': 1}), ir.faces[0].nodes[0].effects)

    def test_each_player_and_opponent_results_are_nontargeted_context_bound(self):
        for text in ("At the beginning of each player's upkeep, that player loses 2 life and draws two cards.",
                     "At the beginning of each opponent's upkeep, that player loses 1 life."):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual('step.begin',node.event)
            self.assertIsNone(node.target_schema)
            self.assertTrue(all(e['player']=='$context.player' for e in node.effects))
            self.assertIn('trigger.event.scheduled_player_result',node.capability_dependencies)
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_unknown_bound_results_and_monarch_schedules_remain_residual(self):
        for text in ("At the beginning of each player's upkeep, that player wins the game.",
                     "At the beginning of the monarch's end step, that player draws a card.",
                     "At the beginning of each player's upkeep, they draw a card for each creature they control."):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_bound_player_damage_shape_requires_exact_player_and_positive_amount(self):
        from quorune.rules.scheduled_player_shapes import scheduled_player_node_capabilities
        effect={'op':'damage','source':'$source','target':'$context.player','amount':1}
        args={'effects':(effect,),'target_schema':None,'mechanic_ids':('scheduled-player-event-result',)}
        self.assertIn('damage.result.player_life',scheduled_player_node_capabilities(**args))
        for changes in ({'target':'$controller'},{'amount':True},{'source':'$target.0'},{'extra':1}):
            self.assertEqual((),scheduled_player_node_capabilities(**{**args,'effects':({**effect,**changes},)}))

    def test_mixed_scheduled_shape_rejects_malformed_second_player(self):
        from quorune.rules.scheduled_player_shapes import scheduled_player_node_capabilities
        draw = {'op': 'draw', 'player': '$context.player', 'count': 1, 'private': True}
        for player in ([], {}, None, True, '$target.0'):
            with self.subTest(player=player):
                self.assertEqual((), scheduled_player_node_capabilities(effects=(draw,
                    {'op': 'life', 'player': player, 'delta': 1}), target_schema=None,
                    mechanic_ids=('scheduled-player-event-result',)))


class AdditionalDrawStepRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'additional-draw.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                              ROOT/'tests/fixtures/additional-draw-step-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional draw',[DeckEntry('Generic Bound Commander',1,'commander'),
                                                 DeckEntry('Generic Bound Plains',12)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def enter_draw_step(self,session,seat):
        from quorune.engine import TURN_STEPS

        engine=session.engine
        engine.permissions.invalidate_current()
        session.state.pending_decision=None
        session.state.active_player=seat
        session.state.started=True
        session.state.phase='beginning'
        session.state.step='upkeep'
        session.state.phase_index=TURN_STEPS.index(('beginning','upkeep'))
        engine._grant_priority(seat)
        engine.pump()
        self.checkpoint(session)
        for _ in range(16):
            if session.state.step=='draw':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual('draw',session.state.step)

    def assert_admission(self,name):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry

        program=compile_best_available_card_program(self.db,self.db.lookup(name),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def test_original_kami_draws_for_active_opponent_after_ordinary_draw(self):
        self.assert_admission('Kami of the Crescent Moon')
        session=self.session(346201)
        source=self.add(session.engine,'Kami of the Crescent Moon')
        hands={seat:len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        self.enter_draw_step(session,'B')
        self.assertEqual(hands['B']+1,len(session.state.players['B'].zones['hand']))
        trigger=next(item for item in session.state.stack if item.source_object_id==source.object_id)
        self.assertEqual(('A','B'),(trigger.controller,trigger.context['player']))
        self.replay(session,load=True)
        self.resolve(session)
        self.assertEqual(hands['B']+2,len(session.state.players['B'].zones['hand']),
                         'additional draw recipient changed')
        for seat in 'ACD':
            self.assertEqual(hands[seat],len(session.state.players[seat].zones['hand']))
        for seat in 'ACD':
            packet=json.dumps(session.packet('pilot:'+seat,full=True),sort_keys=True)
            for oid in session.state.players['B'].zones['hand']:
                self.assertNotIn(oid,packet)
        self.replay(session)

    def test_original_kami_skips_the_additional_draw_after_the_draw_player_leaves(self):
        self.assert_admission('Kami of the Crescent Moon')
        session = self.session(346_229)
        source = self.add(session.engine, 'Kami of the Crescent Moon')
        before = {seat: len(player.zones['hand']) for seat, player in session.state.players.items()}
        self.enter_draw_step(session, 'B')
        self.assertEqual(before['B'] + 1, len(session.state.players['B'].zones['hand']))
        trigger, = [item for item in session.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(('A', 'B', 'draw'),
                         (trigger.controller, trigger.context['player'], trigger.context['step']))
        departed = session.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
        self.assertTrue(departed.ok, departed.summary)
        self.resolve(session)
        self.assertFalse(session.state.stack)
        self.assertEqual({seat: before[seat] for seat in 'ACD'},
                         {seat: len(session.state.players[seat].zones['hand']) for seat in 'ACD'})
        skipped, = [event.details for event in session.state.events if event.code == 'effect.player.skipped']
        self.assertEqual(('B', 'draw'), (skipped['player'], skipped['operation']))
        self.replay(session)

    def test_original_overbeing_additional_draw_occurs_only_on_controller_draw_step(self):
        self.assert_admission('Overbeing of Myth')
        for active in 'AB':
            with self.subTest(active=active):
                session=self.session(346210+int(active=='B'))
                source=self.add(session.engine,'Overbeing of Myth')
                before=len(session.state.players[active].zones['hand'])
                self.enter_draw_step(session,active)
                triggers=[item for item in session.state.stack if item.source_object_id==source.object_id]
                self.assertEqual(int(active=='A'),len(triggers))
                self.resolve(session)
                self.assertEqual(before+1+int(active=='A'),len(session.state.players[active].zones['hand']))
                self.replay(session)

    def test_original_howling_mine_rechecks_untapped_condition_at_resolution(self):
        self.assert_admission('Howling Mine')
        for tapped_at_occurrence,tapped_before_resolution in ((False,False),(True,True),(False,True)):
            with self.subTest(occurrence=tapped_at_occurrence,resolution=tapped_before_resolution):
                session=self.session(346202+int(tapped_at_occurrence)+2*int(tapped_before_resolution))
                source=self.add(session.engine,'Howling Mine')
                tapper=self.add(session.engine,'Icy Manipulator')
                source.tapped=tapped_at_occurrence
                before=len(session.state.players['B'].zones['hand'])
                self.enter_draw_step(session,'B')
                triggers=[item for item in session.state.stack if item.source_object_id==source.object_id]
                self.assertEqual(0 if tapped_at_occurrence else 1,len(triggers))
                if tapped_before_resolution and not tapped_at_occurrence:
                    session.state.players['A'].mana_pool.update({'C':1})
                    session.engine.permissions.invalidate_current()
                    session.state.pending_decision=None
                    session.engine._grant_priority('B')
                    session.engine.pump()
                    self.checkpoint(session)
                    for _ in range(5):
                        if session.state.priority_player=='A':break
                        accepted=session.act(session.pending_principals()[0],{'action_id':'pass'})
                        self.assertTrue(accepted.ok,accepted.summary)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    action=next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
                    accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary)
                    response_id=session.state.stack[-1].stack_id
                    while any(item.stack_id==response_id for item in session.state.stack):
                        accepted=session.act(session.pending_principals()[0],{'action_id':'pass'})
                        self.assertTrue(accepted.ok,accepted.summary)
                    source=session.engine.state.cards[source.object_id]
                    self.assertTrue(source.tapped)
                self.resolve(session)
                self.assertEqual(before+1+int(not tapped_at_occurrence and not tapped_before_resolution),
                                 len(session.state.players['B'].zones['hand']))
                self.replay(session)

    def test_original_howling_mine_rechecks_departure_tap_status_through_loaded_replay(self):
        self.assert_admission("Howling Mine")
        for name, version, tap_first, extra_draw in (
            ("untapped departure", 2, False, True),
            ("tapped departure", 2, True, False),
            ("version one", 1, False, False),
        ):
            with self.subTest(name=name):
                session = self.session(346240 + version + int(tap_first) * 2)
                session.state.ability_source_information_version = version
                mine = self.add(session.engine, "Howling Mine")
                removal = self.add(session.engine, "Disenchant", zone="hand")
                tapper = self.add(session.engine, "Icy Manipulator")
                session.state.players["A"].mana_pool.update({"W": 1, "C": 1 + int(tap_first)})
                before = len(session.state.players["B"].zones["hand"])
                self.enter_draw_step(session, "B")
                # Draw-step priority empties mana from the previous step.
                session.state.players["A"].mana_pool.update({"W": 1, "C": 1 + int(tap_first)})
                self.checkpoint(session)
                trigger, = [item for item in session.state.stack if item.source_object_id == mine.object_id]
                responses = [f"cast:{removal.ref}"]
                if tap_first:
                    responses.insert(0, f"activate:{tapper.ref}:")
                for action_prefix in responses:
                    for _ in range(4):
                        if session.state.priority_player == "A":
                            break
                        result = session.act(session.pending_principals()[0], {"action_id": "pass"})
                        self.assertTrue(result.ok, result.summary)
                    self.assertEqual("A", session.state.priority_player)
                    actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    action = next(row for row in actions if row["id"].startswith(action_prefix))
                    result = session.act("pilot:A", {"action_id": action["id"], "targets": [mine.ref], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    response = session.state.stack[-1].stack_id
                    for _ in range(4):
                        result = session.act(session.pending_principals()[0], {"action_id": "pass"})
                        self.assertTrue(result.ok, result.summary)
                        if all(item.stack_id != response for item in session.state.stack):
                            break
                    self.assertEqual([trigger.stack_id], [item.stack_id for item in session.state.stack])
                    if action_prefix.startswith("activate:"):
                        self.assertTrue(session.state.cards[mine.object_id].tapped)
                self.assertEqual("graveyard", session.state.cards[mine.object_id].zone)
                resumed = self.replay(session, load=True)
                self.resolve(resumed)
                self.assertEqual(before + 1 + int(extra_draw), len(resumed.state.players["B"].zones["hand"]))
                self.replay(resumed, load=True)

    def test_additional_draw_active_player_binding_mutant_is_killed(self):
        from quorune.semantic_runtime.values import resolve_semantic_value

        def substitute(host,value,item):
            return item.controller if value=='$context.player' else resolve_semantic_value(host,value,item)
        with patch('quorune.semantic_runtime.values.resolve_semantic_value',side_effect=substitute):
            with self.assertRaisesRegex(AssertionError,'additional draw recipient changed'):
                self.test_original_kami_draws_for_active_opponent_after_ordinary_draw()


class ScheduledPlayerTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'scheduled.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/scheduled-player-triggers.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scheduled player',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def enter_upkeep_from_untap(self,session,seat):
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.active_player=seat;session.state.started=True;session.state.phase='beginning';session.state.step='untap'
        session.state.phase_index=0;engine._grant_priority(seat);engine.pump()
        self.checkpoint(session)
        for _ in range(16):
            if session.state.step=='upkeep':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('upkeep',session.state.step)

    def resolve_upkeep_after_participant_leaves(self, name, *, seed, skipped_operation, controller_life):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.rules.scheduled_player_shapes import SCHEDULED_PLAYER_DEPARTURE_COVERAGE

        session = self.session(seed)
        source = self.add(session.engine, name)
        private = self.add(session.engine, 'Generic Bound Body', seat='B', zone='hand', ref='departing-hand-card')
        program = compile_best_available_card_program(
            self.db, self.db.lookup(name, fuzzy=False), semantic_registry=session.engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding)
        self.enter_upkeep_from_untap(session, 'B')
        trigger, = [item for item in session.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(('A', 'B', 'upkeep'),
                         (trigger.controller, trigger.context['player'], trigger.context['step']))
        self.assertIn(SCHEDULED_PLAYER_DEPARTURE_COVERAGE,
                      session.engine.semantics.get(trigger.semantic_key).coverage)

        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': 'concede'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(session.state.players['B'].in_game)
        self.assertEqual('B', session.state.active_player)
        self.assertIn(trigger.ref, {item.ref for item in session.state.stack})
        for viewer in 'ACD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))

        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertFalse(session.state.stack)
        self.assertEqual({'A': controller_life, 'B': 40, 'C': 40, 'D': 40},
                         {seat: player.life for seat, player in session.state.players.items()})
        skipped, = [event.details for event in session.state.events if event.code == 'effect.player.skipped']
        self.assertEqual({'stack': trigger.ref, 'player': 'B', 'operation': skipped_operation,
                          'reason': 'scheduled_player_departed'}, skipped)
        self.replay(session)

    def test_original_copper_tablet_resolves_when_the_upkeep_participant_concedes(self):
        self.resolve_upkeep_after_participant_leaves('Copper Tablet', seed=800_403_201,
                                                      skipped_operation='damage', controller_life=40)

    def test_departed_upkeep_recipient_does_not_cancel_the_surviving_controller_result(self):
        self.resolve_upkeep_after_participant_leaves('Generic Mixed Opponent Upkeep Result', seed=800_403_202,
                                                      skipped_operation='draw', controller_life=41)

    def test_mixed_upkeep_resolves_draw_and_locked_controller_life_privately(self):
        self.mixed_upkeep(change_controller=True)

    def test_mixed_upkeep_replays_from_before_triggering_step(self):
        self.mixed_upkeep(change_controller=False)

    def mixed_upkeep(self, *, change_controller):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        session = self.session(306020)
        engine = session.engine
        source = self.add(engine, 'Generic Mixed Opponent Upkeep Result')
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
            semantic_registry=engine.semantics, capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        hands = {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        self.enter_upkeep_from_untap(session, 'B')
        trigger = next(item for item in session.state.stack if item.source_object_id == source.object_id)
        self.assertEqual(('A', 'B'), (trigger.controller, trigger.context['player']))
        if change_controller:
            engine.change_control(source.object_id, 'C', reason='Response changes source controller')
            self.checkpoint(session)
        self.resolve(session)
        self.assertEqual({'A': 41, 'B': 40, 'C': 40, 'D': 40},
            {seat: session.state.players[seat].life for seat in 'ABCD'})
        self.assertEqual({**hands, 'B': hands['B'] + 1},
            {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'})
        private = session.state.cards[session.state.players['B'].zones['hand'][-1]].ref
        for seat in 'ACD':
            packet = session.packet('pilot:' + seat, full=True)
            self.assertNotIn(private, str(packet))
            self.assertEqual({'A': 41, 'B': 40, 'C': 40, 'D': 40},
                {seat: row['life'] for seat, row in packet['state']['players'].items()})
        self.replay(session, load=True)

    def test_actual_seizan_upkeep_affects_active_player_not_source_controller(self):
        session=self.session(306001);engine=session.engine
        source=self.add(engine,'Seizan, Perverter of Truth')
        hand=len(session.state.players['B'].zones['hand'])
        self.enter_upkeep_from_untap(session,'B')
        triggers=[i for i in session.state.stack if i.source_object_id==source.object_id]
        self.assertEqual(1,len(triggers));self.assertEqual('A',triggers[0].controller)
        self.assertEqual('B',triggers[0].context['player'])
        self.resolve(session)
        self.assertEqual(38,session.state.players['B'].life);self.assertEqual(40,session.state.players['A'].life)
        self.assertEqual(hand+2,len(session.state.players['B'].zones['hand']))
        private=session.state.cards[session.state.players['B'].zones['hand'][-1]].ref
        for seat in 'ACD':self.assertNotIn(private,str(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_each_opponent_upkeep_filter_excludes_source_controller(self):
        for seat,count in (('A',0),('B',1),('C',1),('D',1)):
            session=self.session(306002+ord(seat));source=self.add(session.engine,'Generic Opponent Upkeep Loss')
            self.enter_upkeep_from_untap(session,seat)
            self.assertEqual(count,sum(i.source_object_id==source.object_id for i in session.state.stack))
            self.resolve(session)
            self.assertEqual(40-count,session.state.players[seat].life);self.replay(session)

    def test_source_controller_substitution_mutant_is_killed_by_seizan(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        session=self.session(306010);source=self.add(session.engine,'Seizan, Perverter of Truth')
        self.enter_upkeep_from_untap(session,'B')
        def substitute(host,value,item):
            return item.controller if value=='$context.player' else resolve_semantic_value(host,value,item)
        with patch('quorune.semantic_runtime.values.resolve_semantic_value',side_effect=substitute):
            self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(38,session.state.players['B'].life)

    def test_actual_aura_upkeep_uses_recipient_controller_and_keeps_queued_player(self):
        session=self.session(306011);engine=session.engine
        victim=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Stab Wound',zone='hand')
        treason=self.add(engine,'Generic Scheduled Control Response',zone='hand')
        response_programs=engine.semantics.programs_for_oracle(treason.oracle_id)
        self.assertTrue(response_programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in response_programs))
        action=self.ready(session,aura,{'B':1,'R':1,'C':6});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);self.replay(session)
        self.enter_upkeep_from_untap(session,'B')
        triggers=[i for i in session.state.stack if i.source_object_id==aura.object_id]
        self.assertEqual(1,len(triggers));self.assertEqual('A',triggers[0].controller)
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        session.state.players['A'].mana_pool.update({'R':1,'C':2})
        engine.permissions.invalidate_current();session.state.pending_decision=None;engine._grant_priority('A');engine.pump();self.checkpoint(session)
        offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        matching=[a for a in offered if a.get('card')==treason.ref]
        self.assertTrue(matching,{'zone':session.state.cards[treason.object_id].zone,'actions':offered,'mana':session.state.players['A'].mana_pool,'phase':session.state.phase})
        action=matching[0]
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('A',session.state.cards[victim.object_id].controller)
        self.assertEqual(38,session.state.players['B'].life);self.assertEqual(40,session.state.players['A'].life)
        self.replay(session,load=True)

    def test_actual_copper_tablet_deals_damage_to_each_active_upkeep_player(self):
        session=self.session(306012);source=self.add(session.engine,'Copper Tablet')
        self.enter_upkeep_from_untap(session,'D')
        self.assertEqual(1,sum(i.source_object_id==source.object_id for i in session.state.stack))
        self.resolve(session)
        self.assertEqual(39,session.state.players['D'].life)
        self.assertEqual(40,session.state.players['A'].life)
        self.assertTrue(any(e.kind=='player_damaged' and e.target=='D' and e.amount==1 for e in session.state.turn_history.events))
        self.replay(session)




class ManaVaultTriggerTests(InheritedNativeFixture, unittest.TestCase):
    enter_draw_step = AdditionalDrawStepRuntimeTests.enter_draw_step

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "mana-vault.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "mana-vault-cards.json",
            fixtures / "targeted-tap-state-cards.json",
            fixtures / "announced-x-source-sacrifice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Source tap status on draw", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def vault(self, game, *, tapped=True, **other):
        source = self.add(game.engine, "Mana Vault", **other)
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        source.tapped = tapped
        return source

    def lands(self, game, seat, count, *, tapped=False):
        cards = tuple(self.add(game.engine, "Generic Bound Plains", seat=seat, ref=f"vault-{seat}-{i}")
                      for i in range(count))
        for card in cards:
            card.tapped = tapped
        return cards

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def offered_ability(self, game, source, *, actor="A"):
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:"))

    def vault_triggers(self, game, source, step):
        return [item for item in game.state.stack if item.source_object_id == source.object_id
                and item.context.get("event") == "step.begin" and item.context.get("step") == step]

    def damage(self, game, source):
        return [event.details for event in game.state.events
                if event.code == "effect.damage" and event.details.get("source") == source.ref]

    def test_original_vault_upkeep_pay_or_decline_after_real_untap_and_exact_draw_replay(self):
        from test_fixed_untap_participation import FixedUntapParticipationRuntimeTests as untap

        for index, paid in enumerate((False, True)):
            with self.subTest(paid=paid):
                game = self.session(603_450_110 + index)
                source = self.vault(game)
                foreign = self.vault(game, seat="B", ref="foreign-vault")
                lands = self.lands(game, "A", 4, tapped=True)
                untap.end_step(self, game, active="D")
                self.checkpoint(game)
                untap.next_turn(self, game, active="D")
                self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertTrue(all(not game.state.cards[land.object_id].tapped for land in lands))
                upkeep, = self.vault_triggers(game, source, "upkeep")
                self.assertEqual("A", upkeep.controller)
                self.assertFalse(self.vault_triggers(game, foreign, "upkeep"))

                self.until(game, lambda s: s.state.pending_decision is not None
                           and s.state.pending_decision.kind == "semantic.choice")
                self.assertEqual(["A"], game.state.pending_decision.actors)
                choice = game.packet("pilot:A", full=True)["decision"]["ctx"]
                self.assertEqual(4, choice["cost"]["GENERIC"])
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                game = self.replay(game, load=True)
                command = {"action_id": "choose", "pay": paid}
                self.reject(game, "B", command)
                self.reject(game, "A", {**command, "pay": "invalid"})
                self.act(game, "A", command)
                self.assertEqual(not paid, game.state.cards[source.object_id].tapped)
                self.assertTrue(all(game.state.cards[land.object_id].tapped == paid for land in lands))
                self.assertFalse(any(game.state.players["A"].mana_pool.values()))

                self.until(game, lambda s: s.state.active_player == "A" and s.state.step == "draw")
                self.assertFalse(self.vault_triggers(game, foreign, "draw"))
                self.assertEqual(int(not paid), len(self.vault_triggers(game, source, "draw")))
                self.assertEqual(40, game.state.players["A"].life)
                if paid:
                    action = self.offered_ability(game, source)
                    self.reject(game, "A", {"action_id": action, "mana_output": {"C": 2}})
                    self.act(game, "A", {"action_id": action})
                    self.assertEqual(3, game.state.players["A"].mana_pool["C"])
                    self.assertFalse(self.vault_triggers(game, source, "draw"))
                else:
                    self.resolve_top(game)
                self.assertEqual(40 if paid else 39, game.state.players["A"].life)
                self.assertEqual([40, 40, 40], [game.state.players[seat].life for seat in "BCD"])
                self.assertEqual(int(not paid), len(self.damage(game, source)))
                if not paid:
                    hit, = self.damage(game, source)
                    self.assertEqual(("A", 1, 1), (hit["target"], hit["amount"], hit["assigned_amount"]))
                self.replay(game, load=True)

    def test_original_vault_three_colorless_automatically_pays_an_actual_spell_and_replays(self):
        game = self.session(603_450_120)
        source = self.vault(game, tapped=False)
        spell = self.add(game.engine, "Darksteel Ingot", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertEqual([spell.object_id], [item.card_object_id for item in game.state.stack])
        self.assertFalse(self.damage(game, source))
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_original_vault_pending_draw_damage_rechecks_after_a_real_untap(self):
        game = self.session(603_450_130)
        source = self.vault(game)
        key = self.add(game.engine, "Voltaic Key")
        payer, = self.lands(game, "A", 1)
        self.assert_closed(game, key)
        self.enter_draw_step(game, "A")
        trigger, = self.vault_triggers(game, source, "draw")
        self.assertEqual("A", trigger.controller)
        game = self.replay(game, load=True)
        action = self.offered_ability(game, key)
        self.act(game, "A", {"action_id": action, "targets": [source.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertTrue(game.state.cards[payer.object_id].tapped)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual([trigger.ref], [item.ref for item in self.vault_triggers(game, source, "draw")])
        self.resolve_top(game)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertFalse(self.damage(game, source))
        self.replay(game, load=True)

    def test_original_vault_draw_damage_keeps_tapped_last_known_information_after_departure(self):
        game = self.session(603_450_140)
        source = self.vault(game)
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        payer = self.lands(game, "B", 2)
        self.assert_closed(game, removal)
        self.enter_draw_step(game, "A")
        trigger, = self.vault_triggers(game, source, "draw")
        self.cast_for(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in payer))
        pending, = self.vault_triggers(game, source, "draw")
        self.assertEqual(trigger.ref, pending.ref)
        self.assertTrue(pending.context["original_source_tap_status"]["departure_tapped"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        hit, = self.damage(game, source)
        self.assertEqual(("A", 1, 39), (hit["target"], hit["amount"], game.state.players["A"].life))
        self.replay(game, load=True)

    def test_vault_tapped_draw_binding_owns_only_the_exact_trigger_and_condition(self):
        from dataclasses import replace

        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.capabilities import CapabilityRegistry
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Mana Vault", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        damage, = [node for node in ir.faces[0].nodes if TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY in node.capability_dependencies]
        self.assertEqual(tapped_draw_damage_condition(), damage.event_condition)
        self.assertEqual(({"op": "damage", "source": "$source", "target": "$controller", "amount": 1},),
                         tuple(damage.effects))

        for old, new in (
            ("your draw step", "each player's draw step"),
            ("artifact is tapped", "artifact is untapped"),
            ("deals 1 damage to you", "deals 2 damage to you"),
            ("deals 1 damage to you", "deals 1 damage to each opponent"),
        ):
            with self.subTest(replacement=new):
                mutated = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)), **options)
                self.assertFalse(any(TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY in node.capability_dependencies
                                     for face in mutated.faces for node in face.nodes))
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in data["capabilities"] if row["id"] == TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY)
        capability.update(status="blocked", blockers=["tapped draw status withdrawn"])
        withdrawn = CapabilityRegistry(data)
        withdrawn.mark_evidence_verified("0" * 64)
        blocked = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
        self.assertEqual(3, sum(node.exact for node in blocked.faces[0].nodes))
        self.assertTrue(any(TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))

        report, semantics = register_card(self.db, self.registry, "Mana Vault")
        self.assertTrue(report["strict_capability_ready"], report)
        program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                    if TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY in p.capability_dependencies]
        for change in ({"event_condition": None},
                       {"effects": [{**program.effects[0], "amount": True}]}):
            with self.subTest(runtime=change):
                mutated = bind_semantic_program_runtime(replace(program, **change),
                    capability_registry=self.registry, profile="commander_review")
                self.assertFalse(mutated["strict"], mutated)

    def test_vault_native_witness_detects_omitted_live_tap_recheck(self):
        with patch("quorune.rules.trigger_source_information.recheck_original_source_tap", return_value=True) as omitted:
            with self.assertRaises(AssertionError):
                self.test_original_vault_pending_draw_damage_rechecks_after_a_real_untap()
        omitted.assert_called_once()


if __name__=='__main__':unittest.main()
