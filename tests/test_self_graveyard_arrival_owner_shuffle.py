"""Native evidence for shuffling an original object after its graveyard arrival."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_mutate_cast_native as mutate_witnesses
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Dread", "Worldspine Wurm")
ARRIVAL = "object.enter_graveyard"


class SelfGraveyardArrivalOwnerShuffleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "self-graveyard-arrival-owner-shuffle.sqlite3"
        fixture = ROOT / "tests/fixtures/self-graveyard-arrival-owner-shuffle-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original graveyard arrival", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == ARRIVAL]
        return card, program

    def test_both_originals_bind_the_public_arriving_incarnation_and_keep_other_rules(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, program = self.program(name)
                self.assertEqual("graveyard", program.active_zone)
                self.assertIsNone(program.target_schema)
                effect, = program.effects
                self.assertEqual(("move_if_in_zone", "$context.card", "$context.card_zone_change_counter",
                                  "$context.owner", "library", True),
                                 tuple(effect[key] for key in ("op", "card", "expected_zone_change_counter",
                                                              "owner", "destination", "shuffle")))
                for changed in (
                    card.oracle_text.replace("shuffle it into its owner's library", "shuffle your graveyard into your library"),
                    card.oracle_text.replace("shuffle it", "shuffle target card"),
                    card.oracle_text + " Draw a card.",
                ):
                    ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
                    self.assertFalse(any(node.event == ARRIVAL and node.exact for face in ir.faces for node in face.nodes),
                                     changed)

    def test_raw_arrival_cannot_use_lookback_or_replace_the_original_owner(self):
        _, program = self.program("Worldspine Wurm")
        effect, = program.effects
        for instance in (
            replace(program, event="permanent.leave", active_zone="battlefield"),
            replace(program, active_zone="battlefield"),
            replace(program, event_condition=None),
            replace(program, coverage=tuple(tag for tag in program.coverage
                                            if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(program, effects=({**effect, "owner": "$controller"},)),
            replace(program, effects=({**effect, "shuffle": False},)),
        ):
            report = bind_semantic_program_runtime(instance, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(report["strict"], report)

    def shuffles(self, game):
        return {seat: player.stats.get("shuffle_count", 0) for seat, player in game.state.players.items()}

    def assert_arrival_trigger(self, game, card, *, owner="A"):
        self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        items = [item for item in game.state.stack if item.source_object_id == card.object_id
                 and item.context.get("event") == ARRIVAL]
        trigger, = items
        self.assertEqual(owner, trigger.controller)
        self.assertEqual(game.state.cards[card.object_id].logical_object_id,
                         trigger.context["source_logical_object_id"])
        self.assertFalse(trigger.targets)
        return trigger

    def test_actual_discard_and_mill_observe_dread_after_its_hidden_origin(self):
        for origin in ("hand", "library"):
            with self.subTest(origin=origin):
                game = self.session(4867300 + len(origin))
                dread = self.add(game.engine, "Dread", zone=origin)
                other = self.add(game.engine, "Plains", zone="hand")
                spell = self.add(game.engine, "Mind Rot" if origin == "hand" else "Thought Scour",
                                 seat="B", zone="hand")
                if origin == "library":
                    library = game.state.players["A"].zones["library"]
                    library.remove(dread.object_id)
                    library.append(dread.object_id)
                for card in (dread, spell):
                    self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B", mana={"B": 1, "C": 2, "U": 1})
                self.checkpoint(game)
                before = self.shuffles(game)
                pre = authoritative_state_hash(game.state)
                self.assertNotIn(dread.ref, str(game.packet("pilot:B", full=True)))
                self.assertEqual(pre, authoritative_state_hash(game.state))
                self.cast_for(game, "B", spell, ["A"])
                if origin == "hand":
                    pending = self.resolve(game)
                    self.assertEqual(["A"], pending.actors)
                    discard = game.act("pilot:A", {"action_id": "choose", "cards": [dread.ref, other.ref]})
                    self.assertTrue(discard.ok, discard.summary)
                else:
                    self.resolve_top(game)
                self.assert_arrival_trigger(game, dread)
                self.assertEqual(before, self.shuffles(game))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertIn(dread.object_id, game.state.players["A"].zones["library"])
                self.assertEqual({**before, "A": before["A"] + 1}, self.shuffles(game))
                self.assertNotIn(dread.ref, str(game.packet("pilot:B", full=True)["state"]))
                self.replay(game, load=True)

    def test_actual_dread_recovers_its_ability_in_the_graveyard_and_replacement_avoids_it(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                game = self.session(4867320 + replaced)
                dread = self.add(game.engine, "Dread")
                lignify = self.add(game.engine, "Lignify", seat="B", zone="hand")
                murder = self.add(game.engine, "Murder", seat="C", zone="hand")
                rest = self.add(game.engine, "Rest in Peace", seat="D") if replaced else None
                for card in (dread, lignify, murder, rest):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B", mana={"G": 1, "C": 1})
                game.state.players["C"].mana_pool.update({"B": 2, "C": 1})
                self.checkpoint(game)
                before = self.shuffles(game)
                self.cast_for(game, "B", lignify, [dread.ref])
                self.resolve_top(game)
                self.assertNotIn("fear", game.engine._combat_keywords(dread))
                self.cast_for(game, "C", murder, [dread.ref])
                self.resolve_top(game)
                if replaced:
                    self.assertEqual("exile", game.state.cards[dread.object_id].zone)
                    self.assertFalse(game.state.stack)
                    self.assertEqual(before, self.shuffles(game))
                else:
                    self.assert_arrival_trigger(game, dread)
                    self.resolve_top(game)
                    self.assertEqual("library", game.state.cards[dread.object_id].zone)
                    self.assertEqual({**before, "A": before["A"] + 1}, self.shuffles(game))
                self.replay(game, load=True)

    def test_actual_borrowed_wurm_makes_previous_controllers_tokens_and_owner_still_shuffles(self):
        game = self.session(4867330)
        wurm = self.add(game.engine, "Worldspine Wurm")
        game.engine.change_control(wurm.object_id, "B", reason="Prepare borrowed Wurm")
        murder = self.add(game.engine, "Murder", seat="C", zone="hand")
        cremate = self.add(game.engine, "Cremate", seat="D", zone="hand")
        for card in (wurm, murder, cremate):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["C"].mana_pool.update({"B": 2, "C": 1})
        game.state.players["D"].mana_pool["B"] = 1
        self.checkpoint(game)
        before = self.shuffles(game)
        self.cast_for(game, "C", murder, [wurm.ref])
        self.resolve_top(game)
        arrival = self.assert_arrival_trigger(game, wurm)
        self.assertEqual([("A", ARRIVAL), ("B", "creature.dies")],
                         [(item.controller, item.context.get("event")) for item in game.state.stack])
        self.assertEqual(arrival.ref, game.state.stack[0].ref)
        self.cast_for(game, "D", cremate, [wurm.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[wurm.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assert_wurm_tokens(game, owner="B")
        self.assertEqual(before, self.shuffles(game))
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[wurm.object_id].zone)
        self.assertEqual({**before, "A": before["A"] + 1}, self.shuffles(game))
        self.replay(game, load=True)

    def assert_wurm_tokens(self, game, *, owner):
        tokens = [card for card in game.state.cards.values()
                  if card.is_token and card.printed_name == "Wurm Token" and card.zone == "battlefield"]
        self.assertEqual(3, len(tokens))
        for token in tokens:
            self.assertEqual(owner, token.controller)
            self.assertEqual((5, 5), tuple(game.engine._numeric_stat(token.object_id, stat)
                                          for stat in ("power", "toughness")))
            self.assertIn("trample", game.engine._combat_keywords(token))

    def test_actual_wurm_token_arrival_still_shuffles_after_token_ceases(self):
        game = self.session(4867340)
        wurm = self.add(game.engine, "Worldspine Wurm")
        copy = self.add(game.engine, "Cackling Counterpart", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        for card in (wurm, copy, murder):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2, "C": 1})
        game.state.players["B"].mana_pool.update({"B": 2, "C": 1})
        self.checkpoint(game)
        before = self.shuffles(game)
        self.cast_for(game, "A", copy, [wurm.ref])
        self.resolve_top(game)
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.cast_for(game, "B", murder, [token.ref])
        self.resolve_top(game)
        pending = game.state.pending_decision
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, pending.actors))
        batch = [item for batch in game.state.pending_trigger_batches for item in batch.items]
        self.assertEqual({ARRIVAL, "creature.dies"}, {item.normalized_event_id for item in batch})
        self.assertEqual({token.object_id}, {item.source_object_id for item in batch})
        self.assertTrue(token.object_id not in game.state.cards or game.state.cards[token.object_id].zone == "outside")
        game = self.replay(game, load=True)
        order = game.act("pilot:A", {"action_id": "order", "triggers": [item.ref for item in batch]})
        self.assertTrue(order.ok, order.summary)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assert_wurm_tokens(game, owner="A")
        self.assertEqual({**before, "A": before["A"] + 1}, self.shuffles(game))
        self.assertEqual("battlefield", game.state.cards[wurm.object_id].zone)
        self.replay(game, load=True)

    def test_actual_merged_departure_shuffles_only_the_original_arriving_card(self):
        game = self.session(4867350)
        dread = self.add(game.engine, "Dread")
        heron = self.add(game.engine, "Dreamtail Heron", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        for card in (dread, heron, murder):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"B": 2, "C": 1})
        self.checkpoint(game)
        before = self.shuffles(game)
        mutate_witnesses.MutateCastNativeTests.mutate(self, game, heron, dread)
        game = mutate_witnesses.MutateCastNativeTests.choose_merge(self, game, "bottom")
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", murder, [dread.ref])
        mutate_witnesses.MutateCastNativeTests._complete_public_departure_order(self, game, "A", (dread, heron))
        self.assert_arrival_trigger(game, dread)
        self.assertEqual("graveyard", game.state.cards[heron.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("library", game.state.cards[dread.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[heron.object_id].zone)
        self.assertEqual({**before, "A": before["A"] + 1}, self.shuffles(game))
        self.replay(game, load=True)
