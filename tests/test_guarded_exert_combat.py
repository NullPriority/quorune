"""First Exert of an incarnation, current untap group and exact combat replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.exert_model import EXERT_FIRST_THIS_TURN_CAPABILITY, ExertAttackSpec
from quorune.extra_combat_model import EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY, ExtraCombatUntap, decode_extra_combat_effect
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_history import incarnation_exerted_this_turn
from quorune.turn_history_model import EXERTED_PERMANENT_EVENT, TurnHistory, TurnHistoryEvent
from scripts.build_test_database import build_fixture_database


CARD = "Combat Celebrant"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class GuardedExertCombatTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "guarded-exert-combat.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "guarded-exert-combat-cards.json", "exert-cards.json", "empty-permanent-cards.json",
            "activation-result-views-cards.json", "bound-effect-program-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Guarded Exert and combat phases", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_guard_and_other_creature_result_bind_independently(self):
        card = self.db.lookup(CARD, fuzzy=False)
        synthetic = query_record(card.oracle_text)
        ir = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        offer, result = ir.faces[0].nodes
        self.assertEqual((offer.span, offer.text), (result.span, result.text))
        self.assertEqual((0, len(card.oracle_text)), (offer.span.start, offer.span.end))
        self.assertIn(EXERT_FIRST_THIS_TURN_CAPABILITY, offer.capability_dependencies)
        spec = ExertAttackSpec.from_dict(offer.handlers[0]["fragment"]["value"])
        self.assertTrue(spec.requires_unexerted_this_turn)
        self.assertIs(ExtraCombatUntap.OTHER_CONTROLLED_CREATURES, decode_extra_combat_effect(result.effects[0]))
        self.assertIn(EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY, result.capability_dependencies)
        for a, b in (
            ("as it attacks", "as it blocks"),
            ("been exerted this turn", "been exerted this game"),
            ("all other creatures you control", "all other creatures they control"),
            ("additional combat phase", "additional main phase"),
        ):
            with self.subTest(wording=b):
                neighbor = replace(synthetic, oracle_text=synthetic.oracle_text.replace(a, b, 1))
                self.assertNotEqual("exact", compile_oracle_card(neighbor, capability_registry=self.registry,
                                                                capability_profile=PROFILE).status)
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        for capability in (EXERT_FIRST_THIS_TURN_CAPABILITY, EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY):
            program, = [p for p in programs if capability in p.capability_dependencies]
            dependencies = [key for key in program.capability_dependencies if key != capability]
            changed = replace(program, capability_dependencies=dependencies,
                              capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                           profile=PROFILE)["strict"])

    def test_versioned_history_excludes_presentation_and_unqualified_historical_frames(self):
        missing = TurnHistory(turn_sequence=1)
        self.assertNotIn("exert_occurrence_history_version", missing.to_dict())
        event = TurnHistoryEvent(EXERTED_PERMANENT_EVENT, actor="B", object_incarnation="source@1")
        with self.assertRaises(ValueError):
            TurnHistory(turn_sequence=1, events=[event])
        with self.assertRaises(ValueError):
            TurnHistory.from_dict({**missing.to_dict(), "exert_occurrence_history_version": True})
        with self.assertRaises(ValueError):
            TurnHistoryEvent(EXERTED_PERMANENT_EVENT, actor="B", object_incarnation="source@1", target="C")
        current = TurnHistory(turn_sequence=1, exert_occurrence_history_version=1, events=[event])
        self.assertEqual(current.to_dict(), TurnHistory.from_dict(current.to_dict()).to_dict())
        from types import SimpleNamespace

        state = SimpleNamespace(turn_history=missing, turn_sequence=1)
        self.assertIsNone(incarnation_exerted_this_turn(state, "source@1"))
        state.turn_history = current
        self.assertTrue(incarnation_exerted_this_turn(state, "source@1"))
        self.assertFalse(incarnation_exerted_this_turn(state, "source@2"))
        from quorune.turn_history import reset_turn_history

        state.turn_history = reset_turn_history(current, 2)
        state.turn_sequence = 2
        self.assertFalse(incarnation_exerted_this_turn(state, "source@1"))

    def attacker(self, game, name, ref, *, seat="A", tapped=False):
        card = self.add(game.engine, name, seat=seat, ref=ref)
        card.acquired_control_turn_count = -1
        card.tapped = tapped
        return card

    def offer(self, game):
        self.until(game, lambda g: g.state.pending_decision.kind == "combat.attackers")

    def keys(self, game):
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        return {row["value"]: row["options"][0]["value"] for row in ctx.get("exert", [])}

    def declare(self, game, actors, exert=()):
        choices = self.keys(game)
        command = {"action_id": "attack", "attackers": {card.ref: "B" for card in actors},
                   "exert": {card.ref: [choices[card.ref]] for card in exert}}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_native_extra_combat_untaps_other_creatures_and_refuses_repeat_after_untap(self):
        game = self.session(701435001)
        source = self.attacker(game, CARD, "first-exert")
        attacker = self.attacker(game, "Grizzly Bears", "attacking-other")
        bench = self.attacker(game, "Grizzly Bears", "nonattacking-other", tapped=True)
        opponent = self.attacker(game, "Grizzly Bears", "unrelated-foreign", seat="C", tapped=True)
        untap = self.add(game.engine, "Vitalize", zone="hand")
        self.add(game.engine, "Forest")
        for card in (source, untap):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.offer(game)
        offered = self.keys(game)
        self.assertIn(source.ref, offered)
        private = game.state.cards[game.state.players["A"].zones["hand"][0]]
        self.assertNotIn(private.ref, str(game.packet("pilot:C", full=True)))
        self.declare(game, (source, attacker), (source,))
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(0, game.state.extra_combat_phases)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIn(attacker.object_id, game.state.combat.attackers)
        self.assertFalse(game.state.cards[attacker.object_id].tapped)
        self.assertFalse(game.state.cards[bench.object_id].tapped)
        self.assertTrue(game.state.cards[opponent.object_id].tapped)
        self.cast(game, "A", untap)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.offer(game)
        self.assertNotIn(source.ref, self.keys(game))
        self.assertEqual(0, game.state.extra_combat_phases)
        command = {"action_id": "attack", "attackers": {source.ref: "B"}, "exert": {source.ref: [offered[source.ref]]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.declare(game, (source,))
        self.assertEqual(0, self.trigger_count(game, source))
        turn = game.state.turn_sequence
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        phases = [event.phase for event in game.state.events if event.turn_sequence == turn
                  and event.code == "step.begin" and event.step in ("beginning_combat", "main")]
        self.assertEqual(["combat", "postcombat_main"], phases[-2:])
        self.replay(game, load=True)

    def test_two_paid_linked_results_schedule_separate_combats_after_source_departure(self):
        game = self.session(701435002)
        first = self.attacker(game, CARD, "first-extra-combat")
        second = self.attacker(game, CARD, "second-extra-combat")
        bench = self.attacker(game, "Grizzly Bears", "second-exert-bench", tapped=True)
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        for card in (first, second, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.offer(game)
        self.declare(game, (first, second), (first, second))
        self.until(game, lambda g: g.state.pending_decision.kind == "priority" and len(g.state.stack) == 2)
        self.cast(game, "B", bounce, [first.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.extra_combat_phases)
        self.assertFalse(game.state.cards[bench.object_id].tapped)
        turn = game.state.turn_sequence
        prior_starts = sum(event.code == "step.begin" and event.step == "beginning_combat"
                           and event.turn_sequence == turn for event in game.state.events)
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        combats = [event for event in game.state.events if event.turn_sequence == turn
                   and event.code == "step.begin" and event.step == "beginning_combat"]
        self.assertEqual(prior_starts + 2, len(combats))
        self.assertEqual(0, game.state.extra_combat_phases)
        self.replay(game, load=True)
