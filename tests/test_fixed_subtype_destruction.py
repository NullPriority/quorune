from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.affected_permanents import AffectedPermanentSetSpec
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.destruction_templates import mass_destruction_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CARD = "Crux of Fate"
PROFILE = "commander_review"
DRAGONS = "mode_1"
NON_DRAGONS = "mode_2"


class FixedSubtypeDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "fixed-subtype-destruction-cards.json": {CARD, "Firdoch Core"},
            "attached-destruction-set-cards.json": {"Darksteel Myr", "Troll Ascetic", "Grizzly Bears", "Forest"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "creature-subtype-set-results-cards.json": {"Amoeboid Changeling", "Plains", "Isamaru, Hound of Konda"},
            "fixed-behold-additional-cost-cards.json": {"Shivan Dragon"},
        }
        pieces = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in document["cards"]} == names, filename
            oracle_ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "fixed-subtype-destruction.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Fixed subtype destruction", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def test_pinned_crux_closes_both_ordinary_modes_and_unknown_subtypes_stay_residual(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertFalse(program.effects)
        self.assertEqual(1, program.target_schema["mode_count"])
        self.assertLessEqual({"choice.modal.fixed_one", "permanent.destroy.fixed_set"},
                             set(program.capability_dependencies))
        for mode, polarity in ((DRAGONS, (("dragon",), ())), (NON_DRAGONS, ((), ("dragon",)))):
            with self.subTest(mode=mode):
                schema = program.target_schema["modes"][mode]
                effect, = schema["effects"]
                self.assertEqual("destroy_all", effect["op"])
                self.assertFalse(schema["groups"])
                destruction = AffectedPermanentSetSpec.from_dict(effect["set"])
                self.assertEqual("any", destruction.controller_relation.value)
                self.assertEqual(("battlefield",), destruction.query.zones)
                self.assertEqual(("creature",), destruction.query.types_all)
                self.assertEqual(polarity, (destruction.query.subtypes_all, destruction.query.excluded_subtypes))
        synthetic = CardRecord(
            oracle_id="fixed-subtype-destruction-boundary", name="Fixed Subtype Boundary", mana_cost="{B}",
            mana_value=1, type_line="Sorcery", oracle_text="", power=None, toughness=None, loyalty=None, defense=None,
            colors=("B",), color_identity=("B",), keywords=(), produced_mana=(), layout="normal", released_at="",
            legalities={}, faces=(), raw={},
        )
        for text in ("Destroy all Time Lord creatures.", "Destroy all non-Human creatures."):
            with self.subTest(text=text):
                self.assertIsNotNone(mass_destruction_effect_template(text))
                self.assertEqual("exact", compile_oracle_card(replace(synthetic, oracle_text=text), capability_registry=self.registry).status)
        for text in ("Destroy all non-Artifact creatures.", "Destroy all Dragons creatures.",
                     "Destroy all non-Dragon permanents.", "Destroy all non-Dragon creatures that attacked this turn."):
            with self.subTest(text=text):
                self.assertIsNone(mass_destruction_effect_template(text))
                self.assertNotEqual("exact", compile_oracle_card(replace(synthetic, oracle_text=text), capability_registry=self.registry).status)
        with patch("quorune.compiler.qualified_destruction_queries.fixed_creature_subtype_destruction_query", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE).status)

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 3, "B": 2})
        self.checkpoint(game)

    def action_for(self, game, seat, action_id):
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [a for a in actions if a["id"] == action_id]
        return action

    def act_ok(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def cast_mode(self, game, spell, mode, *, rejected_target=None):
        action = self.action_for(game, "A", "cast:" + spell.ref)
        schema = action["target_schema"]
        self.assertEqual([DRAGONS, NON_DRAGONS], schema["legal_modes"])
        self.assertTrue(all(not schema["mode_schemas"][key]["groups"] for key in (DRAGONS, NON_DRAGONS)))
        command = {"action_id": action["id"], "modes": [mode], "targets": [], "pay": "auto"}
        if rejected_target is not None:
            before = authoritative_state_hash(game.state)
            for fields in ({"modes": []}, {"modes": [mode, mode]}, {"modes": [DRAGONS, NON_DRAGONS]},
                           {"modes": ["mode_3"]}, {"targets": [rejected_target]}):
                with self.subTest(rejected=fields):
                    result = game.act("pilot:A", {**command, **fields})
                    self.assertFalse(result.ok, result.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
        self.act_ok(game, "A", command)
        self.assertEqual(([mode], []), (game.state.stack[-1].modes, game.state.stack[-1].targets))

    def activate(self, game, seat, source, text, targets=()):
        self.priority(game, seat)
        ability, = [a for a in game.engine._activated_abilities(source) if text in a.effect_text.casefold()]
        action = self.action_for(game, seat, f"activate:{source.ref}:{ability.ability_id}")
        self.act_ok(game, seat, {"action_id": action["id"], "targets": list(targets), "pay": "auto"})
        self.resolve_top(game)

    def batch(self, game):
        event, = [e for e in game.state.events if e.code == "effect.permanent.destroy_set"]
        return tuple(event.details[field] for field in ("affected_count", "destroyed_count", "shielded_count", "indestructible_count"))

    def creature_parts(self, game, card):
        types, subtypes, _ = game.engine._effective_type_parts(game.state.cards[card.object_id])
        return {value.casefold() for value in types}, {value.casefold() for value in subtypes}

    def test_actual_modes_read_current_creature_subtypes_and_destroy_one_partition_as_a_batch(self):
        for mode in (DRAGONS, NON_DRAGONS):
            with self.subTest(mode=mode):
                dragons = mode == DRAGONS
                game = self.session(159001 + dragons)
                spell = self.add(game.engine, CARD, zone="hand")
                dragon = self.add(game.engine, "Shivan Dragon", seat="B")
                changed = self.add(game.engine, "Grizzly Bears" if dragons else "Shivan Dragon", ref="changed-subtype")
                bear = self.add(game.engine, "Grizzly Bears", seat="D")
                changeling = self.add(game.engine, "Universal Automaton", seat="C")
                amoeboid = self.add(game.engine, "Amoeboid Changeling", seat="C")
                amoeboid.acquired_control_turn_count = -1
                myr = self.add(game.engine, "Darksteel Myr", seat="D")
                core = self.add(game.engine, "Firdoch Core", seat="B")
                land = self.add(game.engine, "Forest")
                troll = None if dragons else self.add(game.engine, "Troll Ascetic", seat="B")
                if troll is not None:
                    game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
                self.start(game, spell, amoeboid, *(card for card in (troll,) if card is not None))
                core_types, core_subtypes = self.creature_parts(game, core)
                self.assertIn("dragon", core_subtypes)
                self.assertNotIn("creature", core_types)
                self.assertEqual(not dragons, "dragon" in self.creature_parts(game, changed)[1])
                self.cast_mode(game, spell, mode, rejected_target=dragon.ref)
                if troll is not None:
                    self.activate(game, "B", troll, "regenerate")
                    self.assertEqual(1, game.state.cards[troll.object_id].regeneration_shields)
                sentence = "gains all creature types" if dragons else "loses all creature types"
                self.activate(game, "C", amoeboid, sentence, [changed.ref])
                self.assertEqual(dragons, "dragon" in self.creature_parts(game, changed)[1])
                game = self.replay(game, load=True)
                self.assertEqual([mode], game.state.stack[-1].modes)
                self.assertIsNone(self.resolve(game))
                dead = (dragon, changed, changeling, amoeboid) if dragons else (changed, bear)
                alive = (bear, myr, core, land) if dragons else (dragon, changeling, amoeboid, myr, core, land, troll)
                self.assertEqual((4, 4, 0, 0) if dragons else (4, 2, 0, 1), self.batch(game))
                for card in dead:
                    self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
                    self.assertIn(card.object_id, game.state.players[card.owner].zones["graveyard"])
                for card in alive:
                    self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
                if troll is not None:
                    survivor = game.state.cards[troll.object_id]
                    self.assertTrue(survivor.tapped)
                    self.assertEqual(0, survivor.regeneration_shields)
                    regenerated, = [e for e in game.state.events if e.code == "permanent.destroy.regenerated"]
                    self.assertEqual(troll.ref, regenerated.details["object"])
                self.replay(game, load=True)

    def test_actual_zero_member_mode_is_still_legal_and_replays(self):
        game = self.session(159000)
        spell = self.add(game.engine, CARD, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        self.start(game, spell)
        self.cast_mode(game, spell, DRAGONS)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 0, 0, 0), self.batch(game))
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)
