from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class CombinedTapCostCompilerTests(unittest.TestCase):
    def test_source_and_selected_tap_cost_excludes_same_source(self):
        text='{1}, {T}, Tap an untapped creature you control: Create a 1/1 green Saproling creature token.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertTrue(node.cost['tap_source'])
        self.assertEqual('$source',node.cost['choices'][0]['q']['exclude_ref'])
        self.assertIn('activation.selected_tap.fixed',node.capability_dependencies)
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))


class CombinedTapCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'combined-taps.sqlite3'
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/combined-tap-activation-costs.json",
            ROOT / "tests/fixtures/fixed-resolution-payment-cards.json",
        ], path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Combined tap',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_akoum_pays_current_discard_once_across_replacement_and_replay(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program

        for has_card in (False, True):
            with self.subTest(has_card=has_card):
                session = self.session(720020 + has_card)
                session.state.config.semantic_policy = "trusted_only"
                source = self.add(session.engine, "Akoum Flameseeker")
                source.acquired_control_turn_count = -1
                ally = self.add(session.engine, "Zulaport Chainmage")
                ally.acquired_control_turn_count = session.state.players["A"].turns_begun
                for object_id in tuple(session.state.players["A"].zones["hand"]):
                    session.engine.move_card(object_id, "library", log=False)
                program = compile_best_available_card_program(
                    self.db, self.db.lookup("Akoum Flameseeker"),
                    semantic_registry=session.engine.semantics, capability_registry=self.registry,
                    capability_profile="commander_review",
                )
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)
                if has_card:
                    selected = self.add(session.engine, "Generic Bound Plains", zone="hand", ref="akoum-private-cost")
                    self.add(session.engine, "Generic Payment Destination Replacement", seat="B")
                    self.add(session.engine, "Generic Payment Competing Replacement", seat="C")
                library = tuple(session.state.players["A"].zones["library"])
                action = self.ready(session, source, {})
                self.assertIn(ally.ref, action["cost_summary"]["choose_cost"][0]["legal_refs"])
                self.checkpoint(session)
                activated = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [ally.ref], "pay": "auto"})
                self.assertTrue(activated.ok, activated.summary)
                self.assertTrue(all(session.state.cards[card.object_id].tapped for card in (source, ally)))
                pending = self.resolve(session)
                if not has_card:
                    self.assertIsNone(pending)
                    self.assertEqual((), tuple(session.state.players["A"].zones["hand"]))
                    self.assertEqual(library, tuple(session.state.players["A"].zones["library"]))
                    self.replay(session, load=True)
                    continue

                self.assertEqual("semantic.choice", pending.kind)
                self.assertEqual(["pilot:A"], list(session.pending_principals()))
                choice = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]
                self.assertEqual([selected.ref], choice["legal_refs"])
                self.assertEqual((1, 1, False),
                                 (choice.get("minimum", 1), choice.get("maximum", 1), choice.get("optional", False)))
                for seat in "BCD":
                    self.assertNotIn(selected.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
                before = authoritative_state_hash(session.state)
                pay = {"action_id": "choose", "cards": [selected.ref]}
                self.assertFalse(session.act("pilot:B", pay).ok)
                self.assertFalse(session.act("pilot:A", {"action_id": "choose", "cards": []}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                paid = session.act("pilot:A", pay)
                self.assertTrue(paid.ok, paid.summary)
                self.assertIn("replacement", session.state.pending_decision.kind)
                self.assertEqual("hand", session.state.cards[selected.object_id].zone)
                self.assertEqual(library, tuple(session.state.players["A"].zones["library"]))
                session = self.replay(session, load=True)
                choice = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
                replaced = session.act("pilot:A", {"action_id": "choose", "replacement": choice})
                self.assertTrue(replaced.ok, replaced.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual("exile", session.state.cards[selected.object_id].zone)
                self.assertEqual(1, len(session.state.players["A"].zones["hand"]))
                self.assertEqual(len(library) - 1, len(session.state.players["A"].zones["library"]))
                self.replay(session, load=True)

    def test_actual_evangel_requires_distinct_taps_and_allows_new_selected_creature(self):
        session=self.session(308001);engine=session.engine
        source=self.add(engine,'Selesnya Evangel');source.acquired_control_turn_count=-1
        selected=self.add(engine,'Generic Bound Body');selected.acquired_control_turn_count=session.state.players['A'].turns_begun
        enemy=self.add(engine,'Generic Bound Body',seat='B',ref='enemy')
        action=self.ready(session,source,{'C':1});self.checkpoint(session)
        legal=action['cost_summary']['choose_cost'][0]['legal_refs']
        self.assertIn(selected.ref,legal);self.assertNotIn(source.ref,legal);self.assertNotIn(enemy.ref,legal)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[source.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[selected.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertTrue(session.state.cards[selected.object_id].tapped)
        self.resolve(session)
        self.assertEqual(1,sum(c.is_token and c.zone=='battlefield' for c in session.state.cards.values()))
        self.replay(session,load=True)

    def test_new_source_is_unavailable_even_with_new_eligible_selected_creature(self):
        session=self.session(308002);engine=session.engine
        source=self.add(engine,'Selesnya Evangel');source.acquired_control_turn_count=session.state.players['A'].turns_begun
        self.add(engine,'Generic Bound Body')
        engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.active_player='A';session.state.started=True;session.state.phase='precombat_main';session.state.step='main'
        session.state.players['A'].mana_pool['C']=1;engine._grant_priority('A');engine.pump()
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('source')==source.ref for a in actions))

    def test_cost_tap_events_observe_complete_source_and_selected_group(self):
        session=self.session(308003);engine=session.engine
        source=self.add(engine,'Selesnya Evangel');source.acquired_control_turn_count=-1
        selected=self.add(engine,'Generic Bound Body')
        action=self.ready(session,source,{'C':1});self.checkpoint(session)
        seen=[]
        original=engine._dispatch_semantic_event
        def observe(event,context,**kwargs):
            if event=='permanent.tap':seen.append((context['card'],session.state.cards[source.object_id].tapped,session.state.cards[selected.object_id].tapped))
            return original(event,context,**kwargs)
        with patch.object(engine,'_dispatch_semantic_event',side_effect=observe):
            accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[selected.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual({source.ref,selected.ref},{r[0] for r in seen})
        self.assertTrue(all(first and second for _,first,second in seen))
        self.resolve(session);self.replay(session)

    def test_omitted_source_tap_mutant_is_killed_by_actual_evangel_activation(self):
        session=self.session(308004);engine=session.engine
        source=self.add(engine,'Selesnya Evangel');source.acquired_control_turn_count=-1
        selected=self.add(engine,'Generic Bound Body')
        action=self.ready(session,source,{'C':1})
        from quorune.rules.activation_costs import pay_fixed_tap_cost
        def omit(host,**kwargs):
            kwargs['tap_source']=False
            return pay_fixed_tap_cost(host,**kwargs)
        with patch('quorune.rules.activation.commit.pay_fixed_tap_cost',side_effect=omit):
            accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[selected.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            with self.assertRaises(AssertionError):self.assertTrue(session.state.cards[source.object_id].tapped)

    def test_actual_chaplain_rejects_duplicate_or_stale_taps_before_source_mutation(self):
        session=self.session(308005);engine=session.engine
        source=self.add(engine,'Devout Chaplain');source.acquired_control_turn_count=-1
        first=self.add(engine,'Generic Bound Body',ref='human-one')
        second=self.add(engine,'Generic Bound Body',ref='human-two')
        target=self.add(engine,'Generic Combined Tap Artifact',seat='B')
        action=self.ready(session,source,{});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[first.ref,first.ref],'targets':[target.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[first.ref,second.ref],'targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (source,first,second)))
        self.resolve(session);self.assertEqual('exile',session.state.cards[target.object_id].zone);self.replay(session)


if __name__=='__main__':unittest.main()
