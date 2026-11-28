from dataclasses import replace
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
from quorune.token_copy_recipes import ANNOUNCED_HASTY_COPY_BATCH_CAPABILITY, TOKEN_COPY_BATCH_FIELD
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Twinflame"
PROFILE = "commander_review"


class TwinflameCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "twinflame.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/twinflame-copies-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Announced Strive copies", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_paid_strive_copies_only_revalidated_targets_and_exiles_the_exact_batch(self):
        for index, (count, blink) in enumerate(((0, False), (2, False), (2, True))):
            with self.subTest(count=count, blink=blink):
                game = self.session(70210590 + index)
                host = game.engine
                spell = self.add(host, CARD_NAME, zone="hand")
                self.assert_closed(game, spell)
                bear = self.add(host, "Grizzly Bears", ref="twinflame-bear")
                giant = self.add(host, "Hill Giant", ref="twinflame-giant")
                outside = self.add(host, "Grizzly Bears", seat="B", ref="twinflame-opposing-bear")
                blink_spell = self.add(host, "Ephemerate", zone="hand")
                bear.tapped = True
                bear.counters["+1/+1"] = 1
                self.prepare(game, mana={"C": 3, "R": 2, "W": 1})
                self.checkpoint(game)
                offer = self.action(game, "A", spell)
                payment = {"C": 3, "R": 2} if count else {"C": 1, "R": 1}
                command = {"action_id": offer["id"], "targets": [bear.ref, giant.ref] if count else [],
                           "pay": "manual", "payment": payment}
                if count:
                    before = authoritative_state_hash(game.state)
                    for denied_targets in ([bear.ref, bear.ref], [bear.ref, outside.ref]):
                        denied = game.act("pilot:A", {**command, "targets": denied_targets})
                        self.assertFalse(denied.ok)
                        self.assertEqual(before, authoritative_state_hash(game.state))
                cast = game.act("pilot:A", command)
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual(1 if count else 4, sum(game.state.players["A"].mana_pool.values()))
                stack_incarnation = game.state.cards[spell.object_id].logical_object_id
                if blink:
                    self.cast_for(game, "A", blink_spell, [giant.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
                expected = set() if not count else {"Grizzly Bears"} if blink else {"Grizzly Bears", "Hill Giant"}
                self.assertEqual(expected, {card.printed_name for card in tokens})
                self.assertEqual(len(expected), len(tokens))
                for token in tokens:
                    self.assertFalse(token.tapped)
                    self.assertEqual({}, token.counters)
                    self.assertIn("haste", {word.casefold() for word in game.engine._copyable_characteristics(token)["keywords"]})
                delays = [item for item in game.state.delayed_triggers
                          if item.active and item.source_logical_object_id == stack_incarnation]
                self.assertEqual(int(bool(count)), len(delays))
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                if count:
                    self.assertEqual(stack_incarnation, delays[0].source_logical_object_id)
                    ids = {card.object_id for card in tokens}
                    self.until(game, lambda current: current.state.step == "end_step")
                    self.assertIsNone(self.resolve(game))
                    self.assertTrue(all(game.state.cards[object_id].zone != "battlefield" for object_id in ids))
                    self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
                    self.assertEqual("battlefield", game.state.cards[giant.object_id].zone)
                self.replay(game, load=True)

    def test_announced_hasty_batch_owner_and_exact_schema_cannot_be_weakened(self):
        record = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (p for p in semantics.programs_for_oracle(record.oracle_id) if p.event == "resolve")
        effect, = program.effects
        for fields in ({"schema_version": True}, {"kind": "announced_opponent_creatures"}):
            altered = replace(program, effects=({**effect, TOKEN_COPY_BATCH_FIELD: {
                **effect[TOKEN_COPY_BATCH_FIELD], **fields,
            }},))
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        remaining = tuple(cap for cap in program.capability_dependencies if cap != ANNOUNCED_HASTY_COPY_BATCH_CAPABILITY)
        omitted = replace(program, capability_dependencies=remaining,
                          capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + ANNOUNCED_HASTY_COPY_BATCH_CAPABILITY,
                      report["blockers"])
        with patch("quorune.compiler.token_copy_templates._token_copy_batch_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)
