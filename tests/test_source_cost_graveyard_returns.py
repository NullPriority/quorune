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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_excluding_graveyard_returns import (
    SOURCE_EXCLUDING_GRAVEYARD_RETURN_CAPABILITY as CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FIXTURES = ROOT / "tests" / "fixtures"
PROFILE = "commander_review"
PRICE = "activation.source_zone_change.fixed"
HAULER = "Corpse Hauler"
ORATOR = "Bonebind Orator"
SOULTENDER = "Eerie Soultender"
STEEL = "Spectral Steel"
PRICES = {
    HAULER: ("battlefield", {"B": 1, "GENERIC": 2}),
    ORATOR: ("graveyard", {"B": 1, "GENERIC": 3}),
    SOULTENDER: ("graveyard", {"B": 1, "GENERIC": 4}),
    STEEL: ("graveyard", {"W": 1, "GENERIC": 1}),
}


class SourceCostGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activated-self-transform-cards.json": {"Ashnod's Altar", "Cremate"},
            "attached-destruction-set-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
            "enchant-public-state-cards.json": {"Bonesplitter"},
            "source-excluding-graveyard-return-cards.json": {"Myr Retriever"},
        }
        parts = [FIXTURES / "source-cost-graveyard-return-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            cards = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, source
            ids = {row["oracle_id"] for row in cards}
            rulings = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps({**doc, "cards": cards, "rulings": rulings}), encoding="utf-8")
            parts.append(part)
        path = folder / "source-cost-graveyard-returns.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source-cost graveyard returns", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _start(self, game, cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)

    def _offers(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["legal_actions"]

    def _offer(self, game, source):
        [row] = [row for row in self._offers(game) if row["id"].startswith(f"activate:{source.ref}:")]
        return row

    def _act(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _reject(self, game, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _activate(self, game, source, target, payment):
        row = self._offer(game, source)
        self.assertIn(target.ref, row["target_schema"]["legal_refs"])
        self._act(game, {"action_id": row["id"], "targets": [target.ref], "pay": "manual", "payment": payment})

    def test_complete_four_activations_match_the_native_target_and_source_price(self):
        for name, (zone, mana) in PRICES.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                report, registry = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                programs = registry.programs_for_oracle(card.oracle_id)
                program, = (p for p in programs if p.event == "activate")
                self.assertEqual(zone, program.active_zone)
                self.assertEqual(mana, {key: amount for key, amount in program.cost_schema["mana"].items() if amount})
                self.assertIs(program.cost_schema["sacrifice_source"], zone == "battlefield")
                self.assertIs(program.cost_schema["exile_source"], zone == "graveyard")
                self.assertIs(program.target_schema["source_exclusion"], True)
                self.assertEqual("you", program.target_schema["owner_relation"])
                self.assertEqual([{"op": "return_graveyard_card_to_owner_hand", "card": "$target.0"}], program.effects)
                if name == STEEL:
                    self.assertEqual(["aura", "equipment"], program.target_schema["subtypes_any"])
                    self.assertTrue(any(p.event == "characteristics.evaluate" for p in programs))
                else:
                    self.assertEqual(["creature"], program.target_schema["types_any"])
                if name == SOULTENDER:
                    self.assertTrue(any(p.event == "permanent.enter.self" for p in programs))
                for changed in (
                    replace(program, active_zone="hand"),
                    replace(program, handlers=[]),
                    replace(program, cost_schema={**program.cost_schema, "exile_source": not program.cost_schema["exile_source"]}),
                    replace(program, target_schema={**program.target_schema, "source_exclusion": False}),
                ):
                    result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                    self.assertFalse(result["strict"], result)
                deps = tuple(capability for capability in program.capability_dependencies if capability != PRICE)
                omitted = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + PRICE, binding["blockers"])

    def test_fresh_fixed_source_activation_frame_omission_is_distinguished(self):
        def witness():
            report, _ = register_card(self.db, self.registry, ORATOR)
            self.assertTrue(report["strict_capability_ready"], report["blockers"])

        witness()
        with patch("quorune.rules.source_excluding_graveyard_returns._fixed_source_move_activation_frame", return_value=False):
            with self.assertRaises(AssertionError):
                witness()
        witness()

    def test_actual_orator_has_no_legal_activation_with_only_itself_in_that_graveyard(self):
        game = self.session(60202041)
        source = self.add(game.engine, ORATOR, zone="graveyard")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard")
        self._start(game, (source,), {"C": 3, "B": 1})
        self.checkpoint(game)
        ability, = game.engine._activated_abilities(game.state.cards[source.object_id])
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertNotIn(action_id, {row["id"] for row in self._offers(game)})
        self._reject(game, {"action_id": action_id, "targets": [source.ref], "pay": "auto"})
        self._reject(game, {"action_id": action_id, "targets": [foreign.ref], "pay": "auto"})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_hauler_sacrifices_before_resolution_and_never_refunds_on_target_loss(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game = self.session(60202042 + stale)
                source = self.add(game.engine, HAULER)
                target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="hauler-target")
                response = self.add(game.engine, "Cremate", seat="B", zone="hand")
                self._start(game, (source, response), {"C": 2, "B": 1})
                game.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(game)
                self._activate(game, source, target, {"C": 2, "B": 1})
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                if stale:
                    self.cast(game, "B", response, [target.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual("exile" if stale else "hand", game.state.cards[target.object_id].zone)
                self.replay(game, load=True)

    def test_actual_spectral_steel_exiles_its_source_and_returns_another_equipment(self):
        game = self.session(60202045)
        source = self.add(game.engine, STEEL, zone="graveyard")
        target = self.add(game.engine, "Bonesplitter", zone="graveyard", ref="steel-target")
        unrelated = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="steel-not-equipment")
        private = self.add(game.engine, ORATOR, seat="C", zone="hand", ref="steel-private")
        self._start(game, (source,), {"C": 1, "W": 1})
        self.checkpoint(game)
        row = self._offer(game, source)
        self.assertEqual([target.ref], row["target_schema"]["legal_refs"])
        self._reject(game, {"action_id": row["id"], "targets": [source.ref], "pay": "auto"})
        self._reject(game, {"action_id": row["id"], "targets": [unrelated.ref], "pay": "auto"})
        self._activate(game, source, target, {"C": 1, "W": 1})
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[unrelated.object_id].zone)
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_soultender_mills_on_entry_then_uses_the_graveyard_activation(self):
        game = self.session(60202046)
        source = self.add(game.engine, SOULTENDER, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="soultender-target")
        altar = self.add(game.engine, "Ashnod's Altar")
        self._start(game, (source, altar), {"C": 4, "B": 2})
        self.checkpoint(game)
        library = len(game.state.players["A"].zones["library"])
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(library - 3, len(game.state.players["A"].zones["library"]))
        row = self._offer(game, altar)
        self._act(game, {"action_id": row["id"], "cost_cards": [source.ref]})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self._activate(game, source, target, {"C": 4, "B": 1})
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_verified_source_price_withdrawal_preserves_the_unpriced_source_trigger(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == PRICE).update(
            status="blocked", blockers=["Fixed source-moving activation price withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((PRICE,), profile=PROFILE)
        self.assertIn(f"status:{PRICE}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in PRICES:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        retained, _ = register_card(self.db, registry, "Myr Retriever")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
