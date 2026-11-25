"""Fixed opponent toughness settings use current control and ordinary layers."""

from dataclasses import replace
import json
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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Maha, Its Feathers Night"
CAPABILITY = "continuous.characteristics.fixed_opponent_base_toughness"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class FixedOpponentBaseToughnessTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        db_path = folder / "opponent-base-toughness.sqlite3"
        build_fixture_database([basic, FIXTURES / "fixed-opponent-base-toughness-cards.json"], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent base toughness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        program, = [item for item in programs if CAPABILITY in item.capability_dependencies]
        return record, programs, program

    def test_complete_original_keeps_printed_flying_trample_and_discard_ward(self):
        record, programs, setting = self.program()
        descriptor, = setting.handlers
        self.assertEqual(1, descriptor["toughness"])
        self.assertEqual(["creature"], list(descriptor["predicate"]["types_all"]))
        deps = {capability for part in programs for capability in part.capability_dependencies}
        self.assertIn("combat.block.flying", deps)
        self.assertTrue(any("trample" in cap for cap in deps), deps)
        self.assertTrue(any("ward" in cap for cap in deps), deps)
        for text in (
            record.oracle_text.replace("your opponents control", "you control"),
            record.oracle_text.replace("Creatures your opponents", "Artifacts your opponents"),
            record.oracle_text.replace("toughness 1", "toughness equal to your hand size"),
            record.oracle_text.replace("toughness 1.", "toughness 1 and lose all abilities."),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                     capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in ir.faces for node in face.nodes), text)

    def test_raw_setting_rejects_forged_subject_noninteger_and_missing_authority(self):
        _, _, program = self.program()
        descriptor, = program.handlers
        for changed in (
            {**descriptor, "toughness": True},
            {**descriptor, "power": 1},
            {**descriptor, "predicate": {**descriptor["predicate"], "types_all": ["artifact"]}},
            {**descriptor, "predicate": {**descriptor["predicate"], "controller": "A"}},
        ):
            with self.assertRaises(ValueError):
                replace(program, handlers=(changed,))
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_base_toughness_stacks_with_timestamps_counters_switch_and_departure(self):
        game = self.session(613040201)
        source = self.add(game.engine, NAME, zone="hand")
        own = self.add(game.engine, "Grizzly Bears", ref="maha-own")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="maha-opponent-b")
        other.counters["+1/+1"] = 1
        third = self.add(game.engine, "Wind Drake", seat="C", ref="maha-opponent-c")
        rock = self.add(game.engine, "Sol Ring", seat="B")
        older = self.add(game.engine, "Gift of Tusks", zone="hand", ref="older-layer-seven")
        newer = self.add(game.engine, "Gift of Tusks", zone="hand", ref="newer-layer-seven")
        switch = self.add(game.engine, "Twisted Image", zone="hand")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        for card in (source, older, newer, switch, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "B": 2, "U": 5})
        self.checkpoint(game)

        self.cast_for(game, "A", older, [other.ref], reserve={"C": 3, "B": 2, "U": 4})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((4, 4), self.stats(game, other))
        self.cast_for(game, "A", source, reserve={"U": 4})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((4, 2), self.stats(game, other))
        self.assertEqual((2, 1), self.stats(game, third))
        self.assertEqual((2, 2), self.stats(game, own))
        self.assertEqual((0, 0), self.stats(game, rock))
        self.assertTrue({"trample", "flying"}.issubset(game.engine._combat_keywords(game.state.cards[source.object_id])))

        self.cast_for(game, "A", newer, [third.ref], reserve={"U": 3})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((3, 3), self.stats(game, third))
        self.cast_for(game, "A", switch, [other.ref], reserve={"U": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 4), self.stats(game, other))
        game = self.replay(game, load=True)

        self.cast_for(game, "A", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual((4, 4), self.stats(game, other))
        self.assertEqual((3, 3), self.stats(game, third))
        self.replay(game, load=True)

    def test_actual_discard_ward_keeps_private_payment_and_paid_control_rebinds_opponents(self):
        game = self.session(702210026)
        source = self.add(game.engine, NAME, zone="hand")
        own = self.add(game.engine, "Grizzly Bears", ref="maha-former-own")
        other = self.add(game.engine, "Wind Drake", seat="B", ref="maha-future-own")
        control = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        payment = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="maha-private-payment")
        alternative = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="a-maha-private-alternative")
        foreign = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="maha-foreign-private")
        for object_id in tuple(game.state.players["B"].zones["hand"]):
            if object_id not in {control.object_id, payment.object_id, alternative.object_id}:
                game.engine.move_card(object_id, "library", log=False)
        self.prepare(game, mana={"C": 3, "B": 2})
        game.state.players["B"].mana_pool.update({"C": 3, "R": 2})
        self.checkpoint(game)

        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), self.stats(game, own))
        self.assertEqual((2, 1), self.stats(game, other))
        self.cast_for(game, "B", control, [source.ref])
        payment_decision = self.resolve(game)
        self.assertIsNotNone(payment_decision)
        self.assertEqual(["B"], payment_decision.actors)
        legal = payment_decision.payload_by_actor["B"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertEqual([alternative.ref, payment.ref], list(legal))
        first = game.packet("pilot:B", full=True)
        self.assertIn(payment.ref, json.dumps(first))
        self.assertNotIn(foreign.ref, json.dumps(first))
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(payment.ref, json.dumps(game.packet(viewer, full=True)))
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "cards": [payment.ref], "plan": "DISCARD_FOR_WARD"}
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertFalse(game.act("pilot:B", {**command, "cards": [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[payment.object_id].zone)
        self.assertEqual("hand", game.state.cards[alternative.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.assertEqual((2, 1), self.stats(game, own))
        self.assertEqual((2, 2), self.stats(game, other))
        self.replay(game, load=True)

    def test_actual_timestamps_kill_omitted_opponent_toughness_setting(self):
        from quorune.semantic_runtime.fixed_characteristic_settings import FixedOpponentBaseToughnessHandler

        with patch.object(FixedOpponentBaseToughnessHandler, "lower", return_value=()):
            with self.assertRaisesRegex(AssertionError, r"\(4, 2\).*\(4, 4\)"):
                self.test_actual_base_toughness_stacks_with_timestamps_counters_switch_and_departure()
