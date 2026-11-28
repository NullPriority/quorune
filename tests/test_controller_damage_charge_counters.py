"""The damage recipient may charge the original artifact and spend the counters."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, register_current_fixture_programs
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
SOURCE = "Confluence Gauge"
SMALL = "Primer Spark"
LARGE = "Arc Burst"
SHIELD = "Conduit Cover"
THEFT = "Borrow the Instrument"
BOUNCE = "Release from Calibration"
BLINK = "Phase the Instrument"


def synthetic_cards():
    definitions = (
        (SOURCE, "{3}", 3, "Artifact", "", (
            "Whenever you are dealt damage, you may put that many charge counters on this artifact.\n"
            "{1}, {T}, Remove three charge counters from this artifact: You gain 4 life and draw one card."
        )),
        (SMALL, "{R}", 1, "Instant", "R", f"{SMALL} deals 1 damage to any target."),
        (LARGE, "{R}", 1, "Instant", "R", f"{LARGE} deals 4 damage to any target."),
        (SHIELD, "{W}", 1, "Instant", "W", (
            "Prevent the next 3 damage that would be dealt to any target this turn."
        )),
        (THEFT, "{U}", 1, "Sorcery", "U", "Gain control of target artifact until end of turn."),
        (BOUNCE, "{U}", 1, "Instant", "U", "Return target artifact to its owner's hand."),
        (BLINK, "{1}{W}", 2, "Instant", "W", (
            "Exile target artifact you control, then return it to the battlefield under its owner's control."
        )),
    )
    return [
        {
            "oracle_id": f"00000000-0000-4000-8000-{120419100 + index:012}",
            "name": name,
            "mana_cost": cost,
            "cmc": mana_value,
            "type_line": type_line,
            "oracle_text": text,
            "colors": list(color),
            "color_identity": list(color),
            "layout": "normal",
            "legalities": {"commander": "legal"},
        }
        for index, (name, cost, mana_value, type_line, color, text) in enumerate(definitions)
    ]


class ControllerDamageChargeCounterTests(InheritedNativeFixture, unittest.TestCase):
    source_name = SOURCE
    small_name = SMALL
    large_name = LARGE
    shield_name = SHIELD
    theft_name = THEFT
    bounce_name = BOUNCE
    blink_name = BLINK
    blink_other_name = None
    small_damage = 1
    large_damage = 4
    shield_amount = 3
    price = 3
    mana_price = 1
    life_gain = 4

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        directory = Path(cls.temporary.name)
        fixture = directory / "synthetic-cards.json"
        fixture.write_text(
            json.dumps({"schema_version": 1, "cards": synthetic_cards(), "rulings": []}),
            encoding="utf-8",
        )
        path = directory / "damage-charge-counters.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures/bound-effect-program-cards.json", fixture], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition(
            "Damage and charge counters",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            [commander],
        )

    def assert_rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def spell(self, game, seat, card, targets=(), *, reserve=None):
        self.cast_for(game, seat, card, targets, reserve=reserve)
        self.resolve_top(game)

    def choose_counters(self, game, seat, choice):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [seat]), (pending.kind, pending.actors))
        result = game.act("pilot:" + seat, {"action_id": "choose", "choice": choice})
        self.assertTrue(result.ok, result.summary)

    def counters(self, game, source):
        return game.state.cards[source.object_id].counters.get("charge", 0)

    def test_charge_subscription_binds_and_rejects_changed_roles(self):
        card = self.db.lookup(self.source_name, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, [item.kind for item in ir.material_residuals])
        self.assertFalse(ir.material_residuals)
        spans = sorted((node.span for node in ir.faces[0].nodes), key=lambda span: span.start)
        self.assertTrue(card.oracle_text.splitlines() == [card.oracle_text[span.start:span.end] for span in spans])
        semantics = SemanticRegistry()
        register_current_fixture_programs(
            self.db, semantics, (card,), capability_registry=self.registry, profile=PROFILE,
        )
        program = compile_best_available_card_program(
            self.db, card, semantic_registry=semantics, capability_registry=self.registry, capability_profile=PROFILE,
        )
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}
        trigger = programs["damage.dealt"]
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, trigger.coverage)
        self.assertEqual({"all": [
            {"field": "target_kind", "op": "eq", "value": "player"},
            {"field": "target", "op": "eq", "value": "$source.controller"},
        ]}, trigger.event_condition)
        self.assertEqual("charge", trigger.effects[0]["effect"]["counter"])
        self.assertEqual("$source.zone_object", trigger.effects[0]["effect"]["card"])
        self.assertEqual("committed_event_amount", trigger.effects[0]["effect"]["amount"]["origin"])
        activation = programs["activate"]
        self.assertEqual(["life", "draw"], [effect["op"] for effect in activation.effects])
        self.assertIs(activation.effects[1]["private"], True)

        invalid_forms = (
            card.oracle_text.replace("dealt damage", "caused to lose life"),
            card.oracle_text.replace("on this artifact.", "on target artifact."),
            card.oracle_text.replace("charge counters on this artifact.", "quest counters on this artifact."),
            card.oracle_text.replace("on this artifact.", "on this creature."),
        )
        for index, text in enumerate(invalid_forms):
            with self.subTest(variant=index):
                changed = compile_oracle_card(
                    replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=PROFILE,
                )
                self.assertNotEqual("exact", changed.status)

        invalid_programs = (
            replace(trigger, event_condition=None),
            replace(trigger, coverage=tuple(tag for tag in trigger.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(trigger, provenance={k: v for k, v in trigger.provenance.items() if k != "template_id"}),
            replace(trigger, effects=[{**trigger.effects[0], "player": "B"}]),
        )
        for index, forged in enumerate(invalid_programs):
            with self.subTest(program_variant=index):
                self.assertFalse(bind_semantic_program_runtime(
                    forged, capability_registry=self.registry, profile=PROFILE,
                )["strict"])

    def test_committed_damage_from_own_dealer_seals_counters_and_choice_authority(self):
        game = self.session(120419101)
        source = self.add(game.engine, self.source_name, zone="hand")
        shield = self.add(game.engine, self.shield_name, zone="hand")
        stopped = self.add(game.engine, self.small_name, zone="hand")
        partial = self.add(game.engine, self.large_name, zone="hand", ref="partly-prevented-shot")
        foreign = self.add(game.engine, self.large_name, zone="hand", ref="different-seat-shot")
        opposing = self.add(game.engine, self.small_name, seat="B", zone="hand", ref="opposing-shot")
        for card in (source, shield, stopped, partial):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "W": 1, "R": 3})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.spell(game, "A", source, reserve={"W": 1, "R": 3})
        self.spell(game, "A", foreign, ["B"])
        self.assertEqual(0, self.trigger_count(game, source))
        self.spell(game, "B", opposing, ["A"])
        self.assertEqual(1, self.trigger_count(game, source))
        self.choose_counters(game, "A", "decline")
        self.assertEqual(0, self.counters(game, source))
        life = game.state.players["A"].life
        self.spell(game, "A", shield, ["A"])
        self.spell(game, "A", stopped, ["A"])
        self.assertEqual(life, game.state.players["A"].life)
        self.assertEqual(0, self.trigger_count(game, source))

        self.spell(game, "A", partial, ["A"])
        remaining = self.large_damage - (self.shield_amount - self.small_damage)
        self.assertEqual(life - remaining, game.state.players["A"].life)
        self.assertEqual(1, self.trigger_count(game, source))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        self.assert_rejected(game, "C", {"action_id": "choose", "choice": "put"})
        self.assert_rejected(game, "A", {"action_id": "choose", "choice": "all"})
        old = game.engine.permissions.capability_for("pilot:A")
        self.choose_counters(game, "A", "put")
        self.assertEqual(remaining, self.counters(game, source))
        before = authoritative_state_hash(game.state)
        rejected = game.engine.try_submit(
            token=old.token, principal="pilot:A", action="choose", payload={"choice": "put"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.replay(game, load=True)

    def test_activation_pays_now_draws_privately_for_resolving_controller_after_departure(self):
        game = self.session(120419102)
        source = self.add(game.engine, self.source_name)
        source.counters.update({"charge": self.price - 1, "quest": self.price})
        theft = self.add(game.engine, self.theft_name, seat="B", zone="hand")
        damaged = self.add(game.engine, self.large_name, seat="B", zone="hand")
        bounce = self.add(game.engine, self.bounce_name, zone="hand")
        for card in (source, theft, damaged, bounce):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 5, "R": 1, "U": 2})
        game.state.players["A"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.spell(game, "B", theft, [source.ref])
        current = game.state.cards[source.object_id]
        self.assertEqual(("A", "B"), (current.owner, current.controller))

        def actions():
            return game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]

        self.priority(game, "B")
        ability, = game.engine._activated_abilities(current)
        command = {"action_id": f"activate:{current.ref}:{ability.ability_id}", "pay": "auto"}
        self.assertFalse(any(row["id"] == command["action_id"] for row in actions()))
        self.assert_rejected(game, "B", command)
        self.assertEqual(self.price - 1, self.counters(game, source))
        self.assertFalse(game.state.cards[source.object_id].tapped)

        self.spell(game, "B", damaged, ["B"])
        self.choose_counters(game, "B", "put")
        self.priority(game, "B")
        paid = next(row for row in actions() if row["id"] == command["action_id"])
        self.assertEqual(self.price, paid["cost_summary"]["remove_source_counter"]["amount"])
        self.assert_rejected(game, "C", command)
        before_life = {seat: player.life for seat, player in game.state.players.items()}
        before_hand = len(game.state.players["B"].zones["hand"])
        private_draw = game.state.players["B"].zones["library"][-1]
        before_mana = sum(game.state.players["B"].mana_pool.values())
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("B", game.state.stack[-1].controller)
        current = game.state.cards[source.object_id]
        self.assertEqual({"charge": self.large_damage - 1, "quest": self.price}, current.counters)
        self.assertTrue(current.tapped)
        self.assertEqual(before_mana - self.mana_price, sum(game.state.players["B"].mana_pool.values()))
        self.assertEqual(before_life["B"], game.state.players["B"].life)
        self.assertEqual(before_hand, len(game.state.players["B"].zones["hand"]))

        self.spell(game, "A", bounce, [current.ref])
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        before_events = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        resolution = [event.code for event in game.state.events[before_events:]
                      if event.code in {"effect.life", "card.draw.private"}]
        self.assertEqual(["effect.life", "card.draw.private"], resolution)
        self.assertEqual({**before_life, "B": before_life["B"] + self.life_gain},
                         {seat: player.life for seat, player in game.state.players.items()})
        self.assertEqual(before_hand + 1, len(game.state.players["B"].zones["hand"]))
        self.assertIn(private_draw, game.state.players["B"].zones["hand"])
        private_ref = game.state.cards[private_draw].ref
        self.assertIn(private_ref, json.dumps(game.packet("pilot:B", full=True)))
        for seat in "ACD":
            self.assertNotIn(private_ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_queued_counters_cannot_move_to_returned_artifact(self):
        game = self.session(120419103)
        source = self.add(game.engine, self.source_name)
        shot = self.add(game.engine, self.small_name, zone="hand")
        blink = self.add(game.engine, self.blink_name, zone="hand")
        targets = [source.ref]
        if self.blink_other_name is not None:
            targets.append(self.add(game.engine, self.blink_other_name).ref)
        self.assert_closed(game, source)
        self.assert_closed(game, blink)
        self.prepare(game, mana={"C": 2, "R": 1, "U": 1, "W": 1})
        self.checkpoint(game)
        self.spell(game, "A", shot, ["A"])
        self.assertEqual(1, self.trigger_count(game, source))
        identity = source.logical_object_id
        self.spell(game, "A", blink, targets)
        returned = game.state.cards[source.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(identity, returned.logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self.counters(game, source))
        self.replay(game, load=True)

    def test_lethal_damage_never_offers_counter_placement(self):
        game = self.session(120419104)
        source = self.add(game.engine, self.source_name)
        shot = self.add(game.engine, self.small_name, seat="B", zone="hand")
        game.state.players["A"].life = self.small_damage
        self.prepare(game, actor="B", active="B", mana={"R": 1})
        self.checkpoint(game)
        self.spell(game, "B", shot, ["A"])
        self.assertFalse(game.state.players["A"].in_game)
        self.assertEqual(0, self.trigger_count(game, source))
        pending = game.state.pending_decision
        self.assertTrue(pending is None or pending.kind != 'semantic.choice')
        self.assertFalse(any(decision.get("kind") == "semantic.choice" for decision in game.decisions))
        self.replay(game, load=True)

    def test_fresh_compiler_mutation_disables_charge_branch(self):
        from quorune.compiler import damage_permanent_event_bindings as compiler

        retained = {body: counter for body, counter in compiler._RECIPIENT_DAMAGE_COUNTER_KINDS.items()
                    if counter != "charge"}
        with patch.dict(compiler._RECIPIENT_DAMAGE_COUNTER_KINDS, retained, clear=True):
            with self.assertRaises(AssertionError):
                self.test_charge_subscription_binds_and_rejects_changed_roles()
