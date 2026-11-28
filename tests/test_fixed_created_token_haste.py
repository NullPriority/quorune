"""Native channel legality and temporary haste on the committed token set."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_token_production import CREATED_TOKEN_HASTE_CAPABILITY, CREATED_TOKEN_HASTE_FIELD
from quorune.haste import has_effective_haste
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.token_creation_capability_shapes import created_token_haste_closure
from quorune.token_creation_events import publish_token_creations
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SOKENZAN = "Sokenzan, Crucible of Defiance"
PROFILE = "commander_review"


class FixedCreatedTokenHasteTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-created-token-haste.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-created-token-haste-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Created-token haste", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, **kwargs):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, **kwargs)
        self.checkpoint(game)

    def actions(self, game, seat="A"):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def activation_actions(self, game, card):
        return [action for action in self.actions(game) if action["id"].startswith("activate:" + card.ref + ":")]

    def act_ok(self, game, seat, request):
        result = game.act("pilot:" + seat, request)
        self.assertTrue(result.ok, result.summary)

    def tokens(self, game):
        return {card.object_id: card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token}

    def test_loyal_apprentice_checks_commander_on_both_boundaries_and_hastes_only_its_thopter(self):
        for seed, mode in enumerate(("absent", "commander_taken", "source_taken"), 61258201):
            with self.subTest(mode=mode):
                game = self.session(seed)
                source = self.add(game.engine, "Loyal Apprentice")
                other = self.add(game.engine, "Grizzly Bears")
                commander, = (card for card in game.state.cards.values()
                              if card.is_commander and card.owner == "A")
                theft = None
                if mode != "absent":
                    theft = self.add(game.engine, "Turn Against", seat="B", zone="hand")
                    for index in range(5):
                        self.add(game.engine, "Mountain", seat="B", ref=f"loyal-response-mana-{index}")
                    self.start(game, source, theft, mana={"W": 1})
                    self.cast(game, "A", commander)
                    self.assertIsNone(self.resolve(game))
                    commander = game.state.cards[commander.object_id]
                    self.assertEqual("battlefield", commander.zone)
                else:
                    self.start(game, source)

                self.assertTrue(has_effective_haste(game.engine, source))
                self.until(game, lambda session: session.state.step == "beginning_combat")
                self.assertEqual(int(mode != "absent"), self.trigger_count(game, source))
                if mode == "absent":
                    self.assertFalse(self.tokens(game))
                    continue

                target = commander if mode == "commander_taken" else source
                self.cast(game, "B", theft, [target.ref])
                self.resolve_top(game)
                self.assertEqual("B", game.state.cards[target.object_id].controller)
                self.assertEqual(1, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                if mode == "commander_taken":
                    self.assertFalse(self.tokens(game))
                    continue

                token, = self.tokens(game).values()
                self.assertEqual(("Thopter Token", "A", "A"),
                                 (token.printed_name, token.owner, token.controller))
                data = game.engine._effective_card_data(token.object_id)
                self.assertIn("Artifact Creature", data["type_line"])
                self.assertIn("Thopter", data["type_line"])
                self.assertIn("Flying", data["keywords"])
                self.assertEqual((1, 1), (game.engine._numeric_stat(token.object_id, "power"),
                                          game.engine._numeric_stat(token.object_id, "toughness")))
                self.assertTrue(has_effective_haste(game.engine, token))
                self.assertFalse(has_effective_haste(game.engine, other))
                self.attack(game, [(token, "B")])
                turn = game.state.turn_sequence
                self.until(game, lambda session: session.state.turn_sequence > turn)
                self.assertFalse(has_effective_haste(game.engine, game.state.cards[token.object_id]))
                self.assertTrue(has_effective_haste(game.engine, game.state.cards[source.object_id]))
                self.replay(game, load=True)

    def test_loyal_apprentice_requires_commander_and_singular_created_token_authority(self):
        card = self.db.lookup("Loyal Apprentice", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = (node for node in ir.faces[0].nodes if node.event == "step.begin")
        self.assertIn("state_query.commander.designation", trigger.capability_dependencies)
        self.assertEqual(1, trigger.effects[0][CREATED_TOKEN_HASTE_FIELD])
        self.assertEqual(card.oracle_text.splitlines()[1], card.oracle_text[trigger.span.start:trigger.span.end])
        _, registry = register_card(self.db, self.registry, card.name)
        program, = (program for program in registry.programs_for_oracle(card.oracle_id)
                    if program.event == "step.begin")
        dependencies = tuple(item for item in program.capability_dependencies
                             if item != "state_query.commander.designation")
        altered = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:state_query.commander.designation",
                      binding["blockers"])
        malformed = replace(card, oracle_text=card.oracle_text.replace(
            "create a 1/1", "create two 1/1").replace("creature token with", "creature tokens with"))
        mismatch = compile_oracle_card(malformed, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(mismatch.material_residuals)

    def test_actual_channel_grants_haste_after_entry_including_replacement_created_food(self):
        game = self.session(61258101)
        source = self.add(game.engine, SOKENZAN, zone="hand")
        took = self.add(game.engine, "Peregrin Took")
        legend = self.add(game.engine, "Isamaru, Hound of Konda")
        other = self.add(game.engine, "Grizzly Bears")
        later_copy = self.add(game.engine, "Cackling Counterpart", zone="hand")
        self.start(game, source, took, legend, later_copy, mana={"R": 1, "C": 1, "U": 3, "W": 1})
        offer, = self.activation_actions(game, source)
        self.assertEqual({"GENERIC": 1, "R": 1}, offer["cost_summary"]["m"])
        command = {"action_id": offer["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.act_ok(game, "A", command)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(self.tokens(game))

        seen_at_entry = {}

        def record_entry(host, creations, **kwargs):
            for creation in creations:
                for object_id in creation.objects:
                    seen_at_entry[object_id] = has_effective_haste(host, host.state.cards[object_id])
            return publish_token_creations(host, creations, **kwargs)

        with patch("quorune.token_creation.publish_token_creations", side_effect=record_entry):
            self.assertIsNone(self.resolve(game))
        created = self.tokens(game)
        self.assertEqual({"Spirit Token", "Food Token"}, {card.printed_name for card in created.values()})
        self.assertEqual(3, len(created))
        self.assertEqual(dict.fromkeys(created, False), seen_at_entry)
        self.assertTrue(all(has_effective_haste(game.engine, card) for card in created.values()))
        self.assertFalse(has_effective_haste(game.engine, game.state.cards[other.object_id]))
        game = self.replay(game, load=True)

        spirit = next(card for card in created.values() if card.printed_name == "Spirit Token")
        self.cast(game, "A", later_copy, [spirit.ref])
        self.assertIsNone(self.resolve(game))
        copies = [card for identity, card in self.tokens(game).items() if identity not in created]
        self.assertEqual({"Spirit Token", "Food Token"}, {card.printed_name for card in copies})
        self.assertTrue(all(not has_effective_haste(game.engine, card) for card in copies))

        self.attack(game, [(spirit, "B")])
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertTrue(all(not has_effective_haste(game.engine, game.state.cards[oid]) for oid in created))
        self.replay(game, load=True)

    def test_actual_countered_channel_keeps_its_paid_discard_and_creates_no_haste(self):
        game = self.session(61258102)
        source = self.add(game.engine, SOKENZAN, zone="hand")
        stifle = self.add(game.engine, "Stifle", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.start(game, source, stifle, mana={"R": 1, "C": 3})
        offer, = self.activation_actions(game, source)
        self.assertEqual({"GENERIC": 3, "R": 1}, offer["cost_summary"]["m"])
        self.act_ok(game, "A", {"action_id": offer["id"], "pay": "auto"})
        ability, = game.state.stack
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.cast(game, "B", stifle, [ability.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.tokens(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_post_entry_grant_failure_rolls_back_creation_and_can_retry(self):
        game = self.session(61258104)
        source = self.add(game.engine, SOKENZAN, zone="hand")
        self.start(game, source, mana={"R": 1, "C": 3})
        offer, = self.activation_actions(game, source)
        self.act_ok(game, "A", {"action_id": offer["id"], "pay": "auto"})
        command = {"action_id": "pass"}
        with patch("quorune.continuous_effect_state.create_resolution_continuous_effect",
                   side_effect=ValueError("Forced post-entry haste failure")):
            for _ in range(8):
                before = authoritative_state_hash(game.state)
                principal = game.pending_principals()[0]
                outcome = game.act(principal, command)
                if not outcome.ok:
                    self.assertIn("Forced post-entry haste failure", outcome.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    self.assertFalse(self.tokens(game))
                    self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                    break
            else:
                self.fail("The actual pending channel action did not reach the injected grant failure")
        outcome = game.act(principal, command)
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertEqual(2, len(self.tokens(game)))
        self.assertTrue(all(has_effective_haste(game.engine, card) for card in self.tokens(game).values()))
        self.replay(game, load=True)

    def test_actual_land_entry_and_red_mana_remain_independent_from_channel(self):
        game = self.session(61258103)
        source = self.add(game.engine, SOKENZAN, zone="hand")
        self.start(game, source)
        self.assertEqual([], self.activation_actions(game, source))
        land, = (action for action in self.actions(game) if action["id"] == "play-land:" + source.ref)
        self.act_ok(game, "A", {"action_id": land["id"]})
        current = game.state.cards[source.object_id]
        mana, = self.activation_actions(game, current)
        self.act_ok(game, "A", {"action_id": mana["id"], "pay": "auto"})
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.assertFalse(self.tokens(game))
        self.replay(game, load=True)

    def test_whole_original_requires_the_ordered_version_and_preserves_its_mana_sibling(self):
        record = self.db.lookup(SOKENZAN, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Sokenzan, Crucible of Defiance")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        source = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual({"mana_ability", "activated_ability"}, {node.kind for node in source.faces[0].nodes})
        program, = (p for p in semantics.programs_for_oracle(record.oracle_id)
                    if CREATED_TOKEN_HASTE_CAPABILITY in p.capability_dependencies)
        effect, = program.effects
        self.assertEqual(1, effect[CREATED_TOKEN_HASTE_FIELD])
        self.assertNotIn("Haste", effect["characteristics"].get("keywords", ()))
        self.assertEqual("hand", program.active_zone)
        for invalid in (True, 2, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                created_token_haste_closure({**effect, CREATED_TOKEN_HASTE_FIELD: invalid})
        deps = tuple(cap for cap in program.capability_dependencies if cap != CREATED_TOKEN_HASTE_CAPABILITY)
        without_haste = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(without_haste, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CREATED_TOKEN_HASTE_CAPABILITY}", bound["blockers"])
        for tail in (
            "It gains haste until end of turn.",
            "They gain trample until end of turn.",
            "They gain haste until your next turn.",
            "They gain haste until end of turn. Change the name of each card in your hand.",
        ):
            with self.subTest(tail=tail):
                changed = replace(record, oracle_text=record.oracle_text.replace("They gain haste until end of turn.", tail))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.token_templates._created_token_haste_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
