"""An actual own-main-phase cast adds an exact result after its ordinary effect."""

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
from quorune.cast_origin_replacement import MAIN_PHASE_CAST_ADDITIVE_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMainPhaseAddendumResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-main-phase-addendum-results.sqlite3"
        support = json.loads((ROOT / "tests/fixtures/cumulative-alternative-mana-upkeep-cards.json").read_bytes())
        names = {"Solemnity", "Branching Evolution", "Hardened Scales"}
        support["cards"] = [card for card in support["cards"] if card["name"] in names]
        assert {card["name"] for card in support["cards"]} == names
        ids = {card["oracle_id"] for card in support["cards"]}
        support["rulings"] = [row for row in support.get("rulings", ()) if row["oracle_id"] in ids]
        support_path = path.with_name("solemnity.json")
        support_path.write_text(json.dumps(support), encoding="utf-8")
        build_fixture_database([ROOT / "tests/fixtures/fixed-main-phase-addendum-results-cards.json", support_path], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exact Addendum result", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_preserve_fixed_base_and_append_only_the_printed_bonus(self):
        for name, result in (
            ("Sphinx's Insight", "life"),
            ("Arrester's Admonition", "draw"),
            ("Arrester's Zeal", "grant_keyword_until_end_of_turn"),
        ):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                spell, = semantics.programs_for_oracle(card.oracle_id)
                instruction, = spell.effects
                self.assertEqual(MAIN_PHASE_CAST_ADDITIVE_VERSION, instruction["schema_version"])
                self.assertEqual(instruction["otherwise_effects"], instruction["effects"][:1])
                self.assertEqual(result, instruction["effects"][1]["op"])
                for changed in (
                    {**instruction, "cast_fact": True},
                    {**instruction, "effects": list(reversed(instruction["effects"]))},
                    {**instruction, "otherwise_effects": []},
                ):
                    self.assertFalse(bind_semantic_program_runtime(
                        replace(spell, effects=[changed]), capability_registry=self.registry,
                        profile="commander_review",
                    )["strict"])
                changed_text = card.oracle_text.replace("your main phase", "your upkeep")
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(card, oracle_text=changed_text), capability_registry=self.registry,
                    capability_profile="commander_review",
                ).status)

    def hand(self, game, seat):
        return set(game.state.players[seat].zones["hand"])

    def test_actual_sphinx_copy_only_draws_and_the_original_draws_before_gaining_life(self):
        game = self.session(60127001)
        original = self.add(game.engine, "Sphinx's Insight", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="B", zone="hand")
        for card in (original, copy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1, "U": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", original)
        stack = game.state.stack[-1]
        self.cast_for(game, "B", copy, [stack.ref])
        a_before, b_before = self.hand(game, "A"), self.hand(game, "B")
        self.resolve_top(game)
        self.assertEqual("spell_copy", game.state.stack[-1].kind)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(2, len(self.hand(game, "B") - b_before))
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(a_before, self.hand(game, "A"))
        start = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.hand(game, "A") - a_before))
        self.assertEqual(42, game.state.players["A"].life)
        codes = [event.code for event in game.state.events[start:]]
        draw = [index for index, code in enumerate(codes) if code == "card.draw"]
        life = [index for index, code in enumerate(codes) if code == "effect.life"]
        self.assertEqual((2, 1), (len(draw), len(life)))
        self.assertLess(max(draw), life[0])
        self.replay(game, load=True)

    def test_actual_admonition_draws_after_the_return_but_never_if_the_only_target_leaves(self):
        game = self.session(60127002)
        original = self.add(game.engine, "Arrester's Admonition", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        for card in (original, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", original, [creature.ref])
        hand_before = self.hand(game, "A")
        altered = self.replay(game, load=True)
        self.cast_for(altered, "B", bounce, [creature.ref])
        self.resolve_top(altered)
        self.assertIsNone(self.resolve(altered))
        self.assertEqual(hand_before, self.hand(altered, "A"))
        self.assertIn(creature.object_id, self.hand(altered, "B"))
        self.replay(altered, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.hand(game, "A") - hand_before))
        self.assertIn(creature.object_id, self.hand(game, "B"))
        self.replay(game, load=True)

    def test_actual_zeal_always_pumps_and_only_own_main_phase_adds_flying(self):
        for actor in ("A", "B"):
            with self.subTest(actor=actor):
                game = self.session(60127003 + (actor == "A"))
                spell = self.add(game.engine, "Arrester's Zeal", zone="hand", seat=actor)
                creature = self.add(game.engine, "Grizzly Bears")
                self.assert_closed(game, spell)
                self.prepare(game, actor=actor, active="A", mana={"W": 1})
                self.checkpoint(game)
                self.cast_for(game, actor, spell, [creature.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual((4, 4), tuple(game.engine._numeric_stat(creature.object_id, key)
                                              for key in ("power", "toughness")))
                self.assertEqual(actor == "A", "flying" in
                                 game.engine._combat_keywords(game.state.cards[creature.object_id]))
                self.replay(game, load=True)


    def test_formation_main_phase_binds_one_creature_cohort_and_copy_keeps_only_indestructible(self):
        game = self.session(60131001)
        spell = self.add(game.engine, "Unbreakable Formation", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="B", zone="hand")
        own = tuple(self.add(game.engine, "Grizzly Bears", ref=f"formation-own-{i}") for i in range(4))
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="formation-copy-controller")
        phase_out = self.add(game.engine, "Grizzly Bears", ref="formation-phased-out")
        phase_out.phased_out = True
        land = self.add(game.engine, "Plains", ref="formation-noncreature")
        late = self.add(game.engine, "Grizzly Bears", zone="hand", ref="formation-late")
        private = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="formation-private")
        for card in (spell, copy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "W": 1, "G": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)

        self.cast_for(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertFalse(any("indestructible" in game.engine._combat_keywords(card) for card in own))
        self.cast_for(game, "B", copy, [game.state.stack[-1].ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[opponent.object_id]
        self.assertIn("indestructible", game.engine._combat_keywords(current))
        self.assertNotIn("vigilance", game.engine._combat_keywords(current))
        self.assertEqual(0, current.counters.get("+1/+1", 0))
        start = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        for card in own:
            current = game.state.cards[card.object_id]
            self.assertEqual(1, current.counters.get("+1/+1"))
            self.assertTrue({"indestructible", "vigilance"} <= game.engine._combat_keywords(current))
        for card in (land, phase_out):
            current = game.state.cards[card.object_id]
            self.assertEqual(0, current.counters.get("+1/+1", 0))
            self.assertFalse({"indestructible", "vigilance"} & game.engine._combat_keywords(current))
        grants = [event.details for event in game.state.events[start:] if event.code == "effect.mass_modify"]
        self.assertEqual([["Indestructible"], ["Vigilance"]], [row["keywords"] for row in grants])
        self.assertEqual({card.ref for card in own}, set(grants[0]["objects"]))
        self.assertEqual(grants[0]["objects"], grants[1]["objects"])
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.cast_for(game, "A", late)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[late.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertFalse({"indestructible", "vigilance"} & game.engine._combat_keywords(current))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        for card in own:
            current = game.state.cards[card.object_id]
            self.assertEqual(1, current.counters.get("+1/+1"))
            self.assertFalse({"indestructible", "vigilance"} & game.engine._combat_keywords(current))
        self.replay(game, load=True)

    def test_formation_addendum_uses_cast_phase_and_vigilance_survives_counter_prohibition(self):
        for own_main in (True, False):
            with self.subTest(own_main=own_main):
                game = self.session(60131010 + int(own_main))
                spell = self.add(game.engine, "Unbreakable Formation", zone="hand")
                body = self.add(game.engine, "Grizzly Bears")
                prohibition = self.add(game.engine, "Solemnity", seat="C")
                self.assert_closed(game, prohibition)
                self.prepare(game, active="A" if own_main else "B", mana={"C": 2, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[body.object_id]
                self.assertEqual(0, current.counters.get("+1/+1", 0))
                self.assertIn("indestructible", game.engine._combat_keywords(current))
                self.assertEqual(own_main, "vigilance" in game.engine._combat_keywords(current))
                self.replay(game, load=True)

    def test_formation_wrong_cast_history_and_forged_cohort_descriptor_fail_closed(self):
        from test_fixed_public_alternative_costs import card_record
        from quorune.rules.main_phase_creature_cohort import COHORT_ADDENDUM_CAPABILITY, COHORT_ADDENDUM_FIELD

        oracle = ("Creatures you control gain indestructible until end of turn.\n"
                  "Addendum — If you cast this spell during your main phase, put a +1/+1 counter "
                  "on each of those creatures and they gain vigilance until end of turn.")
        record = card_record(oracle, type_line="Instant", keywords=("Addendum",))
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}

        def witness():
            actual = compile_oracle_card(record, **options)
            self.assertEqual("exact", actual.status, actual.material_residuals)

        witness()
        for changed in (oracle.replace("your main phase", "your upkeep"),
                        oracle.replace("those creatures", "all creatures"),
                        oracle.replace("gain vigilance", "gain haste")):
            self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=changed), **options).status)
        with patch("quorune.compiler.creature_cohort_addendum.creature_cohort_addendum_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)

        report, semantics = register_card(self.db, self.registry, "Unbreakable Formation")
        self.assertTrue(report["strict_capability_ready"], report)
        spell, = semantics.programs_for_oracle(self.db.lookup("Unbreakable Formation").oracle_id)
        effect, = spell.effects
        for changed in ({**effect, COHORT_ADDENDUM_FIELD: True}, {**effect, "members": []}):
            actual = bind_semantic_program_runtime(replace(spell, effects=[changed]),
                                                  capability_registry=self.registry, profile="commander_review")
            self.assertFalse(actual["strict"], actual)
        deps = tuple(cap for cap in spell.capability_dependencies if cap != COHORT_ADDENDUM_CAPABILITY)
        unbound = replace(spell, capability_dependencies=deps, capability_closure=self.registry.closure(
            deps, profile="commander_review").to_dict())
        actual = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + COHORT_ADDENDUM_CAPABILITY, actual["blockers"])


    def test_formation_pending_counter_replacement_preserves_the_same_later_keyword_grant(self):
        from quorune.record import authoritative_state_hash

        game = self.session(60131022)
        spell = self.add(game.engine, "Unbreakable Formation", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        evolution = self.add(game.engine, "Branching Evolution")
        scales = self.add(game.engine, "Hardened Scales")
        for card in (spell, evolution, scales):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        current = game.state.cards[creature.object_id]
        self.assertIn("indestructible", game.engine._combat_keywords(current))
        self.assertNotIn("vigilance", game.engine._combat_keywords(current))
        self.assertEqual(0, current.counters.get("+1/+1", 0))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        selected = next(row["id"] for row in options if row["source"] == evolution.ref)
        command = {"action_id": "choose", "replacement": selected}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[creature.object_id]
        self.assertEqual(3, current.counters.get("+1/+1"))
        self.assertTrue({"indestructible", "vigilance"} <= game.engine._combat_keywords(current))
        self.replay(game, load=True)
