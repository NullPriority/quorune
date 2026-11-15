"""CR 102.3/121.2c: untargeted opponents draw in surviving APNAP order."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.compiler.draw_templates import fixed_draw_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import fixed_draw_node_capabilities
from quorune.semantic_runtime import ReadOnlyHandlerContext, ReadOnlyRulesQuery
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.generic import DrawEachPlayerHandler
from scripts.build_test_database import build_fixture_database

SCOPE = {"op": "draw_each_player", "scope": "controller_opponents", "count": 1}


class OpponentDrawCompilerTests(unittest.TestCase):
    def test_fixed_opponent_draws_keep_recipient_scope_across_producers(self):
        registry = load_default_capability_registry()
        for text, card_type in (
            ("Each opponent draws a card.", "Sorcery"),
            ("{2}: Each opponent draws two cards.", "Artifact"),
            ("At the beginning of your upkeep, each opponent draws a card.", "Creature"),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(query_record(text), type_line=card_type),
                    capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                effect = ir.faces[0].nodes[0].effects[0]
                self.assertEqual("controller_opponents", effect["scope"])
                self.assertEqual("draw_each_player", effect["op"])
                self.assertNotIn("players", effect)
        shared = fixed_draw_effect_template("You draw two cards and each opponent draws a card.")
        self.assertEqual(({"op": "draw_each_player", "scope": "controller_and_opponents",
            "count": 1, "controller_count": 2},), shared[1])
        self.assertEqual(({"op": "draw_each_player", "count": 1},),
            fixed_draw_effect_template("Each player draws a card.")[1])
        for text in ("Each other player draws a card.", "Each opponent may draw a card.",
                "Each opponent draws X cards.", "Each opponent draws a card. Rewrite history."):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(query_record(text), type_line="Sorcery"), capability_registry=registry).status)

    def test_new_scope_shapes_are_closed_and_do_not_grant_targets(self):
        def capabilities(effect, target=None):
            return fixed_draw_node_capabilities(effects=(effect,), target_schema=target,
                mechanic_ids=("cr-121-drawing-a-card",))
        self.assertEqual(("zone.draw.library_to_hand",), capabilities(SCOPE))
        for changed in ({"scope": "any"}, {"scope": None}, {"scope": []}, {"count": True},
                {"count": 0}, {"count": -1}, {"players": ["$controller"]}, {"extra": "open"}):
            self.assertEqual((), capabilities({**SCOPE, **changed}))
        self.assertEqual((), capabilities(SCOPE, {"categories": ["player"], "count": 1}))
        shared = {**SCOPE, "scope": "controller_and_opponents", "controller_count": 2}
        self.assertEqual(("zone.draw.library_to_hand",), capabilities(shared))
        for changed in ({"controller_count": True}, {"controller_count": 0},
                {"players": ["$controller"]}, {"scope": "controller_opponents"}):
            self.assertEqual((), capabilities({**shared, **changed}))

    def test_verified_draw_owner_withdrawal_keeps_unrelated_life_result(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == "zone.draw.library_to_hand").update(
            status="blocked", blockers=["draw owner withdrawn"])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(
            ROOT/"platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for text, expected in (("Each opponent draws a card.", False), ("You gain 2 life.", True)):
            ir = compile_oracle_card(replace(query_record(text), type_line="Sorcery"), capability_registry=registry)
            self.assertEqual(expected, ir.status == "exact")


class OpponentDrawOwnerTests(unittest.TestCase):
    @staticmethod
    def context(actor="A", order=("C", "D", "A", "B")):
        return ReadOnlyHandlerContext(actor=actor, default_reason="opponent draw scope",
            query=ReadOnlyRulesQuery(seats=("A", "B", "C", "D"),
                active_seats=tuple(seat for seat in "ABCD" if seat in order), apnap_order=order))

    def test_opponent_selection_filters_the_existing_order_without_rotation(self):
        owner = DrawEachPlayerHandler()
        for actor, order, expected in (
            ("A", ("C", "D", "A", "B"), ("C", "D", "B")),
            ("C", ("C", "D", "A", "B"), ("D", "A", "B")),
            ("A", ("C", "D", "A"), ("C", "D")),
            ("A", ("A",), ()),
            ("A", (), ()),
        ):
            with self.subTest(actor=actor, order=order):
                context = self.context(actor, order)
                plan = owner.lower(SCOPE, context)
                self.assertEqual(expected, tuple(intent.player for intent in plan.intents))
                self.assertTrue(all(intent.private and intent.count == 1 for intent in plan.intents))
                self.assertEqual(order, tuple(intent.player for intent in owner.lower(
                    {"op": "draw_each_player", "count": 1}, context).intents))
                shared = owner.lower({**SCOPE, "scope": "controller_and_opponents", "controller_count": 2}, context)
                self.assertEqual(tuple((seat, 2 if seat == actor else 1) for seat in order),
                    tuple((intent.player, intent.count) for intent in shared.intents))

    def test_malformed_scope_count_mixed_recipients_and_unknown_controller_reject(self):
        owner = DrawEachPlayerHandler()
        for changed in ({"scope": "all_active_players"}, {"scope": []}, {"count": True},
                {"count": 0}, {"count": -1}, {"players": ["A"]}, {"other": "open"}):
            with self.subTest(changed=changed), self.assertRaises(SemanticNodeError):
                owner.lower({**SCOPE, **changed}, self.context())
        with self.assertRaises(SemanticNodeError):
            owner.lower(SCOPE, self.context(actor="unknown"))
        with self.assertRaises(SemanticNodeError):
            owner.lower({"op": "draw_each_player", "count": 1, "controller_count": 2}, self.context())
        for count in (None, True, 0, -1, "2"):
            with self.subTest(controller_count=count), self.assertRaises(SemanticNodeError):
                owner.lower({**SCOPE, "scope": "controller_and_opponents", "controller_count": count}, self.context())


class OpponentDrawNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = json.loads((ROOT/"tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        base_path = Path(cls.temporary.name)/"basic-deck.json"
        base_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8", newline="\n")
        path = Path(cls.temporary.name)/"opponent-draw.sqlite3"
        build_fixture_database([base_path, ROOT/"tests/fixtures/opponent-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent draw", [DeckEntry("Sliver Hivelord", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Sliver Hivelord"])

    def assert_private(self, session, private):
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:"+seat, full=True)))

    def assert_replacement_control(self, session, card):
        # Loam is a normally registered reviewed replacement control. It is
        # neither a new whole-card claim nor part of this family's gains.
        program = compile_best_available_card_program(self.db, self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics, capability_registry=self.registry,
            capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["compatible_ready"], binding["blockers"])

    def master_witness(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, "Master of the Feast", ref="borrowed-master")
        source.owner = "D"
        loam = self.add(session.engine, "Life from the Loam", seat="B", zone="graveyard")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="private-opponent-hand")
        self.assert_closed(session, source)
        self.assert_replacement_control(session, loam)
        self.prepare(session, actor="D", active="D")
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine.state.phase, engine.state.step = "ending", "end_step"
        engine.state.last_normal_turn_player = "D"
        engine.state.priority_passes = []
        engine._grant_priority("D")
        engine.pump()
        self.checkpoint(session)
        for _ in range(24):
            if session.state.active_player == "A" and session.state.step == "upkeep":
                break
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(("A", "upkeep"), (session.state.active_player, session.state.step))
        self.assertEqual(1, self.trigger_count(session, source))
        hand = {seat: len(session.state.players[seat].zones["hand"]) for seat in "ABCD"}
        library = {seat: len(session.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.assertEqual("draw.replacement", self.resolve(session).kind)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        self.assertEqual(hand["A"], len(session.state.players["A"].zones["hand"]),
            "controller must not draw before the first opponent replacement")
        for seat in "BCD":
            self.assertEqual(hand[seat], len(session.state.players[seat].zones["hand"]))
        before = authoritative_state_hash(session.state)
        for principal, choice in (("C", loam.ref), ("B", source.ref)):
            self.assertFalse(session.act("pilot:"+principal, {"action_id": "choose", "choice": choice}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:B", {"action_id": "choose", "choice": loam.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[loam.object_id].zone)
        for seat in "ABCD":
            self.assertEqual(hand[seat]+int(seat != "A"), len(session.state.players[seat].zones["hand"]))
            self.assertEqual(library[seat]-(3 if seat == "B" else int(seat != "A")),
                len(session.state.players[seat].zones["library"]))
        self.assert_private(session, private)
        self.replay(session, load=True)

    def test_actual_master_upkeep_uses_controller_opponents_and_resumes_dredge_once(self):
        self.master_witness(985_100)

    def test_fresh_all_player_scope_substitution_is_killed_by_native_controller_consequence(self):
        original = DrawEachPlayerHandler.lower
        observed = []
        def all_players(handler, effect, context):
            if effect.get("scope") == "controller_opponents":
                observed.append(context.actor)
                effect = {key: value for key, value in effect.items() if key != "scope"}
            return original(handler, effect, context)
        with patch.object(DrawEachPlayerHandler, "lower", all_players):
            with self.assertRaisesRegex(AssertionError, "controller must not draw"):
                self.master_witness(985_101)
        self.assertTrue(observed)

    def fox_witness(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, "Clockwork Fox", ref="departing-fox")
        destroy = self.add(session.engine, "Naturalize", zone="hand")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="fox-private-hand")
        loam = self.add(session.engine, "Life from the Loam", seat="B", zone="graveyard")
        self.assert_closed(session, source)
        self.assert_closed(session, destroy)
        self.assert_replacement_control(session, loam)
        self.prepare(session, actor="A", active="B", mana={"C": 1, "G": 1})
        self.checkpoint(session)
        self.cast(session, "A", destroy, targets=[source.ref])
        hand = {seat: len(session.state.players[seat].zones["hand"]) for seat in "ABCD"}
        self.assertEqual("draw.replacement", self.resolve(session).kind)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        self.assertEqual(hand["A"], len(session.state.players["A"].zones["hand"]),
            "controller must wait for the active opponent's draw")
        session = self.replay(session, load=True)
        result = session.act("pilot:B", {"action_id": "choose", "choice": loam.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        for seat in "ABCD":
            self.assertEqual(hand[seat]+(2 if seat == "A" else 1),
                len(session.state.players[seat].zones["hand"]))
        self.assert_private(session, private)
        self.replay(session, load=True)

    def test_actual_fox_departure_draws_different_counts_in_apnap_order_and_replays(self):
        self.fox_witness(985_200)

    def test_fresh_controller_first_draw_order_mutant_is_killed_by_native_replacement_pause(self):
        original = DrawEachPlayerHandler.lower
        observed = []
        def controller_first(handler, effect, context):
            plan = original(handler, effect, context)
            if effect.get("scope") == "controller_and_opponents":
                observed.append(context.actor)
                plan = replace(plan, intents=tuple(sorted(plan.intents,
                    key=lambda intent: intent.player != context.actor)))
            return plan
        with patch.object(DrawEachPlayerHandler, "lower", controller_first):
            with self.assertRaisesRegex(AssertionError, "controller must wait"):
                self.fox_witness(985_201)
        self.assertTrue(observed)
