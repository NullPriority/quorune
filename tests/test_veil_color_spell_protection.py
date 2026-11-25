"""Native ordered draw, spell counter prohibition and resolution-set color hexproof."""

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
from quorune.qualified_hexproof import TURN_COLOR_SPELL_PROTECTION_CAPABILITY, TURN_PLAYER_COLOR_HEXPROOF
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class VeilColorSpellProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "veil-color-protection.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "veil-color-spell-protection-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Turn color protection", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def _ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"B": 1, "C": 3, "R": 1, "U": 6, "W": 1})
        game.state.players["C"].mana_pool.update({"U": 2})
        self.prepare(game, mana={"C": 5, "U": 2, "G": 3, "R": 2})
        self.checkpoint(game)

    def _spell(self, game, card):
        return next(item for item in game.state.stack if item.card_object_id == card.object_id)

    def _keywords(self, game, card):
        return set(game.engine._effective_card_data(game.state.cards[card.object_id])["keywords"])

    def _denied(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _cast_command(self, game, seat, card, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(candidate for candidate in actions if candidate["id"] == "cast:" + card.ref)
        return {"action_id": action["id"], "targets": [target], "pay": "auto"}

    def test_veil_spell_counter_protection_reaches_spells_already_stack_and_cast_later(self):
        game = self.session(611_211_01)
        veil = self.add(game.engine, "Veil of Summer", zone="hand", ref="VEIL-PROTECTION")
        first = self.add(game.engine, "Lightning Bolt", zone="hand", ref="VEIL-ON-STACK")
        later = self.add(game.engine, "Lightning Bolt", zone="hand", ref="VEIL-CAST-LATER")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="VEIL-COUNTER-FIRST")
        counter_later = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="VEIL-COUNTER-LATER")
        third = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="VEIL-COUNTER-THIRD")
        opponent = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="VEIL-OPPOSING-SPELL")
        answer = self.add(game.engine, "Counterspell", zone="hand", ref="VEIL-UNPROTECTED-OPPONENT")
        victim = self.add(game.engine, "Grizzly Bears", seat="C", ref="VEIL-OPPOSING-PERMANENT")
        self._ready(game, veil, first, counter, opponent, answer)
        hand = len(game.state.players["A"].zones["hand"])
        self.cast(game, "A", first, ["D"])
        first_ref = self._spell(game, first).ref
        self.cast(game, "B", counter, [first_ref])
        self.cast(game, "C", third, [first_ref])
        self.cast(game, "A", veil)
        self.resolve_top(game)
        self.assertEqual(hand - 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(["U", "B"], game.state.players["A"].stats[TURN_PLAYER_COLOR_HEXPROOF])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["D"].life)

        self.cast(game, "A", later, ["D"])
        self.cast(game, "B", counter_later, [self._spell(game, later).ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(34, game.state.players["D"].life)
        self.cast(game, "B", opponent, [victim.ref])
        self.cast(game, "A", answer, [self._spell(game, opponent).ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[opponent.object_id].zone)
        self.replay(game, load=True)

    def test_veil_color_hexproof_captures_permanents_and_expires_at_cleanup(self):
        game = self.session(611_211_02)
        veil = self.add(game.engine, "Veil of Summer", zone="hand", ref="GRANT-VEIL")
        own_blue = self.add(game.engine, "Thought Scour", zone="hand", ref="GRANT-OWN-BLUE")
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand", ref="GRANT-BLINK")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="GRANT-BOUNCE")
        destroy = self.add(game.engine, "Doom Blade", seat="B", zone="hand", ref="GRANT-DESTROY")
        mill = self.add(game.engine, "Thought Scour", seat="B", zone="hand", ref="GRANT-MILL")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="GRANT-RED")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B", ref="GRANT-PINGER")
        pinger.acquired_control_turn_count = -1
        old = self.add(game.engine, "Grizzly Bears", ref="GRANT-ORIGINAL")
        stays = self.add(game.engine, "Grizzly Bears", ref="GRANT-STAYS")
        later = self.add(game.engine, "Grizzly Bears", zone="hand", ref="GRANT-LATER")
        stolen = self.add(game.engine, "Grizzly Bears", seat="C", ref="GRANT-LATER-CONTROL")
        steal = self.add(game.engine, "Act of Treason", zone="hand", ref="GRANT-STEAL")
        owner = ZoneTransitionOwner(game.engine)
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            if not game.state.cards[object_id].ref.startswith("GRANT-"):
                owner.move_card(object_id, "library")
        self._ready(game, veil, own_blue, blink, bounce, destroy, mill, bolt, pinger, later, steal)
        turn = game.state.turn_sequence
        self.cast(game, "A", own_blue, ["D"])
        self.assertIsNone(self.resolve(game))
        draws = len(game.state.players["A"].draw_history)
        self.cast(game, "A", veil)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(draws, len(game.state.players["A"].draw_history))
        expected = {"hexproof from blue", "hexproof from black"}
        self.assertLessEqual(expected, self._keywords(game, old))
        self.assertLessEqual(expected, self._keywords(game, stays))
        self.cast(game, "A", later)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", steal, [stolen.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[stolen.object_id].controller)
        for card in (later, stolen):
            self.assertTrue(self._keywords(game, card).isdisjoint(expected))

        self._denied(game, "B", self._cast_command(game, "B", bounce, old.ref))
        self._denied(game, "B", self._cast_command(game, "B", destroy, old.ref))
        self._denied(game, "B", self._cast_command(game, "B", mill, "A"))
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        pinger_id = next(action["id"] for action in actions if action["id"].startswith("activate:" + pinger.ref + ":"))
        self._denied(game, "B", {"action_id": pinger_id, "targets": [old.ref], "pay": "auto"})

        self.cast(game, "B", blink, [old.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._keywords(game, old).isdisjoint(expected))
        self.cast(game, "B", bounce, [later.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[later.object_id].zone)
        self.cast(game, "B", bolt, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)

        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertNotIn(TURN_PLAYER_COLOR_HEXPROOF, game.state.players["A"].stats)
        self.assertNotIn("spells_cant_be_countered_until_end", game.state.players["A"].stats)
        self.assertTrue(self._keywords(game, stays).isdisjoint(expected))
        self.replay(game, load=True)

    def test_veil_draw_replacement_suspends_the_grant_and_resumes_after_private_choice(self):
        game = self.session(611_211_03)
        veil = self.add(game.engine, "Veil of Summer", zone="hand", ref="DREDGE-VEIL")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand", ref="DREDGE-BOLT")
        ritual = self.add(game.engine, "Dark Ritual", seat="B", zone="hand", ref="DREDGE-BLACK-CAST")
        loam = self.add(game.engine, "Life from the Loam", zone="graveyard", ref="DREDGE-LOAM")
        stays = self.add(game.engine, "Grizzly Bears", ref="DREDGE-CONTROLLED-PERMANENT")
        self._ready(game, veil, bolt, ritual, loam)
        self.cast(game, "A", bolt, ["D"])
        self.cast(game, "B", ritual)
        self.cast(game, "A", veil)
        choice = self.resolve(game)
        self.assertEqual(("draw.replacement", ["A"]), (choice.kind, choice.actors))
        self.assertNotIn(TURN_PLAYER_COLOR_HEXPROOF, game.state.players["A"].stats)
        self.assertNotIn("hexproof from blue", self._keywords(game, stays))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": loam.ref}
        self._denied(game, "B", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[loam.object_id].zone)
        self.assertIn("hexproof from blue", self._keywords(game, stays))
        self.assertEqual(37, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_veil_historical_registered_view_remains_pinned_through_replay(self):
        game = self.session(611_211_05)
        veil = self.add(game.engine, "Veil of Summer", zone="hand", ref="HISTORICAL-VEIL")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand", ref="HISTORICAL-BOLT")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="HISTORICAL-COUNTER")
        self._ready(game, bolt, counter)
        legacy = SemanticRegistry().get(f"{veil.oracle_id}:spell:front")
        self.assertIsNotNone(legacy)
        self.assertNotIn("spell_color_history_version", legacy.effects[0])
        game.engine.semantics.put(legacy)
        game.state.turn_history.spell_color_history_version = None
        self.checkpoint(game)

        self.cast(game, "A", bolt, ["D"])
        self.cast(game, "B", counter, [self._spell(game, bolt).ref])
        self.cast(game, "A", veil)
        before = len(game.state.players["A"].draw_history)
        self.resolve_top(game)
        self.assertEqual(before + 1, len(game.state.players["A"].draw_history))
        stats = game.state.players["A"].stats
        self.assertEqual(["U", "B"], stats["hexproof_from_colors_until_end"])
        self.assertNotIn(TURN_PLAYER_COLOR_HEXPROOF, stats)
        self.assertIsNone(self.resolve(game))
        resumed = self.replay(game, load=True)
        stored = resumed.engine.semantics.get(legacy.key)
        self.assertEqual(legacy.effects, stored.effects)

    def test_veil_color_spell_program_rejects_unpaired_order_scope_and_missing_authority(self):
        record = self.db.lookup("Veil of Summer", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        for old, new in (
            ("an opponent has cast", "you have cast"),
            ("or black spell", "or red spell"),
            ("permanents you control", "creatures you control"),
            ("until end of turn", "until your next turn"),
        ):
            changed = replace(record, oracle_text=record.oracle_text.replace(old, new, 1))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == TURN_COLOR_SPELL_PROTECTION_CAPABILITY)
        row.update(status="blocked", blockers=["turn color protection authority withdrawn"])
        self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=CapabilityRegistry(raw)).status)
        with patch("quorune.compiler.temporary_player_rule_templates.turn_color_spell_protection_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)

        game = self.session(611_211_04)
        card = self.add(game.engine, "Veil of Summer")
        self.assert_closed(game, card)
        program = next(p for p in game.engine.semantics.programs_for_oracle(record.oracle_id) if p.event == "resolve")
        draw, grant = program.effects
        for changed in (
            replace(program, effects=(grant, draw)),
            replace(program, effects=(draw,)),
            replace(program, effects=(draw, {**grant, "player": "$target.0"})),
            replace(program, effects=({**draw, "spell_color_history_version": True}, grant)),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
