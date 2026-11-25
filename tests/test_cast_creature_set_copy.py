from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules import cast_creature_set_spell_copy as rules
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_choices import cast_creature_set_copy as choice
from scripts.build_test_database import build_fixture_database


class CastCreatureSetCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-creature-set-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/cast-creature-set-copy-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Fixed receiver spell copies", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander],
        )

    def board(self, seed, source_name, spell_name, *, actor):
        game = self.session(seed)
        source = self.add(game.engine, source_name, seat="A")
        spell = self.add(game.engine, spell_name, seat=actor, zone="hand")
        for card in (source, spell):
            self.assert_closed(game, card)
        return game, source, spell

    def order(self, game, actor, refs):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor[actor]["legal_actions"][0]["choice_schema"]
        self.assertCountEqual(refs, schema["legal_refs"])

    def decide(self, game, actor, **fields):
        result = game.act("pilot:" + actor, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def copies(self, game):
        return [item for item in game.state.stack if item.kind == "spell_copy"]

    def test_actual_zada_replaces_every_slot_and_replays_the_selected_order(self):
        game, source, spell = self.board(707100601, "Zada, Hedron Grinder", "Martial Glory", actor="A")
        first = self.add(game.engine, "Grizzly Bears")
        second = self.add(game.engine, "Hill Giant")
        outsider = self.add(game.engine, "Hill Giant", seat="B", ref="opposing-giant")
        self.prepare(game, mana={"R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [source.ref, source.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        self.order(game, "A", [first.ref, second.ref])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for actor, action in (
            ("A", {"order": [first.ref]}),
            ("A", {"order": [first.ref, first.ref]}),
            ("A", {"order": [first.ref, outsider.ref]}),
            ("A", {"order": [first.ref, second.ref], "targets": [outsider.ref]}),
            ("B", {"order": [first.ref, second.ref]}),
        ):
            with self.subTest(actor=actor, action=action):
                self.assertFalse(game.act("pilot:" + actor, {"action_id": "choose", **action}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))

        order = sorted((first.ref, second.ref), reverse=True)
        self.decide(game, "A", order=order)
        copies = self.copies(game)
        self.assertEqual([("A", [ref, ref]) for ref in order],
                         [(item.controller, item.targets) for item in copies], "selected copy placement order")
        original, = (item for item in game.state.stack if item.kind == "spell")
        self.assertEqual([source.ref, source.ref], original.targets)
        for copy, ref in zip(copies, order, strict=True):
            self.assertEqual({group: [ref for _ in slots] for group, slots in original.context["target_groups"].items()},
                             copy.context["target_groups"])
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        top = next(card for card in (first, second) if card.ref == order[-1])
        previous = next(card for card in (first, second) if card.ref == order[0])
        self.assertEqual((6 if top == second else 5), game.engine._numeric_stat(top.object_id, "power"))
        self.assertEqual((3 if previous == second else 2), game.engine._numeric_stat(previous.object_id, "power"))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_mirrorwing_uses_the_original_caster_and_current_original_legality(self):
        game, source, spell = self.board(707100602, "Mirrorwing Dragon", "Giant Growth", actor="B")
        first = self.add(game.engine, "Grizzly Bears", seat="B")
        second = self.add(game.engine, "Hill Giant", seat="B")
        restricted = self.add(game.engine, "Gladecover Scout", seat="B")
        self.add(game.engine, "Hill Giant", seat="A", ref="dragon-controller-giant")
        self.add(game.engine, "Grizzly Bears", seat="C", ref="thief-bears")
        takeover = self.add(game.engine, "Aethersnatch", seat="C", zone="hand")
        self.assert_closed(game, restricted)
        self.assert_closed(game, takeover)
        self.prepare(game, actor="B", active="B", mana={"G": 1})
        game.state.players["C"].mana_pool.update({"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [source.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        original = next(item for item in game.state.stack if item.kind == "spell")
        self.cast_for(game, "C", takeover, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        self.decide(game, "C")
        original = next(item for item in game.state.stack if item.kind == "spell")
        self.assertEqual("C", original.controller)
        self.order(game, "B", [first.ref, second.ref])
        self.decide(game, "B", order=[second.ref, first.ref])
        self.assertEqual([("B", [second.ref]), ("B", [first.ref])],
                         [(item.controller, item.targets) for item in self.copies(game)])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.engine._numeric_stat(restricted.object_id, "power"))
        self.assertEqual(5, game.engine._numeric_stat(first.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_mirrorwing_creates_one_legal_copy_without_asking_for_order(self):
        game, source, spell = self.board(707100603, "Mirrorwing Dragon", "Cast Down", actor="B")
        eligible = self.add(game.engine, "Grizzly Bears", seat="B")
        legend = self.add(game.engine, "Isamaru, Hound of Konda", seat="B")
        outsider = self.add(game.engine, "Hill Giant", seat="A")
        self.prepare(game, actor="B", active="B", mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [source.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        copy, = self.copies(game)
        self.assertEqual(("B", [eligible.ref]), (copy.controller, copy.targets))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield", "battlefield"),
                         tuple(game.state.cards[card.object_id].zone for card in (eligible, legend, outsider)))
        self.replay(game, load=True)

    def test_actual_zada_rejects_other_casters_other_targets_and_permanent_spells(self):
        for rejected, seed in (("other caster", 707100604), ("second target", 707100605), ("Aura", 707100606)):
            with self.subTest(rejected=rejected):
                actor, spell_name, mana = {
                    "other caster": ("B", "Giant Growth", {"G": 1}),
                    "second target": ("A", "Martial Glory", {"R": 1, "W": 1}),
                    "Aura": ("A", "Holy Strength", {"W": 1}),
                }[rejected]
                game, source, spell = self.board(seed, "Zada, Hedron Grinder", spell_name, actor=actor)
                other = self.add(game.engine, "Grizzly Bears")
                targets = [source.ref, other.ref] if rejected == "second target" else [source.ref]
                self.prepare(game, actor=actor, active=actor, mana=mana)
                self.checkpoint(game)
                self.cast_for(game, actor, spell, targets)
                self.assertEqual(0, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertFalse(self.copies(game))
                self.replay(game, load=True)

    def test_actual_mirrorwing_copies_after_the_original_is_countered(self):
        game, source, spell = self.board(707100607, "Mirrorwing Dragon", "Giant Growth", actor="B")
        receiver = self.add(game.engine, "Grizzly Bears", seat="B")
        counterspell = self.add(game.engine, "Counterspell", seat="D", zone="hand")
        self.assert_closed(game, counterspell)
        self.prepare(game, actor="B", active="B", mana={"G": 1})
        game.state.players["D"].mana_pool.update({"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [source.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        subject = next(item for item in game.state.stack if item.kind == "spell")
        self.cast_for(game, "D", counterspell, [subject.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([("B", [receiver.ref])], [(copy.controller, copy.targets) for copy in self.copies(game)])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.engine._numeric_stat(receiver.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_mirrorwing_skips_a_departed_caster(self):
        game, source, spell = self.board(707100608, "Mirrorwing Dragon", "Giant Growth", actor="B")
        self.add(game.engine, "Grizzly Bears", seat="B")
        self.add(game.engine, "Hill Giant", seat="A")
        self.prepare(game, actor="B", active="B", mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [source.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.copies(game))
        self.replay(game, load=True)

    def test_copy_scope_requires_its_exact_cast_trigger_and_actor_reference(self):
        effect = rules.cast_creature_set_copy_effect(rules.OBSERVED_CASTER)
        base = {
            "effects": (effect,), "event": "spell.cast", "active_zone": "battlefield",
            "target_schema": None, "cost_schema": None, "coverage": (),
            "event_condition": rules.cast_creature_set_copy_condition(rules.OBSERVED_CASTER),
        }
        self.assertIn(rules.CAST_CREATURE_SET_COPY_CAPABILITY,
                      rules.cast_creature_set_copy_program_dependencies(SimpleNamespace(**base)))
        for changes in (
            {"effects": ({**effect, "player": "$controller"},)},
            {"effects": ({**effect, rules.CAST_CREATURE_SET_COPY_FIELD: True},)},
            {"event_condition": rules.cast_creature_set_copy_condition(rules.ABILITY_CONTROLLER)},
            {"target_schema": {}},
            {"event": "spell.copy"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rules.cast_creature_set_copy_program_dependencies(SimpleNamespace(**{**base, **changes}))

    def test_actual_zada_kills_reversing_the_copy_placement_order(self):
        original = choice._copy_intents

        def reverse(effect, order, plans, query):
            return original(effect, tuple(reversed(order)), plans, query)

        with patch.object(choice, "_copy_intents", side_effect=reverse):
            with self.assertRaisesRegex(AssertionError, "selected copy placement order"):
                self.test_actual_zada_replaces_every_slot_and_replays_the_selected_order()
