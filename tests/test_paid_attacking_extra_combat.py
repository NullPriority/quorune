"""Native optional attacks, ordinary mana, and immediate extra-combat untap."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_damage_triggered_extra_combat as retained

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.extra_combat_model import (
    ATTACKING_CREATURES_VERSION, EXTRA_COMBAT_FOLLOWUP_FIELD, ExtraCombatUntap,
    decode_extra_combat_effect, extra_combat_effect,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.paid_attacking_extra_combat import ATTACK_TRIGGER_DEPENDENCIES
from scripts.build_test_database import build_fixture_database


CARD = "Hellkite Charger"


class PaidAttackingExtraCombatTests(InheritedNativeFixture, unittest.TestCase):
    next_attack = retained.DamageTriggeredExtraCombatTests._next_attack
    started_combats = staticmethod(retained.DamageTriggeredExtraCombatTests._started_combats)

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "paid-attack-extra-combat.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "paid-attacking-extra-combat-cards.json", "activation-restriction-material-cards.json",
            "source-attack-alone-cards.json", "static-step-skip-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid attack extra combat", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

    def payment(self, game, pay):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertIsNone(StateProjector(self.db, game.state)._decision("pilot:B"))
        command = {"action_id": "choose", "pay": pay}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_exact_paid_self_attack_shape_rejects_other_triggers_and_malformed_scope(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        _, node = ir.faces[0].nodes
        self.assertEqual(("creature.attacks", "battlefield", None), (node.event, node.active_zone, node.target_schema))
        self.assertEqual(card.oracle_text.splitlines()[1], card.oracle_text[node.span.start:node.span.end])
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, node.event_condition)
        wrapper, = node.effects
        self.assertEqual(5, wrapper["cost"]["GENERIC"])
        self.assertEqual(2, wrapper["cost"]["R"])
        result, = wrapper["effects"]
        self.assertEqual(extra_combat_effect(ExtraCombatUntap.ATTACKING_CREATURES), result)
        self.assertTrue(set(ATTACK_TRIGGER_DEPENDENCIES) <= set(node.capability_dependencies))
        for old, new in (
            ("this creature attacks", "this creature blocks"), ("all attacking creatures", "all creatures you control"),
            ("{5}{R}{R}", "{X}{R}{R}"), ("after this phase", "after this turn"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        with self.assertRaises(ValueError):
            decode_extra_combat_effect({**result, EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": ATTACKING_CREATURES_VERSION, "attacking_creatures": 1}})
        game = self.session(50804001)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = (item for item in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if item.event == "creature.attacks")
        broken = replace(program, event_condition=None)
        binding = bind_semantic_program_runtime(broken, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])

    def test_two_actual_paid_attack_triggers_untap_now_and_add_combat_only(self):
        game = self.session(50080508)
        first = self.add(game.engine, CARD, ref="first-charger")
        second = self.add(game.engine, CARD, ref="second-charger")
        companion = self.add(game.engine, "Grizzly Bears", ref="attacking-companion")
        companion.acquired_control_turn_count = -1
        outsider = self.add(game.engine, "Grizzly Bears", ref="nonattacking-tapped")
        outsider.tapped = True
        island = self.add(game.engine, "Island", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        lands = [self.add(game.engine, "Mountain", ref=f"mountain-payment-{index}") for index in range(14)]
        self.start(game, first, second, bounce)

        self.attack(game, [(first, "B"), (second, "B"), (companion, "B")])
        current = game.state.combat.damage_sequence_id
        turn = game.state.turn_sequence
        self.assertEqual(1, len(self.started_combats(game, turn)))
        self.assertEqual(1, self.trigger_count(game, first))
        self.assertEqual(1, self.trigger_count(game, second))
        self.assertTrue(game.state.cards[first.object_id].tapped)
        self.assertTrue(game.state.cards[companion.object_id].tapped)
        self.cast_for(game, "B", bounce, [second.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[second.object_id].zone)
        self.assertTrue(game.state.cards[island.object_id].tapped)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(0, game.state.extra_combat_phases)
        game = self.replay(game, load=True)
        self.payment(game, True)
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual(7, sum(game.state.cards[m.object_id].tapped for m in lands))
        self.assertFalse(game.state.cards[first.object_id].tapped)
        self.assertFalse(game.state.cards[companion.object_id].tapped)
        self.assertTrue(game.state.cards[outsider.object_id].tapped)
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.payment(game, True)
        self.assertEqual(2, game.state.extra_combat_phases)
        self.assertTrue(all(game.state.cards[m.object_id].tapped for m in lands))
        self.assertFalse(game.state.delayed_triggers)
        game = self.replay(game, load=True)

        added_cutoff = game.state.event_sequence
        additional = self.next_attack(game, current)
        self.assertEqual(1, game.state.extra_combat_phases)
        self.attack(game, [(companion, "B")])
        last = self.next_attack(game, additional)
        self.assertEqual(3, len({current, additional, last}))
        self.assertEqual(0, game.state.extra_combat_phases)
        self.assertTrue(game.state.cards[companion.object_id].tapped)
        self.assertTrue(game.state.cards[outsider.object_id].tapped)
        added = [event for event in self.started_combats(game, turn) if event.event_id > added_cutoff]
        self.assertEqual(2, len(added))
        self.assertFalse(any(event.code == "step.begin" and event.turn_sequence == turn
                             and event.phase == "postcombat_main" for event in game.state.events))
        self.attack(game, [])
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        self.assertEqual(turn, game.state.turn_sequence)
        self.replay(game, load=True)

    def test_declined_attack_trigger_leaves_tap_and_schedule_unchanged(self):
        game = self.session(60802001)
        source = self.add(game.engine, CARD)
        lands = [self.add(game.engine, "Mountain", ref=f"declined-payment-{index}") for index in range(7)]
        self.start(game, source)
        self.attack(game, [(source, "B")])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.payment(game, False)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertTrue(all(not game.state.cards[m.object_id].tapped for m in lands))
        self.assertEqual(0, game.state.extra_combat_phases)
        self.assertFalse(game.state.delayed_triggers)
        self.replay(game, load=True)
