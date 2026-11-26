"""Native Ashiok loyalty costs and linked exile across actual player commands."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.commander_zones import (
    commit_commander_zone_choice_decline,
    pending_commander_zone_state_choices,
)
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_ability_exile_state import record_linked_ability_exile
from quorune.linked_exile_return_model import ExiledObjectIdentity
from quorune.rules.linked_loyalty_exile_model import (
    LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY,
    LINKED_LOYALTY_EXILE_PAIR,
    LINKED_LOYALTY_OPPONENT_ZONES_CAPABILITY,
    LINKED_LOYALTY_TOP_EXILE_CAPABILITY,
    LOYALTY_X_CAPABILITY,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ASHIOK = "Ashiok, Nightmare Weaver"
PROFILE = "commander_review"
LINK_CAPABILITY = "zone.exile.linked_ability_pair"


class AshiokNightmareWeaverTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ashiok-nightmare-weaver.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/ashiok-nightmare-weaver-cards.json", path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Rograkh, Son of Rohgahh"
        cls.deck = DeckDefinition(
            "Ashiok nightmare weaver",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Mountain", 30)],
            [commander],
        )

    def source(self, game, *, seat="B", zone="battlefield", loyalty=3):
        source = self.add(game.engine, ASHIOK, seat=seat, zone=zone, ref=f"ashiok-source-{seat}")
        if zone == "battlefield":
            source.counters["loyalty"] = loyalty
            source.annotations["loyalty_initialized"] = True
        self.assert_closed(game, source)
        return source

    def concealed_library_card(self, game, name, seat, ref):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=ref)
        card.known_to = []
        card.revealed_to = []
        self.assertEqual(card.object_id, game.state.players[seat].zones["library"][-1])
        return card

    def prepare_main(self, game, seat, *, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active=seat, actor=seat, mana=mana)
        self.checkpoint(game)

    def act_ok(self, game, seat, command):
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def assert_rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def offer_ids(self, game, seat):
        self.priority(game, seat)
        decision = game.packet(f"pilot:{seat}", full=True)["decision"]
        return {row["id"] for row in decision["ctx"]["legal"]["actions"]}

    def activate(self, game, seat, source, ability, **payload):
        action_id = f"activate:{source.ref}:ab{ability}"
        self.assertIn(action_id, self.offer_ids(game, seat))
        self.act_ok(game, seat, {"action_id": action_id, **payload})

    def next_main(self, game, seat):
        turn = game.state.turn_sequence

        def arrival_or_cleanup(session):
            decision = session.state.pending_decision
            return decision is not None and (decision.kind == "cleanup.discard" or (
                session.state.turn_sequence > turn
                and session.state.active_player == seat
                and session.state.phase == "precombat_main"
                and decision.kind == "priority"
                and decision.actors == [seat]
            ))

        for _ in range(len(game.state.turn_order) + 1):
            self.until(game, arrival_or_cleanup)
            decision = game.state.pending_decision
            if decision.kind == "priority":
                return
            actor, = decision.actors
            context = game.packet(f"pilot:{actor}", full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            self.act_ok(game, actor, {"action_id": "discard", "cards": cards})
        self.fail(f"Seat {seat} did not reach its next main phase")

    def exact_choice_refs(self, game):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["B"]), (decision.kind, list(decision.actors)))
        packet = game.packet("pilot:B", full=True)["decision"]
        return {row["id"] for row in packet["ctx"]["objects"]}

    def stage_linked_decoys(self, game, source):
        owner = ZoneTransitionOwner(game.engine)
        hidden = self.concealed_library_card(game, "Serra Angel", "C", "ashiok-face-down-decoy")
        hidden = owner.move_card(hidden.object_id, "exile", private_library_exile_viewer="C", log=False)
        artifact = self.add(game.engine, "Gilded Lotus", seat="C", zone="graveyard", ref="ashiok-artifact-decoy")
        artifact = owner.move_card(artifact.object_id, "exile", reveal_to=tuple(game.engine.seats), log=False)
        record_linked_ability_exile(
            game.engine,
            source=ContinuousObjectIdentity(source.object_id, source.logical_object_id),
            ability_pair=LINKED_LOYALTY_EXILE_PAIR,
            objects=tuple(ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id)
                          for card in (hidden, artifact)),
        )
        return hidden, artifact

    def test_plus_two_exiles_opponent_top_three_and_consumes_turn_loyalty(self):
        game = self.session(8406070301)
        source = self.source(game, zone="hand")
        fourth = self.concealed_library_card(game, "Forest", "C", "ashiok-hidden-fourth")
        first = self.concealed_library_card(game, "Spined Wurm", "C", "ashiok-hidden-wurm")
        second = self.concealed_library_card(game, "Air Elemental", "C", "ashiok-hidden-elemental")
        third = self.concealed_library_card(game, "Grizzly Bears", "C", "ashiok-hidden-bears")
        elsewhere = self.concealed_library_card(game, "Charging Rhino", "D", "ashiok-hidden-other-library")
        top = (third, second, first)
        self.prepare_main(game, "B", mana={"C": 1, "U": 1, "B": 1})
        self.cast_for(game, "B", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.cards[source.object_id].counters["loyalty"])
        libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.assertEqual(tuple(card.object_id for card in reversed(top)), libraries["C"][-3:])
        for seat in "ABCD":
            view = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in (*top, fourth, elsewhere):
                self.assertNotIn(card.ref, view)

        turn = game.state.turn_sequence
        action_id = f"activate:{source.ref}:ab1"
        self.assertIn(action_id, self.offer_ids(game, "B"))
        self.assert_rejected(game, "B", {"action_id": action_id, "targets": ["B"]})
        self.assert_rejected(game, "A", {"action_id": action_id, "targets": ["C"]})
        self.activate(game, "B", source, 1, targets=["C"])
        self.assertEqual(5, game.state.cards[source.object_id].counters["loyalty"])
        self.assertEqual(libraries["C"], tuple(game.state.players["C"].zones["library"]))
        self.assertEqual(["C"], list(game.state.stack[-1].targets))
        self.assertIsNone(self.resolve(game))

        for seat in "ABCD":
            self.assertEqual(libraries[seat][:-3] if seat == "C" else libraries[seat],
                             tuple(game.state.players[seat].zones["library"]))
            view = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in top:
                current = game.state.cards[card.object_id]
                self.assertEqual(("exile", "C", False), (current.zone, current.owner, current.face_down))
                self.assertIn(card.ref, view)
            for card in (fourth, elsewhere):
                self.assertNotIn(card.ref, view)
        offers = self.offer_ids(game, "B")
        self.assertEqual(turn, game.state.turn_sequence)
        self.assertNotIn(action_id, offers)
        self.assertNotIn(f"activate:{source.ref}:ab2", offers)
        self.assert_rejected(game, "B", {"action_id": action_id, "targets": ["C"]})
        self.assert_rejected(game, "B", {"action_id": f"activate:{source.ref}:ab2", "x": 0})
        self.replay(game)

    def test_minus_x_uses_source_link_exact_value_and_preserves_owner_after_zero(self):
        game = self.session(8406070302)
        source = self.source(game)
        foreign_source = self.source(game, seat="A")
        hidden, artifact = self.stage_linked_decoys(game, source)
        discovery = self.add(game.engine, "Kindred Discovery", seat="B")
        discovery.annotations["chosen_creature_type"] = "nightmare"
        self.assert_closed(game, discovery)
        first = self.concealed_library_card(game, "Spined Wurm", "C", "ashiok-correct-wurm")
        second = self.concealed_library_card(game, "Air Elemental", "C", "ashiok-correct-elemental")
        lower = self.concealed_library_card(game, "Grizzly Bears", "C", "ashiok-incorrect-lower-value")
        foreign = self.concealed_library_card(game, "Charging Rhino", "D", "ashiok-foreign-source")
        self.prepare_main(game, "A")
        self.activate(game, "A", foreign_source, 1, targets=["D"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[foreign.object_id].zone)
        self.next_main(game, "B")
        self.activate(game, "B", source, 1, targets=["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[source.object_id].counters["loyalty"])
        self.next_main(game, "B")

        action_id = f"activate:{source.ref}:ab2"
        self.assertIn(action_id, self.offer_ids(game, "B"))
        self.assert_rejected(game, "B", {"action_id": action_id, "x": 6})
        self.assert_rejected(game, "B", {"action_id": action_id, "x": -1})
        self.assert_rejected(game, "B", {"action_id": action_id, "x": 5, "targets": [first.ref]})
        source_identity = game.state.cards[source.object_id].logical_object_id
        self.activate(game, "B", source, 2, x=5)
        stack_item = game.state.stack[-1]
        self.assertEqual((5, ()), (stack_item.x_value, tuple(stack_item.targets)))
        departed = game.state.cards[source.object_id]
        self.assertEqual(("graveyard", "B"), (departed.zone, departed.owner))
        self.assertNotEqual(source_identity, departed.logical_object_id)
        self.assertEqual({first.ref, second.ref}, self.exact_choice_refs(game))
        for seat in "ACD":
            self.assertIsNone(game.packet(f"pilot:{seat}", full=True)["decision"])
        for seat in "ABD":
            packet = game.packet(f"pilot:{seat}", full=True)
            masked, = (row for row in packet["state"]["players"]["C"]["ex"] if row["id"] == hidden.ref)
            self.assertEqual(("?", 1), (masked["n"], masked["fd"]))
            self.assertNotIn("cid", masked)
            self.assertNotIn(hidden.printed_name, json.dumps(packet))
        self.assertIn(hidden.printed_name, json.dumps(game.packet("pilot:C", full=True)))

        game = self.replay(game, load=True)
        for illegal in (hidden.ref, artifact.ref, lower.ref, foreign.ref, "forged-ashiok-card"):
            self.assert_rejected(game, "B", {"action_id": "choose", "card": illegal})
        self.assert_rejected(game, "C", {"action_id": "choose", "card": first.ref})
        hand = len(game.state.players["B"].zones["hand"])
        original_exile = game.state.cards[first.object_id].logical_object_id
        self.act_ok(game, "B", {"action_id": "choose", "card": first.ref})
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[first.object_id]
        self.assertEqual(("battlefield", "C", "B"), (current.zone, current.owner, current.controller))
        self.assertNotEqual(original_exile, current.logical_object_id)
        self.assertLessEqual({"nightmare", "wurm"}, set(game.engine._effective_type_parts(current)[1]))
        self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
        for card in (second, lower, foreign, hidden, artifact):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.replay(game)

    def test_minus_ten_exiles_all_other_hands_and_graveyards(self):
        game = self.session(8406070303)
        source = self.source(game, loyalty=12)
        own_hand = self.add(game.engine, "Hill Giant", seat="B", zone="hand", ref="ashiok-own-hand")
        own_grave = self.add(game.engine, "Goblin Piker", seat="B", zone="graveyard", ref="ashiok-own-grave")
        zero_hand = self.add(game.engine, "Memnite", zone="hand", ref="ashiok-ultimate-zero-hand")
        zero_grave = self.add(game.engine, "Ornithopter", seat="D", zone="graveyard", ref="ashiok-ultimate-zero-grave")
        other_hand = self.add(game.engine, "Forest", seat="D", zone="hand", ref="ashiok-ultimate-other-hand")
        self.add(game.engine, "Sol Ring", seat="C", zone="graveyard", ref="ashiok-ultimate-other-grave")
        commander_id, = game.state.players["C"].zones["command"]
        commander = ZoneTransitionOwner(game.engine).move_card(commander_id, "graveyard", log=False)
        choices = pending_commander_zone_state_choices(
            (commander,), active_seats=game.engine.active_seats, apnap_order=game.engine.apnap_order(),
        )
        choice, = choices
        commit_commander_zone_choice_decline(commander, choice)

        self.prepare_main(game, "B")
        frozen = {seat: {zone: tuple(game.state.players[seat].zones[zone])
                         for zone in ("hand", "graveyard")} for seat in "ABCD"}
        libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        private = json.dumps(game.packet("pilot:B", full=True))
        self.assertNotIn(zero_hand.ref, private)
        self.assertNotIn(other_hand.ref, private)
        self.activate(game, "B", source, 3)
        self.assertEqual(2, game.state.cards[source.object_id].counters["loyalty"])
        self.assertEqual(frozen["A"]["hand"], tuple(game.state.players["A"].zones["hand"]))
        pending = self.resolve(game)
        self.assertEqual(("state.commander_zone", ["C"]), (pending.kind, list(pending.actors)))
        exiled = {object_id for seat in "ACD" for ids in frozen[seat].values() for object_id in ids}
        for seat in "ABCD":
            self.assertEqual(libraries[seat], tuple(game.state.players[seat].zones["library"]))
            for zone in ("hand", "graveyard"):
                expected = frozen[seat][zone] if seat == "B" else ()
                self.assertEqual(expected, tuple(game.state.players[seat].zones[zone]))
        for seat in "ABCD":
            view = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card_id in exiled:
                card = game.state.cards[card_id]
                self.assertEqual(("exile", False), (card.zone, card.face_down))
                self.assertIn(card.ref, view)
        self.assertEqual(("hand", "graveyard"), (
            game.state.cards[own_hand.object_id].zone, game.state.cards[own_grave.object_id].zone,
        ))

        self.act_ok(game, "C", {"action_id": "command", "choice": "command"})
        self.assertEqual("command", game.state.cards[commander_id].zone)
        self.assertIsNone(self.resolve(game))
        self.next_main(game, "B")
        self.activate(game, "B", source, 2, x=0)
        self.assertEqual(2, game.state.cards[source.object_id].counters["loyalty"])
        self.assertEqual({zero_hand.ref, zero_grave.ref}, self.exact_choice_refs(game))
        self.assert_rejected(game, "B", {"action_id": "choose", "card": commander.ref})
        self.act_ok(game, "B", {"action_id": "choose", "card": zero_hand.ref})
        self.assertIsNone(self.resolve(game))
        received = game.state.cards[zero_hand.object_id]
        self.assertEqual(("battlefield", "A", "B"), (received.zone, received.owner, received.controller))
        self.assertLessEqual({"construct", "nightmare"}, set(game.engine._effective_type_parts(received)[1]))
        self.replay(game)

    def test_whole_card_requires_each_linked_and_loyalty_authority(self):
        original = self.db.lookup("Ashiok, Nightmare Weaver", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Ashiok, Nightmare Weaver")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        for required in (LINKED_LOYALTY_TOP_EXILE_CAPABILITY, LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY,
                         LINKED_LOYALTY_OPPONENT_ZONES_CAPABILITY):
            with self.subTest(owner=required):
                program, = (program for program in programs if required in program.capability_dependencies)
                keys = {required, LINK_CAPABILITY}
                if required == LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY:
                    keys.add(LOYALTY_X_CAPABILITY)
                self.assertLessEqual(keys, set(program.capability_dependencies))
                for withheld in sorted(keys):
                    deps = tuple(key for key in program.capability_dependencies if key != withheld)
                    changed = replace(
                        program, capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict(),
                    )
                    binding = bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile=PROFILE,
                    )
                    self.assertFalse(binding["strict"])
                    self.assertIn(f"capability:undeclared_runtime_dependency:{withheld}", binding["blockers"])

        def compile_card(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_card(original)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(3, len(ir.faces[0].nodes))
        for before, after in (
            ("top three", "top four"),
            ("mana value X", "mana value X or less"),
            ("a Nightmare in addition to its other types", "a Nightmare"),
            ("all opponents' hands and graveyards", "all players' hands and graveyards"),
        ):
            with self.subTest(altered_text=after):
                changed = replace(original, oracle_text=original.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_card(changed).status)
        with patch(
            "quorune.compiler.linked_loyalty_exile_templates.linked_loyalty_activation_compiler",
            side_effect=lambda fallback, **_context: fallback,
        ):
            self.assertNotEqual("exact", compile_card(original).status)
        vanilla = replace(original, oracle_text="Draw a card.", type_line="Sorcery", loyalty=None)
        self.assertEqual("exact", compile_card(vanilla).status)


if __name__ == "__main__":
    unittest.main()
