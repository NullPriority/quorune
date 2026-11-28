"""Current opponent discard redirection respects replacement choices and entry owners."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.opponent_discard_entry_model import (
    OPPONENT_DISCARD_ENTRY_CAPABILITY, OPPONENT_DISCARD_ENTRY_COUNTER_CAPABILITY, OPPONENT_DISCARD_ENTRY_HANDLER,
    OpponentDiscardEntrySpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class OpponentDiscardEntryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-discard-entry.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-discard-entry-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opponent discard entry", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def scene(self, seed):
        game = self.session(seed)
        for seat in "AB":
            for object_id in tuple(game.state.players[seat].zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        return game

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)

    def card(self, game, card):
        return game.state.cards[card.object_id]

    def offer_discard(self, game, rot, seat, cards):
        self.cast(game, "A", rot, [seat])
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        view = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(len(cards), view["count"])
        self.assertEqual({card.ref for card in cards}, set(view["options"]))
        return {"action_id": "choose", "cards": [card.ref for card in cards]}

    def test_actual_opponent_discards_enter_with_existing_tapped_and_counter_replacements(self):
        for prevent in (False, True):
            with self.subTest(prevent=prevent):
                game = self.scene(709072601 + prevent)
                rot = self.add(game.engine, "Mind Rot", zone="hand")
                dodecapod = self.add(game.engine, "Dodecapod", seat="B", zone="hand", ref="discard-subject-1")
                baloth = self.add(game.engine, "Obstinate Baloth", seat="B", zone="hand", ref="discard-subject-2")
                scales = self.add(game.engine, "Hardened Scales", seat="B")
                orb = self.add(game.engine, "Orb of Dreams", seat="C")
                if prevent:
                    self.add(game.engine, "Solemnity", seat="D")
                self.ready(game, rot, dodecapod, baloth, scales, orb)
                command = self.offer_discard(game, rot, "B", [dodecapod, baloth])
                for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
                    view = json.dumps(game.packet(principal, full=True))
                    self.assertNotIn(dodecapod.ref, view)
                    self.assertNotIn(dodecapod.printed_name, view)
                self.reject(game, "A", command)
                self.act(game, "B", command)
                entered = [self.card(game, card) for card in (dodecapod, baloth)]
                self.assertEqual([("battlefield", "B", True)] * 2,
                                 [(card.zone, card.controller, card.tapped) for card in entered])
                doubled = entered[0].counters.get("+1/+1", 0)
                self.assertEqual(0 if prevent else 3, doubled)
                self.assertEqual(40, game.state.players["B"].life)
                self.assertTrue(any(item.source_object_id == baloth.object_id for item in game.state.stack))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(44, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_two_other_originals_enter_and_keep_their_independent_static_results(self):
        game = self.scene(709072610)
        rot = self.add(game.engine, "Mind Rot", zone="hand")
        liege = self.add(game.engine, "Wilt-Leaf Liege", seat="B", zone="hand")
        smiter = self.add(game.engine, "Loxodon Smiter", seat="B", zone="hand")
        self.ready(game, rot, liege, smiter)
        self.act(game, "B", self.offer_discard(game, rot, "B", [liege, smiter]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["battlefield"] * 2, [self.card(game, card).zone for card in (liege, smiter)])
        self.assertEqual((6, 6), tuple(game.engine._numeric_stat(smiter.object_id, key)
                                      for key in ("power", "toughness")))
        self.replay(game, load=True)

    def test_competing_destination_choice_stays_private_and_counts_as_an_actual_discard(self):
        for enter in (False, True):
            with self.subTest(enter=enter):
                game = self.scene(709072620 + enter)
                rot = self.add(game.engine, "Mind Rot", zone="hand")
                smiter = self.add(game.engine, "Loxodon Smiter", seat="B", zone="hand", ref="discard-private-1")
                leyline = self.add(game.engine, "Leyline of the Void")
                caress = self.add(game.engine, "Liliana's Caress")
                self.ready(game, rot, smiter, leyline, caress)
                self.act(game, "B", self.offer_discard(game, rot, "B", [smiter]))
                self.assertIn("replacement", game.state.pending_decision.kind)
                self.assertEqual(["pilot:B"], game.pending_principals())
                self.assertEqual("hand", self.card(game, smiter).zone)
                for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
                    view = json.dumps(game.packet(principal, full=True))
                    for hidden in (smiter.ref, smiter.object_id, smiter.printed_name):
                        self.assertNotIn(hidden, view)
                game = self.replay(game, load=True)
                options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                option, = (value["id"] for value in options
                           if value["id"].startswith(OPPONENT_DISCARD_ENTRY_HANDLER) is enter)
                command = {"action_id": "choose", "replacement": option}
                self.reject(game, "A", command)
                self.act(game, "B", command)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if enter else "exile", self.card(game, smiter).zone)
                self.assertEqual(38, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_own_effect_and_explicit_historical_origin_absence_preserve_graveyard_discard(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                game = self.scene(709072630 + legacy)
                rot = self.add(game.engine, "Mind Rot", zone="hand")
                recipient = "B" if legacy else "A"
                dodecapod = self.add(game.engine, "Dodecapod", seat=recipient, zone="hand")
                if legacy:
                    game.state.discard_effect_origin_version = None
                self.ready(game, rot, dodecapod)
                self.act(game, recipient, self.offer_discard(game, rot, recipient, [dodecapod]))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", self.card(game, dodecapod).zone)
                self.assertEqual({}, self.card(game, dodecapod).counters)
                self.replay(game, load=True)

    def test_pox_counts_the_original_discard_replacement_entrant_in_its_next_round(self):
        game = self.scene(709072650)
        for seat in "CD":
            for object_id in tuple(game.state.players[seat].zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        pox = self.add(game.engine, "Pox", zone="hand")
        dodecapod = self.add(game.engine, "Dodecapod", seat="B", zone="hand", ref="discard-round-entrant")
        bears = [self.add(game.engine, "Grizzly Bears", seat="B", ref=f"discard-round-bear-{i}") for i in range(3)]
        game.state.config.semantic_policy = "trusted_only"
        for card in (pox, dodecapod):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 3})
        self.checkpoint(game)
        self.cast(game, "A", pox)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        view = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual((1, [dodecapod.ref]), (view["count"], view["options"]))
        self.act(game, "B", {"action_id": "choose", "cards": [dodecapod.ref]})
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        view = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual(2, view["count"])
        self.assertEqual({dodecapod.ref, *(bear.ref for bear in bears)}, set(view["options"]))
        self.assertEqual(2, self.card(game, dodecapod).counters["+1/+1"])
        game = self.replay(game, load=True)
        self.act(game, "B", {"action_id": "choose", "cards": [bear.ref for bear in bears[:2]]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.card(game, dodecapod).zone)
        self.assertEqual(["graveyard", "graveyard", "battlefield"], [self.card(game, bear).zone for bear in bears])
        self.replay(game, load=True)

    def test_actual_activation_discard_price_does_not_acquire_opponent_effect_provenance(self):
        game = self.scene(709072640)
        source = self.add(game.engine, "Rummaging Goblin")
        source.acquired_control_turn_count = -1
        dodecapod = self.add(game.engine, "Dodecapod", zone="hand")
        self.ready(game, source, dodecapod)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (action["id"] for action in actions if action["id"].startswith(f"activate:{source.ref}:"))
        self.act(game, "A", {"action_id": offered, "cost_cards": [dodecapod.ref], "pay": "auto"})
        self.assertEqual("graveyard", self.card(game, dodecapod).zone)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_all_originals_keep_complete_programs_and_counted_entry_cannot_omit_its_owner(self):
        for name in ("Dodecapod", "Obstinate Baloth", "Wilt-Leaf Liege", "Loxodon Smiter"):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = (node for node in ir.faces[0].nodes if node.event == "zone.change")
                self.assertIn("an opponent controls", card.oracle_text[node.span.start:node.span.end])
                self.assertEqual((OpponentDiscardEntrySpec(2 if name == "Dodecapod" else 0).to_descriptor(),),
                                 tuple(node.handlers))
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                replacement, = (p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "zone.change")
                cap = OPPONENT_DISCARD_ENTRY_COUNTER_CAPABILITY if name == "Dodecapod" else OPPONENT_DISCARD_ENTRY_CAPABILITY
                deps = tuple(value for value in replacement.capability_dependencies if value != cap)
                missing = replace(replacement, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Dodecapod", fuzzy=False)
        for before, after in (("an opponent controls", "you control"), ("two +1/+1", "three +1/+1"),
                              ("battlefield with", "battlefield tapped with"), ("your graveyard.", "your graveyard. Draw a card.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
