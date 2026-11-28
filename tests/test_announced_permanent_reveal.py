"""Locked-X public reveal, selectable permanents, and replacement-adjusted remainder."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune import zone_transitions
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.announced_permanent_reveal import CAPABILITY, DEPENDENCIES
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

CARD = "Genesis Wave"
PROFILE = "commander_review"
ORACLE = (
    "Reveal the top X cards of your library. You may put any number of permanent cards with mana value "
    "X or less from among them onto the battlefield. Then put all cards revealed this way that weren't "
    "put onto the battlefield into your graveyard."
)


class AnnouncedPermanentRevealTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "announced-permanent-reveal.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "announced-permanent-reveal-cards.json",
            "state-qualified-zone-cards.json", "ability-source-counter-cards.json", "banding-cards.json",
            "graveyard-exile-replacement-cards.json", "mixed-permanent-target-cards.json",
            "qualified-attack-batch-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Announced permanent reveal", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiler_binds_the_whole_public_prefix_and_both_results_to_announced_x(self):
        record = replace(query_record(ORACLE), type_line="Sorcery", mana_cost="{X}{G}{G}{G}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(ORACLE, ORACLE[node.span.start:node.span.end])
        self.assertTrue(set(DEPENDENCIES) <= set(node.capability_dependencies))
        self.assertEqual(1, node.effects[0]["amount"]["coefficient"])
        for old, new in (("Reveal", "Look at"), ("any number", "exactly one"),
                         ("X or less", "X or greater"), ("into your graveyard", "into your hand")):
            with self.subTest(new=new):
                other = replace(record, oracle_text=ORACLE.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    other, capability_registry=self.registry, capability_profile=PROFILE).status)
        no_x = replace(record, mana_cost="{3}{G}{G}{G}")
        self.assertNotEqual("exact", compile_oracle_card(no_x, capability_registry=self.registry,
                                                        capability_profile=PROFILE).status)
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        program, = semantics.programs_for_oracle(self.db.lookup(CARD, fuzzy=False).oracle_id)
        dependencies = [key for key in program.capability_dependencies if key != CAPABILITY]
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])

    def library(self, game, names, *, exact=False):
        if exact:
            for object_id in tuple(game.state.players["A"].zones["library"]):
                game.engine.move_card(object_id, "exile", log=False)
        top = []
        for index, name in reversed(tuple(enumerate(names))):
            card = self.add(game.engine, name, zone="library", ref=f"reveal-position-{index}")
            top.insert(0, card)
        return tuple(top)

    def cast(self, game, spell, x, *, mana=None):
        self.assert_closed(game, spell)
        self.prepare(game, mana=mana or {"C": x, "G": 3})
        self.checkpoint(game)
        offer = self.action(game, "A", spell)
        receipt = game.act("pilot:A", {"action_id": offer["id"], "x": x, "pay": "auto"})
        self.assertTrue(receipt.ok, receipt.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)

    @staticmethod
    def card(game, card):
        return game.state.cards[card.object_id]

    def choose(self, game, cards):
        receipt = game.act("pilot:A", {"action_id": "choose", "cards": [card.ref for card in cards]})
        self.assertTrue(receipt.ok, receipt.summary)

    def decision(self, game):
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.choice", packet["kind"])
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        return packet

    def test_locked_prefix_only_offers_eligible_cards_and_commits_the_selected_set_together(self):
        game = self.session(10736101)
        spell = self.add(game.engine, CARD, zone="hand")
        body, land, optional, expensive, nonpermanent, outside = self.library(game, (
            "Grizzly Bears", "Generic Bound Plains", "Sol Ring", "Colossal Dreadmaw", "Murder", "Generic Bound Plains",
        ))
        self.cast(game, spell, 5)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        offer = self.decision(game)
        self.assertEqual([card.ref for card in (body, land, optional, expensive, nonpermanent)], offer["ctx"]["revealed"])
        self.assertEqual([body.ref, land.ref, optional.ref], offer["ctx"]["legal_actions"][0]["choice_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        for refs in ([outside.ref], [expensive.ref], [nonpermanent.ref], [body.ref, body.ref]):
            with self.subTest(refs=refs):
                rejected = game.act("pilot:A", {"action_id": "choose", "cards": refs})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [body.ref]}).ok)
        game = self.replay(game, load=True)
        self.choose(game, (body, land))
        self.assertEqual(["battlefield", "battlefield"], [self.card(game, card).zone for card in (body, land)])
        self.assertEqual(self.card(game, body).zone_timestamp, self.card(game, land).zone_timestamp)
        self.assertEqual("library", self.card(game, optional).zone)
        order = self.decision(game)["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual({optional.ref, expensive.ref, nonpermanent.ref}, set(order["legal_refs"]))
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "choose", "cards": [optional.ref, expensive.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, (nonpermanent, optional, expensive))
        self.assertEqual([nonpermanent.object_id, optional.object_id, expensive.object_id],
                         game.state.players["A"].zones["graveyard"][-4:-1])
        self.assertEqual("library", self.card(game, outside).zone)
        self.replay(game, load=True)

    def test_a_real_arriving_replacement_applies_to_the_later_graveyard_instruction(self):
        game = self.session(10736102)
        spell = self.add(game.engine, CARD, zone="hand")
        peace, land, rest = self.library(game, ("Rest in Peace", "Generic Bound Plains", "Murder"))
        self.assert_closed(game, peace)
        self.cast(game, spell, 3)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.choose(game, (peace, land))
        self.assertEqual(("battlefield", "battlefield", "exile"),
                         tuple(self.card(game, card).zone for card in (peace, land, rest)))
        self.assertEqual(self.card(game, peace).zone_timestamp, self.card(game, land).zone_timestamp)
        self.assertEqual("exile", self.card(game, spell).zone)
        self.assertFalse(self.card(game, rest).object_id in game.state.players["A"].zones["graveyard"])
        self.replay(game, load=True)

    def test_zero_does_not_reveal_and_short_library_allows_zero_selections(self):
        for x in (0, 5):
            with self.subTest(x=x):
                game = self.session(10736110 + x)
                spell = self.add(game.engine, CARD, zone="hand")
                body, land = self.library(game, ("Grizzly Bears", "Generic Bound Plains"), exact=True)
                self.cast(game, spell, x)
                result = self.resolve(game)
                if x:
                    self.assertEqual("semantic.choice", result.kind)
                    self.assertEqual([body.ref, land.ref], self.decision(game)["ctx"]["revealed"])
                    self.choose(game, ())
                    self.choose(game, (body, land))
                else:
                    self.assertIsNone(result)
                    self.assertFalse(any(event.code == "library.reveal" for event in game.state.events))
                expected = "graveyard" if x else "library"
                self.assertEqual([expected, expected], [self.card(game, card).zone for card in (body, land)])
                self.assertTrue(game.state.players["A"].in_game)
                self.replay(game, load=True)

    def test_revealed_aura_and_siege_use_native_entry_choices_for_the_same_batch(self):
        game = self.session(10736103)
        spell = self.add(game.engine, CARD, zone="hand")
        existing = self.add(game.engine, "Grizzly Bears", seat="B", ref="aura-existing-creature")
        aura, battle, body = self.library(game, ("Pacifism", "Invasion of Zendikar", "Grizzly Bears"))
        self.cast(game, spell, 4)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choose(game, (aura, battle, body))
        pending = self.decision(game)["ctx"]
        self.assertEqual(aura.ref, pending["entering"])
        self.assertEqual([existing.ref], pending["legal_actions"][0]["choice_schema"]["legal_values"])
        self.assertEqual(["library"] * 3, [self.card(game, row).zone for row in (aura, battle, body)])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        wrong = game.act("pilot:A", {"action_id": "choose", "attachment": body.ref})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "attachment": existing.ref})
        self.assertTrue(result.ok, result.summary)

        pending = self.decision(game)["ctx"]
        self.assertEqual(battle.ref, pending["entering"])
        self.assertEqual(["B", "C", "D"], pending["legal_actions"][0]["choice_schema"]["legal_values"])
        self.assertEqual(["library"] * 3, [self.card(game, row).zone for row in (aura, battle, body)])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        wrong = game.act("pilot:A", {"action_id": "choose", "protector": "A"})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        history_start = len(game.state.turn_history.events)
        with patch.object(zone_transitions, "dispatch_committed_batch_zone_events",
                          wraps=zone_transitions.dispatch_committed_batch_zone_events) as dispatch:
            result = game.act("pilot:A", {"action_id": "choose", "protector": "C"})
        self.assertTrue(result.ok, result.summary)
        grouped_entries = [tuple(occurrence.card_ref for occurrence in call.args[2]
                                 if occurrence.origin == "library" and occurrence.destination == "battlefield")
                           for call in dispatch.call_args_list]
        self.assertEqual([(aura.ref, battle.ref, body.ref)], [group for group in grouped_entries if group])
        actual = tuple(self.card(game, row) for row in (aura, battle, body))
        self.assertEqual(["battlefield"] * 3, [card.zone for card in actual])
        recorded = [row.object_incarnation for row in game.state.turn_history.events[history_start:]
                    if row.kind == "permanent_entered" and row.actor == "A"]
        self.assertEqual([card.logical_object_id for card in actual], recorded)
        self.assertEqual(actual[1].zone_timestamp, actual[2].zone_timestamp)
        self.assertGreaterEqual(actual[0].zone_timestamp, actual[1].zone_timestamp)
        self.assertEqual(existing.object_id, self.card(game, aura).attached_to)
        self.assertEqual("C", self.card(game, battle).battle_protector)
        self.assertGreater(self.card(game, battle).counters["defense"], 0)
        self.replay(game, load=True)

    def test_selected_aura_without_a_legal_attachment_joins_the_actual_graveyard_set(self):
        game = self.session(10736104)
        spell = self.add(game.engine, CARD, zone="hand")
        aura, body, rest = self.library(game, ("Pacifism", "Grizzly Bears", "Murder"))
        self.cast(game, spell, 3)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choose(game, (aura, body))
        self.assertEqual(("library", "battlefield", "library"),
                         tuple(self.card(game, row).zone for row in (aura, body, rest)))
        order = self.decision(game)["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual({aura.ref, rest.ref}, set(order["legal_refs"]))
        game = self.replay(game, load=True)
        self.choose(game, (rest, aura))
        self.assertEqual(("graveyard", "battlefield", "graveyard"),
                         tuple(self.card(game, row).zone for row in (aura, body, rest)))
        self.replay(game, load=True)
