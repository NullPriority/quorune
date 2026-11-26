"""Native witnesses for the original source and target counter batch."""

from dataclasses import replace
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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
CAPABILITY = "counter.producer.target_and_source_multikind"
ARWEN = "Arwen, Mortal Queen"


class SourceTargetCounterBatchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-target-counter-batch.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-target-counter-batch-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source and target counters", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Forest", 30),
        ], [commander])

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def keywords(self, game, card):
        return game.engine._combat_keywords(self.current(game, card))

    def counters(self, game, card):
        return self.current(game, card).counters

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def arwen_scene(self, seed, *, target_seat="B", reply=None, replacements=False):
        game = self.session(seed)
        source = self.add(game.engine, ARWEN, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat=target_seat, ref="arwen-other-creature")
        reply_card = None
        if reply == "Shock":
            reply_card = self.add(game.engine, "Shock", seat="B", zone="hand")
            self.add(game.engine, "Mountain", seat="B")
        elif reply == "Unsummon":
            reply_card = self.add(game.engine, "Unsummon", seat="B", zone="hand")
            self.add(game.engine, "Island", seat="B")
        scales = season = None
        if replacements:
            scales = self.add(game.engine, "Hardened Scales")
            season = self.add(game.engine, "Doubling Season", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        for card in (source, target, reply_card, scales, season):
            if card is not None:
                self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "G": 2, "W": 1} if replacements else {"C": 2, "G": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("battlefield", self.current(game, source).zone)
        self.assertEqual({"indestructible": 1}, self.counters(game, source))
        self.assertIn("indestructible", self.keywords(game, source))
        if season is not None:
            self.cast(game, "A", season)
            self.resolve_top(game)
        self.assertEqual(1, sum(game.state.players["A"].mana_pool.values()))
        return game, source, target, reply_card, scales

    def activate(self, game, source, target, *, check_rejections=False):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        command = {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"}
        if check_rejections:
            self.reject(game, "B", command)
            self.reject(game, "A", {**command, "targets": [source.ref]})
        before = sum(game.state.players["A"].mana_pool.values())
        self.act(game, "A", command)
        self.assertEqual(before - 1, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual(0, self.counters(game, source).get("indestructible", 0))
        self.assertEqual([target.ref], list(game.state.stack[-1].targets))

    def test_paid_original_grants_indestructible_and_places_both_counter_kinds_on_both_roles(self):
        game, source, target, _, _ = self.arwen_scene(9519401)
        self.activate(game, source, target, check_rejections=True)
        self.assertEqual({}, self.counters(game, source))
        self.assertEqual({}, self.counters(game, target))
        self.assertNotIn("indestructible", self.keywords(game, source))
        self.assertNotIn("indestructible", self.keywords(game, target))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1, "lifelink": 1}, self.counters(game, source))
        self.assertEqual({"+1/+1": 1, "lifelink": 1}, self.counters(game, target))
        self.assertIn("lifelink", self.keywords(game, source))
        self.assertNotIn("indestructible", self.keywords(game, source))
        self.assertTrue({"indestructible", "lifelink"} <= self.keywords(game, target))
        self.replay(game, load=True)

    def test_actual_replacements_pause_one_batch_after_grant_without_partial_placement(self):
        game, source, target, _, scales = self.arwen_scene(9519402, target_seat="A", replacements=True)
        self.activate(game, source, target)
        decision = self.resolve(game)
        for _ in range(2):
            self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
            self.assertIn("indestructible", self.keywords(game, target))
            self.assertEqual({}, self.counters(game, source))
            self.assertEqual({}, self.counters(game, target))
            game = self.replay(game, load=True)
            payload = game.packet("pilot:A", full=True)["decision"]["ctx"]
            option, = (row for row in payload["options"] if row["source"] == scales.ref)
            self.act(game, "A", {"action_id": "choose", "replacement": option["id"]})
            decision = self.resolve(game)
        self.assertIsNone(decision)
        self.assertEqual({"+1/+1": 4, "lifelink": 2}, self.counters(game, source))
        self.assertEqual({"+1/+1": 4, "lifelink": 2}, self.counters(game, target))
        self.replay(game, load=True)

    def test_paying_indestructible_counter_after_real_lethal_damage_keeps_target_result(self):
        game, source, target, shock, _ = self.arwen_scene(9519403, reply="Shock")
        self.cast(game, "B", shock, [source.ref])
        self.resolve_top(game)
        self.assertEqual("battlefield", self.current(game, source).zone)
        self.assertEqual(2, self.current(game, source).marked_damage)
        self.assertEqual({"indestructible": 1}, self.counters(game, source))
        self.activate(game, source, target)
        self.assertEqual("graveyard", self.current(game, source).zone)
        self.assertEqual({}, self.counters(game, target))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, source).zone)
        self.assertEqual({}, self.counters(game, source))
        self.assertEqual({"+1/+1": 1, "lifelink": 1}, self.counters(game, target))
        self.assertTrue({"indestructible", "lifelink"} <= self.keywords(game, target))
        self.replay(game, load=True)

    def test_actual_sole_target_departure_stops_grant_and_both_placements(self):
        game, source, target, bounce, _ = self.arwen_scene(9519404, reply="Unsummon")
        self.activate(game, source, target)
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, target).zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.current(game, source).zone)
        self.assertEqual({}, self.counters(game, source))
        self.assertEqual({}, self.counters(game, target))
        self.assertNotIn("indestructible", self.keywords(game, source))
        self.assertNotIn("indestructible", self.keywords(game, target))
        self.replay(game, load=True)

    def test_paired_replacement_identity_retains_both_subjects_and_legacy_fields(self):
        from quorune.semantic_choices.intent_replacement import (
            semantic_intent_identity, validate_semantic_intent_identity,
        )
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_runtime.intents import CounterPlacementAmount, PlaceCounterBatchIntent

        unpaired = PlaceCounterBatchIntent(
            actor="A", object_ref="target-original", reason="Original counter batch",
            placements=(CounterPlacementAmount("+1/+1", 1), CounterPlacementAmount("lifelink", 1)),
            source_ref="source-original",
        )
        kind, old_identity = semantic_intent_identity(unpaired)
        self.assertNotIn("paired_source", old_identity)
        self.assertEqual(old_identity, validate_semantic_intent_identity(kind, old_identity))
        paired = replace(unpaired, paired_source_ref="source-original")
        self.assertEqual(("target-original", "source-original"), paired.object_refs)
        self.assertEqual((kind, {**old_identity, "paired_source": {"schema_version": 1, "ref": "source-original"}}),
                         semantic_intent_identity(paired))
        _, identity = semantic_intent_identity(paired)
        self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))
        for invalid in ({"schema_version": True, "ref": "source-original"},
                        {"schema_version": 1, "ref": "another-source"}):
            with self.assertRaises(SemanticChoiceError):
                validate_semantic_intent_identity(kind, {**identity, "paired_source": invalid})

    def test_complete_original_requires_distinct_target_and_paired_placement_authority(self):
        card = self.db.lookup("Arwen, Mortal Queen", fuzzy=False)
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, "Arwen, Mortal Queen")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(programs))
        activated, = (program for program in programs if CAPABILITY in program.capability_dependencies)
        self.assertEqual("activate", activated.event)
        self.assertTrue(activated.target_schema["source_exclusion"])
        self.assertEqual(["grant_keyword_until_end_of_turn", "place_counter_batch"],
                         [effect["op"] for effect in activated.effects])
        for withdrawn in (CAPABILITY, "permanent.indestructible.ordinary", "damage.result.lifelink",
                          "activation.source_counter_removal.fixed"):
            dependencies = tuple(cap for cap in activated.capability_dependencies if cap != withdrawn)
            changed = replace(activated, capability_dependencies=dependencies,
                              capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"], withdrawn)
        for changed in (
            replace(activated, target_schema={**activated.target_schema, "source_exclusion": False}),
            replace(activated, effects=tuple(reversed(activated.effects))),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("and a lifelink counter on Arwen", "and a flying counter on Arwen"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)
