"""Native evidence for one optional draw from each opposing damage occurrence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Farsight Mask"


class OpponentDamageUntappedSourceDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-damage-untapped-source-draw.sqlite3"
        fixture = ROOT / "tests/fixtures/opponent-damage-untapped-source-draw-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Untapped damage draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "damage.dealt"]
        return card, program

    def test_original_is_an_intervening_condition_and_one_private_optional_draw(self):
        card, program = self.program()
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        self.assertIn("intervening_condition", program.coverage)
        wrapper, = program.effects
        self.assertEqual(("offer_optional_effect", "$controller"), (wrapper["op"], wrapper["player"]))
        draw, = wrapper["effects"]
        self.assertEqual({"op": "draw", "player": "$controller", "count": 1, "private": True}, draw)
        for changed in (
            card.oracle_text.replace("an opponent controls", "you control"),
            card.oracle_text.replace("damage to you", "damage to an opponent"),
            card.oracle_text.replace("this artifact is untapped", "this artifact is tapped"),
            card.oracle_text.replace("draw a card", "draw that many cards"),
            card.oracle_text + " Gain 1 life.",
        ):
            ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_raw_program_cannot_drop_the_intervening_original_or_delegate_the_draw(self):
        _, program = self.program()
        wrapper, = program.effects
        draw, = wrapper["effects"]
        for changed in (
            replace(program, event_condition=None),
            replace(program, coverage=tuple(tag for tag in program.coverage if tag != "intervening_condition")),
            replace(program, effects=(draw,)),
            replace(program, effects=({**wrapper, "effects": ({**draw, "count": 2},)},)),
            replace(program, effects=({**wrapper, "player": "$context.source_controller"},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def change_tap(self, game, actor, spell, target, *, choice):
        self.cast_for(game, actor, spell, [target.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        response = game.act("pilot:" + actor, {"action_id": "choose", "choice": choice})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual(choice == "tap", game.state.cards[target.object_id].tapped)

    def draw_choice(self, game, choice, *, replay=False):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        if replay:
            command = {"action_id": "choose", "choice": choice}
            original = authoritative_state_hash(game.state)
            denied = game.act("pilot:B", command)
            self.assertFalse(denied.ok)
            self.assertEqual(original, authoritative_state_hash(game.state))
            game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": choice})
        self.assertTrue(accepted.ok, accepted.summary)
        return game

    def test_actual_distinct_opposing_damage_offers_one_private_draw_each_and_ignores_own_damage(self):
        game = self.session(4958700)
        mask = self.add(game.engine, ORIGINAL)
        own = self.add(game.engine, "Shock", zone="hand", ref="mask-own-shock")
        first = self.add(game.engine, "Shock", seat="B", zone="hand", ref="mask-first-shock")
        second = self.add(game.engine, "Shock", seat="B", zone="hand", ref="mask-second-shock")
        for card in (mask, own, first, second):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", own, ["A"])
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, mask))
        life = game.state.players["A"].life
        hand = set(game.state.players["A"].zones["hand"])
        for expected, shock in enumerate((first, second), 1):
            self.cast_for(game, "B", shock, ["A"])
            self.resolve_top(game)
            self.assertEqual(expected, self.trigger_count(game, mask))
        self.assertEqual(life - 4, game.state.players["A"].life)
        game = self.draw_choice(game, "apply", replay=True)
        game = self.draw_choice(game, "decline")
        (drawn,) = set(game.state.players["A"].zones["hand"]) - hand
        ref = game.state.cards[drawn].ref
        self.assertIn(ref, str(game.packet("pilot:A", full=True)["state"]))
        self.assertNotIn(ref, str(game.packet("pilot:B", full=True)["state"]))
        self.replay(game, load=True)

    def test_actual_tapped_at_damage_or_on_recheck_prevents_the_draw(self):
        game = self.session(4958710)
        mask = self.add(game.engine, ORIGINAL)
        mask.tapped = True
        first = self.add(game.engine, "Shock", seat="B", zone="hand", ref="mask-tapped-shock")
        second = self.add(game.engine, "Shock", seat="B", zone="hand", ref="mask-untapped-shock")
        untap = self.add(game.engine, "Twiddle", zone="hand", ref="mask-untap")
        tap = self.add(game.engine, "Twiddle", seat="C", zone="hand", ref="mask-tap")
        for card in (mask, first, second, untap, tap):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 1})
        game.state.players["B"].mana_pool["R"] = 2
        game.state.players["C"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "B", first, ["A"])
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, mask))
        self.change_tap(game, "A", untap, mask, choice="untap")
        self.cast_for(game, "B", second, ["A"])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, mask))
        self.change_tap(game, "C", tap, mask, choice="tap")
        before = list(game.state.players["A"].draw_history)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["A"].draw_history)
        self.replay(game, load=True)

    def test_actual_departed_mask_uses_its_status_when_it_left_instead_of_at_damage(self):
        for tapped_on_departure in (False, True):
            with self.subTest(tapped_on_departure=tapped_on_departure):
                game = self.session(4958720 + tapped_on_departure)
                mask = self.add(game.engine, ORIGINAL)
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                destroy = self.add(game.engine, "Disenchant", seat="C", zone="hand")
                tap = self.add(game.engine, "Twiddle", seat="C", zone="hand") if tapped_on_departure else None
                for card in (mask, shock, destroy, tap):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game)
                game.state.players["B"].mana_pool["R"] = 1
                game.state.players["C"].mana_pool.update({"W": 1, "C": 1, "U": 1})
                self.checkpoint(game)
                self.cast_for(game, "B", shock, ["A"])
                self.resolve_top(game)
                self.assertEqual(1, self.trigger_count(game, mask))
                if tap is not None:
                    self.change_tap(game, "C", tap, mask, choice="tap")
                self.cast_for(game, "C", destroy, [mask.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[mask.object_id].zone)
                game = self.replay(game, load=True)
                before = len(game.state.players["A"].draw_history)
                if tapped_on_departure:
                    self.assertIsNone(self.resolve(game))
                else:
                    game = self.draw_choice(game, "apply", replay=True)
                self.assertEqual(before + int(not tapped_on_departure), len(game.state.players["A"].draw_history))
                self.replay(game, load=True)

    def test_actual_departing_owner_leaves_the_queued_draw_with_the_original_controller(self):
        game = self.session(4958730)
        mask = self.add(game.engine, ORIGINAL, seat="B")
        game.engine.change_control(mask.object_id, "A", reason="Prepare a borrowed source")
        shock = self.add(game.engine, "Shock", seat="C", zone="hand")
        for card in (mask, shock):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["C"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "C", shock, ["A"])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, mask))
        self.priority(game, "B")
        concession = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(concession.ok, concession.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertTrue(mask.object_id not in game.state.cards or game.state.cards[mask.object_id].zone == "outside")
        game = self.replay(game, load=True)
        before = len(game.state.players["A"].draw_history)
        game = self.draw_choice(game, "apply")
        self.assertEqual(before + 1, len(game.state.players["A"].draw_history))
        self.replay(game, load=True)
