"""A revalidated stack spell supplies its exact mana value before the counter attempt."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.bounded_hand_cast_model import BoundedHandCastSpec, bounded_hand_cast_payload_dependencies
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.card_programs.stack_counter_overlay import shadowed_reviewed_stack_mana_keys
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.stack_counter_followup import STACK_MANA_VALUE_FOLLOWUP_VERSION
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
ORIGINALS = ("Mana Drain", "Plasm Capture", "Spell Swindle")


class StackManaValueCounterFollowupTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "stack-mana-value-counter-followups.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/stack-mana-value-counter-followups-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Stack mana value counter followups", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_require_a_revalidated_spell_before_their_mana_value_results(self):
        for name in ORIGINALS:
            with self.subTest(card=name):
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                program, = semantics.programs_for_oracle(record.oracle_id)
                self.assertEqual(["spell"], program.target_schema["categories"])
                effect, = program.effects
                self.assertEqual(STACK_MANA_VALUE_FOLLOWUP_VERSION, effect["schema_version"])
                changed = replace(program, target_schema={**program.target_schema, "categories": ["ability"]})
                self.assertFalse(bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                with patch("quorune.compiler.stack_counter_followup._MANA_VALUE_RESULTS", {}):
                    self.assertNotEqual("exact", compile_oracle_card(
                        record, capability_registry=self.registry, capability_profile=PROFILE,
                    ).status)

    def test_current_colorless_result_supersedes_only_the_matching_reviewed_view(self):
        record = self.db.lookup("Mana Drain", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, record.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        current, = semantics.programs_for_oracle(record.oracle_id)
        legacy, = SemanticRegistry().programs_for_oracle(record.oracle_id)
        self.assertEqual({legacy.key}, shadowed_reviewed_stack_mana_keys(record, (current,), (legacy,)))
        counter, delayed = legacy.effects
        changed = replace(legacy, effects=[counter, {**delayed, "amount": 2}])
        stale = replace(legacy, provenance={**legacy.provenance, "source_rulings_hash": "stale"})
        for old in (changed, stale):
            self.assertEqual(set(), shadowed_reviewed_stack_mana_keys(record, (current,), (old,)))

    def cast_x(self, game, actor, spell, target, x):
        self.priority(game, actor)
        action = self.action(game, actor, spell)
        result = game.act("pilot:" + actor, {"action_id": action["id"], "targets": [target], "x": x, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1]

    def advance_to_upkeep(self, game, seat):
        self.until(game, lambda item: item.state.active_player == seat and item.state.step == "upkeep")

    def test_actual_drain_adds_mana_at_the_same_turn_postcombat_main_after_a_failed_counter(self):
        game = self.playing(61127201)
        spell = self.add(game.engine, "Mana Drain", zone="hand")
        fallout = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        for card in (spell, fallout):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 2})
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", fallout)
        threatened = game.state.stack[-1].ref
        self.cast_for(game, "A", spell, [threatened])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        game = self.replay(game, load=True)
        self.until(game, lambda item: item.state.active_player == "A"
                   and item.state.phase == "postcombat_main" and bool(item.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.players["A"].mana_pool.get("C", 0))
        self.replay(game, load=True)

    def test_actual_zero_mana_drain_still_places_its_delayed_ability_on_the_stack(self):
        game = self.playing(61127202)
        spell = self.add(game.engine, "Mana Drain", zone="hand")
        zero = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
        for card in (spell, zero):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "B", zero)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.assertIsNone(self.resolve(game))
        for seat in ("C", "D", "A"):
            self.advance_to_upkeep(game, seat)
        self.until(game, lambda item: item.state.active_player == "A"
                   and item.state.phase == "precombat_main" and bool(item.state.stack))
        self.assertEqual("triggered_ability", game.state.stack[-1].kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_drain_stages_no_result_after_its_only_target_leaves(self):
        game = self.playing(61127207)
        spell = self.add(game.engine, "Mana Drain", zone="hand")
        response = self.add(game.engine, "Mana Drain", seat="C", zone="hand", ref="responding-drain")
        target = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
        for card in (spell, response, target):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        for seat in ("A", "C"):
            game.state.players[seat].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        target_ref = game.state.stack[-1].ref
        self.cast_for(game, "A", spell, [target_ref])
        self.cast_for(game, "C", response, [target_ref])
        self.assertIsNone(self.resolve(game))
        waiting, = game.state.delayed_triggers
        self.assertEqual("C", waiting.controller)
        self.replay(game, load=True)

    def test_actual_reviewed_colorless_result_replays_from_its_serialized_program(self):
        game = self.playing(61127208)
        spell = self.add(game.engine, "Mana Drain", zone="hand")
        target = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        self.assert_closed(game, target)
        reviewed, = SemanticRegistry().programs_for_oracle(spell.oracle_id)
        game.engine.semantics.put(reviewed)
        self.prepare(game, actor="A", active="A", mana={"U": 2})
        game.state.players["B"].mana_pool["U"] = 3
        self.checkpoint(game)
        started = self.cast_x(game, "B", target, "B", 0)
        self.cast_for(game, "A", spell, [started.ref])
        self.assertIsNone(self.resolve(game))
        waiting, = game.state.delayed_triggers
        self.assertEqual(reviewed.effects[1]["label"], waiting.label)
        game = self.replay(game, load=True)
        resident, = game.engine.semantics.programs_for_oracle(spell.oracle_id)
        self.assertEqual(reviewed.effects, resident.effects)
        self.until(game, lambda item: item.state.active_player == "A"
                   and item.state.phase == "postcombat_main" and bool(item.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.players["A"].mana_pool.get("C", 0))
        self.replay(game, load=True)

    def test_actual_plasm_capture_waits_for_precombat_and_allocates_the_full_announced_value(self):
        game = self.playing(61127203)
        spell = self.add(game.engine, "Plasm Capture", zone="hand")
        zenith = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        for card in (spell, zenith):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 2, "G": 2})
        game.state.players["B"].mana_pool["U"] = 12
        self.checkpoint(game)
        target = self.cast_x(game, "B", zenith, "B", 9)
        self.cast_for(game, "A", spell, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda item: item.state.active_player == "A" and item.state.phase == "postcombat_main")
        self.assertEqual([], game.state.stack)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        for seat in ("B", "C", "D", "A"):
            self.advance_to_upkeep(game, seat)
        self.until(game, lambda item: item.state.active_player == "A"
                   and item.state.phase == "precombat_main" and bool(item.state.stack))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(12, game.packet("pilot:A", full=True)["decision"]["ctx"]["amount"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        refused = game.act("pilot:A", {"action_id": "choose", "mana": {"C": 12}})
        self.assertFalse(refused.ok, refused.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        chosen = game.act("pilot:A", {"action_id": "choose", "mana": {"U": 6, "G": 6}})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(6, game.state.players["A"].mana_pool["U"])
        self.assertEqual(6, game.state.players["A"].mana_pool["G"])
        self.replay(game, load=True)

    def test_actual_copied_plasm_uses_its_own_controller_after_the_copy_leaves_the_stack(self):
        game = self.playing(61127205)
        spell = self.add(game.engine, "Plasm Capture", zone="hand")
        copier = self.add(game.engine, "Twincast", seat="B", zone="hand")
        zenith = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        for card in (spell, copier, zenith):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"G": 2, "U": 2})
        game.state.players["B"].mana_pool["U"] = 6
        self.checkpoint(game)
        target = self.cast_x(game, "B", zenith, "B", 1)
        self.cast_for(game, "A", spell, [target.ref])
        self.cast_for(game, "B", copier, [game.state.stack[-1].ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        accepted = game.act("pilot:B", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        copied = game.state.stack[-1]
        self.assertEqual("spell_copy", copied.kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["B"], [trigger.controller for trigger in game.state.delayed_triggers if trigger.active])
        self.advance_to_upkeep(game, "B")
        self.until(game, lambda item: item.state.active_player == "B"
                   and item.state.phase == "precombat_main" and bool(item.state.stack))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        result = game.act("pilot:B", {"action_id": "choose", "mana": {"W": 4}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.state.players["B"].mana_pool["W"])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_swindle_counts_the_copied_announced_value_and_keeps_the_original_spell(self):
        game = self.playing(61127204)
        spell = self.add(game.engine, "Spell Swindle", zone="hand")
        copier = self.add(game.engine, "Twincast", zone="hand")
        zenith = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        for card in (spell, copier, zenith):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 7})
        game.state.players["B"].mana_pool["U"] = 6
        self.checkpoint(game)
        original = self.cast_x(game, "B", zenith, "B", 3)
        self.cast_for(game, "A", copier, [original.ref], reserve={"U": 5})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        chosen = game.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(chosen.ok, chosen.summary)
        copied = game.state.stack[-1]
        self.assertEqual(("spell_copy", 3), (copied.kind, copied.x_value))
        old_hand = tuple(game.state.players["C"].zones["hand"])
        self.cast_for(game, "A", spell, [copied.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(old_hand, tuple(game.state.players["C"].zones["hand"]))
        self.assertEqual("library", game.state.cards[zenith.object_id].zone)
        treasure = [card for card in game.state.cards.values()
                    if card.is_token and card.zone == "battlefield" and card.controller == "A"]
        self.assertEqual(6, len(treasure))
        self.replay(game, load=True)

    def test_actual_intellect_filters_creature_spells_and_draws_its_announced_value_privately(self):
        report, semantics = register_card(self.db, self.registry, "Overwhelming Intellect")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Overwhelming Intellect", fuzzy=False)
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(["creature"], program.target_schema["types_any"])
        with patch("quorune.compiler.stack_counter_followup._MANA_VALUE_RESULTS", {}):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        broadened = replace(program, target_schema={
            field: value for field, value in program.target_schema.items() if field != "types_any"
        })
        self.assertFalse(bind_semantic_program_runtime(
            broadened, capability_registry=self.registry, profile=PROFILE,
        )["strict"])

        game = self.playing(61127209)
        spell = self.add(game.engine, "Overwhelming Intellect", zone="hand")
        noncreature = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        creature = self.add(game.engine, "Endless One", seat="C", zone="hand")
        for card in (spell, noncreature, creature):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"W": 4})
        game.state.players["A"].mana_pool["U"] = 6
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        cast_creature = self.action(game, "C", creature)
        result = game.act("pilot:C", {"action_id": cast_creature["id"], "x": 4, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        right = game.state.stack[-1].ref
        self.cast_for(game, "B", noncreature)
        wrong = game.state.stack[-1].ref
        self.priority(game, "A")
        cast_counter = self.action(game, "A", spell)
        self.assertEqual([right], cast_counter["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        invalid = game.act("pilot:A", {"action_id": cast_counter["id"], "targets": [wrong], "pay": "auto"})
        self.assertFalse(invalid.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        first_hand = set(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", spell, [right])
        self.assertIsNone(self.resolve(game))
        drawn = set(game.state.players["A"].zones["hand"]) - first_hand
        self.assertEqual(4, len(drawn))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        foreign = json.dumps(game.packet("pilot:D", full=True))
        for object_id in drawn:
            self.assertNotIn(game.state.cards[object_id].ref, foreign)
        self.replay(game, load=True)

    def test_actual_intellect_draws_after_a_legal_uncounterable_creature(self):
        game = self.playing(61127210)
        spell = self.add(game.engine, "Overwhelming Intellect", zone="hand")
        creature = self.add(game.engine, "Carnage Tyrant", seat="B", zone="hand")
        for card in (spell, creature):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"G": 6})
        game.state.players["A"].mana_pool["U"] = 6
        self.checkpoint(game)
        self.cast_for(game, "B", creature)
        before = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(before - 1 + 6, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_reinterpret_offers_only_private_spells_within_the_announced_value(self):
        report, _ = register_card(self.db, self.registry, "Reinterpret")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Reinterpret", fuzzy=False)
        with patch("quorune.compiler.stack_counter_followup._MANA_VALUE_RESULTS", {}):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

        game = self.playing(61127211)
        spell = self.add(game.engine, "Reinterpret", zone="hand")
        zenith = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        offered_card = self.add(game.engine, "Air Elemental", zone="hand")
        above_limit = self.add(game.engine, "Colossal Dreadmaw", zone="hand")
        foreign = self.add(game.engine, "Air Elemental", seat="C", zone="hand", ref="foreign-elemental")
        for card in (spell, zenith, offered_card, above_limit, foreign):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 2, "U": 1, "R": 1})
        game.state.players["B"].mana_pool["U"] = 5
        self.checkpoint(game)
        target = self.cast_x(game, "B", zenith, "B", 2)
        self.cast_for(game, "A", spell, [target.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("graveyard", game.state.cards[zenith.object_id].zone)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
        self.assertEqual({offered_card.ref}, {option["card"] for option in options})
        audience = json.dumps(game.packet("pilot:C", full=True))
        self.assertNotIn(json.dumps(offered_card.ref), audience)
        self.assertNotIn(json.dumps(above_limit.ref), audience)
        choice = {"action_id": "choose", "choice": "cast", "cast": {
            "card": offered_card.ref, "cost_option": options[0]["id"],
        }}
        forged = {"action_id": "choose", "choice": "cast", "cast": {"card": above_limit.ref}}
        for pilot, command in (("C", choice), ("A", forged)):
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:" + pilot, command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        selected = game.act("pilot:A", choice)
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("stack", game.state.cards[offered_card.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[offered_card.object_id].zone)
        self.replay(game, load=True)

    def test_actual_reinterpret_uses_legal_protected_spell_and_forces_free_child_x_zero(self):
        game = self.playing(61127212)
        spell = self.add(game.engine, "Reinterpret", zone="hand")
        fallout = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        child = self.add(game.engine, "Endless One", zone="hand")
        for card in (spell, fallout, child):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 2, "U": 1, "R": 1})
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", fallout)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("stack", game.state.cards[fallout.object_id].zone)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
        option, = (item for item in options if item["card"] == child.ref)
        cast = {"card": child.ref, "cost_option": option["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {
            "action_id": "choose", "choice": "cast", "cast": {**cast, "x": 1},
        }).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        selected = game.act("pilot:A", {"action_id": "choose", "choice": "cast", "cast": {**cast, "x": 0}})
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual((child.object_id, 0), (game.state.stack[-1].card_object_id, game.state.stack[-1].x_value))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[child.object_id].zone)
        self.assertEqual(38, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_shared_type_cast_wire_rejects_unbound_or_noncanonical_permissions(self):
        wire = {"schema_version": 2, "shared_card_types": ["artifact", "creature"]}
        spec = BoundedHandCastSpec.from_dict(wire)
        self.assertEqual(wire, spec.to_dict())
        with self.assertRaises(ValueError):
            bounded_hand_cast_payload_dependencies(spec.effect())
        for types in ([], ["creature", "artifact"], ["artifact", "artifact"], ["elf"]):
            with self.subTest(types=types), self.assertRaises(ValueError):
                BoundedHandCastSpec.from_dict({**wire, "shared_card_types": types})

    def test_actual_counterlash_reuses_any_captured_card_type_and_has_no_mana_value_limit(self):
        report, _ = register_card(self.db, self.registry, "Counterlash")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Counterlash", fuzzy=False)
        with patch("quorune.compiler.stack_counter_followup._SHARED_TYPE_HAND_CAST", ""):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

        game = self.playing(61127213)
        spell = self.add(game.engine, "Counterlash", zone="hand")
        target = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
        creature = self.add(game.engine, "Air Elemental", zone="hand")
        artifact = self.add(game.engine, "Arcane Signet", zone="hand")
        other = self.add(game.engine, "Counsel of the Soratami", zone="hand")
        for card in (spell, target, creature, artifact, other):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool["U"] = 6
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
        self.assertEqual({creature.ref, artifact.ref}, {item["card"] for item in options})
        audience = json.dumps(game.packet("pilot:D", full=True))
        for hidden in (creature, artifact, other):
            self.assertNotIn(json.dumps(hidden.ref), audience)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "choice": "cast",
                                            "cast": {"card": other.ref}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        option, = (item for item in options if item["card"] == creature.ref)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "cast",
                                      "cast": {"card": creature.ref, "cost_option": option["id"]}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", game.state.cards[creature.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_actual_counterlash_can_decline_the_private_instant_after_a_protected_target(self):
        game = self.playing(61127214)
        spell = self.add(game.engine, "Counterlash", zone="hand")
        target = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        child = self.add(game.engine, "Opt", zone="hand")
        other = self.add(game.engine, "Counsel of the Soratami", zone="hand")
        for card in (spell, target, child, other):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 6})
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("stack", game.state.cards[target.object_id].zone)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
        self.assertEqual({child.ref}, {item["card"] for item in options})
        result = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[child.object_id].zone)
        self.assertEqual(38, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_hinder_lets_its_resolving_controller_choose_either_library_end(self):
        report, _ = register_card(self.db, self.registry, "Hinder")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Hinder", fuzzy=False)
        with patch("quorune.compiler.stack_counter_followup._LIBRARY_COUNTER_POSITION", ""):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

        for position, index, seed in (("top", -1, 61127215), ("bottom", 0, 61127216)):
            with self.subTest(position=position):
                game = self.playing(seed)
                spell = self.add(game.engine, "Hinder", zone="hand")
                target = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
                for card in (spell, target):
                    self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B")
                game.state.players["A"].mana_pool["U"] = 3
                self.checkpoint(game)
                self.cast_for(game, "B", target)
                self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                self.assertEqual("stack", game.state.cards[target.object_id].zone)
                prior = tuple(game.state.players["B"].zones["library"])
                command = {"action_id": "choose", "position": position}
                for actor, invalid in (("B", command), ("A", {**command, "position": "graveyard"})):
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:" + actor, invalid).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                result = game.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                library = tuple(game.state.players["B"].zones["library"])
                self.assertEqual(target.object_id, library[index])
                self.assertEqual(prior, tuple(ref for ref in library if ref != target.object_id))
                other = json.dumps(game.packet("pilot:C", full=True))
                for object_id in prior:
                    self.assertNotIn(json.dumps(game.state.cards[object_id].ref), other)
                self.assertIsNone(self.resolve(game))
                self.replay(game, load=True)

    def test_actual_hinder_does_not_offer_a_position_for_a_legal_uncounterable_spell(self):
        game = self.playing(61127217)
        spell = self.add(game.engine, "Hinder", zone="hand")
        target = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        for card in (spell, target):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 3})
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual(38, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_hinder_keeps_the_commander_owner_replacement_after_the_casters_choice(self):
        game = self.playing(61127218)
        spell = self.add(game.engine, "Hinder", zone="hand")
        commander, = (card for card in game.state.cards.values() if card.owner == "B" and card.is_commander)
        for card in (spell, commander):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"W": 1})
        game.state.players["A"].mana_pool["U"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", commander)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        selected = game.act("pilot:A", {"action_id": "choose", "position": "bottom"})
        self.assertTrue(selected.ok, selected.summary)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("stack", game.state.cards[commander.object_id].zone)
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        replacement, = (row["id"] for row in options if not row["id"].startswith("decline:"))
        result = game.act("pilot:B", {"action_id": "choose", "replacement": replacement})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("command", game.state.cards[commander.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_hinder_counters_a_copy_without_asking_for_a_library_position(self):
        game = self.playing(61127219)
        spell = self.add(game.engine, "Hinder", zone="hand")
        target = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        copier = self.add(game.engine, "Twincast", seat="C", zone="hand")
        for card in (spell, target, copier):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 3})
        game.state.players["B"].mana_pool["U"] = 3
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)
        original = self.cast_x(game, "B", target, "B", 0)
        self.cast_for(game, "C", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        chosen = game.act("pilot:C", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(chosen.ok, chosen.summary)
        copied = game.state.stack[-1]
        self.assertEqual("spell_copy", copied.kind)
        self.cast_for(game, "A", spell, [copied.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertEqual("library", game.state.cards[target.object_id].zone)
        self.assertTrue(all(copied.card_object_id not in player.zones["library"]
                            for player in game.state.players.values()))
        self.replay(game, load=True)

    def arcane_main(self, game, seat, mana=None):
        self.until(game, lambda item: item.state.current_turn is not None
                   and item.state.current_turn.player == seat
                   and item.state.phase == "precombat_main" and item.state.step == "main")
        self.prepare(game, actor=seat, active=seat, mana=mana)

    def order_next_turn_draw_pair(self, game, active_player):
        self.until(game, lambda item: item.state.pending_decision is not None
                   and item.state.pending_decision.kind == "trigger.order")
        self.assertEqual((active_player, "upkeep"), (game.state.active_player, game.state.step))
        self.assertEqual(["A"], game.state.pending_decision.actors)
        group, = game.state.pending_trigger_batches[0].groups
        self.assertEqual({"A"}, {item.controller for item in group.items})
        refs = {item["context"]["dynamic_effects"][0]["op"]: item.ref for item in group.items}
        self.assertEqual({"draw", "offer_draw"}, set(refs))
        order = {"action_id": "order", "order": [refs["draw"], refs["offer_draw"]]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", order).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        ordered = game.act("pilot:A", order)
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual(refs["offer_draw"], game.state.stack[-1].ref)

    def test_actual_arcane_denial_uses_the_next_turn_and_two_separately_owned_draws(self):
        report, _ = register_card(self.db, self.registry, "Arcane Denial")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Arcane Denial", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        node, = ir.faces[0].nodes
        self.assertEqual(record.oracle_text, record.oracle_text[node.span.start:node.span.end])
        with patch("quorune.compiler.stack_counter_followup._NEXT_TURN_DRAW_PAIR", ""):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

        game = self.playing(61127237)
        spell = self.add(game.engine, "Arcane Denial", zone="hand")
        target = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        for card in (spell, target):
            self.assert_closed(game, card)
        self.arcane_main(game, "C")
        game.state.players["A"].mana_pool["U"] = 2
        game.state.players["B"].mana_pool["U"] = 3
        self.checkpoint(game)
        started = self.cast_x(game, "B", target, "B", 0)
        self.cast_for(game, "A", spell, [started.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual(2, len(game.state.delayed_triggers))
        hands = {seat: set(game.state.players[seat].zones["hand"]) for seat in "AB"}
        self.order_next_turn_draw_pair(game, "D")
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(hands["A"], set(game.state.players["A"].zones["hand"]))
        ctx = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual([0, 1, 2], ctx["legal_actions"][0]["choice_schema"]["legal_values"])
        command = {"action_id": "choose", "count": 2}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:B", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        drawn = {seat: set(game.state.players[seat].zones["hand"]) - hands[seat] for seat in "AB"}
        self.assertEqual((1, 2), (len(drawn["A"]), len(drawn["B"])))
        foreign = json.dumps(game.packet("pilot:C", full=True))
        for object_id in drawn["A"] | drawn["B"]:
            self.assertNotIn(json.dumps(game.state.cards[object_id].ref), foreign)
        self.replay(game, load=True)

    def test_actual_arcane_denial_keeps_both_triggers_and_honors_the_recipients_draw_cap(self):
        game = self.playing(61127238)
        spell = self.add(game.engine, "Arcane Denial", zone="hand")
        target = self.add(game.engine, "Carnage Tyrant", seat="B", zone="hand")
        limit = self.add(game.engine, "Spirit of the Labyrinth", seat="C")
        for card in (spell, target, limit):
            self.assert_closed(game, card)
        self.arcane_main(game, "B", mana={"G": 6})
        game.state.players["A"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertEqual(2, len(game.state.delayed_triggers))
        before_draws = tuple(len(game.state.players[seat].zones["hand"]) for seat in "AB")
        self.order_next_turn_draw_pair(game, "C")
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        ctx = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual([0, 1], ctx["legal_actions"][0]["choice_schema"]["legal_values"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "count": 2}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        declined = game.act("pilot:B", {"action_id": "choose", "count": 0})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((before_draws[0] + 1, before_draws[1]),
                         tuple(len(game.state.players[seat].zones["hand"]) for seat in "AB"))
        self.replay(game, load=True)

    def test_actual_arcane_denial_schedules_neither_draw_for_an_illegal_only_target(self):
        game = self.playing(61127239)
        spell = self.add(game.engine, "Arcane Denial", zone="hand")
        response = self.add(game.engine, "Mana Drain", seat="C", zone="hand")
        target = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
        for card in (spell, response, target):
            self.assert_closed(game, card)
        self.arcane_main(game, "B")
        for seat in ("A", "C"):
            game.state.players[seat].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        ref = game.state.stack[-1].ref
        self.cast_for(game, "A", spell, [ref])
        self.cast_for(game, "C", response, [ref])
        self.assertIsNone(self.resolve(game))
        delayed, = game.state.delayed_triggers
        self.assertEqual("C", delayed.controller)
        self.replay(game, load=True)
