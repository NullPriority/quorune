"""Actual any-number target revalidation and simultaneous former-controller tokens."""

from collections import Counter
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
from quorune.rules.announced_destruction_controller_tokens import (
    ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY, AnnouncedDestructionControllerTokens,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class AnnouncedDestructionControllerTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-destruction-controller-tokens.sqlite3"
        fixture = ROOT / "tests/fixtures/announced-destruction-controller-tokens-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Announced destruction and controller tokens", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"U": 1, "B": 1})
        self.prepare(game, mana=mana or {"C": 4, "R": 2})
        self.checkpoint(game)

    def assert_dragons(self, game, expected):
        tokens = [card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token and card.printed_name == "Dragon Token"]
        self.assertEqual(expected, Counter(card.controller for card in tokens))
        for token in tokens:
            self.assertEqual(token.owner, token.controller)
            self.assertEqual((4, 4), tuple(game.engine._numeric_stat(token.object_id, kind) for kind in ("power", "toughness")))
            self.assertIn("flying", game.engine._combat_keywords(token))
        if tokens:
            self.assertEqual(1, len({card.zone_timestamp for card in tokens}))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_paid_partial_targets_use_current_controllers_and_one_simultaneous_token_batch(self):
        game = self.session(76349001)
        spell = self.add(game.engine, "Descent of the Dragons", zone="hand")
        steal = self.add(game.engine, "Act of Treason", zone="hand")
        flash = self.add(game.engine, "Vedalken Orrery")
        doubler = self.add(game.engine, "Parallel Lives")
        moved = self.add(game.engine, "Grizzly Bears", seat="B", ref="descent-controller-will-change")
        second = self.add(game.engine, "Grizzly Bears", seat="C", ref="descent-other-controller")
        returned = self.add(game.engine, "Grizzly Bears", seat="B", ref="descent-lost-original-target")
        indestructible = self.add(game.engine, "Darksteel Myr", seat="B")
        armored = self.add(game.engine, "Grizzly Bears", seat="B", ref="descent-umbra-protected")
        umbra = self.add(game.engine, "Hyena Umbra", seat="B")
        attach_objects(game.state.cards, umbra, armored, source_timestamp=umbra.zone_timestamp, players=game.state.players)
        invalid = self.add(game.engine, "Sol Ring", seat="D")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.ready(game, spell, steal, flash, doubler, bounce, indestructible, umbra, mana={"C": 6, "R": 3})

        self.priority(game, "A")
        action = self.action(game, "A", spell)
        self.assertIn(second.ref, action["target_schema"]["groups"][0]["legal_refs"])
        self.assertNotIn(invalid.ref, action["target_schema"]["groups"][0]["legal_refs"])
        self.reject(game, "A", {"action_id": action["id"], "targets": [moved.ref, moved.ref], "pay": "auto"})
        self.cast(game, "A", spell, [moved.ref, second.ref, returned.ref, indestructible.ref, armored.ref])
        self.cast(game, "A", steal, [moved.ref])
        self.resolve_top(game)
        self.assertEqual(("B", "A"), (game.state.cards[moved.object_id].owner, game.state.cards[moved.object_id].controller))
        self.cast(game, "B", bounce, [returned.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[returned.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for victim in (moved, second):
            self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
        for survivor in (indestructible, invalid, armored):
            self.assertEqual("battlefield", game.state.cards[survivor.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[umbra.object_id].zone)
        self.assert_dragons(game, {"A": 2, "C": 1})
        self.replay(game, load=True)

    def test_paid_regeneration_and_redirected_destruction_replay_without_an_extra_token(self):
        game = self.session(76349002)
        spell = self.add(game.engine, "Descent of the Dragons", zone="hand")
        skeletons = self.add(game.engine, "Drudge Skeletons", seat="B")
        victim = self.add(game.engine, "Grizzly Bears", seat="C")
        rest = self.add(game.engine, "Rest in Peace")
        leyline = self.add(game.engine, "Leyline of the Void", seat="B")
        hidden = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="descent-replacement-unrelated-hand")
        self.ready(game, spell, skeletons, rest, leyline)
        self.cast(game, "A", spell, [skeletons.ref, victim.ref])
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = (row for row in actions if row["id"].startswith(f"activate:{skeletons.ref}:"))
        paid = game.act("pilot:B", {"action_id": activate["id"], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["C"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        self.assertEqual(1, game.state.cards[skeletons.object_id].regeneration_shields)
        self.assert_dragons(game, {})
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "A", command)
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
        self.assertEqual("exile", game.state.cards[victim.object_id].zone)
        survivor = game.state.cards[skeletons.object_id]
        self.assertEqual(("battlefield", 0), (survivor.zone, survivor.regeneration_shields))
        self.assert_dragons(game, {"C": 1})
        self.replay(game, load=True)

    def test_actual_zero_targets_and_a_lost_original_target_never_create_tokens(self):
        for announce in (False, True):
            with self.subTest(announce=announce):
                game = self.session(76349020 + announce)
                spell = self.add(game.engine, "Descent of the Dragons", zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B", ref="descent-original") if announce else None
                other = self.add(game.engine, "Grizzly Bears", seat="C", ref="descent-unselected") if announce else None
                response = self.add(game.engine, "Unsummon", seat="B", zone="hand") if announce else None
                self.ready(game, spell, *((response,) if response is not None else ()))
                self.cast(game, "A", spell, [target.ref] if announce else [])
                if announce:
                    self.cast(game, "B", response, [target.ref])
                    self.resolve_top(game)
                self.assertIsNone(self.resolve(game))
                if announce:
                    self.assertEqual("hand", game.state.cards[target.object_id].zone)
                    self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
                self.assert_dragons(game, {})
                self.replay(game, load=True)

    def test_complete_original_requires_the_announced_set_and_independent_controller_result(self):
        cap = ANNOUNCED_DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Descent of the Dragons")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        outcome = AnnouncedDestructionControllerTokens.from_dict(program.effects[0]["followup"])
        self.assertEqual("Dragon Token", outcome.token["name"])
        self.assertEqual("any_number", program.target_schema["count_parameter"]["parameter"])
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        unavailable = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(unavailable, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        original = self.db.lookup("Descent of the Dragons", fuzzy=False)
        for before, after in (("any number of target creatures", "any number of target artifacts"),
                              ("destroyed this way", "that died this way"),
                              ("its controller creates", "you create"),
                              ("token with flying.", "token with flying. Rewrite history.")):
            with self.subTest(unsupported=after):
                candidate = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)
