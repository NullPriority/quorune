"""Counter failure and later entrants separate captured cohort from results."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.captured_counter_keyword_grant import (
    CAPTURED_COUNTER_KEYWORD_CAPABILITY, CAPTURED_COUNTER_KEYWORD_OPERATION,
)
from scripts.build_test_database import build_fixture_database


CARD = "Felidar Retreat"
PROFILE = "commander_review"


class CapturedCounterKeywordGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "captured-counter-keyword-grant.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-public-state-cards.json",
            fixtures / "counter-prohibition-cards.json",
            fixtures / "captured-counter-keyword-grant-cards.json",
            fixtures / "additional-token-definition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Captured counter and keyword", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def begin(self, game, source, land, *, mana=None):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana or {})
        self.checkpoint(game)
        result = game.act("pilot:A", {"action_id": f"play-land:{land.ref}"})
        self.assertTrue(result.ok, result.summary)
        decision = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("spectator", full=True)["decision"])

    def choose(self, game, mode):
        command = {"action_id": "choose", "modes": [mode], "targets": []}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "modes": ["mode_1", "mode_2"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("priority", game.state.pending_decision.kind)
        item, = game.state.stack
        self.assertEqual(([mode], []), (item.modes, item.targets))

    def tokens(self, game):
        return tuple(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")

    def assert_creature(self, game, card, counters, vigilance):
        current = game.state.cards[card.object_id]
        self.assertEqual(counters, current.counters.get("+1/+1", 0))
        self.assertEqual(vigilance, "vigilance" in game.engine._combat_keywords(current))

    def test_actual_modal_land_entry_token_branch_is_fixed_before_priority(self):
        game = self.session(60303030)
        source = self.add(game.engine, CARD, seat="D")
        game.engine.change_control(source.object_id, "A", reason="Borrowed landfall source fixture")
        land = self.add(game.engine, "Generic Bound Plains", zone="hand")
        body = self.add(game.engine, "Grizzly Bears")
        self.begin(game, source, land)
        game = self.replay(game, load=True)
        self.choose(game, "mode_1")
        self.assertIsNone(self.resolve(game))
        token, = self.tokens(game)
        effective = game.engine._effective_card_data(token)
        self.assertEqual(("Cat Beast Token", "A", "A", False),
                         (token.printed_name, token.owner, token.controller, token.tapped))
        self.assertEqual(["W"], effective["colors"])
        self.assertEqual((2, 2), tuple(game.engine._numeric_stat(token.object_id, kind)
                                       for kind in ("power", "toughness")))
        self.assertEqual({"cat", "beast"}, game.engine._type_parts(effective["type_line"])[1])
        self.assert_creature(game, body, 0, False)
        self.replay(game, load=True)

    def test_actual_cohort_precedes_counter_prohibition_and_excludes_later_creatures(self):
        for prevented in (False, True):
            with self.subTest(prevented=prevented):
                game = self.session(60303031 + prevented)
                source = self.add(game.engine, CARD)
                land = self.add(game.engine, "Generic Bound Plains", zone="hand")
                original = self.add(game.engine, "Grizzly Bears")
                opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="other-cohort")
                before = self.add(game.engine, "Raise the Alarm", zone="hand", ref="before-resolution")
                after = self.add(game.engine, "Raise the Alarm", zone="hand", ref="after-resolution")
                if prevented:
                    solemnity = self.add(game.engine, "Solemnity", seat="D")
                    self.assert_closed(game, solemnity)
                self.begin(game, source, land, mana={"C": 2, "W": 2})
                self.choose(game, "mode_2")
                self.cast_for(game, "A", before, reserve={"C": 1, "W": 1})
                self.resolve_top(game)
                early = self.tokens(game)
                self.assertEqual(2, len(early))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                for card in (original, *early):
                    self.assert_creature(game, card, int(not prevented), True)
                self.assert_creature(game, opponent, 0, False)

                self.cast(game, "A", after)
                self.assertIsNone(self.resolve(game))
                late = tuple(card for card in self.tokens(game) if card.object_id not in {t.object_id for t in early})
                self.assertEqual(2, len(late))
                for card in late:
                    self.assert_creature(game, card, 0, False)
                self.until(game, lambda g: g.state.active_player == "B")
                for card in (original, *early):
                    self.assert_creature(game, card, int(not prevented), False)
                self.replay(game, load=True)

    def test_canonical_mode_copy_uses_its_own_current_creatures(self):
        game = self.session(60303040)
        source = self.add(game.engine, CARD)
        land = self.add(game.engine, "Generic Bound Plains", zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="copy-cohort")
        self.begin(game, source, land)
        self.choose(game, "mode_2")
        original, = game.state.stack
        copied = game.engine._copy_stack_item(
            controller="B", target=original, targets=(), target_groups={},
            reason="Retain the announced mode on its canonical stack copy",
        )
        self.assertEqual(["mode_2"], copied.modes)
        self.checkpoint(game)
        self.resolve_top(game)
        self.assert_creature(game, other, 1, True)
        self.assert_creature(game, own, 0, False)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assert_creature(game, other, 1, True)
        self.assert_creature(game, own, 1, True)
        self.assertEqual((), self.tokens(game))
        self.replay(game, load=True)

    def test_exact_whole_card_closure_withdrawal_and_grammar_boundary(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(("land.enter", "battlefield"), (node.event, node.active_zone))
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        self.assertEqual(["mode_1", "mode_2"], list(node.target_schema["modes"]))
        mode = node.target_schema["modes"]["mode_2"]
        self.assertEqual([], mode["groups"])
        effect, = mode["effects"]
        self.assertEqual(CAPTURED_COUNTER_KEYWORD_OPERATION, effect["op"])
        self.assertIn(CAPTURED_COUNTER_KEYWORD_CAPABILITY, node.capability_dependencies)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPTURED_COUNTER_KEYWORD_CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPTURED_COUNTER_KEYWORD_CAPABILITY,
                      binding["blockers"])
        for old, new in (
            ("a +1/+1 counter", "a stun counter"), ("gain vigilance", "gain flying"),
            ("you control. Those", "targeted by this ability. Those"),
            ("until end of turn.", "until your next turn."),
        ):
            self.assertNotEqual("exact", compile_card(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)
        with patch("quorune.compiler.modal_context_nodes.captured_counter_keyword_mode", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)

    def test_zero_loyalty_retains_subjects_and_expires_at_the_resolvers_next_turn(self):
        from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors

        for prevented in (False, True):
            with self.subTest(prevented=prevented):
                game = self.session(60642200 + prevented)
                source = self.add(game.engine, "Elspeth, Storm Slayer")
                source.counters["loyalty"] = 5
                member = self.add(game.engine, "Grizzly Bears")
                outsider = self.add(game.engine, "Grizzly Bears", seat="B", ref="loyalty-other-seat")
                late = self.add(game.engine, "Raise the Alarm", zone="hand")
                if prevented:
                    self.add(game.engine, "Solemnity", seat="C")
                game.state.config.semantic_policy = "trusted_only"
                self.assert_closed(game, source)
                self.assert_closed(game, late)
                self.prepare(game, mana={"C": 1, "W": 1})
                self.checkpoint(game)
                programs = game.engine.semantics.runtime_handler_programs_for_oracle(
                    source.oracle_id, active_zone="battlefield", event="activate",
                )
                ability, = [ability for program in programs
                            for ability in activated_abilities_from_descriptors(program.handlers)
                            if ability.loyalty_delta == 0]
                action = f"activate:{source.ref}:{ability.ability_id}"
                offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                self.assertIn(action, (offer["id"] for offer in offers))
                cmd = {"action_id": action, "pay": "auto"}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", cmd).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                answer = game.act("pilot:A", cmd)
                self.assertTrue(answer.ok, answer.summary)
                self.assertEqual(5, game.state.cards[source.object_id].counters["loyalty"])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                captured = game.state.cards[member.object_id]
                self.assertEqual(0 if prevented else 1, captured.counters.get("+1/+1", 0))
                self.assertIn("flying", game.engine._combat_keywords(captured))
                self.assertNotIn("flying", game.engine._combat_keywords(game.state.cards[outsider.object_id]))
                self.cast(game, "A", late)
                self.resolve_top(game)
                later = self.tokens(game)
                self.assertEqual(4, len(later))
                self.assertTrue(all("flying" not in game.engine._combat_keywords(card) for card in later))

                def next_turn(seat):
                    start = game.state.turn_sequence
                    for _ in range(len(game.state.players) + 1):
                        self.until(game, lambda current: (
                            current.state.turn_sequence > start and current.state.active_player == seat
                        ) or (current.state.pending_decision is not None and current.state.pending_decision.kind == "cleanup.discard"))
                        if game.state.turn_sequence > start and game.state.active_player == seat:
                            return
                        actor = game.pending_principals()[0]
                        context = game.packet(actor, full=True)["decision"]["ctx"]
                        refs = [row["id"] for row in context["hand"][:context["count"]]]
                        result = game.act(actor, {"action_id": "discard", "cards": refs})
                        self.assertTrue(result.ok, result.summary)
                    self.fail(f"Next turn for {seat} did not begin")

                next_turn("B")
                self.assertIn("flying", game.engine._combat_keywords(game.state.cards[member.object_id]))
                game = self.replay(game, load=True)
                next_turn("A")
                self.assertNotIn("flying", game.engine._combat_keywords(game.state.cards[member.object_id]))
                self.replay(game, load=True)

    def test_zero_loyalty_flying_shape_and_dependency_fail_closed(self):
        from quorune.rules.captured_counter_keyword_grant import CAPTURED_COUNTER_FLYING_CAPABILITY

        card = self.db.lookup("Elspeth, Storm Slayer", fuzzy=False)
        settings = {"capability_registry": self.registry, "capability_profile": PROFILE}
        compiled = compile_oracle_card(card, **settings)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = [node for face in compiled.faces for node in face.nodes
                 if node.template_id == "loyalty-captured-counter-flying-v1"]
        self.assertIsNone(node.target_schema)
        self.assertEqual(0, node.cost["loyalty_delta"])
        self.assertIn(CAPTURED_COUNTER_FLYING_CAPABILITY, node.capability_dependencies)
        game = self.session(60642202)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = [program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if program.provenance.get("template_id") == node.template_id]
        remaining = tuple(cap for cap in program.capability_dependencies if cap != CAPTURED_COUNTER_FLYING_CAPABILITY)
        omitted = replace(program, capability_dependencies=remaining,
                          capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPTURED_COUNTER_FLYING_CAPABILITY,
                      binding["blockers"])
        for text in ("those creatures gain haste until your next turn",
                     "those creatures gain flying until end of turn"):
            altered = replace(card, oracle_text=card.oracle_text.replace(
                "Those creatures gain flying until your next turn", text.capitalize()))
            self.assertNotEqual("exact", compile_oracle_card(altered, **settings).status)
        with patch("quorune.compiler.loyalty_spell_results.captured_counter_keyword_instruction",
                   return_value=node.effects[0] | {"schema_version": 1}):
            self.assertNotEqual("exact", compile_oracle_card(card, **settings).status)
