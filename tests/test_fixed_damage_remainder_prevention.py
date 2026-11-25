"""Native event-local prevention, command emblems and paired loyalty placements."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from quorune.counter_placement_sets import PAIRED_PLANESWALKER_LOYALTY_CAPABILITY, PAIRED_PLANESWALKER_LOYALTY_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.emblem_program_model import EMBLEM_PROGRAM_ANNOTATION
from quorune.oracle_ir import compile_oracle_card
from quorune.received_damage_model import FIXED_DAMAGE_REMAINDER_CAPABILITY, FixedDamageRemainderScope
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class FixedDamageRemainderPreventionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "fixed-damage-remainder.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "fixed-damage-remainder-prevention-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Fixed damage remainder", [
            DeckEntry(name, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [name])

    def _ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"R": 8, "C": 4, "W": 1})
        game.state.players["C"].mana_pool.update({"R": 4, "U": 2})
        self.prepare(game, mana={"W": 3, "C": 5})
        self.checkpoint(game)

    def _activate(self, game, source, ability):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in actions if row["id"] == f"activate:{source.ref}:{ability}"]
        command = {"action_id": offered["id"], "pay": "auto"}
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _bolt(self, game, source, target, *, seat="B"):
        self.cast(game, seat, source, [target])
        return self.resolve(game)

    def _choose_replacement(self, game, mode, *, reject_foreign=False):
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        decision = game.packet("pilot:A", full=True)["decision"]
        chosen, = [row["id"] for row in decision["ctx"]["options"] if mode in row["id"]]
        command = {"action_id": "choose", "replacement": chosen}
        if reject_foreign:
            self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:B", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_altisaur_caps_only_other_current_controlled_dinosaurs_and_respects_unpreventable_damage(self):
        game = self.session(615_010_01)
        source = self.add(game.engine, "Temple Altisaur", zone="hand", ref="DINO-PREVENTION-SOURCE")
        own = self.add(game.engine, "Colossal Dreadmaw", ref="OWN-DINOSAUR")
        opposing = self.add(game.engine, "Colossal Dreadmaw", seat="C", ref="OPPOSING-DINOSAUR")
        first = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="DINO-FIRST-DAMAGE")
        second = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="DINO-OTHER-DAMAGE")
        pain = self.add(game.engine, "Flaring Pain", seat="B", zone="hand", ref="DINO-UNPREVENTABLE")
        third = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="DINO-PROHIBITED-PREVENTION")
        fourth = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="DINO-SOURCE-DAMAGE")
        self._ready(game, source, first, pain)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertIsNone(self._bolt(game, first, own.ref))
        self.assertIsNone(self._bolt(game, second, opposing.ref))
        self.assertEqual(1, game.state.cards[own.object_id].marked_damage)
        self.assertEqual(3, game.state.cards[opposing.object_id].marked_damage)

        # Separate replay branch preserves a preventable event for the source-exclusion check.
        branch = self.replay(game, load=True)
        self.assertIsNone(self._bolt(branch, fourth, source.ref))
        self.assertEqual(3, branch.state.cards[source.object_id].marked_damage)
        self.replay(branch, load=True)

        self.cast(game, "B", pain)
        self.assertIsNone(self.resolve(game))
        self.assertIsNone(self._bolt(game, third, own.ref))
        self.assertEqual(4, game.state.cards[own.object_id].marked_damage)
        self.replay(game, load=True)

    def test_hyperion_prevention_orders_with_damage_doubling_and_replays_both_orders(self):
        game = self.session(615_010_02)
        source = self.add(game.engine, "Hyperion, Supreme Hero", ref="HYPERION-PREVENTION")
        self.add(game.engine, "Furnace of Rath", seat="B", ref="HYPERION-DOUBLER")
        spell = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="HYPERION-PLAYER-DAMAGE")
        hero_damage = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand", ref="HYPERION-HERO-DAMAGE")
        nonhero = self.add(game.engine, "Colossal Dreadmaw", ref="HYPERION-NONHERO")
        other_damage = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="HYPERION-OTHER-DAMAGE")
        self._ready(game, source, spell)
        decision = self._bolt(game, spell, "A")
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        self.assertEqual(40, game.state.players["A"].life)
        reverse = self.replay(game, load=True)

        self._choose_replacement(game, "fixed_remainder", reject_foreign=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["A"].life)

        self._choose_replacement(reverse, "replacement.damage.quantity")
        self.assertIsNone(self.resolve(reverse))
        self.assertEqual(39, reverse.state.players["A"].life)
        self.assertEqual("replacement.order", self._bolt(reverse, hero_damage, source.ref, seat="C").kind)
        self._choose_replacement(reverse, "replacement.damage.quantity")
        self.assertIsNone(self.resolve(reverse))
        self.assertEqual(1, reverse.state.cards[source.object_id].marked_damage)
        self.assertIsNone(self._bolt(reverse, other_damage, nonhero.ref))
        self.assertEqual("graveyard", reverse.state.cards[nonhero.object_id].zone)
        self.replay(game, load=True)
        self.replay(reverse, load=True)

    def test_ajani_emblem_caps_damage_from_command_after_its_creator_dies(self):
        game = self.session(615_010_03)
        creator = self.add(game.engine, "Ajani Steadfast", ref="DAMAGE-EMBLEM-CREATOR")
        creator.counters["loyalty"] = 7
        walker = self.add(game.engine, "Koth, Fire of Resistance", ref="COMMAND-PROTECTED-WALKER")
        walker.counters["loyalty"] = 5
        creature = self.add(game.engine, "Colossal Dreadmaw", ref="COMMAND-EXCLUDED-CREATURE")
        first = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="COMMAND-PLAYER-DAMAGE")
        second = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="COMMAND-WALKER-DAMAGE")
        third = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand", ref="COMMAND-CREATURE-DAMAGE")
        self._ready(game, creator, walker, first)
        self._activate(game, creator, "ab3")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creator.object_id].zone)
        emblem, = [card for card in game.state.cards.values() if card.object_kind == "emblem"]
        self.assertEqual(("command", "A"), (emblem.zone, emblem.controller))
        self.assertEqual("damage", emblem.annotations[EMBLEM_PROGRAM_ANNOTATION]["abilities"][0]["event"])
        game = self.replay(game, load=True)
        self.assertIsNone(self._bolt(game, first, "A"))
        self.assertIsNone(self._bolt(game, second, walker.ref))
        self.assertIsNone(self._bolt(game, third, creature.ref, seat="C"))
        self.assertEqual(39, game.state.players["A"].life)
        self.assertEqual(4, game.state.cards[walker.object_id].counters["loyalty"])
        self.assertEqual(3, game.state.cards[creature.object_id].marked_damage)
        self.replay(game, load=True)

    def test_ajani_paired_placement_includes_both_kinds_and_excludes_only_current_source(self):
        game = self.session(615_010_04)
        creator = self.add(game.engine, "Ajani Steadfast", ref="PAIRED-COUNTERS-CREATOR")
        creator.counters["loyalty"] = 6
        walker = self.add(game.engine, "Koth, Fire of Resistance", ref="PAIRED-COUNTERS-OVERLAP")
        walker.counters["loyalty"] = 5
        creature = self.add(game.engine, "Colossal Dreadmaw", ref="PAIRED-COUNTERS-CREATURE")
        other = self.add(game.engine, "Koth, Fire of Resistance", seat="C", ref="PAIRED-COUNTERS-OPPONENT")
        other.counters["loyalty"] = 5
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand", ref="PAIRED-COUNTERS-BLINK")
        create_resolution_continuous_effect_components(
            game.engine, source=ResolutionEffectSource("previous-animation"), targets=(creator, walker),
            components=(
                ResolutionContinuousComponent(Layer.TYPE, "4", (ContinuousOperation("add_types", ["Creature"], field="card_types"),)),
                ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7b", (ContinuousOperation("set_power_toughness", [4, 4]),)),
            ),
        )
        self._ready(game, creator, walker, blink)
        self._activate(game, creator, "ab2")
        returned_source = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"loyalty": 4, "+1/+1": 1}, game.state.cards[creator.object_id].counters)
        self.assertEqual({"loyalty": 6, "+1/+1": 1}, game.state.cards[walker.object_id].counters)
        self.assertEqual({"+1/+1": 1}, game.state.cards[creature.object_id].counters)
        self.assertEqual({"loyalty": 5}, game.state.cards[other.object_id].counters)

        old_identity = returned_source.state.cards[creator.object_id].logical_object_id
        self.cast(returned_source, "B", blink, [creator.ref])
        self.assertIsNone(self.resolve(returned_source))
        current = returned_source.state.cards[creator.object_id]
        self.assertNotEqual(old_identity, current.logical_object_id)
        self.assertEqual({"loyalty": 5}, current.counters)
        self.assertEqual({"loyalty": 6, "+1/+1": 1}, returned_source.state.cards[walker.object_id].counters)
        self.replay(game, load=True)
        self.replay(returned_source, load=True)

    def test_remainder_and_paired_counter_shapes_fail_closed_on_widening(self):
        for name in ("Temple Altisaur", "Hyperion, Supreme Hero", "Ajani Steadfast"):
            card = self.db.lookup(name, fuzzy=False)
            exact = compile_oracle_card(card, capability_registry=self.registry)
            self.assertEqual("exact", exact.status, exact.material_residuals)
            for old, new in (
                ("prevent all but 1", "prevent all but 2"),
                ("If a source would deal", "If a creature would deal"),
                ("you control,", "an opponent controls,"),
            ):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new, 1))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)

        from quorune.semantic_runtime.damage_replacements import FixedDamageRemainderHandler
        handler = FixedDamageRemainderHandler()
        desc = FixedDamageRemainderScope("controller_or_planeswalker").descriptor()
        for mod in ({"damage_remaining": True}, {"damage_remaining": 2}, {"damage_remaining": 1, "amount": "all"}):
            with self.assertRaises(ValueError):
                handler.validate({**desc, "modification": mod})

        game = self.session(615_010_05)
        creator = self.add(game.engine, "Ajani Steadfast")
        program, = [p for p in game.engine.semantics.programs_for_oracle(creator.oracle_id)
                    if any(PAIRED_PLANESWALKER_LOYALTY_FIELD in effect for effect in p.effects)]
        effect, = program.effects
        for changed in (
            replace(program, effects=({**effect, PAIRED_PLANESWALKER_LOYALTY_FIELD: True},)),
            replace(program, effects=({**effect, "counter": "loyalty"},)),
            replace(program, effects=({**effect, "source": "$target.0"},)),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])

        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        family = next(row for row in data["capabilities"] if row["id"] == FIXED_DAMAGE_REMAINDER_CAPABILITY)
        paired = next(row for row in data["capabilities"] if row["id"] == PAIRED_PLANESWALKER_LOYALTY_CAPABILITY)
        paired.update(status="blocked", blockers=["paired placements withdrawn"])
        self.assertNotEqual("exact", compile_oracle_card(
            self.db.lookup("Ajani Steadfast"), capability_registry=CapabilityRegistry(data),
        ).status)
        family.update(status="blocked", blockers=["fixed remainder withdrawn"])
        self.assertNotEqual("exact", compile_oracle_card(
            self.db.lookup("Temple Altisaur"), capability_registry=CapabilityRegistry(data),
        ).status)
