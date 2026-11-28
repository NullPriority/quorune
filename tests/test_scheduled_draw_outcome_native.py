"""Native outcomes of a consumed controller next-draw grant."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.next_draw_replacement_templates import next_draw_replacement_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.discard_effect_origin import DiscardEffectOrigin
from quorune.drawing.scheduled_draw_results import schedule_next_draw_replacement
from quorune.next_draw_replacement_model import ResolvedNextDrawReplacement
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ScheduledDrawOutcomeNativeTests(InheritedNativeFixture, unittest.TestCase):
    sources = (
        ("Dawn Account", "gain_life"),
        ("Meadow Tallies", "create_token"),
        ("Silent Assessment", "discard_each_opponent"),
    )
    life_amount = 7

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "scheduled-draw.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/draw-discard-replacement-cards.json",
            ROOT / "tests/fixtures/scheduled-next-draw-result-cards.json",
        ], database)
        cls._use_database(database)

    @classmethod
    def _use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Scheduled draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def prepare_draw(self, seed, kinds, *, nested=False):
        session = self.session(seed)
        draw = self.add(session.engine, "Divination", zone="hand")
        for index in range(3):
            self.add(session.engine, "Island", ref=f"draw-mana-{index}")
        if nested:
            for seat in ("B", "C"):
                self.assert_closed(session, self.add(session.engine, "Rest in Peace", seat=seat, ref=f"rest-{seat}"))
        cards = {}
        for seat in ("B", "C", "D"):
            cards[seat] = self.add(session.engine, "Island", seat=seat, zone="hand", ref=f"private-discard-{seat}")
        self.prepare(session)
        for index, text in enumerate(kinds):
            grant = next_draw_replacement_template(text)
            self.assertIsNotNone(grant)
            source_id = f"retired-ability-{index}"
            schedule_next_draw_replacement(session.state, ResolvedNextDrawReplacement(
                f"draw-grant-{index}", source_id, 0, "A", grant,
                discard_origin=DiscardEffectOrigin(source_id, "A", "activated_ability"),
            ))
        self.checkpoint(session)
        return session, draw, cards

    def test_two_distinct_fixed_results_finish_before_original_draw_tail(self):
        game, draw, _ = self.prepare_draw(53116001, (
            "The next time you would draw a card this turn, you gain 5 life instead.",
            "The next time you would draw a card this turn, create a 2/2 green Bear creature token instead.",
        ))
        prior = game.state.players["A"].life
        before = tuple(game.state.players["A"].zones["library"])
        self.cast_for(game, "A", draw)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(game.state.pending_decision.kind, "draw.replacement")
        game = self.replay(game, load=True)
        decision = game.packet("pilot:A", full=True)["decision"]
        option = next(row["id"] for row in decision["ctx"]["options"] if "retired-ability-0" in row["label"])
        result = game.act("pilot:A", {"action_id": "choose", "choice": option})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(prior + 5, game.state.players["A"].life)
        tokens = [game.state.cards[oid] for oid in game.state.players["A"].zones["battlefield"]
                  if game.state.cards[oid].is_token]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(before, tuple(game.state.players["A"].zones["library"]))
        self.assertFalse(game.state.stack)
        self.assertFalse(any(isinstance(effect, ResolvedNextDrawReplacement) for effect in game.state.continuous_effects))
        self.replay(game, load=True)

    def test_private_apnap_and_nested_zone_choices_resume_before_remaining_draw(self):
        game, draw, cards = self.prepare_draw(53116002, (
            "The next time you would draw a card this turn, each opponent discards a card instead.",
        ), nested=True)
        top = game.state.players["A"].zones["library"][-1]
        self.cast_for(game, "A", draw)
        self.assertIsNotNone(self.resolve(game))
        for seat in ("B", "C", "D"):
            decision = game.packet(f"pilot:{seat}", full=True)["decision"]
            self.assertEqual(decision["kind"], "choice.apnap")
            opposite = "D" if seat != "D" else "C"
            self.assertNotIn(cards[opposite].ref, json.dumps(decision))
            if seat == "B":
                snapshot = authoritative_state_hash(game.state)
                invalid = game.act("pilot:B", {"action_id": "choose", "cards": [cards["C"].ref]})
                self.assertFalse(invalid.ok)
                self.assertEqual(snapshot, authoritative_state_hash(game.state))
            result = game.act(f"pilot:{seat}", {"action_id": "choose", "cards": [cards[seat].ref]})
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in cards.values()))
            if seat == "B":
                game = self.replay(game, load=True)
        self.assertEqual(game.state.pending_decision.kind, "replacement.order")
        game = self.replay(game, load=True)
        for _ in range(3):
            state = game.state.pending_decision
            if state is None or state.kind != "replacement.order":
                break
            chooser = state.actors[0]
            view = game.packet(f"pilot:{chooser}", full=True)["decision"]
            option = next(row["id"] for row in view["ctx"]["options"] if row.get("id") and not row["id"].startswith("decline"))
            payload = {"action_id": "choose", "replacement": option}
            if view["ctx"].get("events"):
                payload["replacement_event"] = view["ctx"]["events"][0]["id"]
            result = game.act(f"pilot:{chooser}", payload)
            self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in cards.values()))
        self.assertEqual("hand", game.state.cards[top].zone)
        decision = game.state.pending_decision
        self.assertIsNotNone(decision)
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual("resolving_entry", decision.continuation.get("replacement_resume_kind"))
        chooser = decision.actors[0]
        view = game.packet(f"pilot:{chooser}", full=True)["decision"]
        result = game.act(f"pilot:{chooser}", {"action_id": "choose", "replacement": view["ctx"]["options"][0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", game.state.cards[draw.object_id].zone)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)


    def test_scheduled_draw_original_activations_bind_their_exact_native_results(self):
        for index, (name, kind) in enumerate(self.sources):
            with self.subTest(kind=kind):
                game, draw, cards = self.prepare_draw(53116100 + index, ())
                source = self.add(game.engine, name)
                self.assert_closed(game, source)
                self.add(game.engine, "Island", ref="grant-payment-mana")
                self.prepare(game)
                self.checkpoint(game)
                original_life = game.state.players["A"].life
                activate = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1"})
                self.assertTrue(activate.ok, activate.summary)
                self.assertIsNone(self.resolve(game))
                grant, = [effect for effect in game.state.continuous_effects
                          if isinstance(effect, ResolvedNextDrawReplacement)]
                self.assertEqual((grant.controller, grant.result.kind, grant.source_ref), ("A", kind, source.ref))
                game = self.replay(game, load=True)
                top = game.state.players["A"].zones["library"][-1]
                self.cast_for(game, "A", draw)
                outcome = self.resolve(game)
                if kind == "discard_each_opponent":
                    self.assertIsNotNone(outcome)
                    for seat in ("B", "C", "D"):
                        result = game.act(f"pilot:{seat}", {"action_id": "choose", "cards": [cards[seat].ref]})
                        self.assertTrue(result.ok, result.summary)
                    self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in cards.values()))
                else:
                    self.assertIsNone(outcome)
                self.assertEqual(game.state.players["A"].life, original_life + (self.life_amount if kind == "gain_life" else 0))
                tokens = [card for card in game.state.cards.values()
                          if card.is_token and card.zone == "battlefield" and card.controller == "A"]
                self.assertEqual(len(tokens), int(kind == "create_token"))
                self.assertEqual(game.state.cards[top].zone, "hand")
                self.assertFalse(any(isinstance(item, ResolvedNextDrawReplacement) for item in game.state.continuous_effects))
                self.replay(game, load=True)

    def test_scheduled_draw_admission_fails_closed_when_capability_is_withdrawn(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.oracle_ir import compile_oracle_card
        from quorune.next_draw_replacement_model import NEXT_DRAW_REPLACEMENT_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry

        card = self.db.lookup(self.sources[0][0], fuzzy=False)
        supported = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", supported.status)
        real_closure = CapabilityRegistry.closure

        def missing_dependency(registry, requested, *, profile):
            closure = real_closure(registry, requested, profile=profile)
            if NEXT_DRAW_REPLACEMENT_CAPABILITY in closure.reachable:
                return replace(closure, trusted=False, blockers=("dependency:scheduled-draw-withdrawn",))
            return closure

        with patch.object(CapabilityRegistry, "closure", new=missing_dependency):
            blocked = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(any("scheduled-draw-withdrawn" in str(residual) for residual in blocked.material_residuals))

    def test_scheduled_draw_catches_a_retained_selected_grant_mutant(self):
        from unittest.mock import patch

        game, draw, _ = self.prepare_draw(53116199, ("The next time you would draw a card this turn, you gain 5 life instead.",))
        self.cast_for(game, "A", draw)
        with patch("quorune.drawing.scheduled_draw_results.consume_continuous_effects", return_value=1):
            with self.assertRaisesRegex(AssertionError, "no longer owns a consumed grant"):
                self.resolve(game)


    def test_scheduled_draw_replaces_the_turn_based_draw_without_an_enclosing_spell(self):
        from test_vanishing_rules import VanishingRuntimeTests

        game, _, _ = self.prepare_draw(53116200, ())
        candidate = self.add(game.engine, self.sources[0][0])
        self.assert_closed(game, candidate)
        VanishingRuntimeTests.upkeep(self, game, seat="A")
        self.checkpoint(game)
        original_life = game.state.players["A"].life
        library = tuple(game.state.players["A"].zones["library"])
        result = game.act("pilot:A", {"action_id": f"activate:{candidate.ref}:ab1"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda session: session.state.phase == "beginning" and session.state.step == "draw"
                   and session.state.pending_decision is not None
                   and session.state.pending_decision.kind == "priority")
        self.assertFalse(game.state.stack)
        self.assertEqual(game.state.players["A"].life, original_life + self.life_amount)
        self.assertEqual(tuple(game.state.players["A"].zones["library"]), library)
        self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
