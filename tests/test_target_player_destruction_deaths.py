"""Actual destruction receipts for the original player's token result."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destruction_result_model import DestructionFollowup
from quorune.rules.target_player_destruction_death_tokens import TARGET_PLAYER_DEATH_TOKEN_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class TargetPlayerDestructionDeathTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-player-destruction-deaths.sqlite3"
        fixture = ROOT / "tests/fixtures/target-player-destruction-deaths-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Target player creature deaths", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 3, "W": 1, "G": 1, "U": 1})
        self.prepare(game, mana={"C": 3, "R": 2})
        self.checkpoint(game)

    def tokens(self, game, name):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == name]

    def cast_mogg(self, game, spell, target):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row["id"] == f"cast:{spell.ref}")
        self.assertIn("A", offer["target_schema"]["groups"][0]["legal_refs"])
        self.assertIn(target, offer["target_schema"]["groups"][0]["legal_refs"])
        self.cast(game, "A", spell, [target])

    def regenerate(self, game, troll):
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row["id"].startswith(f"activate:{troll.ref}:"))
        result = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[troll.object_id].regeneration_shields)

    def test_paid_destruction_counts_real_creature_and_token_deaths_for_the_only_selected_player(self):
        game = self.session(76344001)
        spell = self.add(game.engine, "Mogg Infestation", zone="hand")
        own = self.add(game.engine, "Grizzly Bears", ref="destruction-other-controller")
        die = self.add(game.engine, "Grizzly Bears", seat="B", ref="destruction-real-death")
        save = self.add(game.engine, "Grizzly Bears", seat="B", ref="destruction-return-before-resolution")
        myr = self.add(game.engine, "Darksteel Myr", seat="B")
        troll = self.add(game.engine, "Troll Ascetic", seat="B")
        armored = self.add(game.engine, "Grizzly Bears", seat="B", ref="mogg-umbra-protected")
        umbra = self.add(game.engine, "Hyena Umbra", seat="B")
        attach_objects(game.state.cards, umbra, armored, source_timestamp=umbra.zone_timestamp, players=game.state.players)
        soldiers = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.ready(game, spell, myr, troll, soldiers, bounce, umbra)
        self.cast(game, "B", soldiers)
        self.resolve_top(game)
        self.assertEqual(2, len(self.tokens(game, "Soldier Token")))
        self.cast_mogg(game, spell, "B")
        self.cast(game, "B", bounce, [save.ref])
        self.resolve_top(game)
        self.regenerate(game, troll)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[die.object_id].zone)
        self.assertEqual("hand", game.state.cards[save.object_id].zone)
        for unaffected in (myr, own, troll, armored):
            self.assertEqual("battlefield", game.state.cards[unaffected.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[umbra.object_id].zone)
        self.assertEqual(0, game.state.cards[troll.object_id].regeneration_shields)
        self.assertEqual([], self.tokens(game, "Soldier Token"))
        goblins = self.tokens(game, "Goblin Token")
        self.assertEqual(6, len(goblins))
        self.assertEqual({("B", "B")}, {(card.owner, card.controller) for card in goblins})
        self.assertEqual(1, len({card.zone_timestamp for card in goblins}))
        destruction, = [event for event in game.state.events if event.code == "effect.permanent.destroy_set"]
        birth, = [event for event in game.state.events if event.code == "token.create"
                  and event.actor == "B" and event.details["base_quantity"] == len(goblins)]
        self.assertLess(game.state.events.index(destruction), game.state.events.index(birth))
        self.replay(game, load=True)

    def test_paid_redirected_destruction_never_counts_as_death_and_resumes_atomically(self):
        game = self.session(76344002)
        spell = self.add(game.engine, "Mogg Infestation", zone="hand")
        victim = self.add(game.engine, "Grizzly Bears", seat="B")
        rest = self.add(game.engine, "Rest in Peace", seat="B")
        leyline = self.add(game.engine, "Leyline of the Void", seat="C")
        hidden = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="mogg-unrelated-hidden")
        self.ready(game, spell, rest, leyline)
        self.cast_mogg(game, spell, "B")
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        self.assertEqual([], self.tokens(game, "Goblin Token"))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertFalse(game.act("pilot:B", {**command, "replacement": "unoffered"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(game)
        if pending is not None:
            self.assertEqual("replacement.order", pending.kind)
            options = game.packet(game.pending_principals()[0], full=True)["decision"]["ctx"]["options"]
            result = game.act(game.pending_principals()[0], {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[victim.object_id].zone)
        self.assertEqual([], self.tokens(game, "Goblin Token"))
        self.replay(game, load=True)

    def test_paid_departed_or_empty_target_has_no_substitute_creatures_or_token_recipient(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76344011 + departed)
                spell = self.add(game.engine, "Mogg Infestation", zone="hand")
                other = self.add(game.engine, "Grizzly Bears", seat="C")
                if departed:
                    self.add(game.engine, "Grizzly Bears", seat="B", ref="mogg-original-departing")
                self.ready(game, spell)
                self.cast_mogg(game, spell, "B")
                if departed:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
                self.assertEqual([], self.tokens(game, "Goblin Token"))
                self.replay(game, load=True)

    def test_complete_original_requires_the_scoped_death_receipt_and_recipient(self):
        cap = TARGET_PLAYER_DEATH_TOKEN_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Mogg Infestation")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        result = DestructionFollowup.from_destruction_effect(program.effects[0])
        self.assertTrue(result.uses_target_player_death_tokens)
        self.assertEqual("$target.0", result.scope.target_controller)
        self.assertEqual("any", program.target_schema["player_relation"])
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        unavailable = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(unavailable, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        original = self.db.lookup("Mogg Infestation", fuzzy=False)
        for before, after in (("that died", "destroyed"), ("that player creates", "you create"),
                              ("creates two", "creates three"), ("target player", "target opponent"),
                              ("creature tokens.", "creature tokens. Rewrite history.")):
            with self.subTest(unsupported=after):
                synthetic = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)
