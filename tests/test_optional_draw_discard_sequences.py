from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
from test_controller_creature_keyword_absence import register_card
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class OptionalDrawDiscardCompilerTests(unittest.TestCase):
    def test_fixed_optional_draw_discard_compiles_original_order(self):
        text='Whenever you cast an instant or sorcery spell, you may draw a card. If you do, discard a card.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertEqual('offer_optional_effect',node.effects[0]['op'])
        self.assertEqual(['draw','choose_cards_apnap'],[e['op'] for e in node.effects[0]['effects']])
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_linked_dynamic_random_and_other_player_variants_stay_residual(self):
        for text in ('Whenever you cast a spell, you may draw X cards. If you do, discard X cards.',
                     'Whenever you cast a spell, you may draw a card. If you do, discard a card at random.',
                     'Whenever you cast a spell, target player may draw a card. If they do, discard a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)
        from quorune.compiler.fixed_controller_effect_sequences import fixed_controller_effect_clause
        from quorune.rules.optional_draw_discard import optional_draw_discard_is_closed
        effects=[fixed_controller_effect_clause(text)[0] for text in ('Draw a card.','Discard a card.')]
        self.assertTrue(optional_draw_discard_is_closed(effects,player='$controller'))
        self.assertFalse(optional_draw_discard_is_closed((effects[0],{**effects[1],'players':None}),player='$controller'))
        self.assertFalse(optional_draw_discard_is_closed(({**effects[0],'count':True},effects[1]),player='$controller'))


class OptionalDrawDiscardRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'optional-sequence.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/optional-draw-discard-sequences.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Optional draw discard',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def resolve_to_choice(self,session):
        for _ in range(64):
            pending=session.state.pending_decision
            if pending and pending.kind=='trigger.order':self.order_triggers(session);continue
            if pending and pending.kind!='priority':return pending
            if not session.state.stack:return None
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Optional sequence did not reach its decision')

    def test_actual_academy_decline_consumes_limit_without_draw_or_discard(self):
        session=self.session(310001);engine=session.engine
        source=self.add(engine,'Academy Wall');target=self.add(engine,'Generic Bound Body')
        spells=[self.add(engine,'Unsummon',zone='hand',ref='bounce-'+str(i)) for i in range(2)]
        action=self.ready(session,spells[0],{'U':2});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve_to_choice(session);self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        result=session.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve_to_choice(session));self.replay(session)
        # A second actual spell cannot trigger again after optional decline.
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spells[1].ref)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve_to_choice(session));self.replay(session)

    def test_actual_koi_acceptance_draws_before_private_discard_and_saves_replays(self):
        session=self.session(310002);engine=session.engine
        source=self.add(engine,'Skyswimmer Koi')
        artifact=self.add(engine,'Generic Optional Draw Artifact',zone='hand')
        action=self.ready(session,artifact,{});self.checkpoint(session)
        hand_ids=set(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve_to_choice(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        pending=self.resolve_to_choice(session);self.assertEqual('choice.apnap',pending.kind)
        new_id=next(i for i in session.state.players['A'].zones['hand'] if i not in hand_ids)
        new_ref=session.state.cards[new_id].ref
        for seat in 'BCD':self.assertNotIn(new_ref,str(session.packet('pilot:'+seat,full=True)))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':'choose','cards':[new_ref]});self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        resumed=self.replay(session,load=True)
        chosen=resumed.act('pilot:A',{'action_id':'choose','cards':[new_ref]});self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve_to_choice(resumed));self.assertEqual('graveyard',resumed.state.cards[new_id].zone);self.replay(resumed)

    def test_accepting_prevented_draw_still_discards_after_real_prior_draw(self):
        session=self.session(310003);engine=session.engine
        draw=self.add(engine,'Generic Optional Prior Draw',zone='hand')
        artifact=self.add(engine,'Generic Optional Draw Artifact',zone='hand')
        action=self.ready(session,draw,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve_to_choice(session));self.replay(session)
        self.add(engine,'Spirit of the Labyrinth');self.add(engine,'Skyswimmer Koi')
        action=self.ready(session,artifact,{});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve_to_choice(session).kind)
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('choice.apnap',self.resolve_to_choice(session).kind)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        chosen=session.state.cards[session.state.players['A'].zones['hand'][0]].ref
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[chosen]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve_to_choice(session))
        self.assertEqual(hand-2,len(session.state.players['A'].zones['hand']));self.replay(session)

    def test_omitted_followup_discard_mutant_is_killed_after_actual_acceptance(self):
        from quorune.semantic_choices.optional_effect import OptionalEffectHandler
        from dataclasses import replace
        original=OptionalEffectHandler.complete
        session=self.session(310004);engine=session.engine
        self.add(engine,'Skyswimmer Koi');artifact=self.add(engine,'Generic Optional Draw Artifact',zone='hand')
        action=self.ready(session,artifact,{});accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve_to_choice(session).kind)
        def omit(self,continuation,response,query):
            result=original(self,continuation,response,query)
            return replace(result,prepend_effects=result.prepend_effects[:1])
        with patch.object(OptionalEffectHandler,'complete',omit):
            accepted=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary)
            pending=self.resolve_to_choice(session)
            with self.assertRaises(AssertionError):self.assertIsNotNone(pending)


