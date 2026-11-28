"""Actual optional entry destruction credits only committed original graveyard outcomes."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.announced_destruction_controller_tokens import (
    AnnouncedDestructionControllerTokens, OPTIONAL_GRAVEYARD_CONTROLLER_TOKEN_CAPABILITY,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class OptionalGraveyardControllerTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-destruction.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-graveyard-controller-token-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Optional destruction", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "G": 2})
        self.checkpoint(game)

    def enter(self, game, source):
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))

    def targeted(self, game, cards):
        self.act(game, "A", {"action_id": "choose", "targets": [card.ref for card in cards]})

    def optional(self, game, choice):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "choice": choice}
        self.reject(game, "B", command)
        self.act(game, "A", command)
        self.assertIsNone(self.resolve(game))

    def zones(self, game, *cards):
        return tuple(game.state.cards[card.object_id].zone for card in cards)

    def elephants(self, game):
        return [card for card in game.state.cards.values() if card.zone == "battlefield"
                and card.is_token and card.printed_name == "Elephant Token"]

    def test_committed_graveyard_credits_the_latest_controller_excluding_exile_and_indestructible(self):
        game = self.session(709062101)
        source = self.add(game.engine, "Terastodon", zone="hand")
        stolen = self.add(game.engine, "Arcane Signet", seat="C")
        replaced = self.add(game.engine, "Sol Ring", seat="B")
        protected = self.add(game.engine, "Darksteel Citadel", seat="D")
        creature = self.add(game.engine, "Grizzly Bears", seat="D")
        theft = self.add(game.engine, "Steal Artifact", seat="B", zone="hand")
        orrery = self.add(game.engine, "Vedalken Orrery", seat="B")
        leyline = self.add(game.engine, "Leyline of the Void", seat="C")
        private = self.add(game.engine, "Forest", seat="D", zone="hand", ref="optional-destroy-private")
        game.state.players["B"].mana_pool.update({"U": 2, "C": 2})
        self.ready(game, source, theft, leyline, protected)
        self.enter(game, source)
        self.reject(game, "A", {"action_id": "choose", "targets": [creature.ref]})
        self.reject(game, "A", {"action_id": "choose",
                                "targets": [stolen.ref, replaced.ref, protected.ref, orrery.ref]})
        self.reject(game, "B", {"action_id": "choose", "targets": [stolen.ref]})
        self.targeted(game, [replaced, stolen, protected])
        self.cast(game, "B", theft, [stolen.ref])
        self.resolve_top(game)
        self.assertEqual(("C", "B"), (game.state.cards[stolen.object_id].owner,
                                     game.state.cards[stolen.object_id].controller))
        game = self.replay(game, load=True)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.optional(game, "apply")
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertEqual(("graveyard", "exile", "battlefield"), self.zones(game, stolen, replaced, protected))
        token, = self.elephants(game)
        self.assertEqual("B", token.controller)
        self.assertEqual((3, 3), tuple(game.engine._numeric_stat(token.object_id, key) for key in ("power", "toughness")))
        self.replay(game, load=True)

    def test_zero_announced_targets_skip_the_choice_and_one_declared_target_can_be_declined(self):
        for declare in (False, True):
            with self.subTest(declare=declare):
                game = self.session(709062110 + declare)
                source = self.add(game.engine, "Terastodon", zone="hand")
                target = self.add(game.engine, "Sol Ring", seat="B")
                self.ready(game, source)
                self.enter(game, source)
                self.targeted(game, [target] if declare else [])
                item, = game.state.stack
                self.assertEqual("triggered_ability", item.kind)
                self.assertEqual([target.ref] if declare else [], item.targets)
                if declare:
                    self.optional(game, "decline")
                else:
                    self.assertIsNone(self.resolve(game))
                self.assertEqual(("battlefield",), self.zones(game, target))
                self.assertEqual([], self.elephants(game))
                self.replay(game, load=True)

    def test_originally_nonempty_declared_group_revalidates_before_optional_resolution(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                game = self.session(709062130 + partial)
                source = self.add(game.engine, "Terastodon", zone="hand")
                ring = self.add(game.engine, "Sol Ring", seat="B")
                forest = self.add(game.engine, "Forest", seat="C", ref="remaining-noncreature")
                claim = self.add(game.engine, "Nature's Claim", seat="B", zone="hand")
                game.state.players["B"].mana_pool["G"] = 1
                self.ready(game, source, claim)
                self.enter(game, source)
                self.targeted(game, [ring, forest] if partial else [ring])
                self.cast(game, "B", claim, [ring.ref])
                self.resolve_top(game)
                self.assertEqual(("graveyard", "battlefield"), self.zones(game, ring, forest))
                if partial:
                    self.optional(game, "apply")
                    token, = self.elephants(game)
                    self.assertEqual("C", token.controller)
                    self.assertEqual(("graveyard",), self.zones(game, forest))
                else:
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual([], self.elephants(game))
                    self.assertEqual(("battlefield",), self.zones(game, forest))
                self.replay(game, load=True)

    def test_complete_original_and_independent_graveyard_receipt_authority(self):
        card = self.db.lookup("Terastodon", fuzzy=False)
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
            self.assertEqual("permanent.enter.self", node.event)
            self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"],
                              "types_none": ["creature"], "up_to": 3}, node.target_schema)
            wrapper, = node.effects
            self.assertEqual("offer_optional_effect", wrapper["op"])
            instruction, = wrapper["effects"]
            self.assertTrue(AnnouncedDestructionControllerTokens.from_dict(instruction["followup"]).requires_graveyard)
        report, semantics = register_card(self.db, self.registry, "Terastodon")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        cap = OPTIONAL_GRAVEYARD_CONTROLLER_TOKEN_CAPABILITY
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id) if cap in row.capability_dependencies)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        altered = replace(program, target_schema={**program.target_schema, "up_to": 2})
        self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("up to three", "up to four"), ("noncreature permanents", "creatures"),
                              ("into a graveyard", "into exile"), ("creature token.", "creature token. Draw a card.")):
            copy = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(copy, capability_registry=self.registry).status)
