from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.creature_power_damage_model import CREATURE_CONTROLLER_DAMAGE_SET_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Rakdos Charm"
PROFILE = "commander_review"


class RakdosCharmTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "rakdos-charm.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/rakdos-charm-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Rakdos Charm", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_actual_three_modes_are_independent_and_replay_each_result(self):
        for index, mode in enumerate(("mode_1", "mode_2", "mode_3")):
            with self.subTest(mode=mode):
                game = self.session(12061300 + index)
                host = game.engine
                spell = self.add(host, CARD_NAME, zone="hand")
                self.assert_closed(game, spell)
                artifact = self.add(host, "Sol Ring", seat="B")
                dead_b = [self.add(host, "Plains", seat="B", zone="graveyard", ref=f"charm-grave-b-{n}") for n in range(2)]
                dead_c = self.add(host, "Plains", seat="C", zone="graveyard", ref="charm-grave-c")
                bear_a = self.add(host, "Grizzly Bears", seat="A", ref="charm-bear-a")
                bear_b = self.add(host, "Grizzly Bears", seat="B", ref="charm-bear-b")
                bear_c = self.add(host, "Grizzly Bears", seat="C", ref="charm-bear-c")
                lifelink = self.add(host, "Child of Night", seat="C", zone="hand")
                host.move_card(lifelink.object_id, "battlefield", controller="B", log=False)
                zero = self.add(host, "Ornithopter", seat="D")
                phased = self.add(host, "Grizzly Bears", seat="D", ref="charm-bear-d")
                phased.phased_out = True
                self.prepare(game, mana={"R": 1, "B": 1})
                self.checkpoint(game)
                offer = self.action(game, "A", spell)
                self.assertEqual(["mode_1", "mode_2", "mode_3"], offer["target_schema"]["legal_modes"])
                targets = {"mode_1": ["B"], "mode_2": [artifact.ref], "mode_3": []}[mode]
                wrong = {"mode_1": [artifact.ref], "mode_2": ["B"], "mode_3": ["B"]}[mode]
                command = {"action_id": offer["id"], "modes": [mode], "targets": targets, "pay": "auto"}
                before = authoritative_state_hash(game.state)
                failure = game.act("pilot:A", {**command, "targets": wrong})
                self.assertFalse(failure.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                cast = game.act("pilot:A", command)
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual([mode], game.state.stack[-1].modes)
                game = self.replay(game, load=True)
                events = []
                dispatch = game.engine._dispatch_semantic_event

                def observe(event, context, **fields):
                    if event == "damage.dealt":
                        events.append(dict(context))
                    return dispatch(event, context, **fields)

                with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
                    self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[dead_c.object_id].zone)
                self.assertEqual(["exile" if mode == "mode_1" else "graveyard"] * 2,
                                 [game.state.cards[card.object_id].zone for card in dead_b])
                self.assertEqual("graveyard" if mode == "mode_2" else "battlefield", game.state.cards[artifact.object_id].zone)
                if mode == "mode_3":
                    self.assertEqual({bear_a.object_id: "A", bear_b.object_id: "B", bear_c.object_id: "C",
                                      lifelink.object_id: "B", zero.object_id: "D"},
                                     {item["source_object_id"]: item["target"] for item in events})
                    self.assertEqual(5, len(events))
                    self.assertEqual(1, len({item["event_id"] for item in events}))
                    for item in events:
                        self.assertEqual((1, False, "player"), (item["amount"], item["combat"], item["target_kind"]))
                        self.assertEqual(item["source_controller"], item["target"])
                    self.assertEqual([39, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
                else:
                    self.assertEqual([], events)
                    self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)

    def test_exact_damage_set_requires_the_fixed_amount_and_current_controller_owner(self):
        original = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(original, **options).status)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(original.oracle_id)
        self.assertIn(CREATURE_CONTROLLER_DAMAGE_SET_CAPABILITY, program.capability_dependencies)
        modes = program.target_schema["modes"]
        damage = modes["mode_3"]
        effect, = damage["effects"]
        for changed in ({**effect, "amount": True}, {**effect, "amount": 2}, {**effect, "target": "$controller"}):
            altered = replace(program, target_schema={**program.target_schema, "modes": {
                **modes, "mode_3": {**damage, "effects": [changed]},
            }})
            self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        deps = tuple(owner for owner in program.capability_dependencies if owner != CREATURE_CONTROLLER_DAMAGE_SET_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CREATURE_CONTROLLER_DAMAGE_SET_CAPABILITY,
                      binding["blockers"])
        with patch("quorune.compiler.creature_power_damage_templates.creature_controller_damage_set_effect", return_value={}):
            self.assertNotEqual("exact", compile_oracle_card(original, **options).status)
