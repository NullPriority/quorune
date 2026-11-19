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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_history import TargetPlayerHistoryKind
from quorune.target_source import TARGETING_SOURCE_CONTEXT
from quorune.turn_history import current_turn_history_events
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
RIX = "Rix Maadi Guildmage"
WICKED = "Wicked Akuba"
LOSS_AUTHORITY = "activation.condition.current_turn_events"
DAMAGE_AUTHORITY = "damage.source.stack_current_or_lki"
FIXTURES = ROOT / "tests" / "fixtures"


class CurrentTurnPlayerHistoryTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Murder"},
            "activation-restriction-material-cards.json": {"Cloudshift", "Forest", "Swamp"},
            "mana-source-counter-payment-cards.json": {"Mana Confluence"},
            "activation-public-state-cards.json": {"Wastes"},
            "compiled-reviewed-spell-cards.json": {"Revitalize"},
            "myriad-composition-cards.json": {"Fog"},
        }
        parts = [FIXTURES / "current-turn-player-history-target-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "current-turn-player-history.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current-turn player history targets", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def ordinary_loss_program(self, name):
        record = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        loss, = [program for program in programs if any(effect.get("op") == "lose_life" for effect in program.effects)]
        return record, loss, programs

    def test_complete_originals_keep_exact_distinct_history_shapes_and_the_blocking_sibling(self):
        for name, kind, authority in (
            (RIX, TargetPlayerHistoryKind.LOST_LIFE, LOSS_AUTHORITY),
            (WICKED, TargetPlayerHistoryKind.DAMAGED_BY_SOURCE, DAMAGE_AUTHORITY),
        ):
            with self.subTest(name=name):
                record, program, siblings = self.ordinary_loss_program(name)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(("activate", "battlefield"), (program.event, program.active_zone))
                self.assertEqual({"zones": ["player"], "categories": ["player"], "count": 1,
                    "player_relation": "any", "player_history": {"kind": kind.value}}, thaw_value(program.target_schema))
                self.assertEqual([{"op": "lose_life", "player": "$target.0", "amount": 1}],
                    thaw_value(program.effects))
                self.assertTrue({authority, "life.change.effect", "target.revalidate_resolution"}
                    <= set(program.capability_dependencies))
                self.assertEqual(1, program.cost_schema["mana"]["B"])
                if name == RIX:
                    blocking, = [sibling for sibling in siblings if sibling is not program]
                    self.assertEqual("blocking", blocking.target_schema["combat_state"])
                    self.assertEqual((-1, -1), (blocking.effects[0]["power"], blocking.effects[0]["toughness"]))
                    self.assertEqual(1, program.cost_schema["mana"]["R"])

    def test_raw_history_shape_cannot_drop_the_qualifier_change_origin_or_widen_the_target(self):
        _, program, _ = self.ordinary_loss_program(RIX)
        schema = thaw_value(program.target_schema)
        (effect,) = thaw_value(program.effects)
        for changes in (
            {"target_schema": {k: value for k, value in schema.items() if k != "player_history"}},
            {"target_schema": {**schema, "player_history": {"kind": "unsupported"}}},
            {"target_schema": {**schema, "player_history": {"kind": "damaged_by_source_this_turn"}}},
            {"target_schema": {**schema, "count": 2}},
            {"target_schema": {**schema, "player_relation": "opponent"}},
            {"effects": [{**effect, "amount": 2}]},
            {"event": "resolve", "handlers": ()},
            {"active_zone": "graveyard"},
        ):
            with self.subTest(changes=changes):
                ready = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(ready["strict"], ready["blockers"])

    def offered(self, game, source):
        self.priority(game, "A")
        return [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                if row["id"].startswith("activate:" + source.ref + ":")]

    def activation(self, game, source, target):
        action, = self.offered(game, source)
        return action, {"action_id": action["id"], "targets": [target], "pay": "auto"}

    def test_actual_life_payment_qualifies_after_gain_and_departed_target_cancels_only_its_result(self):
        game = self.session(76155810)
        source = self.add(game.engine, RIX)
        restored = self.add(game.engine, "Revitalize", seat="B", zone="hand")
        confluence = self.add(game.engine, "Mana Confluence", seat="B")
        self.add(game.engine, "Wastes", seat="B")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="private-history-witness")
        for card in (source, restored, confluence):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2, "R": 2})
        self.checkpoint(game)
        self.assertEqual([], self.offered(game, source))

        initial = game.state.players["B"].life
        self.cast(game, "B", restored)
        self.assertTrue(game.state.cards[confluence.object_id].tapped)
        self.assertEqual(initial - 1, game.state.players["B"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial + 2, game.state.players["B"].life)
        paid_history = current_turn_history_events(game.state.turn_history,
            turn_sequence=game.state.turn_sequence, kind="player_lost_life")
        self.assertTrue(any(event.target == "B" and event.amount == 1 for event in paid_history))
        action, command = self.activation(game, source, "B")
        self.assertEqual(["B"], action["target_schema"]["groups"][0]["legal_refs"])

        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": ["C"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial + 1, game.state.players["B"].life)

        _, again = self.activation(game, source, "B")
        paid = game.act("pilot:A", again)
        self.assertTrue(paid.ok, paid.summary)
        self.priority(game, "B")
        conceded = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        after_departure = game.state.players["B"].life
        self.assertIsNone(self.resolve(game))
        self.assertEqual(after_departure, game.state.players["B"].life)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        for viewer in "AC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def combat_world(self, seed, *, prevent=False):
        game = self.session(seed)
        source = self.add(game.engine, WICKED)
        other = self.add(game.engine, "Grizzly Bears", ref="other-committed-damage-source")
        for attacker in (source, other):
            attacker.acquired_control_turn_count = -1
        self.add(game.engine, "Swamp")
        self.add(game.engine, "Generic Bound Plains")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        fog = None
        if prevent:
            self.add(game.engine, "Forest", seat="C")
            fog = self.add(game.engine, "Fog", seat="C", zone="hand")
            self.assert_closed(game, fog)
        for card in (source, blink):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.assertEqual([], self.offered(game, source))
        self.attack(game, [(source, "B"), (other, "C")])
        if fog is not None:
            self.cast(game, "C", fog)
            self.assertIsNone(self.resolve(game))
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        return game, source, blink

    def test_actual_damage_target_rechecks_the_original_source_after_blink(self):
        game, source, blink = self.combat_world(76155811)
        original = source.logical_object_id
        self.assertLess(game.state.players["B"].life, 40)
        self.assertLess(game.state.players["C"].life, 40)
        action, command = self.activation(game, source, "B")
        self.assertEqual(["B"], action["target_schema"]["groups"][0]["legal_refs"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {**command, "targets": ["C"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(original, game.state.stack[-1].context[TARGETING_SOURCE_CONTEXT]["logical_object_id"])

        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual([], self.offered(game, game.state.cards[source.object_id]))
        game = self.replay(game, load=True)
        previous = {seat: game.state.players[seat].life for seat in "BC"}
        self.assertIsNone(self.resolve(game))
        self.assertEqual(previous["B"] - 1, game.state.players["B"].life)
        self.assertEqual(previous["C"], game.state.players["C"].life)
        self.replay(game, load=True)

    def test_actual_prevented_damage_creates_no_player_offer(self):
        game, source, _ = self.combat_world(76155812, prevent=True)
        self.assertEqual([40, 40], [game.state.players[seat].life for seat in "BC"])
        self.assertEqual([], self.offered(game, source))
        self.replay(game, load=True)

    def test_omitting_the_matcher_removes_the_actual_recorded_target_offer(self):
        game, source, _ = self.combat_world(76155813)
        self.assertEqual(1, len(self.offered(game, source)))
        turn = game.state.turn_sequence
        with patch("quorune.selection.targeting.target_player_history_matches", return_value=False):
            passed = game.act("pilot:A", {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
            self.assertEqual([], self.offered(game, source))
        self.assertEqual(turn, game.state.turn_sequence)

    def test_verified_history_withdrawals_block_only_the_dependent_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap, blocked in ((LOSS_AUTHORITY, RIX), (DAMAGE_AUTHORITY, WICKED)):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Player target history prerequisite withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in (RIX, WICKED):
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name != blocked, ready["strict_capability_ready"], ready["blockers"])
                other, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
