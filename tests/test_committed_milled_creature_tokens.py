"""Actual simultaneous Mill and its committed public creature card results."""

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
from quorune.query_effect_amount_model import CastXAmountSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.milled_creature_tokens import MILLED_CREATURE_TOKEN_CAPABILITY, MilledCreatureTokens
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CommittedMilledCreatureTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-milled-creature-tokens.sqlite3"
        fixture = ROOT / "tests/fixtures/committed-milled-creature-tokens-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Announced Mill and committed Zombies", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards, x):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2, **({"C": x} if x else {})})
        self.checkpoint(game)

    def empty_libraries(self, game):
        for seat in "ABCD":
            for object_id in tuple(game.state.players[seat].zones["library"]):
                game.engine.move_card(object_id, "hand", reason="Prepare a short library", log=False)

    def top(self, game, seat, name, suffix="top"):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=f"dread-library-{seat}-{suffix}")
        card.known_to = []
        card.revealed_to = []
        return card

    def assert_hidden(self, game, cards):
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            for card in cards:
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)

    def cast_x(self, game, spell, amount):
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        result = game.act("pilot:A", {"action_id": offer["id"], "x": amount, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(amount, game.state.stack[-1].x_value)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def assert_zombies(self, game, count):
        tokens = [card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token and card.printed_name == "Zombie Token"]
        self.assertEqual(count, len(tokens))
        for token in tokens:
            self.assertEqual(("A", "A", True), (token.owner, token.controller, token.tapped))
            self.assertEqual((2, 2), tuple(game.engine._numeric_stat(token.object_id, kind) for kind in ("power", "toughness")))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_paid_x_counts_public_creatures_with_short_libraries_and_a_real_library_replacement(self):
        game = self.session(76353001)
        self.empty_libraries(game)
        spell = self.add(game.engine, "Dread Summons", zone="hand")
        crypt = self.add(game.engine, "Tormod's Crypt")
        a = self.top(game, "A", "Plains")
        noncreature = self.top(game, "B", "Plains", "bottom")
        creature = self.top(game, "B", "Grizzly Bears")
        redirected = self.top(game, "C", "Blightsteel Colossus")
        self.ready(game, spell, crypt, redirected, x=2)
        self.cast_x(game, spell, 2)
        self.assert_hidden(game, (a, noncreature, creature, redirected))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[a.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual("library", game.state.cards[redirected.object_id].zone)
        amounts = {event.details["player"]: event.details["count"] for event in game.state.events if event.code == "card.mill"}
        self.assertEqual({"A": 1, "B": 2, "C": 1, "D": 0}, amounts)
        self.assert_zombies(game, 1)
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in rows if row["id"].startswith(f"activate:{crypt.ref}:"))
        result = game.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[creature.object_id].zone)
        self.assert_zombies(game, 1)
        self.replay(game, load=True)

    def test_paid_replacement_choice_preserves_hidden_tops_and_replay_with_no_creature_in_graveyard(self):
        game = self.session(76353002)
        self.empty_libraries(game)
        spell = self.add(game.engine, "Dread Summons", zone="hand")
        top = self.top(game, "C", "Grizzly Bears")
        leyline = self.add(game.engine, "Leyline of the Void", seat="B")
        rest = self.add(game.engine, "Rest in Peace", seat="D")
        self.ready(game, spell, leyline, rest, x=1)
        libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.cast_x(game, spell, 1)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["C"]), (pending.kind, pending.actors))
        self.assertEqual(libraries, {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"})
        self.assert_hidden(game, (top,))
        self.assert_zombies(game, 0)
        game = self.replay(game, load=True)
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "B", command)
        self.reject(game, "C", {**command, "replacement": "unoffered"})
        result = game.act("pilot:C", command)
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(game)
        if pending is not None:
            self.assertEqual("replacement.order", pending.kind)
            principal = game.pending_principals()[0]
            options = game.packet(principal, full=True)["decision"]["ctx"]["options"]
            result = game.act(principal, {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[top.object_id].zone)
        self.assert_zombies(game, 0)
        self.replay(game, load=True)

    def test_paid_zero_does_not_move_or_expose_a_top_card_or_create_tokens(self):
        game = self.session(76353003)
        spell = self.add(game.engine, "Dread Summons", zone="hand")
        top = self.top(game, "B", "Grizzly Bears")
        self.ready(game, spell, x=0)
        libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.cast_x(game, spell, 0)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(libraries, {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"})
        self.assert_hidden(game, (top,))
        self.assert_zombies(game, 0)
        self.replay(game, load=True)

    def test_complete_original_uses_only_declared_x_and_independent_public_receipt_authority(self):
        cap = MILLED_CREATURE_TOKEN_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Dread Summons")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        effect, = program.effects
        self.assertEqual("all_active_players", effect["scope"])
        self.assertEqual(CastXAmountSpec().to_dict(), dict(effect["count"]))
        self.assertIs(MilledCreatureTokens.from_dict(effect["followup"]).token["tapped"], True)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        card = self.db.lookup("Dread Summons", fuzzy=False)
        candidates = [replace(card, mana_cost="{2}{B}{B}")]
        for before, after in (("Each player", "Each opponent"), ("a graveyard this way", "exile this way"),
                              ("a tapped", "an untapped"), ("creature token.", "creature token. Rewrite history.")):
            candidates.append(replace(card, oracle_text=card.oracle_text.replace(before, after)))
        for candidate in candidates:
            lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", lowered.status)
