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
from quorune.compiler.target_player_sequences import TARGET_PLAYER_SEQUENCE_CAPABILITY, TARGET_PLAYER_SEQUENCE_MECHANIC
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Thoughtcutter Agent"
HAND = "choice.target_hand.fixed_inspection_move"
FIXTURES = ROOT / "tests" / "fixtures"


class TargetedLifeLossPublicHandObservationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        parts = [FIXTURES / "targeted-life-loss-public-hand-observation-cards.json"]
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Island", "Murder"},
            "activation-restriction-material-cards.json": {"Lightning Bolt"},
            "flashback-cards.json": {"Think Twice"},
        }
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "fixed-loss-public-hand.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Ordered loss and hand observation", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_original_binds_the_paid_activation_and_one_target_for_both_results(self):
        card, program = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(("activate", "battlefield"), (program.event, program.active_zone))
        cost = program.cost_schema
        self.assertEqual((1, 1, True), (cost["mana"]["U"], cost["mana"]["B"], cost["tap_source"]))
        self.assertEqual({"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1},
            thaw_value(program.target_schema))
        self.assertEqual([
            {"op": "lose_life", "player": "$target.0", "amount": 1},
            {"op": "fixed_hand_inspection", "player": "$controller", "target": "$target.0",
             "inspection": "reveal", "action": "observe", "predicate": None},
        ], thaw_value(program.effects))
        self.assertTrue({HAND, TARGET_PLAYER_SEQUENCE_CAPABILITY, "target.revalidate_resolution"}
            <= set(program.capability_dependencies))

    def test_raw_pair_rejects_reversed_or_changed_amount_visibility_and_target_scope(self):
        _, program = self.original_program()
        loss, reveal = thaw_value(program.effects)
        schema = thaw_value(program.target_schema)
        for changes in (
            {"effects": [reveal, loss]},
            {"effects": [{**loss, "amount": 2}, reveal]},
            {"effects": [{**loss, "amount": True}, reveal]},
            {"effects": [loss, {**reveal, "inspection": "look"}]},
            {"effects": [loss, {**reveal, "target": "$target.1"}]},
            {"effects": [loss, {**reveal, "action": "discard"}]},
            {"target_schema": {**schema, "player_relation": "opponent"}},
            {"target_schema": {**schema, "count": 2}},
            {"coverage": tuple(value for value in program.coverage if value != TARGET_PLAYER_SEQUENCE_MECHANIC)},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound["blockers"])

    def prepared_source(self, game):
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def activation(self, game, source, target):
        row, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                if row["id"].startswith("activate:" + source.ref + ":"))
        return {"action_id": row["id"], "targets": [target], "pay": "auto"}

    def test_actual_price_and_life_loss_precede_brief_public_hand_reveal_and_keep_new_draw_private(self):
        game = self.session(76155710)
        source = self.prepared_source(game)
        other = self.add(game.engine, "Grizzly Bears", seat="C")
        witness = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="disclosed-original-hand-card")
        draw = self.add(game.engine, "Think Twice", seat="B", zone="hand")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="separate-private-spell")
        self.add(game.engine, "Island", seat="B")
        self.add(game.engine, "Generic Bound Plains", seat="B")
        self.assert_closed(game, draw)
        self.prepare(game, mana={"U": 1, "B": 1})
        self.checkpoint(game)
        command = self.activation(game, source, "B")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [other.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(witness.ref, json.dumps(game.packet("pilot:C", full=True)))
        initial_reveal = list(witness.revealed_to)
        initial_life = game.state.players["B"].life
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual(initial_life, game.state.players["B"].life)
        self.assertNotIn(witness.ref, json.dumps(game.packet("pilot:C", full=True)))
        game = self.replay(game, load=True)
        observed = []
        inspect = game.engine.inspect_hand_intent

        def observe(intent):
            observed.append((intent.player, game.state.players[intent.player].life, intent.public, intent.keep_revealed))
            return inspect(intent)

        with patch.object(game.engine, "inspect_hand_intent", side_effect=observe):
            self.assertIsNone(self.resolve(game))
        self.assertEqual([("B", initial_life - 1, True, False)], observed)
        known = game.state.cards[witness.object_id]
        self.assertEqual(set("ABCD"), set(known.known_to))
        self.assertEqual(initial_reveal, known.revealed_to)
        for viewer in "ACD":
            self.assertIn(witness.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.cast(game, "B", draw)
        pre_draw = set(game.state.players["B"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        drawn, = set(game.state.players["B"].zones["hand"]) - pre_draw
        new_card = game.state.cards[drawn]
        self.assertEqual(["B"], new_card.known_to)
        for viewer in "ACD":
            self.assertNotIn(new_card.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_lethal_loss_observes_hand_before_the_player_leaves(self):
        game = self.session(76155711)
        source = self.prepared_source(game)
        witness = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="lethal-witness-hand-card")
        game.state.players["B"].life = 1
        self.prepare(game, mana={"U": 1, "B": 1})
        self.checkpoint(game)
        paid = game.act("pilot:A", self.activation(game, source, "B"))
        self.assertTrue(paid.ok, paid.summary)
        ordered = []
        inspect = game.engine.inspect_hand_intent

        def observe(intent):
            player = game.state.players[intent.player]
            ordered.append((player.life, player.in_game, witness.ref in intent.refs))
            return inspect(intent)

        with patch.object(game.engine, "inspect_hand_intent", side_effect=observe):
            self.assertIsNone(self.resolve(game))
        self.assertEqual([(0, True, True)], ordered)
        self.assertFalse(game.state.players["B"].in_game)
        self.replay(game, load=True)

    def test_actual_departed_target_prevents_both_results_and_keeps_the_paid_price(self):
        game = self.session(76155712)
        source = self.prepared_source(game)
        private = self.add(game.engine, "Murder", seat="C", zone="hand", ref="unselected-private-hand-card")
        self.prepare(game, mana={"U": 1, "B": 1})
        self.checkpoint(game)
        paid = game.act("pilot:A", self.activation(game, source, "B"))
        self.assertTrue(paid.ok, paid.summary)
        self.priority(game, "B")
        departed = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        life = game.state.players["B"].life
        with patch.object(game.engine, "inspect_hand_intent", wraps=game.engine.inspect_hand_intent) as inspect:
            self.assertIsNone(self.resolve(game))
        inspect.assert_not_called()
        self.assertEqual(life, game.state.players["B"].life)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        for viewer in "AD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_leaving_the_exact_public_observation_uncompiled_blocks_the_original(self):
        from quorune.compiler.target_player_sequences import _leaf

        def omit_reveal(text):
            return None if text.casefold() == "target player reveals their hand." else _leaf(text)

        with patch("quorune.compiler.target_player_sequences._leaf", side_effect=omit_reveal):
            original, _ = register_card(self.db, self.registry, NAME)
            separate, _ = register_card(self.db, self.registry, "Think Twice")
        self.assertFalse(original["strict_capability_ready"], original)
        self.assertTrue(separate["strict_capability_ready"], separate["blockers"])

    def test_verified_withdrawal_requires_hand_observation_and_shared_player_target(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (HAND, TARGET_PLAYER_SEQUENCE_CAPABILITY):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Public hand observation prerequisite withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, NAME)
                self.assertFalse(original["strict_capability_ready"], original["blockers"])
                other, _ = register_card(self.db, registry, "Think Twice")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
