"""Native evidence for one complete command-zone graveyard casting grant."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.admission import program_has_complete_card_program_admission
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import emblem_nodes
from quorune.compiler.turn_cast_grants import command_graveyard_cast_grant_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.emblem_program_model import EMBLEM_PROGRAM_ANNOTATION
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_grant_riders import CAST_GRANT_RIDER_FIELD
from quorune.rules.casting.turn_cast_grants import current_turn_cast_grants
from quorune.rules.turn_cast_grant_model import (
    COMMAND_GRAVEYARD_CAST_CAPABILITY, COMMAND_GRAVEYARD_CAST_HANDLER,
    TURN_CAST_GRANT_EVENT, TurnCastGrantScope, TurnCastGrantSpec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_turn_cast_grants as cast_witnesses


JAYA = "Jaya Ballard"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class CommandGraveyardCastGrantTests(InheritedNativeFixture, unittest.TestCase):
    options = cast_witnesses.TurnCastGrantTests.options
    use = cast_witnesses.TurnCastGrantTests.use
    reject = cast_witnesses.TurnCastGrantTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-result-views-cards.json": {"Island", "Mountain", "Isamaru, Hound of Konda"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "noncreature-subtype-quantity-cards.json": {"Lava Spike"},
            "turn-cast-rider-cards.json": {
                "Capsize", "Counterspell", "Kess, Dissident Mage", "Lightning Bolt", "Sol Ring", "Think Twice",
            },
        }
        pieces = [FIXTURES / "command-graveyard-cast-grant-cards.json"]
        for name, cards in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in cards]
            assert cards == {row["name"] for row in doc["cards"]}, name
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(doc), encoding="utf-8")
            pieces.append(part)
        path = folder / "command-graveyard-cast-grant.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Command casting grant", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(JAYA, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, JAYA)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _activate_emblem(self, game, creator):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"] == f"activate:{creator.ref}:ab3"]
        command = {"action_id": offer["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.cards[creator.object_id].counters["loyalty"])

    def _emblem(self, game):
        emblem, = [card for card in game.state.cards.values() if card.object_kind == "emblem"]
        self.assertEqual(("A", "A", "command"), (emblem.owner, emblem.controller, emblem.zone))
        self.assertIn(EMBLEM_PROGRAM_ANNOTATION, emblem.annotations)
        return emblem

    def _choice(self, game, card, emblem, *, buyback=False):
        choice, = [
            row for row in self.options(game, card)
            if row.get("permission_source") == emblem.ref and ("Buyback" in row["label"]) == buyback
        ]
        self.assertNotIn(" once:", choice["label"])
        return choice

    def test_exact_command_wire_and_legacy_grants_remain_distinct(self):
        body = ('You may cast instant and sorcery spells from your graveyard. '
                'If a spell cast this way would be put into your graveyard, exile it instead.')
        _, descriptor, dependencies = command_graveyard_cast_grant_handler(body)
        self.assertIn(COMMAND_GRAVEYARD_CAST_CAPABILITY, dependencies)
        self.assertEqual(COMMAND_GRAVEYARD_CAST_HANDLER, descriptor["handler_id"])
        raw = descriptor["grant"]
        self.assertEqual((4, "command_unlimited"), (raw["schema_version"], raw["source_scope"]))
        spec = TurnCastGrantSpec.from_dict(raw)
        self.assertEqual(raw, spec.to_dict())
        self.assertIs(TurnCastGrantScope.COMMAND_UNLIMITED, spec.source_scope)
        old = replace(spec, source_scope=TurnCastGrantScope.ONCE_ON_OWN_TURN).to_dict()
        self.assertEqual(2, old["schema_version"])
        self.assertNotIn("source_scope", old)
        self.assertEqual(old, TurnCastGrantSpec.from_dict(old).to_dict())
        for field, bad in (
            ("schema_version", True), ("source_scope", "once_on_own_turn"),
            ("origins", ["hand"]), ("cast_rider", "none"), ("without_mana_cost", True),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                TurnCastGrantSpec.from_dict(dict(raw, **{field: bad}))
        for wrong in (
            body.replace("instant and sorcery", "creature"),
            body.replace("your graveyard", "exile", 1),
            body.replace("exile it instead", "return it to your hand"),
            body + " Draw a card.",
        ):
            with self.subTest(wrong=wrong):
                self.assertIsNone(command_graveyard_cast_grant_handler(wrong))

    def test_complete_original_keeps_three_loyalty_abilities_and_trusted_command_child(self):
        card, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        child, = [p for p in programs if p.active_zone == "command" and p.event == TURN_CAST_GRANT_EVENT]
        self.assertTrue(program_has_complete_card_program_admission(child))
        self.assertIn(COMMAND_GRAVEYARD_CAST_CAPABILITY, child.capability_dependencies)
        handler, = child.handlers
        self.assertEqual(COMMAND_GRAVEYARD_CAST_HANDLER, handler["handler_id"])
        self.assertTrue(handler["requires_complete_card_program"])
        activations = [p for p in programs if p.active_zone == "battlefield" and p.event == "activate"]
        self.assertEqual(3, len(activations))
        children = [effect for parent in activations for effect in parent.effects
                    if effect["op"] == "create_compiled_emblem"]
        self.assertEqual(1, len(children))
        deps = tuple(cid for cid in child.capability_dependencies if cid != COMMAND_GRAVEYARD_CAST_CAPABILITY)
        malformed = replace(child, capability_dependencies=deps,
                            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{COMMAND_GRAVEYARD_CAST_CAPABILITY}",
                      result["blockers"])

    def test_actual_emblem_survives_creator_and_repeats_paid_casts_with_their_own_destinations(self):
        game = self.session(6328010)
        creator = self.add(game.engine, JAYA)
        creator.counters["loyalty"] = 9
        remove = self.add(game.engine, "Hero's Downfall", seat="B", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", zone="graveyard", ref="command-grant-first-bolt")
        capsize = self.add(game.engine, "Capsize", zone="graveyard")
        target = self.add(game.engine, "Sol Ring", seat="C", ref="command-grant-target")
        nonspell = self.add(game.engine, "Sol Ring", zone="graveyard", ref="command-grant-owned-artifact")
        foreign = self.add(game.engine, "Lightning Bolt", seat="B", zone="graveyard", ref="command-grant-foreign-bolt")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="jaya-private")
        self.assert_closed(game, creator)
        self.prepare(game, mana={"C": 4, "U": 2, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        self.assertEqual([], self.options(game, bolt))
        self._activate_emblem(game, creator)
        self.cast(game, "B", remove, [creator.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[creator.object_id].zone)
        self.assertIsNone(self.resolve(game))
        emblem = self._emblem(game)
        game = self.replay(game, load=True)
        self.assertFalse(current_turn_cast_grants(game.engine, "A", game.state.cards[nonspell.object_id]))
        self.assertFalse(current_turn_cast_grants(game.engine, "A", game.state.cards[foreign.object_id]))
        self.assertFalse(current_turn_cast_grants(game.engine, "B", game.state.cards[foreign.object_id]))
        first = self._choice(game, bolt, emblem)
        self.use(game, bolt, first, {"R": 1}, ["B"])
        rider = game.state.cards[bolt.object_id].annotations[CAST_GRANT_RIDER_FIELD]
        self.assertEqual(("graveyard_exile", "A", emblem.ref), (rider["rider"], rider["caster"], rider["source_ref"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[bolt.object_id].zone)
        self.assertEqual(37, game.state.players["B"].life)
        second = self._choice(game, capsize, emblem, buyback=True)
        self.use(game, capsize, second, {"C": 4, "U": 2}, [target.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[capsize.object_id].zone)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertNotIn(CAST_GRANT_RIDER_FIELD, game.state.cards[capsize.object_id].annotations)
        self.assertEqual("graveyard", game.state.cards[creator.object_id].zone)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_emblem_uses_ordinary_instant_timing_and_exiles_a_countered_spell(self):
        game = self.session(6328020)
        creator = self.add(game.engine, JAYA)
        creator.counters["loyalty"] = 9
        bolt = self.add(game.engine, "Lightning Bolt", zone="graveyard")
        sorcery = self.add(game.engine, "Lava Spike", zone="graveyard")
        mountain = self.add(game.engine, "Mountain")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        for index in range(2):
            self.add(game.engine, "Island", seat="B", ref=f"jaya-counter-mana-{index}")
        self.prepare(game)
        self.checkpoint(game)
        self._activate_emblem(game, creator)
        self.assertIsNone(self.resolve(game))
        emblem = self._emblem(game)
        self.until(game, lambda g: g.state.active_player == "B" and g.state.phase == "precombat_main")
        self.priority(game, "A")
        activation = game.act("pilot:A", {"action_id": f"activate:{mountain.ref}:intrinsic_mountain"})
        self.assertTrue(activation.ok, activation.summary)
        self.assertEqual([], self.options(game, sorcery))
        quote = self._choice(game, bolt, emblem)
        self.use(game, bolt, quote, {"R": 1}, ["C"])
        self.assertEqual("B", game.state.active_player)
        self.cast(game, "B", counter, [game.state.stack[-1].ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[bolt.object_id].zone)
        self.assertEqual(40, game.state.players["C"].life)
        self.assertEqual("graveyard", game.state.cards[sorcery.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_the_command_child_restores_the_original_residual(self):
        with patch.object(emblem_nodes, "command_graveyard_cast_grant_handler", return_value=None):
            card = self.db.lookup(JAYA, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("effect", residual.kind)
            self.assertIn("emblem", residual.text)
            report, _ = register_card(self.db, self.registry, JAYA)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Kess, Dissident Mage")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_command_cast_authority_withdrawal_denies_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        doc = deepcopy(raw)
        next(row for row in doc["capabilities"] if row["id"] == COMMAND_GRAVEYARD_CAST_CAPABILITY).update(
            status="blocked", blockers=["Scoped command source authority withdrawal"])
        registry = CapabilityRegistry(doc)
        index = build_index(registry_value=doc, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((COMMAND_GRAVEYARD_CAST_CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{COMMAND_GRAVEYARD_CAST_CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, JAYA)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Kess, Dissident Mage")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
