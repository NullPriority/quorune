from __future__ import annotations

"""Pinned CR 401.2/608.2/701.20e: viewer and library owner are distinct."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
import test_attachment_followup_bindings as attachment_controls

FIXTURE = ROOT / "tests/fixtures/target-library-inspection-cards.json"
CANDIDATES = ("Merfolk Observer", "Dewdrop Spy", "Saheeli's Silverwing", "Orcish Spy", "Rootwater Mystic",
    "Draugr Thought-Thief", "Eye Spy", "Lurking Informant", "Wu Spy", "Mishra's Bauble", "Thoughtpicker Witch", "Moonring Island")
EXCLUDED = ("Architects of Will", "Elemental Augury", "Natural Selection", "Portent", "Dimir Charm",
    "Dimir Machinations", "Precognition", "Jace, the Mind Sculptor", "Siphon Insight", "Petty Larceny", "Wand of Denial")


class TargetLibraryInspectionModelTests(unittest.TestCase):
    def context(self, mode="look", count=2):
        from types import SimpleNamespace
        from quorune.object_query import ObjectQueryResult
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        from quorune.replacement.immutable import FrozenMap
        from quorune.target_library_inspection_model import TargetLibraryInspectionSpec
        rows = tuple(ObjectQueryResult("object-"+ref,ref,"Secret "+ref,"B","B","library",
            logical_object_id="logical-"+ref) for ref in ("lower","middle","top"))
        query = SnapshotSemanticChoiceQuery(seat_order=tuple("ABCD"),active_order=tuple("ABCD"),object_rows=rows,
            libraries_by_seat=FrozenMap({"B":("lower","middle","top"),"A":()}))
        context = SimpleNamespace(actor="A",stack_controller="A",stack_ref="stack-original",stack_label="Original look",query=query)
        return TargetLibraryInspectionSpec(count,mode).effect(player="A",target="B"),context

    def test_closed_inspection_codec_keeps_viewer_separate_from_targeted_owner(self):
        from quorune.target_library_inspection_model import TargetLibraryInspectionSpec, decode_target_library_inspection
        from quorune.replacement.immutable import FrozenMap
        for count, mode in ((1,"look"),(3,"look"),(1,"may_graveyard"),(2,"one_graveyard"),(2,"one_exile")):
            for opponent in (False, True):
                spec = TargetLibraryInspectionSpec(count, mode, opponent)
                self.assertEqual(spec, decode_target_library_inspection(FrozenMap(spec.effect())))
                self.assertEqual("$controller", spec.effect()["player"])
                self.assertEqual("$target.0", spec.effect()["target"])
                self.assertEqual(spec, TargetLibraryInspectionSpec.from_dict(FrozenMap(spec.to_dict())))
        for args in ((0,"look",False),(4,"look",False),(True,"look",False),(2,"may_graveyard",False),
                     (1,"reorder",False),(1,"look",1)):
            with self.subTest(args=args), self.assertRaises(ValueError):TargetLibraryInspectionSpec(*args)

    def test_literal_grammar_has_exact_fixed_counts_and_single_card_destinations(self):
        from quorune.compiler.target_library_inspection_templates import target_library_inspection_template
        for body, count, mode, opponent in (
            ("Look at the top card of target player's library.",1,"look",False),
            ("Look at the top three cards of target player's library.",3,"look",False),
            ("Look at the top card of target opponent's library.",1,"look",True),
            ("Look at the top card of target player's library. You may put that card into their graveyard.",1,"may_graveyard",False),
            ("Look at the top two cards of target player's library. Put one of them into their graveyard.",2,"one_graveyard",False),
            ("Look at the top two cards of target opponent's library, then exile one of them.",2,"one_exile",True),
        ):
            value = target_library_inspection_template(body)
            self.assertIsNotNone(value, body)
            effect = value.compiled()[1][0]
            self.assertEqual({"schema_version":1,"count":count,"mode":mode,"opponent_only":opponent}, effect["inspection"])
        for body in (
            "Look at the top X cards of target player's library.",
            "Look at the top three cards of target player's library, then put them back in any order.",
            "Look at the top three cards of target player's library. Put one back and the rest into that player's graveyard.",
            "Look at the top card of target player's library. If it's a nonland card, you may pay 2 life. If you do, put it into that player's graveyard.",
        ):
            with self.subTest(body=body):self.assertIsNone(target_library_inspection_template(body))

    def test_materialized_window_is_target_owned_top_only_and_viewer_is_actor(self):
        from types import SimpleNamespace
        from quorune.rules.target_library_inspection import scoped_target_library_inspection_refs, scoped_target_library_inspection_libraries
        from quorune.semantic_choices.target_library_inspection import TargetLibraryInspectionHandler
        effect,context = self.context()
        cards = {name:SimpleNamespace(ref=name) for name in ("lower","middle","top")}
        host = SimpleNamespace(active_seats=tuple("ABCD"),state=SimpleNamespace(cards=cards,
            players={"B":SimpleNamespace(zones={"library":["lower","middle","top"]})}))
        self.assertEqual(("top","middle"),scoped_target_library_inspection_refs(host,"A",effect))
        self.assertEqual({"B": ("middle", "top")}, scoped_target_library_inspection_libraries(host,"A",effect))
        self.assertEqual((),scoped_target_library_inspection_refs(host,"A",{"op":"search"}))
        preparation = TargetLibraryInspectionHandler().prepare(effect,context)
        intent, = preparation.preparation_intents
        self.assertEqual(("A","B","A",False),(intent.actor,intent.player,intent.viewer,intent.public))
        self.assertEqual(("top","middle"),intent.refs_top_first)
        self.assertEqual(["top","middle"],[row["id"] for row in preparation.request.payload()["objects"]])

    def test_completion_revalidates_order_and_original_incarnations_before_one_move(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.target_library_inspection import TargetLibraryInspectionHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.replacement.immutable import FrozenMap
        effect,context = self.context("one_graveyard")
        handler = TargetLibraryInspectionHandler();preparation = handler.prepare(effect,context)
        continuation = SimpleNamespace(effect=preparation.continuation_effect)
        result = handler.complete(continuation,{"cards":["middle"]},context.query)
        intent, = result.intents
        self.assertEqual(("A","middle",("library",),"graveyard"),
            (intent.actor,intent.object_ref,intent.expected_zones,intent.destination))
        self.assertFalse(intent.owned_only)
        changed = replace(context.query,libraries_by_seat=FrozenMap({"B":("lower","top","middle")}))
        with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{"cards":["middle"]},changed)
        rows = tuple(replace(row,logical_object_id="new") if row.ref=="middle" else row for row in context.query.object_rows)
        with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{"cards":["middle"]},replace(context.query,object_rows=rows))
        for selected in ([],["lower"],["top","middle"]):
            with self.subTest(selected=selected), self.assertRaises(SemanticChoiceError):handler.complete(continuation,{"cards":selected},context.query)


class TargetLibraryInspectionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry = load_default_capability_registry()

    def test_target_role_and_full_source_span_preserve_look_and_followup(self):
        for text, opponent in (("Look at the top card of target player's library.",False),
            ("Look at the top card of target opponent's library.",True),
            ("Look at the top card of target player's library. You may put that card into their graveyard.",False)):
            ir = compile_oracle_card(witnesses.record(text), capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(1, node.target_schema["count"])
            self.assertEqual(["player"], node.target_schema["categories"])
            self.assertEqual("opponent" if opponent else "any", node.target_schema.get("player_relation", "any"))
            self.assertEqual("inspect_target_library", node.effects[0]["op"])
            self.assertEqual(text[node.span.start:node.span.end], node.text)

    def test_look_is_not_replaced_by_search_and_new_authority_is_required(self):
        from quorune.rules.capabilities import CapabilityRegistry
        text = "Look at the top card of target player's library."
        for missing in ("library.inspect.fixed_target_player", "target.revalidate_resolution"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(r for r in raw["capabilities"] if r["id"] == missing).update(status="blocked", blockers=["inspection authority withdrawn"])
            registry = CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            ir = compile_oracle_card(witnesses.record(text), capability_registry=registry, capability_profile="commander_review")
            with self.subTest(missing=missing):self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.target_library_inspection_templates.target_library_inspection_template", return_value=None):
            ir = compile_oracle_card(witnesses.record(text), capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)

    def test_bauble_retains_canonical_delayed_draw_under_the_original_controller(self):
        from quorune.compiler.target_library_inspection_templates import target_library_inspection_template
        from quorune.compiler.delayed_draw_templates import fixed_next_turn_upkeep_draw_effect
        from quorune.rules.target_library_inspection import target_library_inspection_node_capabilities
        text = "Look at the top card of target player's library. Draw a card at the beginning of the next turn's upkeep."
        value = target_library_inspection_template(text);self.assertIsNotNone(value)
        _name, effects, schema, mechanics = value.compiled()
        self.assertEqual(fixed_next_turn_upkeep_draw_effect(), effects[1])
        self.assertIn("trigger.delayed.fixed_next_turn_draw", target_library_inspection_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics))
        changed = {**effects[0], "player": "$target.0"}
        self.assertFalse(target_library_inspection_node_capabilities(effects=(changed,), target_schema=schema, mechanic_ids=mechanics))


class TargetLibraryInspectionRegistrationTests(unittest.TestCase):
    def test_complete_original_cards_keep_all_unowned_siblings(self):
        registry = load_default_capability_registry();semantics = SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original-target-library-cards.sqlite3"
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES, *EXCLUDED):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level="provisional", capability_registry=registry,
                            capability_profile="commander_review", promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=registry, capability_profile="commander_review")
                        bound = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                        self.assertEqual(name in CANDIDATES, bool(bound["strict_capability_ready"]
                            and bound["trust_basis"] == "capability_closed"), bound["blockers"])


class TargetLibraryInspectionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = attachment_controls.AttachmentFollowupRuntimeTests.session
    add = attachment_controls.AttachmentFollowupRuntimeTests.add
    prepare = attachment_controls.AttachmentFollowupRuntimeTests.prepare
    closed = attachment_controls.AttachmentFollowupRuntimeTests.closed
    actions = attachment_controls.AttachmentFollowupRuntimeTests.actions
    cast_offered = attachment_controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint = attachment_controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = attachment_controls.AttachmentFollowupRuntimeTests.resolve
    replay = attachment_controls.AttachmentFollowupRuntimeTests.replay
    advance = attachment_controls.AttachmentFollowupRuntimeTests.advance
    until = attachment_controls.AttachmentFollowupRuntimeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / "target-library.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", FIXTURE], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private target library witnesses", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def seed_library(self, session, target, names):
        engine = session.engine
        for oid in tuple(session.state.players[target].zones["library"]):engine.move_card(oid, "exile", log=False)
        cards = [self.add(engine, name, seat=target, zone="library", ref=f"inspected-{target}-{i}")
                 for i, name in enumerate(names)]
        for card in cards:card.known_to = [];card.revealed_to = []
        return cards

    def world(self, seed, name, *, activated=False, target="B", cards=("Sol Ring","Ornithopter","Llanowar Elves","Thran Dynamo"), controls=()):
        session = self.session(seed);source = self.add(session.engine, name, zone="battlefield" if activated else "hand")
        looked = self.seed_library(session, target, cards)
        extras = [self.add(session.engine, control, seat=seat, ref=f"inspection-control-{i}") for i,(control,seat) in enumerate(controls)]
        self.prepare(session, source, {color:20 for color in ("C","W","U","B","R","G")})
        return session, source, looked, extras

    def top(self, session, target, count):
        return tuple(session.state.cards[oid].ref for oid in reversed(session.state.players[target].zones["library"]))[:count]

    def packet(self, session):return session.packet("pilot:A", full=True)["decision"]["ctx"]

    def start(self, session, source, *, target="B", activated=False, cost_objects=()):
        record = self.db.by_oracle_id(source.oracle_id)
        if activated:
            row = next(r for r in self.actions(session) if r["id"].startswith("activate:"+source.ref+":") and r.get("target_schema"))
            command = {"action_id":row["id"],"targets":[target],"pay":"auto"}
            if cost_objects:command["cost_objects"] = list(cost_objects)
            result = session.act("pilot:A", command);self.assertTrue(result.ok, result.summary)
        else:self.cast_offered(session, source, targets=(target,) if record.is_instant or record.is_sorcery else ())
        decision = self.resolve(session)
        if decision is not None and decision.kind == "semantic.target":
            result = session.act("pilot:A", {"action_id":"choose","targets":[target]});self.assertTrue(result.ok,result.summary)
            decision = self.resolve(session)
        return decision

    def reject(self, session, principal, command):
        before = authoritative_state_hash(session.state);result = session.act(principal, command)
        self.assertFalse(result.ok, result.summary);self.assertEqual(before, authoritative_state_hash(session.state))

    def finish(self, session, command):
        result = session.act("pilot:A", {"action_id":"choose",**command});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def assert_private_window(self, session, looked_refs, all_hidden):
        self.assertEqual(looked_refs, tuple(row["id"] for row in self.packet(session)["objects"]))
        for seat in "BCD":
            payload = str(session.packet("pilot:"+seat, full=True))
            for ref in all_hidden:self.assertNotIn(ref,payload)
        for card in session.state.cards.values():
            if card.ref in looked_refs:self.assertEqual(["A"],card.known_to)

    def test_original_enter_look_sources_keep_identity_order_and_viewer_private(self):
        for i,name in enumerate(("Merfolk Observer","Dewdrop Spy","Saheeli's Silverwing")):
            with self.subTest(name=name):
                session, source, cards, _ = self.world(297100+i,name)
                self.checkpoint(session);before = tuple(session.state.players["B"].zones["library"])
                expected = self.top(session,"B",1);decision = self.start(session,source)
                self.assertEqual("semantic.choice",decision.kind)
                self.assert_private_window(session,expected,[card.ref for card in cards])
                self.reject(session,"pilot:B",{"action_id":"choose","choice":"continue"})
                session = self.replay(session,load=True);self.finish(session,{"choice":"continue"})
                self.assertEqual(before,tuple(session.state.players["B"].zones["library"]))
                self.replay(session,load=True)

    def test_opponent_only_target_rejects_self_and_any_player_allows_self(self):
        session, source, _, _ = self.world(297110,"Saheeli's Silverwing")
        self.checkpoint(session);self.cast_offered(session,source)
        self.assertEqual("semantic.target",self.resolve(session).kind)
        self.reject(session,"pilot:A",{"action_id":"choose","targets":["A"]})
        result = session.act("pilot:A",{"action_id":"choose","targets":["B"]});self.assertTrue(result.ok,result.summary)
        self.assertEqual("semantic.choice",self.resolve(session).kind);self.finish(session,{"choice":"continue"})
        session, source, _, _ = self.world(297111,"Merfolk Observer",target="A")
        self.checkpoint(session);self.assertEqual("semantic.choice",self.start(session,source,target="A").kind)
        self.finish(session,{"choice":"continue"});self.replay(session,load=True)

    def test_fixed_top_window_clips_empty_and_short_libraries_without_reordering(self):
        cases = (("Orcish Spy", (), 3), ("Orcish Spy", ("Sol Ring",), 3),
            ("Orcish Spy", ("Sol Ring", "Ornithopter"), 3),
            ("Rootwater Mystic", ("Sol Ring", "Ornithopter"), 1))
        for i,(name,names,count) in enumerate(cases):
            with self.subTest(name=name,count=len(names)):
                session, source, cards, _ = self.world(297120+i,name,activated=True,cards=names)
                self.checkpoint(session);expected = self.top(session,"B",count);before = tuple(session.state.players["B"].zones["library"])
                decision = self.start(session,source,activated=True)
                if not names:self.assertIsNone(decision)
                else:
                    self.assertEqual("semantic.choice",decision.kind)
                    self.assert_private_window(session,expected,[card.ref for card in cards]);self.finish(session,{"choice":"continue"})
                self.assertEqual(before,tuple(session.state.players["B"].zones["library"]));self.replay(session,load=True)

    def test_optional_graveyard_modes_keep_or_move_only_original_top_under_owner(self):
        cases = (("Draugr Thought-Thief",False,"keep"),("Eye Spy",False,"graveyard"),("Lurking Informant",True,"graveyard"))
        for i,(name,activated,choice) in enumerate(cases):
            with self.subTest(name=name,choice=choice):
                session, source, cards, _ = self.world(297130+i,name,activated=activated)
                self.checkpoint(session);top = self.top(session,"B",1)[0];shuffles = session.state.players["B"].stats.get("shuffle_count",0)
                self.assertEqual("semantic.choice",self.start(session,source,activated=activated).kind)
                self.reject(session,"pilot:A",{"action_id":"choose","choice":"exile"})
                session = self.replay(session,load=True);self.finish(session,{"choice":choice})
                card = next(c for c in session.state.cards.values() if c.ref==top)
                self.assertEqual("graveyard" if choice=="graveyard" else "library",card.zone)
                self.assertEqual("B",card.owner);self.assertEqual(shuffles,session.state.players["B"].stats.get("shuffle_count",0))
                self.replay(session,load=True)

    def test_wu_spy_selects_exactly_one_from_current_top_two_and_keeps_other_order(self):
        session, source, cards, _ = self.world(297140,"Wu Spy")
        self.checkpoint(session);top = self.top(session,"B",2);outside = self.top(session,"B",4)[2]
        self.assertEqual("semantic.choice",self.start(session,source).kind)
        self.assert_private_window(session,top,[card.ref for card in cards])
        for selected in ([],[outside],list(top),[top[0],top[0]]):
            self.reject(session,"pilot:A",{"action_id":"choose","cards":selected})
        self.finish(session,{"cards":[top[1]]})
        self.assertEqual(top[0],self.top(session,"B",1)[0])
        self.assertEqual("graveyard",next(c for c in session.state.cards.values() if c.ref==top[1]).zone);self.replay(session,load=True)

    def test_graveyard_move_retains_the_real_destination_replacement_owner(self):
        """The zone pipeline keeps the affected owner's later commander choice."""
        session, source, cards, _ = self.world(
            297145, "Eye Spy", cards=("Sol Ring", "Ornithopter", "Generic Bound Commander"))
        found = cards[-1]
        for card in session.state.cards.values():
            if card.owner == "B" and card.is_commander:
                card.is_commander = False
                card.commander_designation_id = None
        found.is_commander = True
        found.commander_designation_id = "commander:" + found.object_id
        session.state.commander_oracle_ids["B"] = [found.oracle_id]
        self.closed(session, found)
        self.checkpoint(session)
        top = self.top(session, "B", 2)
        self.assertEqual("semantic.choice", self.start(session, source).kind)
        self.assert_private_window(session, top[:1], [card.ref for card in cards])
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "graveyard"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("state.commander_zone", self.resolve(session).kind)
        moved = next(card for card in session.state.cards.values() if card.ref == top[0])
        self.assertEqual(("B", "graveyard"), (moved.owner, moved.zone))
        self.assertEqual(["pilot:B"], list(session.pending_principals()))
        self.reject(session, "pilot:A", {"action_id": "command", "choice": "command"})
        session = self.replay(session, load=True)
        result = session.act("pilot:B", {"action_id": "command", "choice": "command"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("command", session.state.cards[moved.object_id].zone)
        self.assertEqual(top[1], self.top(session, "B", 1)[0])
        self.replay(session, load=True)

    def test_thoughtpicker_pays_actual_creature_cost_then_exiles_one_looked_card(self):
        session, source, cards, _ = self.world(297150,"Thoughtpicker Witch",activated=True)
        paid = self.add(session.engine,"Ornithopter",ref="inspection-paid-creature")
        self.prepare(session,source,{"C":10,"B":5});self.checkpoint(session);top = self.top(session,"B",2)
        self.assertEqual("semantic.choice",self.start(session,source,activated=True,cost_objects=(paid.ref,)).kind)
        self.assertEqual("graveyard",session.state.cards[paid.object_id].zone)
        self.finish(session,{"cards":[top[0]]})
        result = next(c for c in cards if c.ref==top[0]);self.assertEqual("exile",result.zone);self.assertEqual("B",result.owner)
        self.assertEqual(top[1],self.top(session,"B",1)[0]);self.replay(session,load=True)

    def test_inspection_is_not_search_and_ignores_search_prohibitions_and_windows(self):
        session, source, cards, extras = self.world(297160,"Orcish Spy",activated=True,controls=(("Mindlock Orb","B"),("Aven Mindcensor","B")))
        for card in extras:self.closed(session,card)
        self.checkpoint(session);expected = self.top(session,"B",3)
        self.assertEqual("semantic.choice",self.start(session,source,activated=True).kind)
        self.assert_private_window(session,expected,[card.ref for card in cards]);self.finish(session,{"choice":"continue"})
        self.assertFalse(any(e.code in {"library.search","library.search.prohibited"} for e in session.state.events))
        self.replay(session,load=True)

    def test_actual_mill_response_changes_the_inspected_top_before_resolution(self):
        session, source, cards, _ = self.world(297170,"Eye Spy")
        response = self.add(session.engine,"Thought Scour",zone="hand")
        self.prepare(session,source,{"C":10,"U":10});self.closed(session,response);self.checkpoint(session)
        old = self.top(session,"B",2);expected = self.top(session,"B",3)[2]
        self.cast_offered(session,source,targets=("B",));self.cast_offered(session,response,targets=("B",))
        self.assertEqual("semantic.choice",self.resolve(session).kind)
        self.assertEqual([expected],[r["id"] for r in self.packet(session)["objects"]])
        for ref in old:self.assertEqual("graveyard",next(c for c in cards if c.ref==ref).zone)
        self.finish(session,{"choice":"keep"});self.replay(session,load=True)

    def test_mishras_bauble_sacrifices_source_and_defers_the_actors_draw(self):
        session, source, _, _ = self.world(297180,"Mishra's Bauble",activated=True)
        self.checkpoint(session);a = len(session.state.players["A"].draw_history);b = len(session.state.players["B"].draw_history)
        self.assertEqual("semantic.choice",self.start(session,source,activated=True).kind)
        self.assertEqual("graveyard",session.state.cards[source.object_id].zone)
        self.finish(session,{"choice":"continue"});self.assertEqual(a,len(session.state.players["A"].draw_history))
        session = self.replay(session,load=True)
        self.advance(session,lambda current:current.state.active_player!="A" and len(current.state.players["A"].draw_history)==a+1)
        self.assertEqual(b,len(session.state.players["B"].draw_history));self.replay(session,load=True)

    def test_player_hexproof_response_prevents_inspection_and_no_knowledge_leaks(self):
        session, source, cards, extras = self.world(297190,"Eye Spy",controls=(("Vedalken Orrery","B"),))
        aegis = self.add(session.engine,"Aegis of the Gods",seat="B",zone="hand")
        session.state.players["B"].mana_pool.update({"W":5,"C":5});self.closed(session,extras[0]);self.closed(session,aegis)
        self.checkpoint(session);self.cast_offered(session,source,targets=("B",))
        result = session.act("pilot:A",{"action_id":"pass"});self.assertTrue(result.ok,result.summary)
        row = next(r for r in self.actions(session,"B") if r["id"]=="cast:"+aegis.ref)
        result = session.act("pilot:B",{"action_id":row["id"],"pay":"auto"});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        for card in cards:self.assertNotIn("A",session.state.cards[card.object_id].known_to)
        self.replay(session,load=True)

    def test_payload_minimum_requires_new_inspection_authority_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session, source, _, _ = self.world(297200,"Eye Spy")
        missing = "library.inspect.fixed_target_player"
        program = next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if missing in p.capability_dependencies)
        deps = tuple(c for c in program.capability_dependencies if c!=missing)
        changed = replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(changed,capability_registry=self.registry,profile="commander_review")
        self.assertFalse(result["strict"]);self.assertIn("capability:undeclared_runtime_dependency:"+missing,result["blockers"])

    def test_moonring_keeps_real_two_blue_permanent_activation_restriction(self):
        session, source, _, _ = self.world(297210,"Moonring Island",activated=True,controls=(("Merfolk Observer","A"),))
        self.assertFalse(any(r["id"].startswith("activate:"+source.ref+":") and r.get("target_schema") for r in self.actions(session)))
        self.add(session.engine,"Dewdrop Spy",ref="second-blue-permanent")
        self.prepare(session,source,{"U":10,"C":10});self.checkpoint(session)
        self.assertEqual("semantic.choice",self.start(session,source,activated=True).kind)
        self.finish(session,{"choice":"continue"});self.replay(session,load=True)

    def test_wrong_viewer_knowledge_mutation_is_killed_by_a_fresh_original_look(self):
        from quorune.semantic_choices.target_library_inspection import TargetLibraryInspectionHandler
        from quorune.semantic_runtime.intents import RevealLibraryCardsIntent
        session, source, cards, _ = self.world(297220,"Merfolk Observer")
        self.checkpoint(session);expected = self.top(session,"B",1)
        original = TargetLibraryInspectionHandler.prepare
        def wrong(handler,effect,context):
            result = original(handler,effect,context)
            return replace(result,preparation_intents=tuple(replace(intent,viewer=effect['target'])
                if isinstance(intent,RevealLibraryCardsIntent) else intent for intent in result.preparation_intents))
        with patch.object(TargetLibraryInspectionHandler,"prepare",wrong):
            self.assertEqual("semantic.choice",self.start(session,source).kind)
            with self.assertRaises(AssertionError):self.assert_private_window(session,expected,[card.ref for card in cards])


if __name__ == "__main__":unittest.main()
