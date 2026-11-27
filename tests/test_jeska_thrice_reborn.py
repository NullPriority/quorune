"""Real Jeska command-zone, loyalty payment and combat replay witnesses."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.commander_pairing import (
    PRINTED_COMMANDER_ELIGIBILITY_TEMPLATE, CommanderPairingError, validate_commander_pair,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.loyalty_damage import (
    LOYALTY_X_DAMAGE_FIELD, bind_loyalty_x_target_damage, loyalty_x_damage_instruction,
)
from quorune.zone_transitions import ZoneTransitionOwner
from quorune.target_combat_damage_multiplier_model import ResolvedTargetCombatDamageMultiplier


JESKA = "Jeska, Thrice Reborn"
PARTNER = "Rograkh, Son of Rohgahh"


class JeskaThriceRebornTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "jeska.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/counted-library-search-cards.json",
            ROOT / "tests/fixtures/ashiok-nightmare-weaver-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Jeska with partner",
            [DeckEntry("Jeska, Thrice Reborn", 1, "commander"), DeckEntry("Rograkh, Son of Rohgahh", 1, "commander"), DeckEntry("Mountain", 30)],
            [JESKA, PARTNER],
        )

    def commander(self, game, seat, name):
        return next(card for card in game.state.cards.values() if card.owner == seat and card.printed_name == name)

    def multipliers(self, game):
        return tuple(effect for effect in game.state.continuous_effects or ()
                     if isinstance(effect, ResolvedTargetCombatDamageMultiplier))

    def source(self, game, *, seat="A", loyalty=3, zone="battlefield"):
        card = self.add(game.engine, "Jeska, Thrice Reborn", seat=seat, zone=zone, ref=f"extra-jeska-{seat}")
        if zone == "battlefield":
            card.counters["loyalty"] = loyalty
            card.annotations["loyalty_initialized"] = True
        self.assert_closed(game, card)
        return card

    def main(self, game, seat, *, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active=seat, actor=seat, mana=mana)
        self.checkpoint(game)

    def submit(self, game, seat, action, **payload):
        result = game.act(f"pilot:{seat}", {"action_id": action, **payload})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, action, **payload):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", {"action_id": action, **payload})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate(self, game, seat, card, number, **payload):
        self.priority(game, seat)
        action = f"activate:{card.ref}:ab{number}"
        rows = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in rows})
        self.submit(game, seat, action, **payload)

    def next_main(self, game, seat):
        start = game.state.turn_sequence
        for _ in range(len(game.state.turn_order) + 1):
            self.until(game, lambda s: s.state.pending_decision is not None and (
                s.state.pending_decision.kind == "cleanup.discard"
                or (s.state.turn_sequence > start and s.state.active_player == seat
                    and s.state.phase == "precombat_main" and s.state.pending_decision.kind == "priority")
            ))
            decision = game.state.pending_decision
            if decision.kind == "priority":
                return
            actor, = decision.actors
            context = game.packet(f"pilot:{actor}", full=True)["decision"]["ctx"]
            self.submit(game, actor, "discard", cards=[row["id"] for row in context["hand"][:context["count"]]])
        self.fail(f"{seat} never reached their next main phase")

    def test_strict_source_and_mutated_instruction(self):
        record = self.db.lookup(JESKA)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertFalse(ir.material_residuals)

        def compile_without(ref):
            with patch(ref, return_value=None):
                return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        for producer in (
            "quorune.compiler.fixed_self_entry_counter_templates._counted_dynamic_amount_body",
            "quorune.compiler.loyalty_damage_templates.loyalty_damage_activation_template",
        ):
            with self.subTest(producer=producer):
                self.assertNotEqual("exact", compile_without(producer).status)
        introduced = replace(record, oracle_text=record.oracle_text.replace("up to three targets", "up to four targets"))
        self.assertNotEqual("exact", compile_oracle_card(introduced, capability_registry=self.registry).status)
        with self.assertRaisesRegex(ValueError, "announced X"):
            bind_loyalty_x_target_damage(None, {**loyalty_x_damage_instruction(), LOYALTY_X_DAMAGE_FIELD: True},
                                         SimpleNamespace(x_value=1))

    def test_own_and_partner_command_casts_count_and_hand_cast_does_not(self):
        game = self.session(10010701)
        partner = self.commander(game, "A", PARTNER)
        jeska = self.commander(game, "A", JESKA)
        hand = self.source(game, seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B", ref="jeska-hand-mana-red")
        for index in range(2):
            self.add(game.engine, "Mountain", seat="B", ref=f"jeska-hand-mana-{index}")
        self.main(game, "A", mana={"C": 2, "R": 1})
        self.cast_for(game, "A", partner)
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", jeska)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[jeska.object_id].counters["loyalty"])

        registry = game.engine.semantics
        current_programs = registry.programs_for_oracle

        def without_printed_permission(*args, **keywords):
            return tuple(program for program in current_programs(*args, **keywords)
                         if program.provenance.get("template_id") != PRINTED_COMMANDER_ELIGIBILITY_TEMPLATE)

        records = (self.db.lookup(JESKA), self.db.lookup(PARTNER))
        self.assertTrue(all(validate_commander_pair(self.db, registry, records)))
        with patch.object(registry, "programs_for_oracle", side_effect=without_printed_permission):
            with self.assertRaises(CommanderPairingError):
                validate_commander_pair(self.db, registry, records)

        self.next_main(game, "B")
        self.cast_for(game, "B", hand)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[hand.object_id].zone)
        self.replay(game, load=True)

    def test_native_minus_x_uses_equal_damage_and_last_known_source(self):
        game = self.session(10010702)
        source = self.source(game)
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        walker = self.add(game.engine, "Ashiok, Nightmare Weaver", seat="D")
        walker.counters["loyalty"] = 5
        walker.annotations["loyalty_initialized"] = True
        self.main(game, "A")
        action = f"activate:{source.ref}:ab3"
        self.reject(game, "A", action, x=4, targets=["C"])
        self.reject(game, "A", action, x=3, targets=["C", "C"])
        self.reject(game, "A", action, x=3, targets=["A", "B", "C", "D"])
        self.activate(game, "A", source, 3, x=3, targets=[creature.ref, "C", walker.ref])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(3, game.state.stack[-1].x_value)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual(37, game.state.players["C"].life)
        self.assertEqual(2, game.state.cards[walker.object_id].counters["loyalty"])
        self.replay(game, load=True)

    def test_native_minus_x_accepts_zero_targets_and_zero_amount(self):
        for targets, announced in (([], 1), (["C"], 0)):
            with self.subTest(targets=targets, announced=announced):
                game = self.session(10010703 + announced)
                source = self.source(game)
                self.main(game, "A")
                self.activate(game, "A", source, 3, x=announced, targets=targets)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40, game.state.players["C"].life)
                self.assertEqual(3 - announced, game.state.cards[source.object_id].counters["loyalty"])
                self.reject(game, "A", f"activate:{source.ref}:ab3", x=0, targets=[])
                self.replay(game, load=True)

    def test_native_trample_multiplier_stacks_and_preserves_blocker_damage(self):
        game = self.session(10010705)
        attacker = self.commander(game, "B", PARTNER)
        attacker = ZoneTransitionOwner(game.engine).move_card(attacker.object_id, "battlefield", log=False)
        attacker.counters["+1/+1"] = 6
        attacker.acquired_control_turn_count = -1
        source_a, source_b = (self.source(game, seat=seat) for seat in "AB")
        blocker = self.add(game.engine, "Grizzly Bears", seat="C")
        other_blocker = self.add(game.engine, "Grizzly Bears", seat="C", ref="second-menace-blocker")
        self.main(game, "A")
        self.activate(game, "A", source_a, 2, targets=[attacker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.multipliers(game)))
        self.next_main(game, "B")
        self.assertEqual("A", self.multipliers(game)[0].expires_on_next_turn_of)
        self.activate(game, "B", source_b, 2, targets=[attacker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"A", "B"}, {effect.expires_on_next_turn_of for effect in self.multipliers(game)})
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.attackers")
        self.submit(game, "B", "attack", attackers={attacker.ref: "C"})
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.blockers")
        self.submit(game, "C", "block", blocks={blocker.ref: attacker.ref, other_blocker.ref: attacker.ref})
        self.until(game, lambda s: s.state.pending_decision is not None
                   and s.state.pending_decision.kind in {"combat.damage", "replacement.order"})
        if game.state.pending_decision.kind == "combat.damage":
            self.submit(game, "B", "assign_damage", assignments=[
                {"source": attacker.ref, "target": blocker.ref, "amount": 2},
                {"source": attacker.ref, "target": other_blocker.ref, "amount": 2},
                {"source": attacker.ref, "target": "C", "amount": 2},
            ])
            self.until(game, lambda s: s.state.pending_decision is not None
                       and s.state.pending_decision.kind == "replacement.order")
        game = self.replay(game, load=True)
        self.assertEqual(["C"], list(game.state.pending_decision.actors))
        self.assertEqual(40, game.state.players["C"].life)
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        selection = {"replacement": options[0]["id"]}
        self.reject(game, "D", "choose", choices=selection)
        self.submit(game, "C", "choose", choices=selection)
        self.assertEqual(22, game.state.players["C"].life)
        self.assertEqual(18, game.state.players["C"].commander_damage_received[attacker.commander_designation_id])
        for member in (blocker, other_blocker):
            self.assertEqual("graveyard", game.state.cards[member.object_id].zone)
        self.replay(game, load=True)

    def test_native_multiplier_expires_at_the_granting_controllers_next_turn(self):
        game = self.session(10010706)
        source = self.source(game, seat="A")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        creature.acquired_control_turn_count = -1
        self.main(game, "A")
        self.activate(game, "A", source, 2, targets=[creature.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(creature.logical_object_id, self.multipliers(game)[0].target.logical_object_id)
        game = self.replay(game, load=True)
        self.next_main(game, "B")
        self.assertEqual(1, len(self.multipliers(game)))
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.attackers")
        self.submit(game, "B", "attack", attackers={creature.ref: "A"})
        self.until(game, lambda s: s.state.players["A"].life < 40)
        self.assertEqual(38, game.state.players["A"].life)
        self.next_main(game, "A")
        self.assertFalse(self.multipliers(game))
        self.replay(game, load=True)
