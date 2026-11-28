"""Complete pinned originals, including their independent companion abilities."""
import unittest
import source_counter_pronoun_fixture as fixtures
from pathlib import Path
from tempfile import TemporaryDirectory

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SourceCounterPronounNativeTests(fixtures.SourceCounterPronounFixture, unittest.TestCase):
    def test_actual_time_bomb_upkeep_then_sacrifice_damages_every_player_and_creature(self):
        for index, age in enumerate((False, True)):
            with self.subTest(upkeep=age):
                s = self.session(670100 + index)
                source = self.add(s.engine, 'Time Bomb', zone='hand')
                ring = self.add(s.engine, 'Sol Ring')
                small = self.add(s.engine, 'Merfolk of the Pearl Trident', seat='B', ref='bomb-small-creature')
                large = self.add(s.engine, 'Grizzly Bears', seat='C', ref='bomb-large-creature')
                self.assert_closed(s, source);self.ready(s, {'C': 4})
                self.cast(s, 'A', source);self.settle(s)
                if age:
                    self.step(s, 'A', 'beginning', 'upkeep');self.settle(s)
                self.assertEqual(int(age), s.state.cards[source.object_id].counters.get('time', 0))
                self.tap_mana(s, ring);life = {seat:s.state.players[seat].life for seat in 'ABCD'}
                s = self.activate_counter_source(s, source, resume=age)
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertFalse(s.state.cards[source.object_id].counters)
                self.settle(s)
                for seat in 'ABCD':self.assertEqual(life[seat] - int(age), s.state.players[seat].life)
                self.assertEqual('graveyard' if age else 'battlefield', s.state.cards[small.object_id].zone)
                self.assertEqual(int(age), s.state.cards[large.object_id].marked_damage)
                self.private(s)
                if age:self.replay(s, load=True)

    def test_actual_magma_mine_cost_departure_count_survives_regrowth_and_new_source(self):
        s = self.session(670110)
        source = self.add(s.engine, 'Magma Mine', zone='hand')
        regrowth = self.add(s.engine, 'Regrowth', zone='hand')
        self.add(s.engine, 'Vedalken Orrery')
        ring = self.add(s.engine, 'Sol Ring');forest = self.add(s.engine, 'Forest');wastes = self.add(s.engine, 'Wastes')
        self.assert_closed(s, source);self.ready(s, {'C': 9})
        self.cast(s, 'A', source);self.settle(s)
        for _ in range(2):self.activate_counter_source(s, source, number=1);self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('pressure'))
        incarnation = s.state.cards[source.object_id].logical_object_id
        life = {seat:s.state.players[seat].life for seat in 'ABCD'}
        s = self.activate_counter_source(s, source, ['D'], number=2, resume=True)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        for mana_source in (ring, forest, wastes):self.tap_mana(s, mana_source)
        self.cast(s, 'A', regrowth, [source.ref]);self.resolve_top(s)
        self.cast(s, 'A', source);self.resolve_top(s)
        self.assertNotEqual(incarnation, s.state.cards[source.object_id].logical_object_id)
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.settle(s)
        self.assertEqual(life['D'] - 2, s.state.players['D'].life)
        for seat in 'ABC':self.assertEqual(life[seat], s.state.players[seat].life)
        self.private(s);self.replay(s, load=True)

    def test_actual_heliophial_sunburst_counts_spent_colors_and_zero_before_source_sacrifice(self):
        for index, (mana, expected) in enumerate((({'W': 1, 'U': 1, 'B': 1, 'C': 2}, 3), ({'C': 5}, 0))):
            with self.subTest(colors=expected):
                s = self.session(670120 + index)
                source = self.add(s.engine, 'Heliophial', zone='hand');ring = self.add(s.engine, 'Sol Ring')
                self.assert_closed(s, source);self.ready(s, mana)
                self.cast(s, 'A', source);self.settle(s)
                self.assertEqual(expected, s.state.cards[source.object_id].counters.get('charge', 0))
                life = s.state.players['C'].life;self.tap_mana(s, ring)
                s = self.activate_counter_source(s, source, ['C'], resume=index == 0)
                self.settle(s)
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual(life - expected, s.state.players['C'].life)
                self.private(s)
                if index == 0:self.replay(s, load=True)

    def test_actual_shrine_counts_own_upkeep_and_red_cast_but_not_foreign_or_blue_casts(self):
        s = self.session(670130)
        source = self.add(s.engine, 'Shrine of Burning Rage', zone='hand')
        red = self.add(s.engine, 'Shock', zone='hand');foreign = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='foreign-red-spell')
        blue = self.add(s.engine, 'Unsummon', zone='hand')
        returned = self.add(s.engine, 'Merfolk of the Pearl Trident', ref='shrine-nonred-subject')
        ring = self.add(s.engine, 'Sol Ring');wastes = self.add(s.engine, 'Wastes')
        mountain = self.add(s.engine, 'Mountain');self.add(s.engine, 'Mountain', seat='B', ref='foreign-red-land')
        island = self.add(s.engine, 'Island')
        self.assert_closed(s, source);self.ready(s, {'C': 2})
        self.cast(s, 'A', source);self.settle(s)
        self.step(s, 'A', 'beginning', 'upkeep');self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('charge'))
        self.tap_mana(s, mountain);self.cast(s, 'A', red, ['D']);self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('charge'))
        self.cast(s, 'B', foreign, ['C']);self.settle(s)
        self.tap_mana(s, island);self.cast(s, 'A', blue, [returned.ref]);self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('charge'))
        self.tap_mana(s, ring);self.tap_mana(s, wastes)
        life = s.state.players['B'].life
        s = self.activate_counter_source(s, source, ['B'], resume=True);self.settle(s)
        self.assertEqual(life - 2, s.state.players['B'].life)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.private(s);self.replay(s, load=True)

    def test_actual_javelineer_paid_upkeep_targets_only_combat_creature_then_declined_upkeep(self):
        s = self.session(670140)
        source = self.add(s.engine, 'Kjeldoran Javelineer', zone='hand');ring = self.add(s.engine, 'Sol Ring')
        attacker = self.add(s.engine, 'Merfolk of the Pearl Trident', seat='B', ref='javelineer-combat-target')
        resting = self.add(s.engine, 'Grizzly Bears', seat='B', ref='javelineer-resting-creature')
        attacker.acquired_control_turn_count = -1
        self.assert_closed(s, source);self.ready(s, {'W': 1})
        self.cast(s, 'A', source);self.settle(s)
        s.state.cards[source.object_id].acquired_control_turn_count = -1;self.checkpoint(s)
        self.step(s, 'A', 'beginning', 'upkeep');self.tap_mana(s, ring)
        self.upkeep_choice(s, pay=True);self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('age'))
        self.step(s, 'B', 'combat', 'beginning_combat')
        self.until(s, lambda s: getattr(s.state.pending_decision, 'kind', None) == 'combat.attackers')
        result = s.act('pilot:B', {'action_id': 'attack', 'attackers': {attacker.ref: 'A'}});self.assertTrue(result.ok, result.summary)
        self.until(s, lambda s: getattr(s.state.pending_decision, 'kind', None) == 'combat.blockers')
        result = s.act('pilot:A', {'action_id': 'block', 'blocks': {}});self.assertTrue(result.ok, result.summary)
        self.priority(s, 'A')
        row = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':'))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': row['id'], 'targets': [resting.ref]}).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.activate_counter_source(s, source, [attacker.ref], resume=True);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[attacker.object_id].zone)
        self.assertEqual(0, s.state.cards[resting.object_id].marked_damage)
        self.step(s, 'A', 'beginning', 'upkeep');self.upkeep_choice(s, pay=False);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.private(s);self.replay(s, load=True)

class QuestingDruidCastCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "questing-druid.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/source-counter-pronoun-cards.json",
            ROOT / "tests/fixtures/fixed-stack-spell-copy-cards.json",
            ROOT / "tests/fixtures/fixed-resolution-mana-cards.json",
            ROOT / "tests/fixtures/closed-effect-program-cards.json",
            ROOT / "tests/fixtures/life-gain-prohibition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Spell color trigger witness",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def druid_counter_count(self, game, druid):
        return game.state.cards[druid.object_id].counters.get("+1/+1", 0)

    def druid_triggers(self, game, druid):
        return [item for item in game.state.stack
                if item.kind == "triggered_ability" and item.source_object_id == druid.object_id]

    def test_actual_questing_druid_uses_committed_colors_and_one_multicolor_event(self):
        game = self.session(60322201)
        druid = self.add(game.engine, "Questing Druid // Seek the Beast")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        spells = (
            (self.add(game.engine, "Revitalize", zone="hand"), {"W"}, []),
            (self.add(game.engine, "Unsummon", zone="hand"), {"U"}, [creature.ref]),
            (self.add(game.engine, "Dark Ritual", zone="hand"), {"B"}, []),
            (self.add(game.engine, "Shock", zone="hand"), {"R"}, ["C"]),
            (self.add(game.engine, "Lightning Helix", zone="hand"), {"W", "R"}, ["D"]),
        )
        green = self.add(game.engine, "Llanowar Elves", zone="hand")
        colorless = self.add(game.engine, "Sol Ring", zone="hand")
        foreign = self.add(game.engine, "Shock", seat="B", zone="hand", ref="questing-foreign-cast")
        self.assert_closed(game, druid)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 6, "W": 4, "U": 4, "B": 3, "R": 5, "G": 4})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)

        for count, (spell, colors, targets) in enumerate(spells, 1):
            with self.subTest(spell=spell.printed_name):
                self.cast(game, "A", spell, targets)
                trigger, = self.druid_triggers(game, druid)
                self.assertEqual("A", trigger.context["controller"])
                self.assertEqual(colors, set(trigger.context["colors"]))
                self.assertEqual(count - 1, self.druid_counter_count(game, druid))
                if len(colors) > 1:
                    game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(count, self.druid_counter_count(game, druid))

        self.cast(game, "A", green)
        self.assertFalse(self.druid_triggers(game, druid))
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        offer = self.action(game, "A", colorless)
        chosen = game.act("pilot:A", {
            "action_id": offer["id"], "pay": "manual", "payment": {"U": 1}, "targets": [],
        })
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertFalse(self.druid_triggers(game, druid))
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", foreign, ["D"])
        self.assertFalse(self.druid_triggers(game, druid))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, self.druid_counter_count(game, druid))
        self.replay(game, load=True)

    def test_actual_questing_druid_ignores_uncast_spell_copy(self):
        game = self.session(60322202)
        druid = self.add(game.engine, "Questing Druid // Seek the Beast")
        original = self.add(game.engine, "Revitalize", seat="B", zone="hand")
        copier = self.add(game.engine, "Reverberate", zone="hand")
        self.assert_closed(game, druid)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"R": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "B", original)
        self.assertFalse(self.druid_triggers(game, druid))
        self.cast(game, "A", copier, [game.state.stack[-1].ref])
        self.assertEqual(1, len(self.druid_triggers(game, druid)))
        self.resolve_top(game)
        self.assertEqual(1, self.druid_counter_count(game, druid))
        self.resolve_top(game)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual("A", copied.controller)
        self.assertFalse(self.druid_triggers(game, druid))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self.druid_counter_count(game, druid))
        self.assertEqual(2, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_questing_druid_adventure_keeps_its_printed_play_window(self):
        from quorune.adventure import ADVENTURE_PERMISSION

        game = self.session(60322203)
        source = self.add(game.engine, "Questing Druid // Seek the Beast", zone="hand")
        land = self.add(game.engine, "Mountain", zone="library", ref="questing-impulse-land")
        shock = self.add(game.engine, "Shock", zone="library", ref="questing-impulse-instant")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 4, "R": 3, "G": 2})
        self.checkpoint(game)

        def legal():
            return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

        def accepted(request):
            result = game.act("pilot:A", request)
            self.assertTrue(result.ok, result.summary)

        adventure, = (row for row in legal() if row.get("face") == "Seek the Beast")
        accepted({"action_id": adventure["id"], "face": "Seek the Beast", "pay": "auto"})
        self.assertEqual("Seek the Beast", game.state.stack[-1].chosen_face)
        self.assertIsNone(self.resolve(game))
        actual = game.state.cards[source.object_id]
        self.assertEqual("exile", actual.zone)
        self.assertEqual("Questing Druid", actual.annotations[ADVENTURE_PERMISSION]["normal_face"])
        self.assertEqual({"exile"}, {game.state.cards[card.object_id].zone for card in (land, shock)})
        game = self.replay(game, load=True)
        self.priority(game, "A")
        self.assertIn("cast:" + shock.ref, {row["id"] for row in legal()})
        accepted({"action_id": "play-land:" + land.ref})
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        normal, = (row for row in legal() if row.get("card") == source.ref and row["id"].startswith("cast:"))
        face = {"face": normal["face"]} if normal.get("face") else {}
        accepted({"action_id": normal["id"], "pay": "auto", **face})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(0, self.druid_counter_count(game, source))
        turn = game.state.turn_sequence
        self.until(game, lambda view: view.state.step == "end_step" and view.state.active_player == "A")
        self.assertEqual(turn, game.state.turn_sequence)
        self.priority(game, "A")
        self.assertNotIn("cast:" + shock.ref, {row["id"] for row in legal()})
        self.assertEqual("exile", game.state.cards[shock.object_id].zone)
        self.replay(game, load=True)