class EntryExemptDrawDiscardTests(InheritedNativeFixture, unittest.TestCase):
    PROFILE = "commander_review"
    SOURCE = "Moon-Circuit Hacker"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-exempt-draw-discard.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json",
            "optional-draw-discard-sequences.json",
            "discard-result-negative-offset-cards.json",
            "attached-exile-activation-cards.json",
            "committed-damage-dealer-optional-draw-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry-exempt draw and discard", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_whole_hacker_binds_its_exact_damage_source_and_fixed_private_instructions(self):
        card = self.db.lookup(self.SOURCE, fuzzy=False)
        self.assertEqual("7cd8b017-8d59-4b55-bb9a-8d8e492ef1ac", card.oracle_id)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=self.PROFILE)
        self.assertEqual(("exact", ()), (ir.status, ir.material_residuals))
        damage, = (node for node in ir.faces[0].nodes if node.event == "damage.dealt.self")
        self.assertEqual(card.oracle_text.splitlines()[1], card.oracle_text[damage.span.start:damage.span.end])
        ready, semantics = register_card(self.db, self.registry, self.SOURCE)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        self.assertEqual("capability_closed", ready["trust_basis"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual({"activate", "damage.dealt.self"}, {program.event for program in programs})
        program, = (program for program in programs if program.event == damage.event)
        wrapper, = program.effects
        self.assertEqual(["draw", "choose_cards_apnap"], [effect["op"] for effect in wrapper["effects"]])
        self.assertEqual("$controller", wrapper["player"])
        self.assertIn(PUBLIC_HISTORY_QUERY_CAPABILITY, program.capability_dependencies)

        dependencies = [cap for cap in program.capability_dependencies if cap != PUBLIC_HISTORY_QUERY_CAPABILITY]
        for mutant in (
            replace(program, event="damage.dealt"),
            replace(program, event_condition={"field": "combat", "op": "truthy", "value": True}),
            replace(program, effects=({**wrapper, "effects": wrapper["effects"][:1]},)),
            replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=self.PROFILE).to_dict()),
        ):
            with self.subTest(mutant=mutant):
                result = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=self.PROFILE)
                self.assertFalse(result["strict"], result)

        for original, changed in (
            ("this creature deals combat damage", "a creature you control deals combat damage"),
            ("to a player,", "to a planeswalker,"),
            ("a card unless this creature", "a card at random unless this creature"),
            ("unless this creature entered this turn", "unless another creature entered this turn"),
        ):
            with self.subTest(changed=changed):
                altered = replace(card, oracle_text=card.oracle_text.replace(original, changed))
                self.assertNotEqual("exact", compile_oracle_card(
                    altered, capability_registry=self.registry, capability_profile=self.PROFILE,
                ).status)

    def combat(self, seed, *, ninjutsu=False, response=None, dredge=False, restriction=False):
        game = self.session(seed)
        source = self.add(game.engine, self.SOURCE, zone="hand" if ninjutsu else "battlefield")
        attacker = self.add(game.engine, "Grizzly Bears") if ninjutsu else source
        attacker.acquired_control_turn_count = -1
        self.add(game.engine, "Grizzly Bears", seat="B", ref="optional-available-blocker")
        self.add(game.engine, "Generic Bound Plains", zone="hand", ref="optional-private-held")
        self.add(game.engine, "Island", ref="optional-blue-mana")
        reply = None
        if response == "Cloudshift":
            reply = self.add(game.engine, response, zone="hand")
            self.add(game.engine, "Generic Bound Plains", ref="optional-white-mana")
        elif response == "Unsummon":
            reply = self.add(game.engine, response, seat="C", zone="hand")
            self.add(game.engine, "Island", seat="C", ref="optional-bounce-mana")
        elif response == "Word of Seizing":
            reply = self.add(game.engine, response, seat="C", zone="hand")
            for index in range(5):
                self.add(game.engine, "Mountain", seat="C", ref=f"optional-theft-mana-{index}")
        if reply is not None:
            self.assert_closed(game, reply)
        dredger = self.add(game.engine, "Dakmor Salvage", zone="graveyard") if dredge else None
        if dredger:
            self.assert_closed(game, dredger)
        prior_draw = None
        if restriction:
            spirit = self.add(game.engine, "Spirit of the Labyrinth", seat="C")
            prior_draw = self.add(game.engine, "Generic Optional Prior Draw", zone="hand")
            self.assert_closed(game, spirit)
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        if prior_draw:
            self.cast_for(game, "A", prior_draw)
            self.assertIsNone(self.resolve(game))
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        self.assertTrue(game.act("pilot:B", {"action_id": "block", "blocks": {}}).ok)
        if ninjutsu:
            self.priority(game, "A")
            offer, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(f"activate:{source.ref}:"))
            command = {"action_id": offer["id"], "cost_objects": [attacker.ref], "pay": "auto"}
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:C", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            self.accept(game, "A", command)
            self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
            self.resolve_top(game)
            entered = game.state.cards[source.object_id]
            self.assertEqual(("battlefield", True, "B"), (entered.zone, entered.tapped, entered.attacking))
        self.until(game, lambda s: s.state.step == "combat_damage" and bool(s.state.stack))
        self.assertEqual(38, game.state.players["B"].life)
        trigger, = game.state.stack
        original = game.state.cards[source.object_id].logical_object_id
        self.assertEqual(("A", source.object_id, original), (
            trigger.controller, trigger.source_object_id, trigger.context["source_logical_object_id"],
        ))
        if reply:
            self.cast_for(game, reply.owner, reply, [game.state.cards[source.object_id].ref])
            self.resolve_top(game)
        return game, source, original, dredger

    def accept(self, game, seat, command):
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def apply_draw(self, game, hand):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(hand, set(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.accept(game, "A", command)
        return game

    def assert_private(self, game, ref):
        self.assertIn(json.dumps(ref), json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(json.dumps(ref), json.dumps(game.packet(principal, full=True)))

    def test_native_ninjutsu_entry_and_bounce_keep_the_original_turn_exemption(self):
        game, source, original, _ = self.combat(576801, ninjutsu=True, response="Unsummon")
        departed = game.state.cards[source.object_id]
        self.assertEqual("hand", departed.zone)
        self.assertNotEqual(original, departed.logical_object_id)
        hand = set(game.state.players["A"].zones["hand"])
        game = self.apply_draw(game, hand)
        self.assertIsNone(self.resolve(game))
        drawn, = set(game.state.players["A"].zones["hand"]) - hand
        self.assert_private(game, game.state.cards[drawn].ref)
        self.replay(game, load=True)

    def test_native_old_hacker_blinked_in_response_still_requires_private_discard(self):
        game, source, original, _ = self.combat(576802, response="Cloudshift")
        returned = game.state.cards[source.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(original, returned.logical_object_id)
        hand = set(game.state.players["A"].zones["hand"])
        game = self.apply_draw(game, hand)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (pending.kind, list(pending.actors)))
        drawn, = set(game.state.players["A"].zones["hand"]) - hand
        ref = game.state.cards[drawn].ref
        self.assert_private(game, ref)
        command = {"action_id": "choose", "cards": [ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.accept(game, "A", command)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[drawn].zone)
        self.assertEqual(hand, set(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_native_decline_after_control_changes_stays_with_trigger_controller(self):
        game, source, _, _ = self.combat(576803, response="Word of Seizing")
        self.assertEqual("C", game.state.cards[source.object_id].controller)
        hands = {seat: tuple(player.zones["hand"]) for seat, player in game.state.players.items()}
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        self.accept(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hands, {seat: tuple(player.zones["hand"]) for seat, player in game.state.players.items()})
        self.replay(game, load=True)

    def test_native_dredge_replaces_the_draw_and_retains_the_hand_discard(self):
        game, _, _, dredger = self.combat(576804, dredge=True)
        hand = set(game.state.players["A"].zones["hand"])
        library_count = len(game.state.players["A"].zones["library"])
        game = self.apply_draw(game, hand)
        self.assertEqual("draw.replacement", self.resolve(game).kind)
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        dredge, = (option for option in options if "Dredge" in option["label"])
        self.accept(game, "A", {"action_id": "choose", "choice": dredge["id"]})
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        self.assertEqual(library_count - 2, len(game.state.players["A"].zones["library"]))
        self.assertEqual({dredger.object_id}, set(game.state.players["A"].zones["hand"]) - hand)
        private_ref = "optional-private-held"
        self.assert_private(game, private_ref)
        game = self.replay(game, load=True)
        self.accept(game, "A", {"action_id": "choose", "cards": [private_ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards["fixture:" + private_ref].zone)
        self.replay(game, load=True)

    def test_native_draw_restriction_removes_the_optional_choice_and_discard(self):
        game, _, _, _ = self.combat(576805, restriction=True)
        hand = tuple(game.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand, tuple(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)


if __name__=='__main__':unittest.main()
