"""Actual public sacrifice and later private discard through ordinary APNAP."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.sacrifice_discard_fallback import SACRIFICE_DISCARD_FALLBACK_CAPABILITY, sacrifice_discard_fallback_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CreaturePlaneswalkerSacrificeDiscardTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-planeswalker-sacrifice-discard.sqlite3"
        fixture = ROOT / "tests/fixtures/creature-planeswalker-sacrifice-discard-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Public sacrifice and private fallback", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def empty_hand(self, game, *seats):
        for seat in seats:
            for object_id in tuple(game.state.players[seat].zones["hand"]):
                game.engine.move_card(object_id, "library", reason="Prepare the private hand boundary", log=False)

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool["U"] = 1
        game.state.players["C"].mana_pool.update({"U": 1, "C": 1})
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)

    def entry(self, game, spell):
        self.cast(game, "A", spell)
        self.resolve_top(game)
        self.assertEqual("battlefield", self.zone(game, spell))
        self.assertTrue(any(item.source_object_id == spell.object_id and item.kind == "triggered_ability"
                            for item in game.state.stack))

    def stage(self, game, seat):
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(1, context["count"])
        return context

    def choose(self, game, seat, card):
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref]})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_actual_self_and_planeswalker_sacrifices_commit_before_the_two_private_discards(self):
        game = self.session(76355001)
        self.empty_hand(game, "C", "D")
        source = self.add(game.engine, "Plaguecrafter", zone="hand")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        c_drop = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="plague-c-private-discard")
        c_keep = self.add(game.engine, "Forest", seat="C", zone="hand", ref="plague-c-private-keep")
        d_drop = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="plague-d-private-discard")
        d_keep = self.add(game.engine, "Island", seat="D", zone="hand", ref="plague-d-private-keep")
        self.ready(game, source, walker)
        self.entry(game, source)
        self.assertEqual([source.ref], self.stage(game, "A")["options"])
        self.reject(game, "A", {"action_id": "choose", "cards": [walker.ref]})
        self.choose(game, "A", source)
        context = self.stage(game, "B")
        self.assertEqual([walker.ref], context["options"])
        self.assertEqual([source.ref], context["prior_public_choices"]["A"])
        self.assertEqual("battlefield", self.zone(game, source))
        self.reject(game, "B", {"action_id": "choose", "cards": [artifact.ref]})
        self.choose(game, "B", walker)

        context = self.stage(game, "C")
        self.assertEqual("graveyard", self.zone(game, source))
        self.assertEqual("graveyard", self.zone(game, walker))
        self.assertEqual("battlefield", self.zone(game, artifact))
        self.assertEqual({c_drop.ref, c_keep.ref}, set(context["options"]))
        self.assertEqual({}, context["prior_public_choices"])
        for principal in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            self.assertNotIn(c_drop.ref, packet)
            self.assertNotIn(c_drop.object_id, packet)
        game = self.replay(game, load=True)
        self.choose(game, "C", c_drop)
        context = self.stage(game, "D")
        self.assertEqual("hand", self.zone(game, c_drop))
        self.assertEqual({d_drop.ref, d_keep.ref}, set(context["options"]))
        self.assertEqual({}, context["prior_public_choices"])
        self.assertNotIn(c_drop.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.reject(game, "D", {"action_id": "choose", "cards": [c_drop.ref]})
        self.choose(game, "D", d_drop)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard"), (self.zone(game, c_drop), self.zone(game, d_drop)))
        self.assertEqual(("hand", "hand"), (self.zone(game, c_keep), self.zone(game, d_keep)))
        sacrifices, = (event for event in game.state.events if event.code == "choice.sacrifice")
        discards, = (event for event in game.state.events if event.code == "choice.discard")
        self.assertLess(game.state.events.index(sacrifices), game.state.events.index(discards))
        self.replay(game, load=True)

    def test_actual_bounced_source_and_phased_creature_leave_only_their_players_in_the_fallback(self):
        game = self.session(76355002)
        self.empty_hand(game, "A", "C", "D")
        source = self.add(game.engine, "Plaguecrafter", zone="hand")
        a_drop = self.add(game.engine, "Plains", zone="hand", ref="plague-a-private-drop")
        b_victim = self.add(game.engine, "Grizzly Bears", seat="B", ref="plague-b-ordinary-victim")
        c_victim = self.add(game.engine, "Grizzly Bears", seat="C", ref="plague-c-phased")
        c_drop = self.add(game.engine, "Forest", seat="C", zone="hand", ref="plague-c-after-phasing")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        phase = self.add(game.engine, "Reality Ripple", seat="C", zone="hand")
        self.ready(game, source, bounce, phase)
        self.entry(game, source)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.cast(game, "C", phase, [c_victim.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.zone(game, source))
        self.assertTrue(game.state.cards[c_victim.object_id].phased_out)
        b_hand = tuple(game.state.players["B"].zones["hand"])
        self.assertEqual([b_victim.ref], self.stage(game, "B")["options"])
        self.choose(game, "B", b_victim)
        self.assertEqual({source.ref, a_drop.ref}, set(self.stage(game, "A")["options"]))
        self.assertEqual("graveyard", self.zone(game, b_victim))
        self.choose(game, "A", a_drop)
        self.assertEqual([c_drop.ref], self.stage(game, "C")["options"])
        self.choose(game, "C", c_drop)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard", "hand"), (
            self.zone(game, a_drop), self.zone(game, c_drop), self.zone(game, source),
        ))
        self.assertEqual(b_hand, tuple(game.state.players["B"].zones["hand"]))
        self.assertTrue(game.state.cards[c_victim.object_id].phased_out)
        self.replay(game, load=True)

    def test_actual_exile_replacement_preserves_sacrifice_qualification_and_later_discard(self):
        game = self.session(76355003)
        self.empty_hand(game, "C", "D")
        source = self.add(game.engine, "Plaguecrafter", zone="hand")
        victim = self.add(game.engine, "Grizzly Bears", seat="B")
        c_drop = self.add(game.engine, "Island", seat="C", zone="hand", ref="plague-pending-private")
        leyline = self.add(game.engine, "Leyline of the Void", seat="B")
        rest = self.add(game.engine, "Rest in Peace", seat="D")
        self.ready(game, source, leyline, rest)
        self.entry(game, source)
        b_hand = tuple(game.state.players["B"].zones["hand"])
        self.stage(game, "A")
        self.choose(game, "A", source)
        self.stage(game, "B")
        self.choose(game, "B", victim)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(("battlefield", "battlefield", "hand"), (
            self.zone(game, source), self.zone(game, victim), self.zone(game, c_drop),
        ))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        result = game.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([c_drop.ref], self.stage(game, "C")["options"])
        self.assertEqual(("exile", "exile"), (self.zone(game, source), self.zone(game, victim)))
        self.choose(game, "C", c_drop)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["C"]), (pending.kind, pending.actors))
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        result = game.act("pilot:C", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", self.zone(game, c_drop))
        self.assertEqual(b_hand, tuple(game.state.players["B"].zones["hand"]))
        self.replay(game, load=True)

    def test_complete_original_retains_the_independent_exact_entry_sacrifice_and_fallback(self):
        cap = SACRIFICE_DISCARD_FALLBACK_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Plaguecrafter")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        self.assertEqual("permanent.enter.self", program.event)
        self.assertEqual((sacrifice_discard_fallback_effect(),), tuple(dict(value) for value in program.effects))
        self.assertIsNone(program.target_schema)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Plaguecrafter", fuzzy=False)
        for before, after in (("creature or planeswalker", "creature or artifact"), ("Each player who can't", "Each opponent who can't"),
                              ("discards a card.", "may discard a card."), ("a card.", "a card. Rewrite history.")):
            with self.subTest(unsupported=after):
                candidate = replace(card, oracle_text=card.oracle_text.replace(before, after))
                lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)
