"""Original attacking-source cast copies through ordinary commander, combat and replay actions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_spell_copy import ATTACK_CAST_COPY_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Fire Lord Azula"
SUPPORT = {
    "Fervor": "firebending-token-cards.json",
    "Lightning Bolt": "activation-restriction-material-cards.json",
    "Ambush Viper": "compiled-reviewed-trigger-cards.json",
    "Unsummon": "ability-label-composition-cards.json",
    "Forest": "ability-label-composition-cards.json",
    "Island": "activation-restriction-material-cards.json",
    "Mountain": "activation-result-views-cards.json",
}


class AttackingSourceCastCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        work = Path(cls.temp.name)
        fixture_root = ROOT / "tests/fixtures"
        base = fixture_root / "bound-effect-program-cards.json"
        copy_cards = fixture_root / "event-spell-copy-cards.json"
        covered = {row["name"] for fixture in (base, copy_cards) for row in json.loads(fixture.read_bytes())["cards"]}
        neighbors = []
        for name, source in SUPPORT.items():
            if name not in covered:
                payload = json.loads((fixture_root / source).read_bytes())
                matches = [row for row in payload["cards"] if row["name"] == name]
                if len(matches) != 1:
                    raise ValueError(f"Expected one fixture row for {name} in {source}")
                neighbors.extend(matches)
        subset = work / "support.json"
        subset.write_text(json.dumps({"schema_version": 1, "cards": neighbors, "rulings": []}), encoding="utf8")
        database = work / "attack-cast-copy.sqlite3"
        build_fixture_database([base, copy_cards, subset], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attacking cast copy", [
            DeckEntry(CARD, 1, "commander"), DeckEntry("Mountain", 30),
        ], [CARD])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_printed_copy_is_exact_and_condition_mutations_fail_closed(self):
        record = self.db.lookup(CARD, fuzzy=False)
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        fire, copying = ir.faces[0].nodes
        self.assertIn("trigger.keyword.firebending.fixed", fire.capability_dependencies)
        self.assertIn(ATTACK_CAST_COPY_CAPABILITY, copying.capability_dependencies)
        self.assertEqual(("spell.cast", "battlefield"), (copying.event, copying.active_zone))
        for old, new in (
            ("is attacking", "is blocking"),
            ("you cast", "an opponent casts"),
            ("copy that spell", "you may copy that spell"),
            ("choose new targets for the copy", "draw a card"),
        ):
            with self.subTest(new=new):
                self.assertNotEqual("exact", self.compile(
                    replace(record, oracle_text=record.oracle_text.replace(old, new)),
                ).status)
        from quorune.rules.event_spell_copy import attacking_cast_copy_dependencies
        from quorune.semantics import SemanticProgram

        descriptor = SemanticProgram(
            key="attack-cast-copy", label="sealed", effects=copying.effects, event=copying.event,
            active_zone=copying.active_zone, event_condition=copying.event_condition, coverage=copying.runtime_coverage,
        )
        self.assertIn(ATTACK_CAST_COPY_CAPABILITY, attacking_cast_copy_dependencies(descriptor))
        with self.assertRaises(ValueError):
            attacking_cast_copy_dependencies(replace(descriptor, event_condition=None))

    def deny(self, game, principal, payload):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act(principal, payload).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def send_attack(self, game, source):
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        command = {"action_id": "attack", "attacks": {game.state.cards[source.object_id].ref: "B"}}
        self.deny(game, "pilot:B", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])

    def test_commander_cast_attack_and_opposing_cast_boundary_with_native_retarget(self):
        game = self.session(506410501)
        game.state.config.semantic_policy = "trusted_only"
        azula = game.state.cards[game.state.players["A"].zones["command"][0]]
        fervor = self.add(game.engine, "Fervor")
        precombat = self.add(game.engine, "Lightning Bolt", zone="hand", ref="before-attack-bolt")
        own = self.add(game.engine, "Lightning Bolt", zone="hand", ref="attacking-cast-bolt")
        opposing = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="opposing-cast-bolt")
        self.add(game.engine, "Mountain", seat="B")
        for card in (azula, fervor, own):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 1, "B": 1, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", azula, reserve={"R": 1})
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[azula.object_id].zone)
        self.cast_for(game, "A", precombat, ["D"])
        self.assertEqual(0, self.trigger_count(game, azula))
        self.resolve_top(game)
        self.send_attack(game, azula)
        self.cast_for(game, "B", opposing, ["D"])
        self.assertEqual(0, self.trigger_count(game, azula))
        self.resolve_top(game)
        self.cast_for(game, "A", own, ["B"])
        self.assertEqual(1, self.trigger_count(game, azula))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        self.deny(game, "pilot:B", {"action_id": "choose", "targets": ["C"]})
        self.deny(game, "pilot:A", {"action_id": "choose", "targets": ["stale-target"]})
        result = game.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(result.ok, result.summary)
        copy, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(("A", ["C"]), (copy.controller, copy.targets))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 37, 37, 34], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_attacking_trigger_survives_source_bounce_and_copies_permanent_as_token(self):
        game = self.session(506410502)
        game.state.config.semantic_policy = "trusted_only"
        azula = self.add(game.engine, CARD, ref="noncommander-azula")
        viper = self.add(game.engine, "Ambush Viper", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Forest")
        self.add(game.engine, "Island", seat="B")
        for card in (azula, viper, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.send_attack(game, azula)
        self.cast_for(game, "A", viper)
        self.assertEqual(1, self.trigger_count(game, azula))
        self.cast_for(game, "B", bounce, [game.state.cards[azula.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[azula.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        found = [card for card in game.state.cards.values()
                 if card.printed_name == "Ambush Viper" and card.zone == "battlefield"]
        self.assertEqual([False, True], sorted(card.is_token for card in found))
        self.assertTrue(all(card.controller == "A" for card in found))
        self.replay(game, load=True)
