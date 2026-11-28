"""The Ozolith's departure copy and combat movement through native commands."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler import counter_movement_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_counter_movement_model import OPTIONAL_COMBAT_ALL_COUNTER_MOVEMENT_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import (
    CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry,
)
from quorune.rules.departure_counter_copy import DEPARTURE_COUNTERS, DEPARTURE_COUNTER_COPY_CAPABILITY
from scripts.build_test_database import build_fixture_database


OZOLITH = "The Ozolith"
COPY = DEPARTURE_COUNTER_COPY_CAPABILITY
MOVE = OPTIONAL_COMBAT_ALL_COUNTER_MOVEMENT_CAPABILITY
PROFILE = "commander_review"


def counter_archive_record():
    return CardRecord(
        oracle_id="f5d4d7ab-9955-4a88-b65e-ab932832e4f1", name="Counter Archive",
        mana_cost="{1}", mana_value=1, type_line="Legendary Artifact",
        oracle_text=(
            "Whenever a creature you control leaves the battlefield, if it had counters on it, "
            "put those counters on Counter Archive.\n"
            "At the beginning of combat on your turn, if Counter Archive has counters on it, "
            "you may move all counters from Counter Archive onto target creature."
        ),
        power=None, toughness=None, loyalty=None, defense=None, colors=(), color_identity=(),
        keywords=(), produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class OzolithCounterCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, card=None, capabilities=None):
        return compile_oracle_card(
            card or counter_archive_record(), capability_registry=capabilities or self.capabilities,
            capability_profile=PROFILE,
        )

    def test_printed_counter_copy_and_optional_move_are_distinct_exact_nodes(self):
        card = counter_archive_record()
        result = self.compile(card)
        self.assertEqual("exact", result.status, result.material_residuals)
        copy, move = result.faces[0].nodes
        self.assertEqual(("permanent.leave", "step.begin"), (copy.event, move.event))
        self.assertEqual(["place_counter_batch", "move_fixed_counter"],
                         [copy.effects[0]["op"], move.effects[0]["op"]])
        self.assertIsNone(copy.target_schema)
        self.assertEqual(["creature"], move.target_schema["types_any"])
        self.assertNotIn("controller_relation", move.target_schema)
        self.assertIn(COPY, copy.capability_dependencies)
        self.assertIn(MOVE, move.capability_dependencies)
        for node, expected in zip((copy, move), card.oracle_text.splitlines(), strict=True):
            self.assertEqual(expected, card.oracle_text[node.span.start:node.span.end])

    def test_removing_either_capability_or_original_binding_fails_closed(self):
        source = DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf8")
        for capability in (COPY, MOVE):
            with self.subTest(withdrawn=capability):
                raw = json.loads(source)
                row = next(row for row in raw["capabilities"] if row["id"] == capability)
                row.update(status="blocked", blockers=["Withdraw the exact counter family"])
                blocked = CapabilityRegistry(raw)
                blocked.mark_evidence_verified("1" * 64)
                result = self.compile(capabilities=blocked)
                self.assertNotEqual("exact", result.status, capability)

        result_for = counter_movement_templates.source_counter_trigger_result
        for operation in ("place_counter_batch", "move_fixed_counter"):
            with self.subTest(broken_role=operation):
                def omit_role(binding, body):
                    candidate = result_for(binding, body)
                    if candidate is None or candidate[1][0]["op"] != operation:
                        return candidate
                    template, (effect,), targets, mechanics = candidate
                    wrong_role = ({"card": "$context.card"} if operation == "place_counter_batch"
                                  else {"from_ref": "$target.0"})
                    return template, ({**effect, **wrong_role},), targets, mechanics

                with patch.object(counter_movement_templates, "source_counter_trigger_result", omit_role):
                    self.assertNotEqual("exact", self.compile().status)

        card = counter_archive_record()
        for before, after in (
            ("a creature you control", "a creature an opponent controls"),
            ("if it had counters on it", "if it had +1/+1 counters on it"),
            ("combat on your turn", "each combat"),
            ("move all counters", "move any number of counters"),
            ("onto target creature", "onto target artifact"),
        ):
            with self.subTest(after=after):
                self.assertNotEqual("exact", self.compile(
                    replace(card, oracle_text=card.oracle_text.replace(before, after)),
                ).status)


class OzolithCounterNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "ozolith.sqlite3"
        build_fixture_database(ROOT / "tests" / "fixtures" / "ozolith-counter-cards.json", database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Ozolith counter witness", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def ready(self, game, *cards, mana=None, opponent_mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def source_stack(self, game, source):
        return [item for item in game.state.stack if item.source_object_id == source.object_id]

    def combat_target(self, game, receiver):
        self.until(game, lambda current: (
            current.state.pending_decision is not None
            and current.state.pending_decision.kind == "semantic.target"
        ))
        self.assertEqual(("A", "beginning_combat"), (game.state.active_player, game.state.step))
        self.target(game, "A", [self.current(game, receiver).ref])

    def movement_choice(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))

    def test_actual_nondeath_departure_copies_the_preserved_four_kind_lki_snapshot(self):
        game = self.session(122020101)
        source = self.add(game.engine, OZOLITH, zone="hand")
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="borrowed-countered-bear")
        game.engine.change_control(borrowed.object_id, "A", reason="borrowed-owner fixture")
        counters = {"+1/+1": 2, "charge": 3, "flying": 1, "time": 2}
        borrowed.counters.update(counters)
        enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-countered-bear")
        enemy.counters["+1/+1"] = 4
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.ready(game, source, bounce, kill, mana={"B": 2, "C": 2}, opponent_mana={"U": 1})
        self.cast_for(game, "A", source, reserve={"B": 2, "C": 1})
        self.resolve_top(game)
        self.assertEqual("battlefield", self.current(game, source).zone)
        self.cast_for(game, "B", bounce, [borrowed.ref])
        self.resolve_top(game)
        self.assertEqual(("hand", "B", {}), (
            self.current(game, borrowed).zone, self.current(game, borrowed).owner, self.current(game, borrowed).counters,
        ))
        item, = self.source_stack(game, source)
        self.assertEqual(("A", []), (item.controller, item.targets))
        self.assertEqual(counters, dict(item.context[DEPARTURE_COUNTERS]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(counters, self.current(game, source).counters)

        self.cast_for(game, "A", kill, [enemy.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, enemy).zone)
        self.assertEqual([], self.source_stack(game, source))
        self.assertEqual(counters, self.current(game, source).counters)
        self.replay(game, load=True)

    def test_mixed_move_keeps_the_impossible_kind_and_removes_original_quantities(self):
        game = self.session(122050102)
        source = self.add(game.engine, OZOLITH)
        receiver = self.add(game.engine, "Grizzly Bears", seat="B")
        halver = self.add(game.engine, "Vorinclex, Monstrous Raider", seat="B")
        counters = {"+1/+1": 2, "charge": 2, "flying": 1}
        source.counters.update(counters)
        self.ready(game, source, halver)
        self.combat_target(game, receiver)
        self.movement_choice(game)
        self.reject(game, "C", {"action_id": "choose", "choice": "move"})
        self.reject(game, "A", {"action_id": "choose", "choice": "move", "counter_kind": "+1/+1"})
        self.assertEqual(counters, self.current(game, source).counters)
        self.assertEqual({}, self.current(game, receiver).counters)
        game = self.replay(game, load=True)

        done = game.act("pilot:A", {"action_id": "choose", "choice": "move"})
        self.assertTrue(done.ok, done.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"flying": 1}, self.current(game, source).counters)
        self.assertEqual({"+1/+1": 1, "charge": 1}, self.current(game, receiver).counters)
        self.replay(game, load=True)

    def test_optional_move_checks_initial_and_resolving_counter_presence_and_live_target(self):
        for index, scenario in enumerate(("empty", "decline", "empty_on_resolution", "target_left")):
            with self.subTest(scenario=scenario):
                game = self.session(603040103 + index)
                source = self.add(game.engine, OZOLITH)
                receiver = self.add(game.engine, "Grizzly Bears", seat="B")
                support = []
                if scenario != "empty":
                    source.counters.update({"+1/+1": 1, "charge": 1})
                if scenario == "empty_on_resolution":
                    hexmage = self.add(game.engine, "Vampire Hexmage", seat="B")
                    support.append(hexmage)
                if scenario == "target_left":
                    bounce = self.add(game.engine, "Unsummon", zone="hand")
                    self.add(game.engine, "Island", ref="combat-blue")
                    support.append(bounce)
                self.ready(game, source, *support)
                if scenario == "empty":
                    self.until(game, lambda current: current.state.step == "beginning_combat")
                    self.assertEqual([], self.source_stack(game, source))
                else:
                    self.combat_target(game, receiver)
                    if scenario == "empty_on_resolution":
                        self.priority(game, "B")
                        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                        offer, = [row for row in actions if row["id"].startswith("activate:" + hexmage.ref + ":")]
                        result = game.act("pilot:B", {"action_id": offer["id"], "targets": [source.ref], "pay": "auto"})
                        self.assertTrue(result.ok, result.summary)
                        self.resolve_top(game)
                        self.assertEqual({}, self.current(game, source).counters)
                    elif scenario == "target_left":
                        self.cast_for(game, "A", bounce, [receiver.ref])
                        self.resolve_top(game)
                        self.assertEqual("hand", self.current(game, receiver).zone)
                    else:
                        self.movement_choice(game)
                        result = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
                        self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                    if scenario != "empty_on_resolution":
                        self.assertEqual({"+1/+1": 1, "charge": 1}, self.current(game, source).counters)
                self.assertEqual({}, self.current(game, receiver).counters)
                self.replay(game, load=True)

    def test_blinked_source_cannot_move_counters_from_its_new_incarnation(self):
        game = self.session(400070107)
        source = self.add(game.engine, OZOLITH)
        source.counters["charge"] = 1
        receiver = self.add(game.engine, "Grizzly Bears", seat="B", ref="combat-recipient")
        departing = self.add(game.engine, "Grizzly Bears", ref="new-source-counter-provider")
        departing.counters["flying"] = 2
        blink = self.add(game.engine, "Ghostly Flicker", zone="hand")
        kill = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.add(game.engine, "Island", ref="blue-flicker-land")
        other = self.add(game.engine, "Plains", ref="other-blink-target")
        self.add(game.engine, "Plains", ref="extra-white-payment")
        for index in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"black-removal-payment-{index}")
        self.ready(game, source, blink, kill)
        original = source.logical_object_id
        self.combat_target(game, receiver)
        self.cast_for(game, "A", blink, [source.ref, other.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, self.current(game, source).logical_object_id)
        self.assertEqual({}, self.current(game, source).counters)

        self.cast_for(game, "B", kill, [departing.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, departing).zone)
        self.resolve_top(game)
        self.assertEqual({"flying": 2}, self.current(game, source).counters)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"flying": 2}, self.current(game, source).counters)
        self.assertEqual({}, self.current(game, receiver).counters)
        self.replay(game, load=True)
