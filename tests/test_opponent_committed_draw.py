"""Native proof for a single source-entry and qualifying opponent-draw ability."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as vanishing_witness
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.card_programs.reviewed_overlay import shadowed_reviewed_multi_event_keys
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.permissions import PermissionDenied
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import (
    DRAW_EXCEPT_FIRST_OWN_STEP_EVENT,
    FixedEventSubscriptionSet,
)
from scripts.build_test_database import build_fixture_database


BOWMASTERS = "Orcish Bowmasters"
ORACLE_ID = "ea5103f5-27e0-4eb1-902c-7f34652d6bf3"
DRAW_CAPABILITY = "trigger.event.normalized_card_draw"
PROFILE = "commander_review"
FIXTURE_CARDS = {
    "scryfall-exact-lists.json": {BOWMASTERS},
    "draw-step-exempt-multiplier-cards.json": {
        "Isamaru, Hound of Konda", "Darkblast", "Island", "Reach Through Mists", "Plains",
    },
    "announced-x-public-damage-set-cards.json": {"Grizzly Bears", "Mending Hands"},
    "qualified-attack-cards.json": {"Unsummon"},
    "counted-library-search-cards.json": {"Demonic Tutor"},
    "opponent-committed-draw-cards.json": {"Quick Study"},
    "turn-strike-opponent-draw-test-cards.json": {"Strikekeeper Test Creature"},
}


class OpponentCommittedDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        cards, rulings = [], []
        for fixture_name, names in FIXTURE_CARDS.items():
            fixture = json.loads((ROOT / "tests/fixtures" / fixture_name).read_text(encoding="utf-8"))
            selected = [row for row in fixture["cards"] if row["name"] in names]
            if {row["name"] for row in selected} != names:
                raise ValueError(f"Missing original card data in {fixture_name}")
            ids = {row["oracle_id"] for row in selected}
            cards.extend(selected)
            rulings.extend(row for row in fixture["rulings"] if row["oracle_id"] in ids)
        inputs = root / "cards.json"
        inputs.write_text(
            json.dumps({"schema_version": 1, "cards": cards, "rulings": rulings}), encoding="utf-8",
        )
        database = root / "draw.sqlite3"
        build_fixture_database([inputs], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Opponent committed draw",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander],
        )

    def assert_no_trigger(self, game, source):
        self.assertEqual([], self.bowmasters_triggers(game, source))
        self.assertEqual("priority", game.state.pending_decision.kind)

    def bowmasters_triggers(self, game, source):
        items_by_ref = {item.ref: item for item in game.state.stack}
        for batch in game.state.pending_trigger_batches:
            items_by_ref.update((item.ref, StackItem.from_dict(item.to_dict())) for item in batch.items)
        return [item for item in items_by_ref.values()
                if item.kind == "triggered_ability" and item.source_object_id == source.object_id]

    def choose_targets(self, game, *refs):
        for ref in refs:
            decision = game.state.pending_decision
            if decision.kind == "trigger.order":
                order = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                self.accept(game, "A", {"action_id": "order", "triggers": [row["id"] for row in order]})
            self.assertEqual(("semantic.target", ["A"]),
                             (game.state.pending_decision.kind, game.state.pending_decision.actors))
            self.accept(game, "A", {"action_id": "choose", "targets": [ref]})

    def accept(self, game, seat, command):
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def army(self, game):
        armies = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"
                  and card.controller == "A"
                  and "army" in game.engine._type_parts(game.engine._effective_card_data(card)["type_line"])[1]]
        self.assertLessEqual(len(armies), 1)
        return armies[0] if armies else None

    def test_whole_card_and_reviewed_split_runtime_authority(self):
        record = self.db.by_oracle_id(ORACLE_ID)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual((), ir.material_residuals)
        flash, ability = ir.faces[0].nodes
        self.assertEqual(("cast.permission", 0, 5), (flash.event, flash.span.start, flash.span.end))
        self.assertEqual(record.oracle_text.splitlines()[1],
                         record.oracle_text[ability.span.start:ability.span.end])
        self.assertEqual(("damage", "amass"), tuple(effect["op"] for effect in ability.effects))
        subscriptions = FixedEventSubscriptionSet.from_condition(ability.event_condition).subscriptions
        self.assertEqual(("permanent.enter", DRAW_EXCEPT_FIRST_OWN_STEP_EVENT), tuple(row.event for row in subscriptions))
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, subscriptions[0].condition)
        self.assertEqual({"field": "player", "op": "ne", "value": "$source.controller"}, subscriptions[1].condition)

        game = self.session(57651001)
        reviewed = tuple(row for row in game.engine.semantics.programs_for_oracle(ORACLE_ID)
                         if row.ability_id.startswith("trigger:"))
        self.assertEqual({"permanent.enter.self", DRAW_EXCEPT_FIRST_OWN_STEP_EVENT}, {row.event for row in reviewed})
        source = self.add(game.engine, BOWMASTERS, zone="hand")
        self.assert_closed(game, source)
        (program,) = [row for row in game.engine.semantics.programs_for_oracle(ORACLE_ID)
                      if row.ability_id.startswith("trigger:")]
        self.assertEqual({row.key for row in reviewed}, shadowed_reviewed_multi_event_keys(record, [program], reviewed))

        entry, draw = sorted(reviewed, key=lambda row: row.event, reverse=True)
        for variant in (
            [entry],
            [entry, replace(draw, event_condition={**draw.event_condition, "op": "eq"})],
            [entry, replace(draw, target_schema={**draw.target_schema, "count": 2})],
            [entry, replace(draw, active_zone="hand")],
            [entry, replace(draw, oracle_id="different-printed-source")],
            [entry, replace(draw, effects=draw.effects[::-1])],
            [entry, replace(draw, provenance={**draw.provenance, "source_rulings_hash": "obsolete"})],
            [entry, draw, replace(draw, key=draw.key + "-duplicate")],
        ):
            self.assertEqual(set(), shadowed_reviewed_multi_event_keys(record, [program], variant))
        dependencies = tuple(value for value in program.capability_dependencies if value != DRAW_CAPABILITY)
        forged = replace(program, capability_dependencies=dependencies,
                         capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{DRAW_CAPABILITY}", binding["blockers"])

        for wording in (
            record.oracle_text.replace("first one", "second one"),
            record.oracle_text.replace("their draw steps", "your draw steps"),
            record.oracle_text.replace("an opponent draws a card", "an opponent puts a card into their hand"),
        ):
            changed = compile_oracle_card(replace(record, oracle_text=wording), capability_registry=self.registry,
                                          capability_profile=PROFILE)
            self.assertNotEqual("exact", changed.status)
            self.assertTrue(changed.material_residuals)

    def test_suffix_turn_strike_and_opponent_draw_damage_replay(self):
        game = self.session(748130)
        source = self.add(game.engine, "Strikekeeper Test Creature")
        drawing = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="needle-draw-mana")
        self.assert_closed(game, source)
        self.assert_closed(game, drawing)
        self.prepare(game)
        self.assertIn("first strike", game.engine._combat_keywords(source))
        self.checkpoint(game)
        life = {seat: game.state.players[seat].life for seat in "ABCD"}

        self.cast_for(game, "B", drawing)
        self.resolve_top(game)
        trigger, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("B", trigger.context["player"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life["B"] - 1, game.state.players["B"].life)
        for seat in "ACD":
            self.assertEqual(life[seat], game.state.players[seat].life)
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertNotIn("first strike", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.replay(game, load=True)

    def test_actual_off_turn_flash_entry_has_one_target_and_damage_before_amass(self):
        game = self.session(57651002)
        source = self.add(game.engine, BOWMASTERS, zone="hand")
        land = self.add(game.engine, "Plains", seat="D", ref="untargetable-by-any-target")
        self.assert_closed(game, source)
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool.update({"C": 1, "B": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", source)
        self.resolve_top(game)
        (entry,) = self.bowmasters_triggers(game, source)
        self.assertEqual("permanent.enter", entry.context["event"])
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual("B", game.state.active_player)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)

        game = self.replay(game, load=True)
        token = game.engine.permissions.capability_for("pilot:A").token
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "targets": ["C"]}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": [land.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.choose_targets(game, "C")
        before = authoritative_state_hash(game.state)
        with self.assertRaises(PermissionDenied):
            game.engine.submit(token=token, principal="pilot:A", action="choose", payload={"targets": ["D"]})
        self.assertEqual(before, authoritative_state_hash(game.state))

        life = game.state.players["C"].life
        start = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 1, game.state.players["C"].life)
        army = self.army(game)
        self.assertEqual(1, army.counters["+1/+1"])
        self.assertIn("orc", game.engine._type_parts(game.engine._effective_card_data(army)["type_line"])[1])
        codes = [event.code for event in game.state.events[start:]]
        self.assertLess(codes.index("effect.damage"), codes.index("keyword_action.amass.completed"))
        self.replay(game, load=True)

    def test_actual_replaced_normal_draw_other_seat_and_later_draws(self):
        game = self.session(57651003)
        source = self.add(game.engine, BOWMASTERS)
        dredge = self.add(game.engine, "Darkblast", seat="B", zone="graveyard")
        first = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand", ref="first-actual-own-draw")
        study = self.add(game.engine, "Quick Study", seat="B", zone="hand")
        other = self.add(game.engine, "Reach Through Mists", seat="C", zone="hand", ref="other-seat-draw")
        own = self.add(game.engine, "Reach Through Mists", seat="A", zone="hand", ref="source-controller-draw")
        c_top = self.add(game.engine, "Plains", seat="C", zone="library", ref="other-seat-private-card")
        for card in (source, dredge, first, study, other, own):
            self.assert_closed(game, card)

        for seat, count in (("A", 1), ("B", 4), ("C", 1)):
            for index in range(count):
                self.add(game.engine, "Island", seat=seat, ref=f"draw-island-{seat}-{index}")
        self.prepare(game, actor="B", active="B")
        vanishing_witness.VanishingRuntimeTests.upkeep(self, game, seat="B")
        self.checkpoint(game)
        self.until(game, lambda state: state.state.pending_decision.kind == "draw.replacement")
        self.assertEqual(("draw", ["B"]), (game.state.step, game.state.pending_decision.actors))
        self.accept(game, "B", {"action_id": "choose", "choice": dredge.ref})
        self.assertEqual("hand", game.state.cards[dredge.object_id].zone)
        self.assert_no_trigger(game, source)

        self.cast_for(game, "B", first)
        self.assertIsNone(self.resolve(game))
        self.assert_no_trigger(game, source)
        self.cast_for(game, "A", own)
        self.assertIsNone(self.resolve(game))
        self.assert_no_trigger(game, source)

        self.cast_for(game, "C", other)
        self.resolve_top(game)
        (other_trigger,) = self.bowmasters_triggers(game, source)
        self.assertEqual(
            (DRAW_EXCEPT_FIRST_OWN_STEP_EVENT, "C", 1, False, None),
            tuple(other_trigger.context[key] for key in
                  ("event", "player", "draw_ordinal", "in_own_draw_step", "draw_step_ordinal")),
        )
        self.assertEqual("hand", game.state.cards[c_top.object_id].zone)
        for seat in "ABD":
            self.assertNotIn(c_top.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.choose_targets(game, "D")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self.army(game).counters["+1/+1"])

        game = self.replay(game, load=True)
        self.cast_for(game, "B", study)
        self.resolve_top(game)
        later = self.bowmasters_triggers(game, source)
        self.assertEqual([2, 3], sorted(item.context["draw_step_ordinal"] for item in later))
        self.assertEqual({("B", True, "A")}, {(item.context["player"], item.context["in_own_draw_step"],
                                              item.controller) for item in later})
        game = self.replay(game, load=True)
        self.choose_targets(game, "C", "D")
        self.assertIsNone(self.resolve(game))
        self.assertEqual((39, 38), (game.state.players["C"].life, game.state.players["D"].life))
        self.assertEqual(3, self.army(game).counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_non_draw_library_to_hand_does_not_trigger(self):
        game = self.session(57651004)
        source = self.add(game.engine, BOWMASTERS)
        tutor = self.add(game.engine, "Demonic Tutor", seat="B", zone="hand")
        selected = self.add(game.engine, "Plains", seat="B", zone="library", ref="tutored-private-card")
        self.assert_closed(game, tutor)
        self.prepare(game, actor="B", active="B", mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", tutor)
        decision = self.resolve(game)
        self.assertEqual(("semantic.search", ["B"]), (decision.kind, decision.actors))
        self.accept(game, "B", {"action_id": "choose", "search_cards": [selected.ref]})
        self.assertEqual("hand", game.state.cards[selected.object_id].zone)
        self.assert_no_trigger(game, source)
        self.assertIsNone(self.army(game))
        for seat in "ACD":
            self.assertNotIn(selected.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_archived_split_programs_continue_to_load_and_replay_their_own_keys(self):
        game = self.session(57651007)
        legacy = tuple(game.engine.semantics.programs_for_oracle(ORACLE_ID))
        source = self.add(game.engine, BOWMASTERS)
        spell = self.add(game.engine, "Quick Study", zone="hand", seat="B")
        # Use the original reviewed payload as the archived record's registry.
        registry = game.engine.semantics
        for program in registry.programs_for_oracle(ORACLE_ID):
            registry.remove(program.key)
        for program in legacy:
            registry.put(program)
        self.prepare(game, active="B", actor="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", spell)
        self.resolve_top(game)
        retired_draw_key = next(row.key for row in legacy if row.event == DRAW_EXCEPT_FIRST_OWN_STEP_EVENT)
        triggers = self.bowmasters_triggers(game, source)
        self.assertEqual([retired_draw_key, retired_draw_key], [item.semantic_key for item in triggers])

        game = self.replay(game, load=True)
        loaded = game.engine.semantics.programs_for_oracle(ORACLE_ID)
        self.assertEqual({p.key: p.to_dict() for p in legacy}, {p.key: p.to_dict() for p in loaded})
        self.choose_targets(game, "C", "D")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, self.army(game).counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_sole_target_departure_cancels_amass_but_prevention_does_not(self):
        for name, color in (("Unsummon", "U"), ("Mending Hands", "W")):
            with self.subTest(response=name):
                game = self.session(57651005 if name == "Unsummon" else 57651006)
                source = self.add(game.engine, BOWMASTERS, zone="hand")
                subject = self.add(game.engine, "Grizzly Bears", seat="B")
                response = self.add(game.engine, name, seat="B", zone="hand")
                for card in (source, response):
                    self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B", mana={color: 1})
                game.state.players["A"].mana_pool.update({"C": 1, "B": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.resolve_top(game)
                self.choose_targets(game, subject.ref)
                self.cast_for(game, "B", response, [subject.ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(0, game.state.cards[subject.object_id].marked_damage)
                if name == "Unsummon":
                    self.assertEqual("hand", game.state.cards[subject.object_id].zone)
                    self.assertIsNone(self.army(game))
                else:
                    self.assertEqual("battlefield", game.state.cards[subject.object_id].zone)
                    self.assertEqual(1, self.army(game).counters["+1/+1"])
                self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
