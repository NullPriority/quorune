"""Four closed resolution and entry rules over independent synthetic witnesses."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import EXILED_MANA_VALUE_ENTRY_COUNTER_CAPABILITY
from quorune.opponent_library_top_model import OPPONENT_HALF_TOP_EXILE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.counter_scaled_annihilator import COUNTER_SCALED_ANNIHILATOR_CAPABILITY
from quorune.trigger_participation import WardSacrificeScope, WardSpec
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


WARD_CAPABILITY = "trigger.keyword.ward.two_permanents"
PROFILE = "commander_review"


class CountedWardAndAttackValuesTests(InheritedNativeFixture, unittest.TestCase):
    cast_source = "Trial Half-Library Cartographer"
    ward_source = "Trial Two-Tribute Sentinel"
    entry_source = "Trial Exile-Size Scout"
    attack_source = "Trial Counter-Bound Ravager"
    basic = "Generic Bound Plains"
    small = "Trial Small Witness"
    high = "Trial Public Colossus"
    private = "Trial Sealed Leviathan"
    ward_target = "Trial Ward Mark"
    counter_spell = "Trial Ward Interruption"
    draw_response = "Trial Sudden Insight"
    counter_gain = "Trial Counter Pair"
    gain_count = 2
    bounce_spell = "Trial Sudden Dispersal"
    forest = "Trial Forest"
    island = "Trial Island"
    doubler = "Doubling Season"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "counted-ward-and-attack-values.sqlite3"
        fixture = ROOT / "tests/fixtures"
        build_fixture_database([
            fixture / "bound-effect-program-cards.json",
            fixture / "ward-sacrifice-cards.json",
            fixture / "counter-replacement-cards.json",
            fixture / "counted-ward-and-attack-values-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Counted public effects",
            [DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30)], [commander])

    def ready(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana or {})
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose(self, game, seat, refs):
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": list(refs)})
        self.assertTrue(result.ok, result.summary)

    def snapshot_privacy(self, game, hidden, seats):
        for seat in seats:
            payload = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(hidden.ref, payload)
            self.assertNotIn(hidden.object_id, payload)

    def _target_cast_exile(self, game, source, count):
        owner = ZoneTransitionOwner(game.engine)
        for object_id in tuple(game.state.players["B"].zones["library"]):
            owner.move_card(object_id, "graveyard")
        for index in range(count):
            value = self.add(game.engine, self.high if index > 1 else self.small, seat="B",
                             zone="library", ref=f"private-library-card-{index}")
            value.known_to = []
            value.revealed_to = []
        cost = int(self.db.lookup(source.printed_name).mana_value)
        self.ready(game, source, mana={"C": cost})
        self.cast(game, "A", source)
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "semantic.target")
        self.reject(game, "A", {"action_id": "choose", "targets": ["A"]})
        self.target(game, "A", ["B"])

    def test_targeted_top_half_is_current_face_up_and_independent_of_its_spell(self):
        for count, countered in ((5, False), (5, True), (0, False)):
            with self.subTest(count=count, countered=countered):
                game = self.session(86_410 + count + countered)
                source = self.add(game.engine, self.cast_source, zone="hand")
                counter = self.add(game.engine, self.counter_spell, seat="C", zone="hand")
                self.add(game.engine, self.island, seat="C", ref="counter-island-one")
                self.add(game.engine, self.island, seat="C", ref="counter-island-two")
                draw = self.add(game.engine, self.draw_response, seat="B", zone="hand")
                self.add(game.engine, self.island, seat="B", ref="draw-island")
                self._target_cast_exile(game, source, count)
                other_libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ACD"}
                before = tuple(game.state.players["B"].zones["library"])
                for object_id in before:
                    self.snapshot_privacy(game, game.state.cards[object_id], "ABCD")
                if countered:
                    spell, trigger = game.state.stack
                    self.assertEqual(("spell", "triggered_ability"), (spell.kind, trigger.kind))
                    self.cast(game, "C", counter, [spell.ref])
                    self.resolve_top(game)
                    self.assertNotIn(spell.ref, [item.ref for item in game.state.stack])
                if count and not countered:
                    self.cast(game, "B", draw)
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[before[-1]].zone)
                    before = before[:-1]
                if countered:
                    game = self.replay(game, load=True)
                self.resolve_top(game)
                removed = (len(before) + 1) // 2
                remaining = len(before) - removed
                self.assertEqual(before[:remaining], tuple(game.state.players["B"].zones["library"]))
                self.assertEqual(other_libraries,
                    {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ACD"})
                for object_id in before[remaining:]:
                    card = game.state.cards[object_id]
                    self.assertEqual(("exile", "B", False), (card.zone, card.owner, card.face_down))
                    self.assertNotIn("temporary_play_permission", card.annotations)
                for object_id in before[:remaining]:
                    self.snapshot_privacy(game, game.state.cards[object_id], "ACD")
                if not countered:
                    self.resolve_top(game)
                    current = game.state.cards[source.object_id]
                    self.assertEqual("battlefield", current.zone)
                    if self.cast_source == self.entry_source:
                        self.assertEqual(6 if count else 0, current.counters.get("+1/+1", 0))
                self.replay(game, load=True)

    def test_two_permanent_ward_payer_can_pay_both_or_decline(self):
        for choice in ("pay", "decline", "short", "stale"):
            with self.subTest(choice=choice):
                game = self.session(86_430 + ("pay", "decline", "short", "stale").index(choice))
                source = self.add(game.engine, self.ward_source, seat="B")
                first = self.add(game.engine, self.basic, ref="payer-land")
                second = self.add(game.engine, self.small, seat="C", ref="borrowed-permanent")
                foreign = self.add(game.engine, self.small, seat="D", ref="foreign-permanent")
                secret = self.add(game.engine, self.private, seat="D", zone="hand", ref="ward-hidden")
                instant = self.add(game.engine, self.ward_target, zone="hand")
                if choice != "short":
                    game.engine.change_control(second.object_id, "A", reason="Borrowed permanent")
                self.ready(game, source, instant, mana={"G": 1})
                self.cast(game, "A", instant, [source.ref])
                trigger, = (item for item in game.state.stack if item.semantic_key == "builtin:ward")
                self.assertEqual(WardSacrificeScope.TWO_PERMANENTS,
                    WardSpec.from_dict(trigger.context["ward_spec"]).sacrifice_scope)
                original = trigger.context["target_stack"]
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                self.snapshot_privacy(game, secret, "ABC")
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                self.reject(game, "B", {"action_id": "choose", "cards": [first.ref, second.ref]})
                for cards in ([first.ref], [first.ref, first.ref], [first.ref, foreign.ref]):
                    self.reject(game, "A", {"action_id": "choose", "cards": cards})
                if choice == "stale":
                    mover = ZoneTransitionOwner(game.engine)
                    mover.move_card(second.object_id, "exile")
                    mover.move_card(second.object_id, "battlefield")
                    self.checkpoint(game)
                    self.reject(game, "A", {"action_id": "choose", "cards": [first.ref, second.ref]})
                if choice == "short":
                    self.reject(game, "A", {"action_id": "choose", "cards": [first.ref, second.ref]})
                if choice in {"pay", "stale"}:
                    game = self.replay(game, load=True)
                self.choose(game, "A", [first.ref, second.ref] if choice == "pay" else [])
                self.assertEqual(choice == "pay", any(item.ref == original for item in game.state.stack))
                self.assertEqual("graveyard" if choice == "pay" else "battlefield",
                                 game.state.cards[first.object_id].zone)
                if choice == "pay":
                    self.assertIn(second.object_id, game.state.players["C"].zones["graveyard"])
                    self.resolve_top(game)
                self.replay(game, load=True)

    def test_exile_entry_reads_all_public_cards_including_self_and_replacement(self):
        for origin, doubled in (("hand", True), ("hand", False), ("exile", False)):
            with self.subTest(origin=origin, doubled=doubled):
                game = self.session(86_450 + (origin == "exile") + 2 * doubled)
                source = self.add(game.engine, self.entry_source, zone=origin)
                self.add(game.engine, self.small, seat="B", zone="exile")
                if doubled:
                    self.add(game.engine, self.high, seat="C", zone="exile")
                    self.add(game.engine, self.doubler)
                hidden = self.add(game.engine, self.private, seat="D", zone="exile", ref="secret-exile")
                hidden.face_down = True
                hidden.known_to = []
                hidden.revealed_to = []
                cost = int(self.db.lookup(source.printed_name).mana_value)
                self.ready(game, source, mana={"C": cost})
                if origin == "hand":
                    self.cast(game, "A", source)
                    self.finish_pending_spell_cast(game, source)
                    game = self.replay(game, load=True)
                    self.resolve_top(game)
                else:
                    ZoneTransitionOwner(game.engine).move_card(source.object_id, "battlefield")
                    self.checkpoint(game)
                current = game.state.cards[source.object_id]
                expected = cost if origin == "exile" else 12 if doubled else 2
                self.assertEqual(("battlefield", expected), (current.zone, current.counters.get("+1/+1", 0)))
                self.replay(game, load=True)

    def finish_pending_spell_cast(self, game, source):
        if self.cast_source == self.entry_source:
            self.target(game, "A", ["B"])
            self.resolve_top(game)

    def test_annihilator_tracks_resolution_count_and_original_defender(self):
        for mode in ("current", "lki", "zero"):
            with self.subTest(mode=mode):
                game = self.session(86_470 + ("current", "lki", "zero").index(mode))
                source = self.add(game.engine, self.attack_source)
                source.acquired_control_turn_count = -1
                if mode != "zero":
                    source.counters["+1/+1"] = 1
                defender_costs = [self.add(game.engine, self.basic, seat="B", ref=f"defender-permanent-{i}")
                                  for i in range(4)]
                wrong = self.add(game.engine, self.small, seat="C", ref="foreign-annihilator")
                hidden = self.add(game.engine, self.private, seat="D", zone="hand", ref="annihilator-hidden")
                gain = self.add(game.engine, self.counter_gain, zone="hand")
                bounce = self.add(game.engine, self.bounce_spell, zone="hand")
                self.add(game.engine, self.forest)
                self.add(game.engine, self.island)
                self.ready(game, source, gain, bounce)
                self.attack(game, [(source, "B")])
                self.assertEqual(1, self.trigger_count(game, source))
                trigger = game.state.stack[-1]
                self.assertEqual(("A", "B"), (trigger.controller, trigger.context["defending_player"]))
                if mode != "zero":
                    self.cast(game, "A", gain, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual(1 + self.gain_count, game.state.cards[source.object_id].counters["+1/+1"])
                if mode == "lki":
                    self.cast(game, "A", bounce, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                game = self.replay(game, load=True)
                pending = self.resolve(game)
                if mode == "zero":
                    self.assertIsNone(pending)
                    self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield"
                                        for card in defender_costs))
                else:
                    self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
                    self.snapshot_privacy(game, hidden, "ABC")
                    expected = 1 + self.gain_count
                    choices = [card.ref for card in defender_costs[:expected]]
                    self.reject(game, "A", {"action_id": "choose", "cards": choices})
                    self.reject(game, "B", {"action_id": "choose", "cards": [*choices[:-1], wrong.ref]})
                    self.reject(game, "B", {"action_id": "choose", "cards": choices[:1]})
                    game = self.replay(game, load=True)
                    self.choose(game, "B", choices)
                    self.assertEqual(["graveyard"] * expected + ["battlefield"] * (4 - expected),
                        [game.state.cards[card.object_id].zone for card in defender_costs])
                self.replay(game, load=True)

    def test_new_capabilities_cannot_be_removed_from_their_typed_programs(self):
        cases = ((self.cast_source, OPPONENT_HALF_TOP_EXILE_CAPABILITY),
                 (self.ward_source, WARD_CAPABILITY), (self.entry_source, EXILED_MANA_VALUE_ENTRY_COUNTER_CAPABILITY),
                 (self.attack_source, COUNTER_SCALED_ANNIHILATOR_CAPABILITY))
        game = self.session(86_490)
        for name, capability in cases:
            with self.subTest(capability=capability):
                source = self.add(game.engine, name)
                self.assert_closed(game, source)
                program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                            if capability in row.capability_dependencies)
                dependencies = tuple(key for key in program.capability_dependencies if key != capability)
                if capability == WARD_CAPABILITY:
                    dependencies = ("trigger.keyword.ward.fixed_sacrifice",)
                altered = replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
                report = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(report["strict"], report)
                self.assertIn("capability:undeclared_runtime_dependency:" + capability, report["blockers"])

    def test_unsupported_neighbors_and_required_compiler_branches(self):
        cases = [
            (self.cast_source, "rounded up", "rounded down",
             "quorune.compiler.opponent_top_exile_templates.opponent_top_exile_trigger_result"),
            (self.ward_source, "2 permanents", "three permanents",
             "quorune.compiler.ward_cost_templates.ward_sacrifice_scope"),
            (self.entry_source, "cards in exile", "cards in a hand",
             "quorune.compiler.runtime_templates.dynamic_self_entry_counter_handler"),
            (self.attack_source, "+1/+1", "charge",
             "quorune.oracle_ir.counter_scaled_annihilator_mechanics"),
        ]
        for name, supported, unsupported, owner in cases:
            with self.subTest(name=name):
                row = self.db.lookup(name)
                compile_card = lambda record: compile_oracle_card(record, capability_registry=self.registry,
                                                                 capability_profile=PROFILE)
                self.assertEqual("exact", compile_card(row).status)
                changed = replace(row, oracle_text=row.oracle_text.replace(supported, unsupported))
                self.assertNotEqual("exact", compile_card(changed).status)
                with patch(owner, return_value=None):
                    self.assertNotEqual("exact", compile_card(row).status)
