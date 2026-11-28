"""The independent caster Bird result uses the original as-cast mana value."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_outcome_shapes import CAST_EVENT_COUNTER_BIRDS_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Dovescape"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class CastEventCounterAndManaValueTokensTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "cast-counter-birds.sqlite3"
        build_fixture_database([basic, FIXTURES / "cast-event-counter-and-mana-value-tokens-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast counter and Birds", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        return record, program

    def test_complete_original_owns_sealed_spell_counter_and_original_caster_birds(self):
        record, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertIsNone(program.target_schema)
        counter, tokens = program.effects
        self.assertEqual({"op": "counter_stack_target", "stack": "$context.stack"}, counter)
        self.assertEqual("$context.player", tokens["controller"])
        self.assertEqual("committed_cast_spell_mana_value", tokens["quantity"]["origin"])
        self.assertEqual("Bird Token", tokens["name"])
        self.assertEqual(["Flying"], tokens["characteristics"]["keywords"])
        for text in (
            record.oracle_text.replace("That player creates", "You create"),
            record.oracle_text.replace("noncreature spell", "creature spell"),
            record.oracle_text.replace("That player creates", "If you do, that player creates"),
            record.oracle_text.replace("the spell's mana value", "your life total"),
            record.oracle_text + " Invent an unsupported instruction.",
        ):
            with self.subTest(text=text):
                value = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in value.faces for n in f.nodes))

    def test_raw_result_rejects_reordered_rebound_or_unowned_token_creation(self):
        _, program = self.program()
        counter, tokens = program.effects
        for changed in (
            replace(program, effects=(tokens, counter)),
            replace(program, effects=(counter, {**tokens, "controller": "$controller"})),
            replace(program, effects=(counter, {**tokens, "quantity": 4})),
            replace(program, event_condition=None),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def scene(self, seed, spell_name, mana):
        game = self.session(seed)
        source = self.add(game.engine, NAME)
        spell = self.add(game.engine, spell_name, seat="B", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="dovescape-private")
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        self.prepare(game, actor="B", active="B", mana=mana)
        self.checkpoint(game)
        return game, source, spell, private

    def birds(self, game):
        return [card for card in game.state.cards.values() if card.printed_name == "Bird Token"
                and card.is_token and card.zone == "battlefield"]

    def cast_blaze(self, game, spell):
        action = self.action(game, "B", spell)
        result = game.act("pilot:B", {"action_id": action["id"], "x": 3, "targets": ["D"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_x_spell_counters_and_creates_exactly_as_cast_birds_for_its_caster(self):
        game, _, spell, private = self.scene(202360901, "Blaze", {"C": 3, "R": 1})
        self.cast_blaze(game, spell)
        spell_item, trigger = game.state.stack
        self.assertEqual("B", spell_item.controller)
        self.assertEqual("A", trigger.controller)
        self.assertEqual(spell_item.ref, trigger.context["stack"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual(40, game.state.players["D"].life)
        birds = self.birds(game)
        self.assertEqual(4, len(birds))
        for card in birds:
            self.assertEqual("B", card.controller)
            self.assertEqual("B", card.owner)
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(card.object_id, stat)
                                          for stat in ("power", "toughness")))
            self.assertIn("flying", game.engine._combat_keywords(card))
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_uncounterable_spell_still_creates_birds_before_resolving(self):
        game, _, verdict, _ = self.scene(202360902, "Supreme Verdict", {"C": 1, "W": 2, "U": 1})
        self.cast_for(game, "B", verdict)
        spell, _ = game.state.stack
        self.resolve_top(game)
        self.assertEqual([spell.ref], [item.ref for item in game.state.stack])
        self.assertEqual(4, len(self.birds(game)))
        self.assertTrue(all(card.controller == "B" for card in self.birds(game)))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.birds(game))
        self.assertEqual("graveyard", game.state.cards[verdict.object_id].zone)
        self.replay(game, load=True)

    def test_actual_split_spell_creates_from_the_cast_half_and_does_not_resolve(self):
        game, source, spell, _ = self.scene(202360903, "Fire // Ice", {"C": 1, "U": 1})
        command = f"cast:{spell.ref}:face:Ice"
        offers = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(command, [row["id"] for row in offers])
        before = len(game.state.players["B"].zones["hand"])
        cast = game.act("pilot:B", {"action_id": command, "targets": [source.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual(before - 1, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(2, len(self.birds(game)))
        self.replay(game, load=True)

    def test_actual_departed_caster_does_not_create_birds_or_leave_the_trigger_open(self):
        game, _, spell, _ = self.scene(202360904, "Blaze", {"C": 3, "R": 1})
        self.cast_blaze(game, spell)
        self.priority(game, "B")
        conceded = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn("B", game.engine.active_seats)
        self.assertFalse(game.state.stack)
        self.assertFalse(self.birds(game))
        self.assertEqual(40, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_native_bird_witness_kills_printed_value_mutation_after_actual_x_cast(self):
        from quorune import cast_event_amounts

        facts = cast_event_amounts.committed_cast_mana_value_facts

        def printed_value(item):
            return {**facts(item), "mana_value": 1}

        with patch.object(cast_event_amounts, "committed_cast_mana_value_facts", side_effect=printed_value):
            with self.assertRaisesRegex(AssertionError, "4 != 1"):
                self.test_actual_x_spell_counters_and_creates_exactly_as_cast_birds_for_its_caster()

class CounterbalanceNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "counterbalance.sqlite3"
        build_fixture_database([
            FIXTURES / "bound-effect-program-cards.json",
            FIXTURES / "cast-event-counter-and-mana-value-tokens-cards.json",
            FIXTURES / "fixed-stack-spell-copy-cards.json",
            FIXTURES / "life-gain-prohibition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Optional reveal on cast", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def library_card(self, game, name, ref):
        card = self.add(game.engine, name, zone="library", ref=ref)
        card.known_to = []
        card.revealed_to = []
        return card

    def counterbalance_scene(self, seed, *, original="Blaze", top_name="Counterspell"):
        game = self.session(seed)
        source = self.add(game.engine, "Counterbalance")
        spell = self.add(game.engine, original, seat="B", zone="hand", ref="balance-original")
        top = self.library_card(game, top_name, "balance-initial-top") if top_name else None
        if top is None:
            for oid in tuple(game.state.players["A"].zones["library"]):
                game.engine.move_card(oid, "outside", log=False, semantic_events=False)
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        return game, source, spell, top

    def cast_blaze_for_balance(self, game, spell):
        self.priority(game, "B")
        offer = self.action(game, "B", spell)
        result = game.act("pilot:B", {
            "action_id": offer["id"], "targets": ["D"], "x": 1, "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        cast, trigger = game.state.stack
        self.assertEqual((cast.ref, 2), (trigger.context["stack"], trigger.context["mana_value"]))
        self.assertFalse(trigger.targets)
        return cast.ref

    def balance_choice(self, game, *, hidden=None):
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        decision = game.packet("pilot:A", full=True)["decision"]
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual("choice", schema["field"])
        self.assertEqual({"apply", "decline"}, set(schema["legal_values"]))
        if hidden is not None:
            for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
                self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
            self.assertNotIn(hidden.printed_name, json.dumps(decision))
            self.assertNotIn(hidden.ref, json.dumps(game.state.pending_decision.continuation))
        return decision

    def submit_balance(self, game, choice):
        result = game.act("pilot:A", {"action_id": "choose", "choice": choice})
        self.assertTrue(result.ok, result.summary)

    def reveal_events(self, game):
        return [event for event in game.state.events if event.code == "library.top.reveal"]

    def test_actual_counterbalance_matches_committed_x_and_the_current_top_after_response(self):
        game, _, spell, match = self.counterbalance_scene(70163100)
        first = self.library_card(game, "Generic Bound Plains", "balance-top-before-draw")
        draw = self.add(game.engine, "Revitalize", zone="hand")
        self.prepare(game, actor="B", active="B", mana={"C": 1, "R": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        original = self.cast_blaze_for_balance(game, spell)
        self.cast(game, "A", draw)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertEqual([original], [item.context["stack"] for item in game.state.stack
                                      if item.kind == "triggered_ability"])

        self.balance_choice(game, hidden=match)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "apply"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.submit_balance(game, "apply")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["D"].life)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("library", game.state.cards[match.object_id].zone)
        self.assertEqual(match.object_id, game.state.players["A"].zones["library"][-1])
        reveal, = self.reveal_events(game)
        self.assertEqual(match.printed_name, reveal.details["card"])
        self.replay(game, load=True)

    def test_actual_counterbalance_decline_empty_and_library_x_zero(self):
        for index, mode in enumerate(("decline", "empty", "library_x")):
            with self.subTest(mode=mode):
                top_name = {"decline": "Counterspell", "empty": None, "library_x": "Blaze"}[mode]
                game, _, spell, top = self.counterbalance_scene(70163110 + index, top_name=top_name)
                self.prepare(game, actor="B", active="B", mana={"C": 1, "R": 1})
                self.checkpoint(game)
                self.cast_blaze_for_balance(game, spell)
                if top is not None:
                    self.balance_choice(game, hidden=top)
                    self.submit_balance(game, "decline" if mode == "decline" else "apply")
                self.assertIsNone(self.resolve(game))
                self.assertEqual(39, game.state.players["D"].life)
                if mode == "library_x":
                    reveal, = self.reveal_events(game)
                    self.assertEqual(top.printed_name, reveal.details["card"])
                else:
                    self.assertFalse(self.reveal_events(game))
                if top is not None:
                    self.assertEqual("library", game.state.cards[top.object_id].zone)
                self.replay(game, load=True)

    def test_actual_counterbalance_preserves_reveal_after_original_departed(self):
        game, _, spell, top = self.counterbalance_scene(70163120)
        answer = self.add(game.engine, "Counterspell", zone="hand", ref="balance-other-counterspell")
        self.prepare(game, actor="B", active="B", mana={"C": 1, "R": 1})
        game.state.players["A"].mana_pool["U"] = 2
        self.checkpoint(game)
        original = self.cast_blaze_for_balance(game, spell)
        self.cast(game, "A", answer, [original])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertNotIn(original, {item.ref for item in game.state.stack})
        self.balance_choice(game, hidden=top)
        game = self.replay(game, load=True)
        self.submit_balance(game, "apply")
        self.assertIsNone(self.resolve(game))
        reveal, = self.reveal_events(game)
        self.assertEqual(top.printed_name, reveal.details["card"])
        self.assertEqual(40, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_counterbalance_reveals_a_match_for_uncounterable_original(self):
        game, _, spell, top = self.counterbalance_scene(
            70163130, original="Supreme Verdict", top_name="Supreme Verdict",
        )
        bear = self.add(game.engine, "Grizzly Bears")
        self.prepare(game, actor="B", active="B", mana={"C": 1, "W": 2, "U": 1})
        self.checkpoint(game)
        self.cast(game, "B", spell)
        cast = game.state.stack[0]
        self.assertFalse(game.engine._stack_item_can_be_countered(cast))
        self.balance_choice(game, hidden=top)
        self.submit_balance(game, "apply")
        self.assertIsNone(self.resolve(game))
        reveal, = self.reveal_events(game)
        self.assertEqual(top.printed_name, reveal.details["card"])
        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
        self.replay(game, load=True)

    def test_actual_counterbalance_ignores_own_cast_and_uncast_copy(self):
        game, source, unused, top = self.counterbalance_scene(70163140)
        draw = self.add(game.engine, "Revitalize", zone="hand")
        copier = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        self.cast(game, "A", draw)
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.cast(game, "B", copier, [game.state.stack[-1].ref])
        self.balance_choice(game, hidden=top)
        self.submit_balance(game, "decline")
        self.resolve_top(game)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual("B", copied.controller)
        self.assertFalse(any(item.source_object_id == source.object_id for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(game.engine._current_turn_history("spell_cast")))
        self.assertEqual("hand", game.state.cards[unused.object_id].zone)
        self.assertFalse(self.reveal_events(game))
        self.replay(game, load=True)

    def test_actual_counterbalance_distinguishes_a_cast_half_from_the_combined_library_card(self):
        game, source, split, top = self.counterbalance_scene(
            70163150, original="Fire // Ice", top_name="Fire // Ice",
        )
        self.prepare(game, actor="B", active="B", mana={"C": 1, "U": 1})
        self.checkpoint(game)
        command = {"action_id": f"cast:{split.ref}:face:Ice", "targets": [source.ref], "pay": "auto"}
        cast = game.act("pilot:B", command)
        self.assertTrue(cast.ok, cast.summary)
        original, trigger = game.state.stack
        self.assertEqual((original.ref, 2.0), (trigger.context["stack"], trigger.context["mana_value"]))
        self.balance_choice(game, hidden=top)
        self.submit_balance(game, "apply")
        self.assertIsNone(self.resolve(game))
        reveal, = self.reveal_events(game)
        self.assertEqual(top.printed_name, reveal.details["card"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("library", game.state.cards[top.object_id].zone)
        self.replay(game, load=True)

    def test_counterbalance_descriptor_rejects_wrong_caster_scope_or_rebound_occurrence(self):
        from quorune.rules.event_outcome_shapes import (
            CAST_REVEAL_MANA_VALUE_COUNTER, cast_reveal_counter_effect, validate_cast_reveal_counter,
        )

        for invalid in (True, -1, float("inf"), float("nan")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_cast_reveal_counter(
                    cast_reveal_counter_effect(player="A", stack="S1", mana_value=invalid), actor="A",
                )

        result, semantics = register_card(self.db, self.registry, "Counterbalance")
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        source = self.db.lookup("Counterbalance")
        program, = semantics.programs_for_oracle(source.oracle_id)
        original, = program.effects
        leaf, = original["effects"]
        substitutions = (
            replace(program, event_condition=None),
            replace(program, target_schema={"zones": ["stack"], "categories": ["spell"], "count": 1}),
            replace(program, effects=({**original, "effects": [{**leaf, "stack": "$target.0"}]},)),
            replace(program, effects=({**original, "effects": [{
                **leaf, CAST_REVEAL_MANA_VALUE_COUNTER: {"schema_version": True, "mana_value": "$context.mana_value"},
            }]},)),
        )
        for modified in substitutions:
            with self.subTest(modified=modified.effects):
                binding = bind_semantic_program_runtime(modified, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"], binding)
