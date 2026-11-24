"""Private, optional-find named searches with a direct battlefield destination."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.named_search_model import LiteralNameBattlefieldSearchSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SENTINEL = "Llanowar Sentinel"
CLANCALLER = "Elvish Clancaller"
INFANTRY = "Magitek Infantry"
SQUAD = "Whisper Squad"
WITCH = "Bogbrew Witch"


class LiteralNameLibraryBattlefieldSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "literal-name-battlefield-search.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/literal-name-library-battlefield-search-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Named battlefield searches", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_preserve_fixed_names_price_timing_and_entry_orientation(self):
        for name, events, names, tapped in (
            (SENTINEL, {"permanent.enter.self"}, (SENTINEL,), False),
            (CLANCALLER, {"activate", "characteristics.evaluate"}, (CLANCALLER,), False),
            (INFANTRY, {"activate", "continuous"}, (INFANTRY,), True),
            (SQUAD, {"activate"}, (SQUAD,), True),
            (WITCH, {"activate"}, ("Festering Newt", "Bubbling Cauldron"), True),
        ):
            with self.subTest(name=name):
                bound, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual(events, {program.event for program in programs})
                program, = (program for program in programs if program.event in ("activate", "permanent.enter.self"))
                self.assertIsNone(program.target_schema)
                result, = program.effects
                if name == SENTINEL:
                    self.assertEqual(("offer_optional_mana_payment", "$controller"), (result["op"], result["player"]))
                    self.assertEqual((1, 1), (result["cost"]["GENERIC"], result["cost"]["G"]))
                    result, = result["effects"]
                spec = LiteralNameBattlefieldSearchSpec.from_effect(result)
                self.assertEqual((names, tapped), (spec.names, spec.enters_tapped))
                self.assertEqual({"minimum": 0, "maximum": 1}, result["count"])
                self.assertEqual(("library", "battlefield", True),
                                 (result["zone"], result["destination"], result["shuffle_after"]))
                for change in ({"searching_player": "B"}, {"shuffle_after": False},
                               {"count": {"minimum": 1, "maximum": 1}}, {"enters_tapped_override": 1}):
                    with self.assertRaises(ValueError):
                        LiteralNameBattlefieldSearchSpec.from_effect({**result, **change})
        record = self.db.lookup(WITCH, fuzzy=False)
        for text in (
            record.oracle_text.replace("your library", "target player's library"),
            record.oracle_text.replace("Festering Newt or Bubbling Cauldron", "Festering Newt or Bubbling Cauldron or Plains"),
            record.oracle_text + " Then shuffle target player's library.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry)
                self.assertNotEqual("exact", ir.status)

        aura, _ = register_card(self.db, self.registry, "Gigantiform")
        self.assertFalse(aura["strict_capability_ready"])

    def hide(self, game, name, ref, *, seat="A"):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=ref)
        card.known_to.clear()
        card.revealed_to.clear()
        return card

    def command(self, game, **fields):
        result = game.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, actor, **fields):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act(actor, {"action_id": "choose", **fields}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def shuffles(self, game):
        return game.state.players["A"].stats.get("shuffle_count", 0)

    def search(self, game, matches, hidden):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        schema = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, int(bool(matches))), (schema["minimum"], schema["maximum"]))
        self.assertEqual({card.ref for card in matches}, set(schema["legal_refs"]))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            projection = game.packet(viewer, full=True)
            self.assertIsNone(projection["decision"])
            self.assertTrue(all(card.ref not in json.dumps(projection) for card in hidden))
        return schema

    def activate(self, game, source):
        action_prefix = f"activate:{source.ref}:"
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(value for value in actions if value["id"].startswith(action_prefix))
        self.assertIsNone(action.get("target_schema"))
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_sentinel_chains_paid_search_and_can_find_nothing_with_a_match_remaining(self):
        game = self.session(70123211)
        source = self.add(game.engine, SENTINEL, zone="hand", ref="sentinel-original")
        first = self.hide(game, SENTINEL, "sentinel-hidden-first")
        second = self.hide(game, SENTINEL, "sentinel-hidden-second")
        wrong = self.hide(game, "Runeclaw Bear", "sentinel-hidden-wrong")
        foreign = self.hide(game, SENTINEL, "sentinel-hidden-foreign", seat="B")
        hidden = [first, second, wrong, foreign]
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "G": 3})
        self.checkpoint(game)

        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        payment = self.resolve(game)
        self.assertEqual(["A"], payment.actors)
        self.assertNotEqual("semantic.search", payment.kind)
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertTrue(all(card.ref not in json.dumps(game.packet(viewer, full=True)) for card in hidden))
        self.reject(game, "pilot:B", pay=True)
        self.command(game, pay=True)
        self.search(game, [first, second], hidden)
        self.reject(game, "pilot:B", search_cards=[first.ref])
        for refs in ([wrong.ref], [foreign.ref], [first.ref, second.ref]):
            self.reject(game, "pilot:A", search_cards=refs)

        game = self.replay(game, load=True)
        before = self.shuffles(game)
        self.command(game, search_cards=[first.ref])
        moved = game.state.cards[first.object_id]
        self.assertEqual(("battlefield", "A", False), (moved.zone, moved.controller, moved.tapped))
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual(1, self.trigger_count(game, first))
        self.assertEqual(["A"], self.resolve(game).actors)
        self.command(game, pay=True)
        self.search(game, [second], [second, wrong, foreign])
        self.command(game, search_cards=[])
        self.assertEqual(before + 2, self.shuffles(game))
        self.assertEqual("library", game.state.cards[second.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.replay(game, load=True)

    def test_actual_sentinel_declines_before_opening_or_shuffling_its_library(self):
        game = self.session(70123212)
        source = self.add(game.engine, SENTINEL, zone="hand")
        matching = self.hide(game, SENTINEL, "declined-hidden-match")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        before = self.shuffles(game)
        self.assertEqual(["A"], self.resolve(game).actors)
        self.command(game, pay=False)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, self.shuffles(game))
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertEqual("library", game.state.cards[matching.object_id].zone)
        self.replay(game, load=True)

    def test_actual_elf_activation_taps_its_source_and_installs_both_native_lords(self):
        game = self.session(70123213)
        source = self.add(game.engine, CLANCALLER)
        source.acquired_control_turn_count = -1
        chosen = self.hide(game, CLANCALLER, "hidden-second-clancaller")
        wrong = self.hide(game, INFANTRY, "clancaller-hidden-wrong")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "G": 2})
        self.checkpoint(game)
        self.activate(game, source)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.resolve(game)
        self.search(game, [chosen], [chosen, wrong])
        self.command(game, search_cards=[chosen.ref])
        self.assertEqual(("battlefield", False), (game.state.cards[chosen.object_id].zone,
                                                  game.state.cards[chosen.object_id].tapped))
        for card in (source, chosen):
            self.assertEqual(2, game.engine._numeric_stat(card.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_whisper_squad_and_magitek_infantry_place_the_selected_copy_tapped(self):
        for index, name, mana in (
            (0, SQUAD, {"C": 1, "B": 1}),
            (1, INFANTRY, {"C": 2, "W": 1}),
        ):
            with self.subTest(name=name):
                game = self.session(70123214 + index)
                source = self.add(game.engine, name)
                matched = self.hide(game, name, f"copy-hidden-{index}")
                wrong = self.hide(game, "Runeclaw Bear", f"copy-unmatched-{index}")
                self.assert_closed(game, source)
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                self.activate(game, source)
                self.assertFalse(game.state.cards[source.object_id].tapped)
                self.resolve(game)
                self.search(game, [matched], [matched, wrong])
                self.reject(game, "pilot:A", search_cards=[wrong.ref])
                self.command(game, search_cards=[matched.ref])
                moved = game.state.cards[matched.object_id]
                self.assertEqual(("battlefield", "A", True), (moved.zone, moved.controller, moved.tapped))
                if name == INFANTRY:
                    self.assertEqual(2, game.engine._numeric_stat(source.object_id, "power"))
                self.replay(game, load=True)

    def test_actual_witch_offers_either_printed_name_at_resolution_and_only_one_tapped_result(self):
        game = self.session(70123216)
        source = self.add(game.engine, WITCH)
        source.acquired_control_turn_count = -1
        newt = self.hide(game, "Festering Newt", "hidden-newt")
        cauldron = self.hide(game, "Bubbling Cauldron", "hidden-cauldron")
        wrong = self.hide(game, WITCH, "hidden-unmatched-witch")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.activate(game, source)
        self.resolve(game)
        self.search(game, [newt, cauldron], [newt, cauldron, wrong])
        self.reject(game, "pilot:A", search_cards=[newt.ref, cauldron.ref])
        game = self.replay(game, load=True)
        self.command(game, search_cards=[cauldron.ref])
        self.assertEqual(("battlefield", True), (game.state.cards[cauldron.object_id].zone,
                                                 game.state.cards[cauldron.object_id].tapped))
        self.assertEqual("library", game.state.cards[newt.object_id].zone)
        self.replay(game, load=True)

    def test_actual_named_search_resumes_its_private_entry_replacement_before_one_shuffle(self):
        game = self.session(70123217)
        source = self.add(game.engine, CLANCALLER)
        source.acquired_control_turn_count = -1
        chosen = self.hide(game, CLANCALLER, "replacement-hidden-clancaller")
        wrong = self.hide(game, "Runeclaw Bear", "replacement-hidden-unmatched")
        grumgully = self.add(game.engine, "Grumgully, the Generous")
        scales = self.add(game.engine, "Hardened Scales")
        doubling = self.add(game.engine, "Doubling Season")
        for card in (source, grumgully, scales, doubling):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "G": 2})
        self.checkpoint(game)
        self.activate(game, source)
        self.resolve(game)
        self.search(game, [chosen], [chosen, wrong])
        before = self.shuffles(game)
        self.command(game, search_cards=[chosen.ref])
        decision = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("library", game.state.cards[chosen.object_id].zone)
        self.assertEqual(before, self.shuffles(game))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        selected = next(option["id"] for option in options
                        if "hardened" in (option["id"] + option.get("label", "")).casefold())
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(wrong.ref, json.dumps(game.packet(viewer, full=True)))
        self.reject(game, "pilot:B", replacement=selected)
        game = self.replay(game, load=True)
        self.command(game, replacement=selected)
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[chosen.object_id]
        self.assertEqual(("battlefield", 4), (card.zone, card.counters.get("+1/+1")))
        self.assertEqual(before + 1, self.shuffles(game))
        self.replay(game, load=True)
