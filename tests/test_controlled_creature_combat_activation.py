"""Activation legality and exact grouped phase insertion for a sorcery-timed untap."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_damage_triggered_extra_combat as combat

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.extra_combat_model import ExtraCombatUntap, extra_combat_effect
from quorune.rules.controlled_creature_main_combat import CONTROLLED_COMBAT_DEPENDENCIES
from scripts.build_test_database import build_fixture_database


CARD = "Aggravated Assault"
BATCH = ("combat", "main")


def queued_phases(game):
    return tuple(entry.kind for entry in game.state.main_phase_extra_combats.pending)


class ControlledCreatureCombatActivationTests(InheritedNativeFixture, unittest.TestCase):
    next_attack = combat.DamageTriggeredExtraCombatTests._next_attack

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "controlled-creature-combat.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "next-personal-turn-protection-cards.json", "controlled-creature-combat-cards.json",
            "static-step-skip-cards.json", "additional-beginning-originals-cards.json",
            "paid-attacking-extra-combat-cards.json", "damage-triggered-extra-combat-cards.json",
            "main-phase-trigger-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled creature combat activation", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def actions(self, game, seat):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return {row["id"]: row for row in rows}

    def activate(self, game, source):
        action, = (name for name in self.actions(game, "A") if name.startswith(f"activate:{source.ref}:"))
        result = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return action

    def assert_rejected(self, game, seat, action):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": action, "pay": "auto"})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_exact_compiler_and_runtime_require_ordinary_sorcery_activation(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(("activate", "battlefield", None), (node.event, node.active_zone, node.target_schema))
        self.assertEqual((extra_combat_effect(ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR),),
                         tuple(node.effects))
        self.assertTrue(set(CONTROLLED_COMBAT_DEPENDENCIES) <= set(node.capability_dependencies))
        for old, new in (
            (" Activate only as a sorcery.", ""), ("creatures you control", "creatures your opponents control"),
            ("this main phase", "this turn"), ("followed by an additional main phase", "followed by an additional upkeep"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        game = self.session(60205007)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        self.assertIs(program.handlers[0]["ability"]["sorcery_speed"], True)
        handler = {**program.handlers[0], "ability": {**program.handlers[0]["ability"], "sorcery_speed": False}}
        broken = replace(program, handlers=[handler])
        result = bind_semantic_program_runtime(broken, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])

    def test_immediate_untap_repeated_original_and_inserted_main_pairs_replay(self):
        game = self.session(50080201)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD)
        creature = self.add(game.engine, "Grizzly Bears")
        creature.tapped = True
        survivor = self.add(game.engine, "Cautious Survivor")
        survivor.acquired_control_turn_count = -1
        outside = self.add(game.engine, "Grizzly Bears", seat="B", ref="opposing-tapped-creature")
        outside.tapped = True
        ring = self.add(game.engine, "Sol Ring")
        ring.tapped = True
        for index in range(5):
            self.add(game.engine, "Mountain", ref=f"subsequent-main-mana-{index}")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand", seat="B")
        game.state.players["B"].mana_pool["R"] = 1
        for card in (source, bolt, survivor):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "R": 4})
        self.checkpoint(game)

        action, = (name for name in self.actions(game, "A") if name.startswith(f"activate:{source.ref}:"))
        self.cast_for(game, "B", bolt, ["D"])
        self.assertNotIn(action, self.actions(game, "A"))
        self.assert_rejected(game, "A", action)
        self.resolve_top(game)

        self.assertEqual(action, self.activate(game, source))
        self.assertTrue(game.state.cards[creature.object_id].tapped)
        self.assertIsNone(game.state.main_phase_extra_combats)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[creature.object_id].tapped)
        self.assertTrue(game.state.cards[outside.object_id].tapped)
        self.assertTrue(game.state.cards[ring.object_id].tapped)
        self.assertEqual(BATCH, queued_phases(game))
        self.assertFalse(game.state.delayed_triggers)

        self.activate(game, source)
        self.resolve_top(game)
        self.assertEqual(BATCH * 2, queued_phases(game))
        game = self.replay(game, load=True)
        self.until(game, lambda s: (s.state.phase, s.state.step) == ("combat", "beginning_combat"))
        self.assertNotIn(action, self.actions(game, "A"))
        self.assert_rejected(game, "A", action)
        self.attack(game, [(survivor, "B")])
        self.until(game, lambda s: s.state.inserted_main_phase_count == 1 and s.state.phase == "postcombat_main")
        self.assertEqual(BATCH, queued_phases(game))
        self.assertTrue(game.state.cards[survivor.object_id].tapped)
        self.assertEqual(1, self.trigger_count(game, survivor))
        self.resolve_top(game)
        self.assertEqual(42, game.state.players["A"].life)
        self.activate(game, source)
        self.resolve_top(game)
        self.assertEqual(BATCH * 2, queued_phases(game))
        game = self.replay(game, load=True)
        self.until(game, lambda s: s.state.main_phase_extra_combats is None and s.state.phase == "combat")
        self.assertEqual(3, game.state.inserted_main_phase_count)
        self.assertEqual("A", game.state.active_player)
        reports = [event for event in game.state.events if event.code == "combat.extra.scheduled" and "main_phase_count" in event.details]
        self.assertEqual(["precombat_main", "precombat_main", "postcombat_main"],
                         [event.details["after_phase"] for event in reports])
        self.assertTrue(all(event.details["main_phase_count"] == 1 for event in reports))
        self.assertFalse(game.state.delayed_triggers)
        self.attack(game, [(survivor, "B")])
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        self.assertTrue(game.state.cards[survivor.object_id].tapped)
        self.assertEqual(0, self.trigger_count(game, survivor))
        self.assertEqual(42, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_paid_activation_survives_departed_source_and_uses_native_replay(self):
        game = self.session(11307001)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD)
        creature = self.add(game.engine, "Grizzly Bears")
        creature.tapped = True
        disenchant = self.add(game.engine, "Disenchant", zone="hand", seat="B")
        for card in (source, disenchant):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"W": 1, "C": 1})
        self.prepare(game, mana={"C": 3, "R": 2})
        self.checkpoint(game)
        self.activate(game, source)
        self.cast_for(game, "B", disenchant, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertTrue(game.state.cards[creature.object_id].tapped)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[creature.object_id].tapped)
        self.assertEqual(BATCH, queued_phases(game))
        self.replay(game, load=True)

    def test_native_combat_only_precedes_waiting_main_and_landfall_grant_keeps_its_untap(self):
        game = self.session(50080207)
        game.state.config.semantic_policy = "trusted_only"
        assault = self.add(game.engine, CARD)
        charger = self.add(game.engine, "Hellkite Charger")
        charger.tapped = True
        moraug = self.add(game.engine, "Moraug, Fury of Akoum")
        bear = self.add(game.engine, "Grizzly Bears")
        bear.acquired_control_turn_count = -1
        landfall = self.add(game.engine, "Mountain", zone="hand", ref="landfall-in-added-main")
        mountains = [self.add(game.engine, "Mountain", ref=f"native-combat-payment-{index}") for index in range(7)]
        for card in (assault, charger, moraug):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 2})
        self.checkpoint(game)

        self.activate(game, assault)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[charger.object_id].tapped)
        self.assertEqual(BATCH, queued_phases(game))
        self.attack(game, [(charger, "B")])
        combat_from_assault = game.state.combat.damage_sequence_id
        self.assertEqual(("main",), queued_phases(game))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        paid = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in mountains))
        self.assertEqual(1, game.state.extra_combat_phases)
        self.assertEqual(("main",), queued_phases(game))

        combat_from_charger = self.next_attack(game, combat_from_assault)
        self.assertNotEqual(combat_from_assault, combat_from_charger)
        self.assertEqual(0, game.state.inserted_main_phase_count)
        self.assertEqual(("main",), queued_phases(game))
        self.attack(game, [(bear, "B")])
        self.until(game, lambda s: s.state.phase == "postcombat_main" and s.state.inserted_main_phase_count == 1)
        self.assertTrue(game.state.cards[bear.object_id].tapped)
        self.assertEqual((), queued_phases(game))
        game = self.replay(game, load=True)
        self.priority(game, "A")
        played = game.act("pilot:A", {"action_id": "play-land:" + landfall.ref})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual(1, self.trigger_count(game, moraug))
        self.resolve_top(game)
        self.assertEqual(("combat",), queued_phases(game))
        self.assertEqual(1, sum(trigger.active for trigger in game.state.delayed_triggers))
        game = self.replay(game, load=True)
        self.until(game, lambda s: any(item.label == "Untap creatures at the beginning of the granted combat"
                                       for item in s.state.stack))
        self.assertEqual("beginning_combat", game.state.step)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[bear.object_id].tapped)
        self.assertEqual(1, game.state.inserted_main_phase_count)
        self.assertEqual(0, game.state.extra_combat_phases)
        self.replay(game, load=True)
