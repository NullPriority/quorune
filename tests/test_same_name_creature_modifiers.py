from __future__ import annotations

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
from quorune.compiler.fixed_public_characteristic_sets import fixed_public_characteristic_set_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.same_name_creature_modifiers import (
    SAME_NAME_STAT_CAPABILITY as CAPABILITY,
    SAME_NAME_STAT_VERSION_FIELD as VERSION,
    SameNameCreatureStats,
    decode_same_name_creature_stats,
    same_name_stat_target_schema,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FIXTURES = ROOT / "tests" / "fixtures"
PROFILE = "commander_review"
COURAGE = "Echoing Courage"
DECAY = "Echoing Decay"
BLIGHT = "Bile Blight"
MORPH = "Lumithread Field"
WURM = "Spined Wurm"


class SameNameCreatureModifierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Ranger's Guile", "Unsummon"},
            "activation-paid-result-slot-cards.json": {WURM},
            "morph-rules-cards.json": {MORPH},
            "temporary-creature-copy-set-cards.json": {"Mirage Mirror"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
            "activation-public-state-cards.json": {"Giant Growth"},
        }
        parts = [FIXTURES / "same-name-creature-modifier-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            cards = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, source
            ids = {row["oracle_id"] for row in cards}
            rulings = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps({**doc, "cards": cards, "rulings": rulings}), encoding="utf-8")
            parts.append(part)
        path = folder / "same-name-creature-modifiers.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Same-name creature modifiers", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _start(self, game, cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)

    def _act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, value) for value in ("power", "toughness"))

    def _copy_wurm(self, game, player, mirror, target):
        self.priority(game, player)
        rows = game.packet("pilot:" + player, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in rows if row["id"].startswith(f"activate:{mirror.ref}:")]
        self._act(game, player, {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        self.resolve_top(game)

    def test_pinned_spells_close_only_the_exact_one_target_version(self):
        for name, modifier in ((COURAGE, 2), (DECAY, -2), (BLIGHT, -3)):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                report, registry = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = registry.programs_for_oracle(card.oracle_id)
                effect, = program.effects
                self.assertEqual((SameNameCreatureStats(modifier, modifier), "$target.0"),
                                 decode_same_name_creature_stats(effect))
                self.assertEqual(same_name_stat_target_schema(), program.target_schema)
                self.assertEqual(1, effect[VERSION])
                for altered in (
                    {VERSION: True}, {VERSION: 2}, {"card": "$target.1"},
                    {"power": True}, {"power": 0, "toughness": 0}, {"duration": "permanent"},
                ):
                    changed = replace(program, effects=[{**effect, **altered}])
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile=PROFILE,
                    )["strict"], altered)
                multi_target = replace(program, target_schema={**program.target_schema, "count": 2})
                self.assertFalse(bind_semantic_program_runtime(
                    multi_target, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                deps = tuple(key for key in program.capability_dependencies if key != CAPABILITY)
                undeclared = replace(program, capability_dependencies=deps, coverage=(),
                                     capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(undeclared, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        text = self.db.lookup(COURAGE, fuzzy=False).oracle_text
        for near_miss in (
            text.replace("creatures", "permanents"),
            text.replace("+2/+2", "+X/+X"),
            text.replace("end of turn", "your next turn"),
            text.replace("all other creatures", "all other creatures you control"),
            text + " Draw a card.",
        ):
            self.assertIsNone(fixed_public_characteristic_set_effect_template(near_miss), near_miss)

    def test_fresh_same_name_producer_omission_blocks_the_original(self):
        def witness():
            report, _ = register_card(self.db, self.registry, COURAGE)
            self.assertTrue(report["strict_capability_ready"], report["blockers"])

        witness()
        with patch("quorune.compiler.fixed_public_characteristic_sets._same_name_creature_stat_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        witness()

    def test_actual_echoing_courage_locks_the_current_copy_names_through_cleanup(self):
        game = self.session(201200600)
        spell = self.add(game.engine, COURAGE, zone="hand")
        anchor = self.add(game.engine, WURM, ref="echo-anchor")
        early = self.add(game.engine, "Mirage Mirror", seat="B", ref="echo-copy-early")
        late = self.add(game.engine, "Mirage Mirror", seat="C", ref="echo-copy-late")
        other = self.add(game.engine, "Grizzly Bears", seat="D", ref="echo-unrelated")
        self._start(game, (spell, early, late), {"C": 1, "G": 1})
        for seat in "BC":
            game.state.players[seat].mana_pool["C"] = 2
        self.checkpoint(game)
        self.cast(game, "A", spell, [anchor.ref])
        self._copy_wurm(game, "B", early, anchor)
        self.assertEqual((5, 4), self._stats(game, early))
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        event, = [event for event in game.state.events[cursor:] if event.code == "effect.mass_modify"]
        self.assertEqual({anchor.ref, early.ref}, set(event.details["objects"]))
        self.assertEqual((7, 6), self._stats(game, anchor))
        self.assertEqual((7, 6), self._stats(game, early))
        self.assertEqual((2, 2), self._stats(game, other))
        self._copy_wurm(game, "C", late, anchor)
        self.assertEqual((5, 4), self._stats(game, late))
        self.assertEqual((7, 6), self._stats(game, early))
        turn = game.state.turn_sequence
        self.until(game, lambda state: state.state.turn_sequence > turn)
        self.assertEqual((5, 4), self._stats(game, anchor))
        self.assertEqual("Mirage Mirror", game.engine._effective_card_data(early.object_id)["name"])
        self.replay(game, load=True)

    def test_actual_bile_blight_affects_the_hexproof_member_and_rejects_it_as_a_target(self):
        game = self.session(201200601)
        spell = self.add(game.engine, BLIGHT, zone="hand")
        guile = self.add(game.engine, "Ranger's Guile", zone="hand", seat="B")
        anchor = self.add(game.engine, WURM, ref="blight-anchor")
        protected = self.add(game.engine, WURM, seat="B", ref="blight-protected")
        self._start(game, (spell, guile), {"B": 2})
        game.state.players["B"].mana_pool["G"] = 1
        self.checkpoint(game)
        self.cast(game, "B", guile, [protected.ref])
        self.assertIsNone(self.resolve(game))
        self.assertIn("hexproof", game.engine._combat_keywords(game.state.cards[protected.object_id]))
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": offer["id"], "targets": [protected.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [anchor.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 1), self._stats(game, anchor))
        self.assertEqual((3, 2), self._stats(game, protected))
        self.replay(game, load=True)

    def test_actual_echoing_decay_fizzles_on_departure_and_batches_the_legal_result(self):
        game = self.session(201200602)
        first = self.add(game.engine, DECAY, zone="hand", ref="decay-first")
        second = self.add(game.engine, DECAY, zone="hand", ref="decay-second")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        departing = self.add(game.engine, "Grizzly Bears", ref="decay-departing")
        anchor = self.add(game.engine, "Grizzly Bears", seat="C", ref="decay-anchor")
        survivor = self.add(game.engine, "Grizzly Bears", seat="D", ref="decay-survivor")
        survivor.counters["+1/+1"] = 1
        self._start(game, (first, second, bounce), {"C": 2, "B": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "A", first, [departing.ref])
        self.cast(game, "B", bounce, [departing.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[departing.object_id].zone)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        self.assertFalse(any(event.code == "effect.mass_modify" for event in game.state.events[cursor:]))
        self.assertEqual((2, 2), self._stats(game, anchor))
        self.cast(game, "A", second, [anchor.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[anchor.object_id].zone)
        self.assertEqual((1, 1), self._stats(game, survivor))
        events = game.state.events[cursor:]
        result, = [event for event in events if event.code == "effect.mass_modify"]
        movement, = [event for event in events if event.code == "state.creatures_died"]
        self.assertEqual([anchor.ref], movement.details["put_in_graveyard"])
        self.assertEqual({anchor.ref, survivor.ref}, set(result.details["objects"]))
        self.assertLess(events.index(result), events.index(movement))
        self.replay(game, load=True)

    def test_actual_nameless_creatures_receive_a_modifier_only_when_targeted(self):
        game = self.session(201200603)
        spell = self.add(game.engine, COURAGE, zone="hand")
        first = self.add(game.engine, MORPH, zone="hand", ref="echo-hidden-first")
        second = self.add(game.engine, MORPH, zone="hand", ref="echo-hidden-second")
        self._start(game, (spell, first, second), {"C": 7, "G": 1})
        self.checkpoint(game)
        for card in (first, second):
            self.priority(game, "A")
            self._act(game, "A", {"action_id": f"cast-morph:{card.ref}", "pay": "manual", "payment": {"C": 3}})
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[card.object_id].face_down)
            self.assertEqual((2, 2), self._stats(game, card))
        self.cast(game, "A", spell, [first.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((4, 4), self._stats(game, first))
        self.assertEqual((2, 2), self._stats(game, second))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(MORPH, packet)
            self.assertNotIn(first.oracle_id[:8], packet)
        self.replay(game, load=True)

    def test_verified_same_name_withdrawal_keeps_the_ordinary_targeted_modifier(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Current same-name stat set authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (COURAGE, DECAY, BLIGHT):
            rejected, _ = register_card(self.db, registry, name)
            self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Giant Growth")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
