"""Actual second casts copy their original choices and suspend only the live card."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.cast_lifecycles import SUSPEND_HASTE_CONTEXT_FIELD
from quorune.continuous_effect_state import ResolutionEffectSource
from quorune.control_effects import gain_control_of_stack_spell
from quorune.counter_removal import CounterRemoval, commit_counter_removal_effect, plan_counter_removal_effect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.haste import has_effective_haste
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.cast_spell_suspend import CAPABILITY, current_cast_suspended_exile, second_controller_cast_condition
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.suspend import SUSPEND_CAST_SEMANTIC_KEY, SUSPEND_UPKEEP_SEMANTIC_KEY
from quorune.trigger_processing import begin_pending_trigger_batch, collect_trigger_items, enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class CastSpellSuspendTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-spell-suspend.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/cast-spell-suspend-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast-linked suspension", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_exact_shape_and_dependency_withdrawal(self):
        card = self.db.lookup("Taigam, Master Opportunist")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        compiled = compile_oracle_card(card, **options)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        ability, = compiled.faces[0].nodes
        self.assertEqual(second_controller_cast_condition(), ability.event_condition)
        self.assertEqual(["copy_stack_item", "exile"], [effect["op"] for effect in ability.effects])
        self.assertIn(CAPABILITY, ability.capability_dependencies)
        for old, new in (("second spell", "third spell"), ("four time counters", "three time counters"),
                         ("it gains suspend", "it gains haste")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == "token.creation.defined_transforming")
        row.update(status="blocked", blockers=["Test-only physical copy faces withdrawn"])
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(card, **{**options, "capability_registry": registry}).status)

    def scene(self, seed, second_card, *, answer=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        taigam = self.add(game.engine, "Taigam, Master Opportunist", ref="taigam-observer")
        first = self.add(game.engine, "Grizzly Bears", zone="hand", ref="first-cast-bears")
        second = self.add(game.engine, second_card, zone="hand", ref="second-cast-original")
        third = self.add(game.engine, "Grizzly Bears", zone="hand", ref="third-cast-bears") if answer else None
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand") if answer else None
        self.assert_closed(game, taigam)
        self.prepare(game, mana={"C": 4, "G": 2, **({"R": 1} if answer else {})})
        if answer:
            game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        reserve = {"C": 3, "G": 1, **({"R": 1} if answer else {})}
        self.cast_for(game, "A", first, reserve=reserve)
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.resolve_top(game)
        return game, taigam, second, third, counter

    def suspended_creature(self, seed):
        game, taigam, original, _, _ = self.scene(seed, "Grizzly Bears")
        before = game.state.cards[original.object_id].logical_object_id
        self.cast(game, "A", original)
        self.assertEqual(["spell", "triggered_ability"], [item.kind for item in game.state.stack])
        self.assertEqual(taigam.object_id, game.state.stack[-1].source_object_id)
        self.resolve_top(game)
        card = game.state.cards[original.object_id]
        self.assertEqual(("exile", 4), (card.zone, card.counters.get("time")))
        self.assertNotEqual(before, card.logical_object_id)
        marker = current_cast_suspended_exile(card)
        self.assertIsNotNone(marker)
        self.assertTrue(marker.grants_suspend)
        copies = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual(1, len(copies))
        self.assertNotEqual(card.object_id, copies[0].card_object_id)
        self.resolve_top(game)
        tokens = [body for body in game.state.cards.values() if body.zone == "battlefield" and body.is_token]
        self.assertEqual(1, len(tokens))
        self.assertEqual(2, len([row for row in game.engine._current_turn_history("spell_cast") if row.actor == "A"]))
        return self.replay(game, load=True), original.object_id

    def place_trigger_batch(self, game, items=()):
        engine = game.engine
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        if items:
            enqueue_trigger_batch(engine, items)
        self.assertFalse(begin_pending_trigger_batch(engine))
        engine._grant_priority("A")
        engine.pump()

    def test_original_creature_uses_owner_upkeep_and_any_last_counter_removal(self):
        game, object_id = self.suspended_creature(70262071)
        every_actor = {
            seat: collect_trigger_items(game.engine, "step.begin", {
                "phase": "beginning", "step": "upkeep", "player": seat, "previous_upkeep_timestamp": 0,
            })
            for seat in "ABCD"
        }
        self.assertEqual([1, 0, 0, 0], [len(every_actor[seat]) for seat in "ABCD"])
        self.place_trigger_batch(game, every_actor["A"])
        self.assertEqual(SUSPEND_UPKEEP_SEMANTIC_KEY, game.state.stack[-1].semantic_key)
        self.checkpoint(game)
        self.resolve_top(game)
        card = game.state.cards[object_id]
        self.assertEqual(3, card.counters["time"])
        self.assertFalse(any(item.semantic_key == SUSPEND_CAST_SEMANTIC_KEY for item in game.state.stack))
        game = self.replay(game, load=True)
        card = game.state.cards[object_id]
        # Seed the ordinary counter-removal transaction outside the upkeep producer.
        # The player-facing cast triggered by that transaction is replayed below.
        removal = CounterRemoval(object_id, "time", 3, expected_zone="exile",
                                 expected_logical_object_id=card.logical_object_id)
        commit_counter_removal_effect(game.engine, plan_counter_removal_effect(game.engine, removal))
        self.place_trigger_batch(game)
        self.assertEqual(SUSPEND_CAST_SEMANTIC_KEY, game.state.stack[-1].semantic_key)
        self.assertEqual(0, card.counters.get("time", 0))
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.checkpoint(game)
        decision = self.resolve(game)
        self.assertEqual("selection.exile_cast", decision.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "cast"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "cast"})
        self.assertTrue(accepted.ok, accepted.summary)
        changed = self.replay(game, load=True)
        stack_spell = next(item for item in changed.state.stack if item.card_object_id == object_id)
        self.assertIn(SUSPEND_HASTE_CONTEXT_FIELD, stack_spell.context)
        for seat in ("B", "A"):
            gain_control_of_stack_spell(changed.engine, stack_ref=stack_spell.ref, actor=seat,
                                        source=ResolutionEffectSource("fixture-control"),
                                        reason="Fixture changes the resolving creature's spell control")
        self.assertNotIn(SUSPEND_HASTE_CONTEXT_FIELD, stack_spell.context)
        self.settle(game)
        current = game.state.cards[object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertTrue(has_effective_haste(game.engine, current))
        self.assertIsNone(current_cast_suspended_exile(current))
        replayed = self.replay(game, load=True)
        replayed.engine.change_control(object_id, "B", reason="Fixture transfers the resolved creature")
        self.assertFalse(has_effective_haste(replayed.engine, replayed.state.cards[object_id]))
        replayed.engine.move_card(object_id, "exile", log=False)
        self.assertIsNone(current_cast_suspended_exile(replayed.state.cards[object_id]))

    def test_countered_second_cast_still_copies_x_and_keeps_the_same_target(self):
        game, taigam, spell, third, answer = self.scene(70262072, "Blaze", answer=True)
        offer = self.action(game, "A", spell)
        result = game.act("pilot:A", {"action_id": offer["id"], "x": 2, "targets": ["C"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        original = next(item for item in game.state.stack if item.card_object_id == spell.object_id)
        self.assertEqual(taigam.object_id, game.state.stack[-1].source_object_id)
        self.cast(game, "B", answer, [original.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.resolve_top(game)
        copy, = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual((2, ["C"]), (copy.x_value, copy.targets))
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["C"].life)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertIsNone(current_cast_suspended_exile(game.state.cards[spell.object_id]))
        self.cast(game, "A", third)
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.settle(game)
        self.assertEqual(3, len([row for row in game.engine._current_turn_history("spell_cast") if row.actor == "A"]))
        self.replay(game, load=True)

    def test_omitting_the_grant_breaks_the_native_exile_witness(self):
        with patch("quorune.effect_runtime.cast_spell_suspend.record_cast_suspension"):
            with self.assertRaises(AssertionError):
                self.suspended_creature(70262073)
