"""Original optional and mandatory target roles reuse one atomic +1/+1 motion."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_counter_movement_model import FIXED_COUNTER_MOVEMENT_CAPABILITY, FixedCounterMovementSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


PROFILE = "commander_review"


class FixedOneCounterMovementTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.MonarchMovementReplacementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-one-counter-movement.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-one-counter-movement-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("One-counter movement", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def add_counted(self, game, name, count, *, seat="A", ref=None):
        settings = {"seat": seat, **({"ref": ref} if ref else {})}
        card = self.add(game.engine, name, **settings)
        if count:
            card.counters["+1/+1"] = count
        return card

    def count(self, game, card):
        return game.state.cards[card.object_id].counters.get("+1/+1", 0)

    def lands(self, game, name, amount, *, seat="A"):
        for index in range(amount):
            self.add(game.engine, name, seat=seat, ref=f"move-price-{seat}-{index}")

    def ready(self, game, *cards, actor="A"):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=actor, active=actor)
        self.checkpoint(game)

    def activate(self, game, source, ability, targets):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"] == f"activate:{source.ref}:{ability}")
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_upkeep_targets_empty_or_live_donor_then_moves_only_when_possible_and_accepted(self):
        for mode in ("move", "decline", "empty", "departed"):
            with self.subTest(mode=mode):
                game = self.session(76335010 + len(mode))
                source = self.add_counted(game, "Arcbound Fiend", 3)
                donor = self.add_counted(game, "Grizzly Bears", int(mode != "empty"), seat="B")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand") if mode == "departed" else None
                if bounce:
                    self.lands(game, "Island", 1, seat="B")
                    self.assert_closed(game, bounce)
                previous = game.state.turn_order[game.state.turn_order.index("A") - 1]
                game.state.last_normal_turn_player = previous
                self.ready(game, source, actor=previous)
                self.until(game, lambda session: session.state.active_player == "A" and session.state.step == "upkeep")
                self.target(game, "A", [donor.ref])
                if bounce:
                    self.cast(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                pending = self.resolve(game)
                if mode in {"move", "decline"}:
                    self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                    self.reject(game, "B", {"action_id": "choose", "choice": "move"})
                    game = self.replay(game, load=True)
                    result = game.act("pilot:A", {"action_id": "choose", "choice": mode})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                else:
                    self.assertIsNone(pending)
                self.assertEqual(int(mode in {"decline", "departed"}), self.count(game, donor))
                if mode == "departed":
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                else:
                    self.assertEqual(4 if mode == "move" else 3, self.count(game, source))
                self.replay(game, load=True)

    def test_actual_root_kin_mandatory_move_uses_only_a_controlled_donor(self):
        game = self.session(76335030)
        source = self.add_counted(game, "Cytoplast Root-Kin", 4)
        donor = self.add_counted(game, "Grizzly Bears", 2, ref="owned-donor")
        foreign = self.add_counted(game, "Grizzly Bears", 2, seat="B", ref="foreign-donor")
        self.lands(game, "Plains", 2)
        self.ready(game, source)
        self.reject(game, "A", {"action_id": f"activate:{source.ref}:ab3", "targets": [foreign.ref], "pay": "auto"})
        self.activate(game, source, "ab3", [donor.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((5, 1, 2), tuple(self.count(game, card) for card in (source, donor, foreign)))
        self.replay(game, load=True)

    def test_actual_daghatar_requires_two_distinct_live_targets_and_keeps_placement_replacement(self):
        for missing, competing in ((False, False), (True, False), (False, True)):
            with self.subTest(missing=missing, competing=competing):
                game = self.session(76335040 + int(missing) + 2 * int(competing))
                source = self.add_counted(game, "Daghatar the Adamant", 4)
                donor = self.add_counted(game, "Grizzly Bears", 2, seat="B", ref="daghatar-donor")
                recipient = self.add_counted(game, "Grizzly Bears", 0, ref="daghatar-recipient")
                scales = self.add(game.engine, "Hardened Scales")
                season = self.add(game.engine, "Doubling Season") if competing else None
                if season is not None:
                    self.assert_closed(game, season)
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand") if missing else None
                self.lands(game, "Forest", 3)
                if bounce:
                    self.lands(game, "Island", 1, seat="B")
                    self.assert_closed(game, bounce)
                self.ready(game, source, scales)
                self.reject(game, "A", {"action_id": f"activate:{source.ref}:ab3",
                                         "targets": [donor.ref, donor.ref], "pay": "auto"})
                self.activate(game, source, "ab3", [donor.ref, recipient.ref])
                if bounce:
                    self.cast(game, "B", bounce, [recipient.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                pending = self.resolve(game)
                if competing:
                    self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
                    self.assertEqual((2, 0), (self.count(game, donor), self.count(game, recipient)))
                    game = self.replay(game, load=True)
                    options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                    selection, = (row["id"] for row in options if row["source"] == scales.ref)
                    result = game.act("pilot:A", {"action_id": "choose", "replacement": selection})
                    self.assertTrue(result.ok, result.summary)
                    pending = self.resolve(game)
                self.assertIsNone(pending)
                expected = (2, 0) if missing else (1, 4 if competing else 2)
                self.assertEqual(expected, (self.count(game, donor), self.count(game, recipient)))
                self.replay(game, load=True)

    def test_complete_originals_keep_their_exact_roles_and_require_movement_authority(self):
        cap = FIXED_COUNTER_MOVEMENT_CAPABILITY
        for name, (report, semantics) in (
            ("Arcbound Fiend", register_card(self.db, self.registry, "Arcbound Fiend")),
            ("Cytoplast Root-Kin", register_card(self.db, self.registry, "Cytoplast Root-Kin")),
            ("Daghatar the Adamant", register_card(self.db, self.registry, "Daghatar the Adamant")),
        ):
            with self.subTest(original=name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (row for row in semantics.programs_for_oracle(report["oracle_id"]) if cap in row.capability_dependencies)
                spec = FixedCounterMovementSpec.from_effect(program.effects[0])
                self.assertEqual(name == "Arcbound Fiend", spec.optional)
                deps = tuple(key for key in program.capability_dependencies if key != cap)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Arcbound Fiend", fuzzy=False)
        for old, new in (("move a +1/+1 counter", "move two +1/+1 counters"),
                         ("At the beginning of your upkeep", "At the beginning of combat on your turn"),
                         ("onto this creature.", "onto this creature. Rewrite history.")):
            self.assertIn(old, card.oracle_text)
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
