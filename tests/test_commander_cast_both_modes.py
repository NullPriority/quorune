"""Native cast-time commander authority, live opponent count, and impulse access."""

from copy import deepcopy
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
from quorune.impulse_access_model import ImpulseAccessDuration
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.modal_selection import (
    COMMANDER_CAST_BOTH_MODES_CAPABILITY,
    COMMANDER_CAST_MODE_LIMIT_FIELD,
)
from scripts.build_test_database import build_fixture_database


SPELL = "Jeska's Will"
PROFILE = "commander_review"
FIXTURE = ROOT / "tests/fixtures/commander-cast-both-modes-cards.json"
CAST_COST = {"C": 2, "R": 1}


class CommanderCastBothModesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        source = Path(cls.temporary.name) / "commander-cast-both.sqlite3"
        build_fixture_database([FIXTURE], source)
        cls.db = CardDatabase(source)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Commander mode limits",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def compile(self, record=None, *, registry=None):
        return compile_oracle_card(
            record or self.db.lookup(SPELL, fuzzy=False),
            capability_registry=registry or self.registry,
            capability_profile=PROFILE,
        )

    def ready(self, game, source):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=CAST_COST.copy())
        game.state.players["A"].land_plays_remaining = 1
        self.checkpoint(game)

    def actions(self, game, seat="A"):
        self.priority(game, seat)
        return game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]

    def offer(self, game, card):
        return self.action(game, "A", game.state.cards[card.object_id])

    def submit(self, game, offer, modes, targets=(), *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {
            "action_id": offer["id"], "modes": list(modes), "targets": list(targets),
            "pay": "manual", "payment": CAST_COST.copy(),
        })
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    @staticmethod
    def commander(game, owner):
        return next(card for card in game.state.cards.values() if card.owner == owner and card.is_commander)

    @staticmethod
    def card(game, saved):
        return game.state.cards[saved.object_id]

    def library_card(self, game, name, ref):
        card = self.add(game.engine, name, zone="library", ref=ref)
        card.known_to = []
        card.revealed_to = []
        return card

    def assert_hidden(self, game, card, viewers):
        current = self.card(game, card)
        for viewer in viewers:
            contents = json.dumps(game.packet(f"pilot:{viewer}", full=True))
            self.assertNotIn(current.ref, contents)
            self.assertNotIn(current.object_id, contents)

    def test_exact_original_span_and_independent_mode_leaves(self):
        card = self.db.lookup(SPELL, fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(("spell_ability", "stack", "resolve"), (node.kind, node.active_zone, node.event))
        self.assertEqual(
            {"schema_version": 1, "condition": "controls_commander"},
            node.target_schema[COMMANDER_CAST_MODE_LIMIT_FIELD],
        )
        self.assertTrue({
            COMMANDER_CAST_BOTH_MODES_CAPABILITY, "choice.modal.fixed_nonrepeating",
            "mana.production.public_quantity", "quantity_expression.target_player_hand_count",
            "target.revalidate_resolution", "zone.impulse_access.fixed",
        } <= set(node.capability_dependencies))
        first, second = node.target_schema["modes"].values()
        mana, = first["effects"]
        self.assertEqual(("mana", "$controller", "R"), (mana["op"], mana["player"], mana["color"]))
        self.assertEqual("target_player_hand_count", mana["amount"]["origin"])
        group, = first["groups"]
        self.assertEqual(("opponent", 1), (group["player_relation"], group["count"]))
        self.assertEqual([], second["groups"])
        self.assertEqual(
            [{"op": "fixed_impulse_access", "player": "$controller", "count": 3,
              "duration": ImpulseAccessDuration.END_OF_TURN.value}],
            second["effects"],
        )

    def test_pinned_unrepresented_surfaces_and_forged_mode_authority_fail_closed(self):
        original = self.db.lookup(SPELL, fuzzy=False)
        text = original.oracle_text
        for changed in (
            text.replace("control a commander", "own a commander"),
            text.replace("as you cast this spell", "as this spell resolves"),
            text.replace("target opponent's", "each opponent's"),
            text.replace("play them this turn", "play them without paying their mana costs"),
            text.replace("Exile the top three cards of your library. You may play them this turn.",
                         "Destroy target creature."),
        ):
            with self.subTest(text=changed):
                self.assertNotEqual("exact", self.compile(replace(original, oracle_text=changed)).status)

        game = self.session(5764101)
        source = self.add(game.engine, SPELL, zone="hand")
        self.assert_closed(game, source)
        program, = (candidate for candidate in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if COMMANDER_CAST_BOTH_MODES_CAPABILITY in candidate.capability_dependencies)
        schema = deepcopy(program.target_schema)
        schema[COMMANDER_CAST_MODE_LIMIT_FIELD]["schema_version"] = True
        wrong_schema = replace(program, target_schema=schema)
        result = bind_semantic_program_runtime(wrong_schema, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        broken_branch = deepcopy(program.target_schema)
        broken_branch["modes"]["mode_1"]["effects"][0]["amount"] = 2
        wrong_amount = replace(program, target_schema=broken_branch)
        result = bind_semantic_program_runtime(wrong_amount, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        remaining = tuple(cap for cap in program.capability_dependencies if cap != COMMANDER_CAST_BOTH_MODES_CAPABILITY)
        missing = replace(program, capability_dependencies=remaining,
                          capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + COMMANDER_CAST_BOTH_MODES_CAPABILITY, result["blockers"])

    def test_no_commander_and_phased_commander_offer_single_mode_and_reject_malformed_choices(self):
        for phased in (False, True):
            with self.subTest(phased=phased):
                game = self.session(5764102 + phased)
                source = self.add(game.engine, SPELL, zone="hand")
                if phased:
                    commander = self.commander(game, "A")
                    game.engine.move_card(commander.object_id, "battlefield", controller="A", log=False)
                    self.card(game, commander).phased_out = True
                for object_id in tuple(game.state.players["C"].zones["hand"]):
                    game.engine.move_card(object_id, "outside", log=False)
                retained = self.library_card(game, "Forest", "commander-mode-still-hidden")
                self.ready(game, source)
                offer = self.offer(game, source)
                self.assertEqual(1, offer["target_schema"]["max_modes"])
                self.assertEqual(["mode_1", "mode_2"], offer["target_schema"]["legal_modes"])
                for modes, targets in (
                    ([], []), (["mode_1", "mode_2"], ["C"]), (["mode_1", "mode_1"], ["C"]),
                    (["mode_1"], ["A"]), (["mode_1"], []), (["mode_2"], ["C"]),
                ):
                    self.submit(game, offer, modes, targets, accepted=False)
                self.submit(game, offer, ["mode_1"], ["C"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(0, game.state.players["A"].mana_pool["R"])
                self.assertEqual("library", self.card(game, retained).zone)
                self.assertEqual("graveyard", self.card(game, source).zone)
                self.replay(game, load=True)

    def test_actual_both_modes_lock_through_commanders_control_change_and_read_current_hand_then_allow_play(self):
        game = self.session(5764104)
        source = self.add(game.engine, SPELL, zone="hand")
        response = self.add(game.engine, "Act of Aggression", seat="B", zone="hand")
        self.assert_closed(game, response)
        commander = self.commander(game, "B")
        game.engine.move_card(commander.object_id, "battlefield", controller="A", log=False)
        self.assertEqual(("B", "A"), (self.card(game, commander).owner, self.card(game, commander).controller))
        untouched = self.library_card(game, "Plains", "commander-mode-fourth-hidden")
        remaining = self.library_card(game, "Mountain", "commander-mode-unplayed")
        playable = self.library_card(game, "Raging Goblin", "commander-mode-exiled-spell")
        land = self.library_card(game, "Forest", "commander-mode-exiled-land")
        secret = self.add(game.engine, "Forest", seat="B", zone="hand", ref="commander-mode-target-private")
        self.ready(game, source)
        game.state.players["B"].mana_pool["R"] = 5
        self.checkpoint(game)

        announced_count = len(game.state.players["B"].zones["hand"])
        offer = self.offer(game, source)
        self.assertEqual(2, offer["target_schema"]["max_modes"])
        for card in (remaining, playable, land, untouched):
            self.assert_hidden(game, card, "ABCD")
        self.assert_hidden(game, secret, "ACD")
        self.submit(game, offer, ["mode_2", "mode_1"], ["B"])
        self.assertEqual(["mode_1", "mode_2"], game.state.stack[-1].modes)
        self.priority(game, "B")
        option = self.action(game, "B", self.card(game, response))
        mana_only, = (item for item in option["cost_options"] if not item.get("additional_life"))
        cast_response = game.act("pilot:B", {
            "action_id": option["id"], "cost_option": mana_only["id"], "pay": "auto",
            "targets": [self.card(game, commander).ref],
        })
        self.assertTrue(cast_response.ok, cast_response.summary)
        game = self.replay(game, load=True)
        self.resolve_top(game)

        self.assertEqual("B", self.card(game, commander).controller)
        self.assertEqual(announced_count - 1, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(["mode_1", "mode_2"], game.state.stack[-1].modes)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(announced_count - 1, game.state.players["A"].mana_pool["R"])
        for at_top in (land, playable, remaining):
            public = self.card(game, at_top)
            self.assertEqual(("exile", False), (public.zone, public.face_down))
            self.assertIn("temporary_play_permission", public.annotations)
            for viewer in "ABCD":
                contents = json.dumps(game.packet(f"pilot:{viewer}", full=True))
                self.assertIn(public.ref, contents)
                self.assertNotIn(public.object_id, contents)
        self.assert_hidden(game, untouched, "ABCD")
        self.assert_hidden(game, secret, "ACD")

        game = self.replay(game, load=True)
        land_id = "play-land:" + self.card(game, land).ref
        spell_id = "cast:" + self.card(game, playable).ref
        self.assertTrue({land_id, spell_id} <= {action["id"] for action in self.actions(game)})
        played = game.act("pilot:A", {"action_id": land_id})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", self.card(game, land).zone)
        self.assertNotIn("play-land:" + self.card(game, remaining).ref,
                         {action["id"] for action in self.actions(game)})
        self.cast(game, "A", self.card(game, playable))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.card(game, playable).zone)
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        still_exiled = self.card(game, remaining)
        self.assertEqual("exile", still_exiled.zone)
        self.assertNotIn("temporary_play_permission", still_exiled.annotations)
        self.replay(game, load=True)

    def test_sole_departing_target_fizzles_both_modes_but_exile_only_needs_no_target(self):
        for modes in (["mode_1", "mode_2"], ["mode_2"]):
            with self.subTest(modes=modes):
                game = self.session(5764106 + len(modes))
                source = self.add(game.engine, SPELL, zone="hand")
                commander = self.commander(game, "A")
                game.engine.move_card(commander.object_id, "battlefield", controller="A", log=False)
                for object_id in tuple(game.state.players["A"].zones["library"]):
                    game.engine.move_card(object_id, "outside", log=False)
                top = self.library_card(game, "Forest", "commander-mode-short-top")
                beneath = self.library_card(game, "Mountain", "commander-mode-short-new-top")
                self.ready(game, source)
                targets = ["B"] if "mode_1" in modes else []
                self.submit(game, self.offer(game, source), modes, targets)
                game = self.replay(game, load=True)
                self.assertEqual(["A"], game.state.pending_decision.actors)
                self.priority(game, "B")
                self.assertEqual(["B"], game.state.pending_decision.actors)
                self.assertEqual(1, len(game.state.stack))
                result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                self.assertTrue(result.ok, result.summary)
                self.assertFalse(game.state.players["B"].in_game)
                self.assertIsNone(self.resolve(game))
                wanted_zone = "library" if targets else "exile"
                self.assertEqual([wanted_zone, wanted_zone], [self.card(game, card).zone for card in (beneath, top)])
                self.assertEqual(0, game.state.players["A"].mana_pool["R"])
                self.assertEqual("graveyard", self.card(game, source).zone)
                self.replay(game, load=True)

    def test_commanders_mode_capability_withdrawal_preserves_plain_fixed_modes(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        values = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        affected = next(row for row in values["capabilities"] if row["id"] == COMMANDER_CAST_BOTH_MODES_CAPABILITY)
        affected.update(status="blocked", blockers=["commander modal owner withdrawn"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        registry = CapabilityRegistry(values)
        index = build_index(registry_value=values, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = self.db.lookup(SPELL, fuzzy=False)
        denied = self.compile(card, registry=registry)
        self.assertNotEqual("exact", denied.status)
        self.assertTrue(any(COMMANDER_CAST_BOTH_MODES_CAPABILITY in blocker
                            for residual in denied.material_residuals for blocker in residual.blockers))
        ordinary = "Choose one or both \u2014\n" + card.oracle_text.split("\n", 1)[1]
        self.assertEqual("exact", self.compile(replace(card, oracle_text=ordinary), registry=registry).status)

    def test_unconditional_both_modes_mutant_is_killed_by_a_native_offer(self):
        def maximum_modes(seed):
            game = self.session(seed)
            source = self.add(game.engine, SPELL, zone="hand")
            self.ready(game, source)
            return self.offer(game, source)["target_schema"]["max_modes"]

        def witness(seed):
            self.assertEqual(1, maximum_modes(seed), "No commander permits exactly one mode")

        witness(5764110)
        with patch("quorune.public_alternative_costs.fixed_public_alternative_condition_met", return_value=True):
            with self.assertRaisesRegex(AssertionError, "No commander permits exactly one mode"):
                witness(5764111)
