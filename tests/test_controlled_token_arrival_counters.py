"""Matched token arrivals, ordinary source counters and exact pending replay."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.rules import aggregated_arrival_counters
from quorune.rules.aggregated_arrival_counters import (
    MATCHED_ARRIVAL_CAPABILITY, MATCHED_ARRIVAL_CONTEXT, MATCHED_ARRIVAL_COUNT,
    matched_arrival_identities,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
ORIGINAL = "Woodland Champion"
FIXTURES = ROOT / "tests" / "fixtures"


class ControlledTokenArrivalCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "matched-token-arrival.sqlite3"
        build_fixture_database([
            FIXTURES / "bound-effect-program-cards.json",
            FIXTURES / "aggregated-zone-event-triggers.json",
            FIXTURES / "impending-cards.json",
            FIXTURES / "additional-token-definition-cards.json",
            FIXTURES / "source-block-defender-cards.json",
            FIXTURES / "controlled-token-arrival-cards.json",
        ], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Matched token entries", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def original(self, game):
        champion = self.add(game.engine, ORIGINAL)
        self.assert_closed(game, champion)
        return champion

    def assert_champion_trigger(self, game, source, count):
        (trigger,) = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertEqual([], trigger.targets)
        members = matched_arrival_identities(trigger.context)
        self.assertEqual(count, len(members))
        return members

    def test_exact_compiler_and_runtime_require_grouped_controller_token_entry(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)

        def compile_record(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        compiled = compile_record(card)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        (trigger,) = compiled.faces[0].nodes
        self.assertEqual("permanent.enter", trigger.event)
        self.assertIsNone(trigger.target_schema)
        self.assertIn("one_or_more_event_batch", trigger.runtime_coverage)
        self.assertIn(MATCHED_ARRIVAL_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual(MATCHED_ARRIVAL_COUNT, trigger.effects[0]["amount"])
        self.assertEqual("$source.zone_object", trigger.effects[0]["card"])
        for changed in (
            card.oracle_text.replace("one or more tokens", "a token"),
            card.oracle_text.replace("you control", "an opponent controls"),
            card.oracle_text.replace("tokens you control enter", "creatures you control enter"),
            card.oracle_text.replace("on this creature", "on target creature"),
        ):
            self.assertNotEqual("exact", compile_record(replace(card, oracle_text=changed)).status, changed)

        game = self.session(60320630)
        source = self.original(game)
        (program,) = game.engine.semantics.programs_for_oracle(source.oracle_id)
        keep = tuple(cap for cap in program.capability_dependencies if cap != MATCHED_ARRIVAL_CAPABILITY)
        missing = replace(program, capability_dependencies=keep,
                          capability_closure=self.registry.closure(keep, profile=PROFILE).to_dict())
        for changed in (missing, replace(program, event_condition=None),
                        replace(program, coverage=tuple(key for key in program.coverage
                                                        if key != "one_or_more_event_batch"))):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        missing_result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + MATCHED_ARRIVAL_CAPABILITY,
                      missing_result["blockers"])

    def test_group_and_trigger_copy_share_distinct_members_and_reject_bad_ledger(self):
        def pending(member, group="same-occurrence"):
            return SimpleNamespace(semantic_key="original", source_object_id="source-1",
                context={"one_or_more_aggregation_id": group, MATCHED_ARRIVAL_CONTEXT: (member,)})

        def verify_members():
            first, copied = pending("token-1@1"), pending("token-1@1")
            incoming = pending("token-2@1")
            coalesce = aggregated_arrival_counters.coalesce_trigger_aggregation
            self.assertTrue(coalesce(incoming, (first, copied)))
            self.assertTrue(coalesce(incoming, (first, copied)))
            self.assertFalse(coalesce(pending("token-3@1", "next"), (first, copied)))
            self.assertEqual(("token-1@1", "token-2@1"), matched_arrival_identities(first.context))
            self.assertEqual(matched_arrival_identities(first.context), matched_arrival_identities(copied.context))

        verify_members()

        def deduplicate_without_count(item, waiting):
            return any(existing.context["one_or_more_aggregation_id"]
                       == item.context["one_or_more_aggregation_id"] for existing in waiting)

        with patch.object(aggregated_arrival_counters, "coalesce_trigger_aggregation", deduplicate_without_count):
            with self.assertRaises(AssertionError):
                verify_members()
        for malformed in ((), ["token-1@1", "token-1@1"], [None]):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                matched_arrival_identities({MATCHED_ARRIVAL_CONTEXT: malformed})

    def test_actual_simultaneous_tokens_stay_counted_after_one_leaves_and_replay(self):
        game = self.session(60320631)
        source = self.original(game)
        alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        foreign = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand", ref="foreign-alarm")
        unsummon = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="token-departure")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="arrival-private")
        self.prepare(game, mana={"W": 1, "C": 1})
        game.state.players["B"].mana_pool.update({"W": 1, "U": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", foreign, reserve={"U": 1})
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.cast(game, "A", alarm)
        self.resolve_top(game)
        own_tokens = [card for card in game.state.cards.values()
                      if card.is_token and card.zone == "battlefield" and card.controller == "A"]
        self.assertEqual(2, len(own_tokens))
        counted = self.assert_champion_trigger(game, source, 2)
        self.assertEqual(set(counted), {card.logical_object_id for card in own_tokens})
        self.cast(game, "B", unsummon, [own_tokens[0].ref])
        self.resolve_top(game)
        self.assertEqual(1, sum(card.is_token and card.zone == "battlefield" and card.controller == "A"
                                for card in game.state.cards.values()))
        game = self.replay(game, load=True)
        self.assertEqual(counted, self.assert_champion_trigger(game, source, 2))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[source.object_id].counters.get("+1/+1"))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_spell_copy_token_counts_once_when_the_original_nontoken_does_not(self):
        game = self.session(60320632)
        source = self.original(game)
        original = self.add(game.engine, "Grizzly Bears", zone="hand")
        copier = self.add(game.engine, "Double Major", zone="hand")
        self.assert_closed(game, copier)
        self.prepare(game, mana={"C": 1, "G": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", original, reserve={"G": 1, "U": 1})
        original_spell = game.state.stack[-1]
        self.cast_for(game, "A", copier, [original_spell.ref])
        self.resolve_top(game)
        spell_copy = next(item for item in game.state.stack if item.kind == "spell_copy")
        copied_id = spell_copy.card_object_id
        self.resolve_top(game)
        entrant = game.state.cards[copied_id]
        self.assertTrue(entrant.is_token)
        self.assertEqual((entrant.logical_object_id,), self.assert_champion_trigger(game, source, 1))
        self.assertFalse(any(event.code == "token.create" for event in game.state.events))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.assertFalse(game.state.cards[original.object_id].is_token)
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.replay(game, load=True)

    def test_actual_noncreature_token_counts_and_source_blink_invalidates_the_old_result(self):
        game = self.session(60320633)
        source = self.original(game)
        inspector = self.add(game.engine, "Thraben Inspector", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.assert_closed(game, blink)
        self.prepare(game, mana={"W": 3, "C": 1})
        self.checkpoint(game)
        self.cast(game, "A", inspector)
        self.assertIsNone(self.resolve(game))
        (clue,) = (card for card in game.state.cards.values()
                   if card.is_token and card.zone == "battlefield" and card.printed_name == "Clue Token")
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.assertEqual("A", clue.controller)
        self.cast_for(game, "A", alarm, reserve={"W": 1})
        self.resolve_top(game)
        self.assert_champion_trigger(game, source, 2)
        incarnation = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(incarnation, game.state.cards[source.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)
