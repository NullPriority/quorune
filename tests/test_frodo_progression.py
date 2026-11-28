"""Original Frodo progression, combat Ring branching, and incarnation replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousEffectDuration
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.ring_state import commit_ring_bearer_choice, current_ring_state, prepare_ring_temptation
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.source_subtype_progression import PROGRESSION_CAPABILITY, RING_DAMAGE_OUTCOME_CAPABILITY, uses_ring_damage_outcome
from scripts.build_test_database import build_fixture_database


NAME = "Frodo, Sauron's Bane"


class FrodoProgressionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "frodo.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "bound-effect-program-cards.json", fixtures / "frodo-progression-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Source subtype progression", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_frodo_closes_original_activations_and_exact_combat_child(self):
        card = self.db.lookup(NAME, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        scout, rogue, child = ir.faces[0].nodes
        self.assertEqual(("scout", "rogue"), (scout.effects[0]["stage"], rogue.effects[0]["stage"]))
        self.assertEqual("damage.dealt.self", child.event)
        self.assertIsNone(child.target_schema)
        self.assertTrue(uses_ring_damage_outcome(child.effects[0]))
        self.assertEqual(child.text, card.oracle_text[child.span.start:child.span.end])
        self.assertNotIn("until end of turn", str(rogue.effects[0]))
        for changed in (
            card.oracle_text.replace("it becomes a Halfling Scout", "it becomes a Halfling Wizard"),
            card.oracle_text.replace("deals combat damage", "deals damage"),
            card.oracle_text.replace("four or more", "three or more"),
        ):
            rejected = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry,
                                           capability_profile="commander_review")
            self.assertNotEqual("exact", rejected.status)

        registry_path = ROOT / "quorune/rules/capability-registry.json"
        for cap in (PROGRESSION_CAPABILITY, RING_DAMAGE_OUTCOME_CAPABILITY, "game.loss.effect", "designation.ring.temptation"):
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
            row = next(row for row in raw["capabilities"] if row["id"] == cap)
            row.update(status="blocked", blockers=["Focused source progression dependency mutation"])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            with self.subTest(capability=cap):
                self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                                capability_profile="commander_review").status)

    def _activate(self, game, source, phrase):
        packet = game.packet("pilot:A", full=True)
        action = next(row for row in packet["decision"]["ctx"]["legal"]["actions"]
                      if row.get("action") == "activate" and row["id"].startswith(f"activate:{source.ref}:")
                      and phrase in row["label"])
        command = {"action_id": action["id"], "pay": "auto"}
        if action.get("cost_options"):
            option = next(row for row in action["cost_options"] if row["requirements"]["W"] == 2)
            command["cost_option"] = option["id"]
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _check_form(self, game, source, phase, stats, lifelink):
        card = game.state.cards[source.object_id]
        _, subtypes, supertypes = game.engine._effective_type_parts(card)
        self.assertEqual({"halfling", phase}, set(subtypes))
        self.assertIn("legendary", supertypes)
        self.assertEqual(stats, tuple(game.engine._numeric_stat(card.object_id, n) for n in ("power", "toughness")))
        self.assertEqual(lifelink, "lifelink" in game.engine._combat_keywords(card))

    def test_frodo_resolves_subtype_conditions_and_keeps_grant_only_on_the_incarnation(self):
        game = self.session(701_054_81)
        source = self.add(game.engine, NAME, ref="PROGRESSION")
        blink = self.add(game.engine, "Cloudshift", zone="hand", ref="FRESH-INCARNATION")
        self.assert_closed(game, source)
        self.assert_closed(game, blink)
        self.prepare(game, mana={"B": 6, "W": 3})
        self.checkpoint(game)

        old_activation = self.replay(game, load=True)
        original_id = old_activation.state.cards[source.object_id].logical_object_id
        self._activate(old_activation, source, "is a Citizen")
        self.cast(old_activation, "A", blink, [source.ref])
        self.resolve_top(old_activation)
        self.assertNotEqual(original_id, old_activation.state.cards[source.object_id].logical_object_id)
        self.resolve_top(old_activation)
        self._check_form(old_activation, source, "citizen", (1, 2), False)
        self.replay(old_activation, load=True)

        self._activate(game, source, "is a Scout")
        self.resolve_top(game)
        self._check_form(game, source, "citizen", (1, 2), False)

        self._activate(game, source, "is a Scout")
        self._activate(game, source, "is a Citizen")
        self.resolve_top(game)
        self._check_form(game, source, "scout", (2, 3), True)
        self.resolve_top(game)
        self._check_form(game, source, "rogue", (2, 3), True)
        card = game.state.cards[source.object_id]
        original_id = card.logical_object_id
        progression = [effect for effect in game.state.continuous_effects
                       if any(bound.logical_object_id == original_id for bound in effect.locked_objects)]
        self.assertEqual(5, len(progression))
        self.assertTrue(all(effect.duration is ContinuousEffectDuration.ZONE_OBJECT for effect in progression))
        game = self.replay(game, load=True)

        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(original_id, game.state.cards[source.object_id].logical_object_id)
        self._check_form(game, source, "citizen", (1, 2), False)
        self.replay(game, load=True)

    def test_frodo_first_damage_tempts_the_current_controller_and_second_causes_game_loss(self):
        game = self.session(701_054_82)
        for _ in range(3):
            progress = prepare_ring_temptation(game.engine, player="A", reason="Starting Ring history")
            commit_ring_bearer_choice(game.engine, progress, None)
        source = self.add(game.engine, NAME, seat="B", ref="BORROWED-PROGRESSION")
        game.engine.change_control(source.object_id, "A", reason="Starting battlefield control")
        source.acquired_control_turn_count = -1
        bearer = self.add(game.engine, "Grizzly Bears", ref="OTHER-BEARER")
        strobe = self.add(game.engine, "Assault Strobe", zone="hand", ref="DOUBLE-STRIKE")
        self.assert_closed(game, source)
        self.assert_closed(game, strobe)
        self.prepare(game, mana={"B": 3, "W": 2, "R": 1})
        self.checkpoint(game)
        self._activate(game, source, "is a Citizen")
        self.resolve_top(game)
        self._activate(game, source, "is a Scout")
        self.resolve_top(game)
        self._check_form(game, source, "rogue", (2, 3), True)
        self.cast(game, "A", strobe, [source.ref])
        self.resolve_top(game)
        self.assertIn("double strike", game.engine._combat_keywords(game.state.cards[source.object_id]))

        prior_life = game.state.players["A"].life
        self.attack(game, [(source, "C")])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "semantic.choice")
        self.assertEqual(4, current_ring_state(game.engine, "A").temptation_count)
        self.assertIsNone(current_ring_state(game.engine, "B"))
        self.assertTrue(game.state.players["C"].in_game)
        self.assertEqual(prior_life + 2, game.state.players["A"].life)
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "objects": [bearer.ref]}
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        selected = game.act("pilot:A", command)
        self.assertTrue(selected.ok, selected.summary)
        self.assertTrue(game.state.players["C"].in_game)
        self.until(game, lambda current: not current.state.players["C"].in_game)
        self.assertEqual(prior_life + 4, game.state.players["A"].life)
        self.assertEqual(4, current_ring_state(game.engine, "A").temptation_count)
        self.assertTrue(game.state.players["B"].in_game)
        self.replay(game, load=True)
