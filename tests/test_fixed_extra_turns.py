from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, capability_dependencies_for_node, load_default_capability_registry
from quorune.record import authoritative_state_hash


def turn_record(text: str, *, type_line: str = "Sorcery") -> CardRecord:
    return CardRecord(oracle_id="fixture-fixed-extra-turn", name="Fixed Extra Turn Witness",
        mana_cost="{2}", mana_value=2, type_line=type_line, oracle_text=text,
        power="2" if "Creature" in type_line else None, toughness="2" if "Creature" in type_line else None,
        loyalty=None, defense=None, colors=(), color_identity=(), keywords=(), produced_mana=(),
        layout="normal", released_at="2026-08-07", legalities={"commander": "legal"}, faces=(), raw={})


class FixedExtraTurnCompilerTests(unittest.TestCase):
    # CR 500.7: represented extra turns are created individually after the
    # current turn, and the most recently created one is taken first. A target
    # remains a normal announced and resolution-revalidated player target.
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, *, type_line="Sorcery"):
        return compile_oracle_card(turn_record(text, type_line=type_line),
            capability_registry=self.registry, capability_profile="commander_review")

    def test_fixed_extra_turn_controller_and_target_grammar_preserves_recipients(self):
        for text, count, player, relation in (
            ("Take an extra turn after this one.", 1, "$controller", None),
            ("Take two extra turns after this one.", 2, "$controller", None),
            ("Target player takes an extra turn after this one.", 1, "$target.0", "any"),
            ("Target opponent takes an extra turn after this one.", 1, "$target.0", "opponent"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(count, len(node.effects))
                self.assertTrue(all(effect == {"op": "extra_turn", "player": player, "source": "$source"} for effect in node.effects))
                self.assertIn("turn.extra.fixed_after_current", node.capability_dependencies)
                if relation is None:
                    self.assertIsNone(node.target_schema)
                else:
                    self.assertEqual(relation, node.target_schema["player_relation"])
                    self.assertIn("target.revalidate_resolution", node.capability_dependencies)

    def test_fixed_extra_turn_reuses_activation_and_trigger_carriers(self):
        for text, kind in (
            ("{1}, {T}: Take an extra turn after this one.", "activated_ability"),
            ("Whenever you cast a creature spell, take an extra turn after this one.", "triggered_ability"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Artifact")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(kind, ir.faces[0].nodes[0].kind)

    def test_unrepresented_extra_turn_qualifiers_and_riders_remain_material(self):
        for text in (
            "Take X extra turns after this one.",
            "Take an extra turn after the next turn.",
            "Take an extra turn after this one if you control a Wizard.",
            "Take an extra turn after this one. At the beginning of that turn's end step, you lose the game.",
            "Each player takes an extra turn after this one.",
            "Take one extra turns after this one.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_extra_turn_shape_dependency_and_compiler_mutants_fail_closed(self):
        from quorune.rules.extra_turn_shapes import fixed_extra_turn_node_capabilities
        text = "Target player takes an extra turn after this one."
        node = self.compile(text).faces[0].nodes[0]
        effect = dict(node.effects[0])
        for change in ({"player": "$source.controller"}, {"player": []}, {"source": "$target.0"}, {"quantity": True}):
            with self.subTest(change=change):
                self.assertEqual((), fixed_extra_turn_node_capabilities(effects=({**effect, **change},),
                    target_schema=node.target_schema, mechanic_ids=node.mechanics))
        schema = {**node.target_schema, "count": True}
        self.assertEqual((), fixed_extra_turn_node_capabilities(effects=node.effects,
            target_schema=schema, mechanic_ids=node.mechanics))
        self.assertEqual((), fixed_extra_turn_node_capabilities(effects=node.effects,
            target_schema=None, mechanic_ids=node.mechanics))
        self.assertNotIn("turn.extra.fixed_after_current", capability_dependencies_for_node(
            effects=({**effect, "quantity": True},), target_schema=node.target_schema, mechanic_ids=node.mechanics))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == "turn.extra.fixed_after_current").update(
            status="blocked", blockers=["extra turn scheduler unavailable"])
        blocked = compile_oracle_card(turn_record(text), capability_registry=CapabilityRegistry(raw), capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(blocked.material_residuals)
        with patch("quorune.compiler.resolution_effect_templates.fixed_extra_turn_effect_template", return_value=None):
            mutant = self.compile(text)
        self.assertNotEqual("exact", mutant.status)
        self.assertTrue(mutant.material_residuals)


class FixedExtraTurnRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-extra-turns.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-extra-turn-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Extra turns", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _witnesses
    session = _witnesses.BoundEffectProgramRuntimeTests.session
    add = _witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = _witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = _witnesses.BoundEffectProgramRuntimeTests.replay

    def ready(self, session, source, mana):
        from quorune.turn_step_owner import TURN_STEPS
        session.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        return self._witnesses.BoundEffectProgramRuntimeTests.ready(self, session, source, mana)

    def assert_whole_admission(self, session, name):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program = compile_best_available_card_program(self.db, self.db.lookup(name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)

    def advance_to_scheduled_turn(self, session, turn_id):
        for _ in range(64):
            if session.state.current_turn is not None and session.state.current_turn.turn_id == turn_id:
                self.assertTrue(session.state.current_turn.extra)
                return
            if session.state.pending_decision.kind == "cleanup.discard":
                principal = session.pending_principals()[0]
                context = session.packet(principal, full=True)["decision"]["ctx"]
                cards = [card["id"] for card in context["hand"][:context["count"]]]
                discarded = session.act(principal, {"a": "discard", "cards": cards})
                self.assertTrue(discarded.ok, discarded.summary)
                continue
            self.assertEqual("priority", session.state.pending_decision.kind)
            passed = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.fail("Scheduled extra turn did not begin through ordinary turn progression")

    def test_original_turn_spells_lock_recipients_schedule_lifo_and_replay(self):
        session = self.session(336010)
        first = self.add(session.engine, "Time Warp", zone="hand")
        second = self.add(session.engine, "Time Stretch", zone="hand")
        for card in (first, second):
            self.assert_whole_admission(session, card.printed_name)
        action = self.ready(session, first, {"U": 15})
        self.checkpoint(session)
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        malformed = session.act("pilot:A", {"action_id": action["id"], "targets": ["unknown"], "pay": "auto"})
        self.assertFalse(malformed.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        cast = session.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertFalse(session.state.extra_turns)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(["B"], [turn.player for turn in session.state.extra_turns])
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(action for action in actions if action.get("card") == second.ref)
        cast = session.act("pilot:A", {"action_id": action["id"], "targets": ["A"], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.resolve(session)
        self.assertEqual(["A", "A", "B"], [turn.player for turn in session.state.extra_turns])
        self.assertEqual(3, len({turn.turn_id for turn in session.state.extra_turns}))
        self.assertEqual(0, session.state.players["A"].mana_pool.get("U", 0))
        self.assertEqual("graveyard", session.state.cards[first.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[second.object_id].zone)
        scheduled = session.state.extra_turns[0].turn_id
        self.advance_to_scheduled_turn(session, scheduled)
        self.assertEqual("A", session.state.active_player)
        self.assertEqual(["A", "B"], [turn.player for turn in session.state.extra_turns])
        self.replay(session, load=True)

    def test_original_scepter_pays_source_counter_price_before_scheduling_and_replays(self):
        session = self.session(336011)
        source = self.add(session.engine, "Magistrate's Scepter")
        source.counters["charge"] = 3
        self.assert_whole_admission(session, source.printed_name)
        action = self.ready(session, source, {})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        activated = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get("charge", 0))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertFalse(session.state.extra_turns)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(["A"], [turn.player for turn in session.state.extra_turns])
        scheduled = session.state.extra_turns[0].turn_id
        self.advance_to_scheduled_turn(session, scheduled)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get("charge", 0))
        self.assertFalse(session.state.extra_turns)
        self.replay(session, load=True)

    def test_original_target_turn_spell_fizzles_after_accepted_target_concession(self):
        session = self.session(336012)
        source = self.add(session.engine, "Time Warp", zone="hand")
        self.assert_whole_admission(session, source.printed_name)
        action = self.ready(session, source, {"U": 5})
        self.checkpoint(session)
        cast = session.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertTrue(session.act("pilot:A", {"action_id": "pass"}).ok)
        self.assertEqual(["pilot:B"], session.pending_principals())
        conceded = session.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertFalse(session.state.players["B"].in_game)
        self.resolve(session)
        self.assertFalse(session.state.extra_turns)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("U", 0))
        self.replay(session, load=True)


class ScheduledTurnSkipCompilerTests(unittest.TestCase):
    def test_scheduled_skip_templates_require_closed_scope_and_dependencies(self):
        from quorune.compiler.scheduled_turn_skip_templates import scheduled_turn_skip_effect_template
        from quorune.turn_skip_model import TURN_SKIP_CAPABILITY, TurnSkipSpec

        registry = load_default_capability_registry()
        examples = (
            ("You skip your next two turns.", "turn", "next", 2),
            ("Target player skips their next draw step.", "draw", "next", 1),
            ("Each opponent skips their next untap step.", "untap", "next", 1),
            ("Target opponent skips their next combat phase.", "combat", "next", 1),
            ("Target player skips their next combat phase this turn.", "combat", "this_turn", 1),
            ("Target player skips all combat phases of their next turn.", "combat", "all_of_next_turn", 1),
        )
        for text, boundary, scope, count in examples:
            with self.subTest(text=text):
                template = scheduled_turn_skip_effect_template(text)
                self.assertIsNotNone(template)
                spec = TurnSkipSpec.from_dict(template[1][0]["spec"])
                self.assertEqual((boundary, scope, count), (spec.boundary.value, spec.scope.value, spec.count))
                self.assertEqual("exact", compile_oracle_card(turn_record(text), capability_registry=registry).status)
        for text in (
            "You skip your next X turns.", "You skip your next two combat phases.",
            "Target player skips your next draw step.", "You skip the current combat phase.",
            "Target opponent skips their next combat phase if they control a creature.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(scheduled_turn_skip_effect_template(text))

        text = "Target player skips their next draw step."
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        capability = next(row for row in raw["capabilities"] if row["id"] == TURN_SKIP_CAPABILITY)
        capability.update(status="blocked", blockers=["withdrawn for the dependency test"])
        self.assertNotEqual("exact", compile_oracle_card(turn_record(text), capability_registry=CapabilityRegistry(raw)).status)
        with patch("quorune.compiler.resolution_effect_templates.scheduled_turn_skip_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(turn_record(text), capability_registry=registry).status)


from inherited_composition_fixture import InheritedNativeFixture


class ScheduledTurnSkipRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    import test_qualified_public_history as _history

    until = _history.QualifiedPublicHistoryNativeTests.until

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "scheduled-turn-skips.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "fixed-extra-turn-cards.json",
            fixtures / "qualified-attack-cards.json",
            fixtures / "static-step-skip-cards.json",
            fixtures / "scheduled-turn-skip-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Scheduled skips", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                DeckEntry("Generic Bound Plains", 32)], ["Generic Bound Commander"],
        )

    def test_actual_meditate_draws_and_skips_the_queued_extra_turn(self):
        session = self.session(61410001)
        meditate = self.add(session.engine, "Meditate", zone="hand")
        warp = self.add(session.engine, "Time Warp", zone="hand")
        for source in (meditate, warp):
            self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5, "U": 3})
        self.checkpoint(session)
        sequence = session.state.turn_sequence
        self.cast(session, "A", meditate)
        hand = len(session.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 4, len(session.state.players["A"].zones["hand"]))
        self.cast(session, "A", warp, ["A"])
        self.assertIsNone(self.resolve(session))
        extra, = session.state.extra_turns
        self.assertEqual("A", extra.player)
        self.until(session, lambda game: game.state.active_player == "B")
        self.assertEqual(sequence + 1, session.state.turn_sequence)
        self.assertEqual("B", session.state.current_turn.player)
        self.assertFalse(session.state.current_turn.extra)
        skipped, = [event for event in session.state.events if event.code == "turn.skip.applied"]
        self.assertEqual((extra.turn_id, True), (skipped.details["turn"], skipped.details["extra"]))
        self.assertFalse(session.state.scheduled_turn_skips)
        self.replay(session, load=True)

    def test_actual_stonehorn_skips_only_the_announced_opponents_combat(self):
        session = self.session(61410002)
        source = self.add(session.engine, "Stonehorn Dignitary", zone="hand")
        angel = self.add(session.engine, "Blinding Angel")
        angel.acquired_control_turn_count = -1
        for card in (source, angel):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "W": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.target(session, "A", ["B"])
        self.assertIsNone(self.resolve(session))
        grant, = session.state.scheduled_turn_skips
        self.assertEqual(("B", "combat"), (grant.player, grant.spec.boundary.value))
        self.attack(session, [(angel, "B")])
        self.until(session, lambda game: len(game.state.scheduled_turn_skips) == 2)
        self.assertEqual(38, session.state.players["B"].life)
        self.assertEqual({"B"}, {skip.player for skip in session.state.scheduled_turn_skips})
        self.until(session, lambda game: game.state.pending_decision.kind == "replacement.order")
        self.assertEqual(["pilot:B"], session.pending_principals())
        result = session.act("pilot:B", {"action_id": "choose", "replacement": grant.grant_id})
        self.assertTrue(result.ok, result.summary)
        self.until(session, lambda game: game.state.active_player == "B" and game.state.phase == "postcombat_main")
        self.assertFalse(any(
            event.code == "step.begin" and event.active_player == "B" and event.phase == "combat"
            for event in session.state.events
        ))
        remaining, = session.state.scheduled_turn_skips
        self.assertNotEqual(grant.grant_id, remaining.grant_id)
        self.until(session, lambda game: game.state.active_player == "C" and game.state.step == "beginning_combat")
        self.assertTrue(any(
            event.code == "step.begin" and event.active_player == "C" and event.step == "beginning_combat"
            for event in session.state.events
        ))
        self.replay(session, load=True)

    def test_actual_static_draw_skip_choice_preserves_the_one_shot_after_load(self):
        session = self.session(61410003)
        fatigue = self.add(session.engine, "Fatigue", zone="hand")
        warp = self.add(session.engine, "Time Warp", zone="hand")
        sovereign = self.add(session.engine, "Infernal Sovereign", seat="B")
        bounce = self.add(session.engine, "Unsummon", zone="hand")
        private = self.add(session.engine, "Unsummon", seat="D", zone="hand", ref="skip-private")
        self.add(session.engine, "Island")
        for source in (fatigue, warp, sovereign, bounce):
            self.assert_closed(session, source)
        self.prepare(session, mana={"C": 4, "U": 3})
        self.checkpoint(session)
        self.cast(session, "A", fatigue, ["B"])
        self.assertIsNone(self.resolve(session))
        self.cast(session, "A", warp, ["B"])
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: game.state.pending_decision.kind == "replacement.order")
        self.assertEqual(["pilot:B"], session.pending_principals())
        packet = session.packet("pilot:B", full=True)
        self.assertNotIn(private.ref, json.dumps(packet))
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        options = packet["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        static, = [option["id"] for option in options if option["source"] == sovereign.ref]
        before = authoritative_state_hash(session.state)
        command = {"action_id": "choose", "replacement": static}
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertFalse(session.act("pilot:B", {**command, "replacement": "unknown"}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        accepted = session.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, len(session.state.scheduled_turn_skips))
        self.assertTrue(session.state.current_turn.extra)
        self.cast(session, "A", bounce, [sovereign.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[sovereign.object_id].zone)
        self.until(session, lambda game: game.state.active_player == "B" and not game.state.current_turn.extra
                   and game.state.phase == "precombat_main")
        self.assertFalse(session.state.scheduled_turn_skips)
        self.assertFalse(any(
            event.code == "step.begin" and event.active_player == "B" and event.step == "draw"
            for event in session.state.events
        ))
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
