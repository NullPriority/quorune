"""An untargeted tapped land return reads the graveyard after native Surveil."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.graveyard_return_choices import TAPPED_LAND_RETURN_CAPABILITY, TAPPED_LAND_RETURN_VERSION
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


SOURCE = "Deeproot Wayfinder"


class CombatSurveilLandReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "surveil-land-return.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/combat-surveil-land-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat Surveil and return witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def scene(self, seed, *, mode="player", top_name="Forest", old_land=True, replacement=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SOURCE, ref="wayfinder")
        top = self.add(game.engine, top_name, zone="library", ref="private-top-card")
        old = self.add(game.engine, "Island", zone="graveyard", ref="old-land") if old_land else None
        foreign = self.add(game.engine, "Island", zone="graveyard", seat="B", ref="foreign-land")
        nonland = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="old-creature")
        recipient = None
        if mode == "battle":
            recipient = self.add(game.engine, "Invasion of Zendikar", seat="C", ref="defended-battle")
            recipient.battle_protector = "B"
            recipient.counters["defense"] = 6
        elif mode == "planeswalker":
            recipient = self.add(game.engine, "Jace Beleren", seat="B", ref="defended-planeswalker")
            recipient.counters["loyalty"] = 4
        if replacement:
            peace = self.add(game.engine, "Rest in Peace", seat="B", ref="rest-in-peace")
            self.assert_closed(game, peace)
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, recipient.ref if recipient else "B")])
        return game, source, top, old, foreign, nonland, recipient

    def await_surveil(self, game, source, top):
        self.until(game, lambda active: any(item.source_object_id == source.object_id for item in active.state.stack))
        trigger, = game.state.stack
        self.assertEqual([], trigger.targets)
        self.assertEqual("A", trigger.controller)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("ordered_partition", schema["shape"])
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))

    def surveil(self, game, top, *, move=True):
        chosen, rest = ([], [top.ref]) if move else ([top.ref], [])
        command = {"action_id": "choose", "cards": {"top": chosen, "graveyard": rest}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def return_choice(self, game, card):
        response = {"action_id": "choose", "card": None if card is None else card.ref}
        result = game.act("pilot:A", response)
        self.assertTrue(result.ok, result.summary)

    def test_actual_player_damage_can_return_the_sealed_just_surveilled_land_and_replay(self):
        game, source, top, old, foreign, nonland, _recipient = self.scene(40072901)
        self.await_surveil(game, source, top)
        self.assertEqual(38, game.state.players["B"].life)
        game = self.replay(game, load=True)
        self.surveil(game, top)
        pending = game.state.pending_decision
        offered = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        surfaced = game.state.cards[top.object_id]
        self.assertEqual({old.ref, surfaced.ref}, set(offered["legal_refs"]))
        before = authoritative_state_hash(game.state)
        for actor, candidate in (("B", surfaced), ("A", foreign), ("A", nonland)):
            self.assertFalse(game.act("pilot:" + actor, {"action_id": "choose", "card": candidate.ref}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.return_choice(game, surfaced)
        arrived = game.state.cards[top.object_id]
        self.assertEqual(("battlefield", "A", True), (arrived.zone, arrived.controller, arrived.tapped))
        self.assertEqual("graveyard", game.state.cards[old.object_id].zone)
        self.replay(game, load=True)

    def test_actual_battle_damage_keeps_top_private_and_can_return_an_older_land(self):
        game, source, top, old, _foreign, _nonland, battle = self.scene(40072902, mode="battle")
        self.await_surveil(game, source, top)
        self.assertEqual(4, game.state.cards[battle.object_id].counters["defense"])
        self.assertEqual(40, game.state.players["B"].life)
        self.surveil(game, top, move=False)
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.return_choice(game, old)
        self.assertEqual("library", game.state.cards[top.object_id].zone)
        self.assertTrue(game.state.cards[old.object_id].tapped)
        self.replay(game, load=True)

    def test_planeswalker_damage_does_not_trigger(self):
        game, source, _top, _old, _foreign, _nonland, walker = self.scene(40072903, mode="planeswalker")
        self.until(game, lambda active: active.state.cards[walker.object_id].counters.get("loyalty") == 2)
        self.assertFalse(any(item.source_object_id == source.object_id for item in game.state.stack))
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_decline_and_no_land_leave_the_prior_surveil_intact(self):
        for available in (False, True):
            with self.subTest(land=available):
                game, source, top, old, _foreign, _nonland, _recipient = self.scene(
                    40072907 + available, top_name="Grizzly Bears", old_land=available,
                )
                self.await_surveil(game, source, top)
                self.surveil(game, top)
                if old is not None:
                    self.return_choice(game, None)
                    self.assertEqual("graveyard", game.state.cards[old.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[top.object_id].zone)
                self.assertFalse(any(item.source_object_id == source.object_id for item in game.state.stack))
                self.replay(game, load=True)

    def test_replaced_surveilled_land_never_becomes_a_graveyard_choice(self):
        game, source, top, _old, _foreign, _nonland, _recipient = self.scene(
            40072912, old_land=False, replacement=True,
        )
        self.await_surveil(game, source, top)
        self.surveil(game, top)
        self.assertEqual("exile", game.state.cards[top.object_id].zone)
        self.assertFalse(any(item.source_object_id == source.object_id for item in game.state.stack))
        self.replay(game, load=True)

    def test_exact_order_and_untargeted_scope_require_the_owning_capability(self):
        source = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        compiled = compile_oracle_card(source, **options)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = compiled.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertEqual(["surveil", "choose_own_graveyard_return"], [effect["op"] for effect in node.effects])
        self.assertEqual(TAPPED_LAND_RETURN_VERSION, node.effects[1]["selection"]["schema_version"])
        for before, after in (("surveil 1", "surveil 2"), ("a land card", "target land card"),
                              ("battle,", "planeswalker,"), ("battlefield tapped.", "battlefield.")):
            with self.subTest(changed=after):
                wrong = replace(source, oracle_text=source.oracle_text.replace(before, after))
                self.assertNotEqual(source.oracle_text, wrong.oracle_text)
                different = compile_oracle_card(wrong, **options)
                self.assertFalse(any(TAPPED_LAND_RETURN_CAPABILITY in n.capability_dependencies
                                     for face in different.faces for n in face.nodes))
        registry_raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in registry_raw["capabilities"] if row["id"] == TAPPED_LAND_RETURN_CAPABILITY)
        row.update(status="blocked", blockers=["Test-only withdrawal of untargeted tapped land choice"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        registry = CapabilityRegistry(registry_raw)
        index = build_index(registry_value=registry_raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(source, **{**options, "capability_registry": registry}).status)

    def test_mutating_the_tapped_entry_policy_is_rejected_by_the_real_movement_owner(self):
        from quorune.rules.graveyard_return_choices import chosen_tapped_land_move

        game, source, top, _old, _foreign, _nonland, _recipient = self.scene(40072917)
        self.await_surveil(game, source, top)
        self.surveil(game, top)

        def untapped(card):
            instruction = chosen_tapped_land_move(card)
            return {**instruction, "set": {**instruction["set"], "battlefield_tapped": False}}

        before = authoritative_state_hash(game.state)
        response = {"action_id": "choose", "card": game.state.cards[top.object_id].ref}
        with patch("quorune.semantic_choices.graveyard_return.chosen_tapped_land_move", side_effect=untapped):
            self.assertFalse(game.act("pilot:A", response).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertTrue(game.act("pilot:A", response).ok)
