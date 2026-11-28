from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.flashback import FLASHBACK_CAST_ANNOTATION, FLASHBACK_CAST_OPTION_ID
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_graveyard_flashback import (
    DEPENDENCIES, GRAVEYARD_FLASHBACK_CAPABILITY, GRAVEYARD_FLASHBACK_STATS_FIELD, INSTRUCTION, TARGET_SCHEMA,
    target_graveyard_flashback_runtime_dependencies, validate_target_graveyard_flashback_effect,
)
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
from test_controller_creature_keyword_absence import register_card


CARD = "Flashback"
PROFILE = "commander_review"


class TemporaryGraveyardFlashbackTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "temporary-graveyard-flashback-cards.json": {CARD},
            "flashback-cards.json": {"Think Twice"},
            "fuse-cards.json": {"Wear // Tear"},
            "enchanted-player-static-cards.json": {"Ghostly Prison"},
            "cast-spell-suspend-cards.json": {"Blaze"},
            "graveyard-exile-replacement-cards.json": {"Ancestral Vision", "Isamaru, Hound of Konda", "Island"},
            "targeted-graveyard-exile-cards.json": {"Cremate", "Pull from Eternity", "Shock", "Sol Ring", "Counterspell"},
            "bargain-cards.json": {"Divination"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
        }
        parts = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in document["cards"]} == names, filename
            oracle_ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            part = folder / filename
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "temporary-graveyard-flashback.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Temporary Flashback", [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def test_exact_lowering_and_full_authority_reject_unowned_or_changed_descriptor(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(TARGET_SCHEMA, FrozenMap(program.target_schema))
        effect, = program.effects
        self.assertEqual(INSTRUCTION, FrozenMap(effect))
        self.assertEqual(set(DEPENDENCIES), set(target_graveyard_flashback_runtime_dependencies(program)))
        for changed in ({"card": "$target.1"}, {"schema_version": True}, {"until": "next_turn"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_target_graveyard_flashback_effect({**effect, **changed})
        for old, new in (("your graveyard", "a graveyard"), ("until end of turn", "until your next turn"),
                         ("its mana cost", "{0}"), ("instant or sorcery", "sorcery")):
            with self.subTest(new=new):
                ir = compile_oracle_card(record(card.oracle_text.replace(old, new, 1), "Instant"), capability_registry=self.registry)
                self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.targeted_graveyard_cast_templates.targeted_graveyard_flashback_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)
        deps = tuple(value for value in program.capability_dependencies if value != GRAVEYARD_FLASHBACK_CAPABILITY)
        forged = replace(program, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn(f"capability:undeclared_runtime_dependency:{GRAVEYARD_FLASHBACK_CAPABILITY}", result["blockers"])

    def start(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def offers(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return [a for a in actions if a["id"] == f"cast:{card.ref}"]

    def grant(self, game, spell, target, *, seat="A"):
        self.cast(game, seat, spell, [target.ref])
        self.assertIsNone(self.resolve(game))

    def cast_granted(self, game, card, *, seat="A", targets=(), x=None):
        action, = self.offers(game, seat, card)
        selected, = [o for o in action["cost_options"] if o["id"].startswith("granted-flashback:")]
        command = {"action_id": action["id"], "cost_option": selected["id"], "targets": list(targets), "pay": "auto"}
        if x is not None:
            command["x"] = x
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[card.object_id].annotations.get(FLASHBACK_CAST_ANNOTATION))
        return selected

    def test_actual_printed_price_is_distinct_from_native_flashback_and_exiles_on_departure(self):
        for countered in (False, True):
            with self.subTest(countered=countered):
                game = self.session(1649001 + countered)
                spell = self.add(game.engine, CARD, zone="hand")
                target = self.add(game.engine, "Think Twice", zone="graveyard")
                counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
                game.state.players["B"].mana_pool.update(U=2)
                self.start(game, spell, target, counter, mana={"C": 2, "U": 1, "R": 1})
                self.grant(game, spell, target)
                game = self.replay(game, load=True)
                action, = self.offers(game, "A", target)
                native, = [o for o in action["cost_options"] if o["id"] == FLASHBACK_CAST_OPTION_ID]
                self.assertEqual((2, 1), (native["requirements"]["GENERIC"], native["requirements"]["U"]))
                selected = self.cast_granted(game, target)
                self.assertEqual((1, 1), (selected["requirements"]["GENERIC"], selected["requirements"]["U"]))
                self.assertEqual(1, game.state.players["A"].mana_pool["C"])
                if countered:
                    self.cast(game, "B", counter, [game.state.stack[-1].ref])
                hand = len(game.state.players["A"].zones["hand"])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", game.state.cards[target.object_id].zone)
                self.assertEqual(hand + int(not countered), len(game.state.players["A"].zones["hand"]))
                self.replay(game, load=True)

    def test_actual_granted_x_is_paid_and_missing_mana_cost_cannot_be_used(self):
        game = self.session(1649003)
        spell = self.add(game.engine, CARD, zone="hand")
        other_grant = self.add(game.engine, CARD, zone="hand", ref="grant-to-costless-card")
        blaze = self.add(game.engine, "Blaze", zone="graveyard")
        costless = self.add(game.engine, "Ancestral Vision", zone="graveyard")
        self.start(game, spell, blaze, other_grant, mana={"C": 3, "R": 3, "U": 1})
        self.grant(game, other_grant, costless)
        self.assertEqual([], self.offers(game, "A", costless))
        self.assertEqual("graveyard", game.state.cards[costless.object_id].zone)
        self.grant(game, spell, blaze)
        before = sum(game.state.players["A"].mana_pool.values())
        self.cast_granted(game, blaze, targets=("B",), x=3)
        self.assertEqual(before - 4, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((37, "exile"), (game.state.players["B"].life, game.state.cards[blaze.object_id].zone))
        self.replay(game, load=True)

    def test_actual_sorcery_does_not_gain_opponent_turn_timing_and_expiration_replays(self):
        game = self.session(1649004)
        spell = self.add(game.engine, CARD, seat="B", zone="hand")
        target = self.add(game.engine, "Divination", seat="B", zone="graveyard")
        for index in range(3):
            self.add(game.engine, "Island", seat="B", ref=f"next-main-island-{index}")
        game.state.players["B"].mana_pool.update(C=2, U=1, R=1)
        self.start(game, spell, target)
        self.grant(game, spell, target, seat="B")
        self.assertIn(GRAVEYARD_FLASHBACK_STATS_FIELD, game.state.players["B"].stats)
        self.assertEqual([], self.offers(game, "B", target))
        self.finish_turn(game)
        self.assertNotIn(GRAVEYARD_FLASHBACK_STATS_FIELD, game.state.players["B"].stats)
        self.until(game, lambda g: g.state.active_player == "B" and g.state.phase == "precombat_main")
        self.assertEqual([], self.offers(game, "B", target))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_split_half_uses_its_own_mana_cost_and_replays(self):
        game = self.session(1649006)
        spell = self.add(game.engine, CARD, zone="hand")
        split = self.add(game.engine, "Wear // Tear", zone="graveyard")
        enchantment = self.add(game.engine, "Ghostly Prison", seat="B")
        self.start(game, spell, split, mana={"R": 1, "W": 1})
        self.grant(game, spell, split)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in actions if row.get("card") == split.ref]
        self.assertEqual("Tear", offered["face"])
        option, = [row for row in offered["cost_options"] if row["id"].startswith("granted-flashback:")]
        self.assertEqual({"W": 1}, {key: value for key, value in option["requirements"].items() if value})
        accepted = game.act("pilot:A", {"action_id": offered["id"], "face": "Tear", "cost_option": option["id"],
                                      "targets": [enchantment.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("Tear", game.state.cards[split.object_id].active_face)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[enchantment.object_id].zone)
        self.assertEqual("exile", game.state.cards[split.object_id].zone)
        self.replay(game, load=True)

    def test_actual_illegal_targets_and_graveyard_round_trip_do_not_grant(self):
        game = self.session(1649005)
        spell = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Shock", zone="graveyard")
        foreign = self.add(game.engine, "Shock", seat="B", zone="graveyard", ref="other-graveyard-instant")
        artifact = self.add(game.engine, "Sol Ring", zone="graveyard")
        cremate = self.add(game.engine, "Cremate", seat="B", zone="hand")
        pull = self.add(game.engine, "Pull from Eternity", seat="C", zone="hand")
        game.state.players["B"].mana_pool.update(B=1)
        game.state.players["C"].mana_pool.update(W=1)
        self.start(game, spell, cremate, pull, mana={"R": 2})
        offered, = self.offers(game, "A", spell)
        before = authoritative_state_hash(game.state)
        for bad in (foreign, artifact):
            with self.subTest(bad=bad.ref):
                result = game.act("pilot:A", {"action_id": offered["id"], "targets": [bad.ref], "pay": "auto"})
                self.assertFalse(result.ok, result.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [target.ref])
        old = target.logical_object_id
        self.cast(game, "B", cremate, [target.ref])
        self.resolve_top(game)
        self.cast(game, "C", pull, [target.ref])
        self.resolve_top(game)
        self.assertNotEqual(old, game.state.cards[target.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn(GRAVEYARD_FLASHBACK_STATS_FIELD, game.state.players["A"].stats)
        self.assertEqual([], self.offers(game, "A", target))
        self.replay(game, load=True)
