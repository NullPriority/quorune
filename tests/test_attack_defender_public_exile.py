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
from quorune.rules.event_player_choice_shapes import attacked_defender_exile_effect
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Bane of Bala Ged"
ATTACK = "trigger.event.normalized_self_attack"
REPLACEMENT = "zone.change.destination_replacement"
FIXTURES = ROOT / "tests" / "fixtures"


class AttackDefenderPublicExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Unsummon", "Island", "Murder"},
            "targeted-controller-untap-cards.json": {"Ajani Vengeant"},
            "attack-defender-cast-prohibition-cards.json": {"Xantid Swarm"},
        }
        fixtures = [FIXTURES / "attack-defender-public-exile-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(piece)
        database = folder / "attacked-defender-exile.sqlite3"
        build_fixture_database(fixtures, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defending-player permanent exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def registered(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        return record, program

    def test_complete_original_binds_exact_untargeted_controlled_permanent_choice(self):
        record, program = self.registered()
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(("creature.attacks", "battlefield"), (program.event, program.active_zone))
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, program.event_condition)
        self.assertIsNone(program.target_schema)
        self.assertTrue({ATTACK, REPLACEMENT, "trigger.placement.apnap", "trigger.event.normalized_zone_change"}
            <= set(program.capability_dependencies))
        self.assertEqual([attacked_defender_exile_effect()], thaw_value(program.effects))

    def test_raw_exile_choices_reject_changed_scope_recipient_count_and_public_predicate(self):
        _, program = self.registered()
        with_exile, = thaw_value(program.effects)
        for changes in (
            {"event": "creature.becomes_blocked"},
            {"active_zone": "graveyard"},
            {"event_condition": None},
            {"effects": [{**with_exile, "players": ["$controller"]}]},
            {"effects": [{**with_exile, "count": 1}]},
            {"effects": [{**with_exile, "count": 2.0}]},
            {"effects": [{**with_exile, "predicate": {**with_exile["predicate"], "include_phased_out": 0}}]},
            {"effects": [{**with_exile, "predicate": {**with_exile["predicate"], "types_all": ["creature"]}}]},
            {"effects": [{key: value for key, value in with_exile.items() if key != "predicate"}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}},
        ):
            with self.subTest(changes=changes):
                binding = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"], binding)

    def ready_source(self, game):
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def attack_choice(self, game, source, receiver, defender="B"):
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, receiver)])
        trigger, = game.state.stack
        self.assertEqual((source.object_id, defender, []),
            (trigger.source_object_id, trigger.context["defending_player"], trigger.targets))

    def pending_choice(self, game, defender):
        choice = self.resolve(game)
        self.assertIsNotNone(choice)
        self.assertEqual(("choice.apnap", [defender]), (choice.kind, choice.actors))
        return game.packet("pilot:" + defender, full=True)["decision"]["ctx"]

    def test_actual_planeswalker_defender_exiles_two_before_blocks_in_one_public_batch(self):
        game = self.session(76155610)
        source = self.ready_source(game)
        walker = self.add(game.engine, "Ajani Vengeant", seat="B")
        walker.counters["loyalty"] = 4
        controlled = self.add(game.engine, "Grizzly Bears", seat="B", ref="defender-controls-foreign-permanent")
        controlled.owner = "D"
        remains = self.add(game.engine, "Grizzly Bears", seat="B", ref="unselected-defender-permanent")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="owned-by-defender-not-controlled")
        foreign.owner = "B"
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="private-exile-witness-spell")
        self.assert_closed(game, walker)
        self.attack_choice(game, source, walker.ref)
        ctx = self.pending_choice(game, "B")
        self.assertEqual(2, ctx["count"])
        self.assertEqual({walker.ref, controlled.ref, remains.ref}, set(ctx["options"]))
        self.assertIsNone(game.packet("pilot:A", full=True).get("decision"))
        command = {"action_id": "choose", "cards": [walker.ref, controlled.ref]}
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for viewer, attempted in (("A", command), ("B", {**command, "cards": [walker.ref, foreign.ref]}),
                                   ("B", {**command, "cards": [walker.ref]})):
            result = game.act("pilot:" + viewer, attempted)
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        with patch.object(game.engine, "move_objects_simultaneously_intent",
                          wraps=game.engine.move_objects_simultaneously_intent) as committed:
            result = game.act("pilot:B", command)
            self.assertTrue(result.ok, result.summary)
        move, = [call.args[0] for call in committed.call_args_list if call.args[0].destination == "exile"]
        self.assertEqual({walker.ref, controlled.ref}, set(move.object_refs))
        self.assertEqual(("battlefield",), move.expected_zones)
        self.assertEqual(ZoneTransitionKind.ORDINARY, move.transition_kind)
        self.assertIn(walker.object_id, game.state.players["B"].zones["exile"])
        self.assertIn(controlled.object_id, game.state.players["D"].zones["exile"])
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        self.assertIn(source.object_id, game.state.combat.attackers)
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.blockers")
        self.assertEqual(["B"], game.state.pending_decision.actors)
        result = game.act("pilot:B", {"action_id": "block", "blocks": {}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_short_choice_exiles_the_only_permanent_after_source_departure(self):
        game = self.session(76155611)
        source = self.ready_source(game)
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        land = self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, bounce)
        self.attack_choice(game, source, "B")
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        ctx = self.pending_choice(game, "B")
        self.assertEqual((1, [land.ref]), (ctx["count"], ctx["options"]))
        accepted = game.act("pilot:B", {"action_id": "choose", "cards": [land.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("exile", game.state.cards[land.object_id].zone)
        self.replay(game, load=True)

    def test_actual_empty_and_departed_defenders_need_no_choice_or_other_seat_exile(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76155612 + departed)
                source = self.ready_source(game)
                foreign = self.add(game.engine, "Grizzly Bears", seat="C")
                if departed:
                    self.add(game.engine, "Island", seat="B")
                self.attack_choice(game, source, "B")
                if departed:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertNotIn("B", game.engine.active_seats)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
                self.assertFalse(game.state.stack)
                self.assertFalse(game.state.game_over)
                self.replay(game, load=True)

    def test_excluding_the_public_exile_producer_blocks_ordinary_original_registration(self):
        with patch("quorune.compiler.event_player_choice_templates.attacked_defender_exile_result", return_value=None):
            original, _ = register_card(self.db, self.registry, NAME)
            other, _ = register_card(self.db, self.registry, "Xantid Swarm")
        self.assertFalse(original["strict_capability_ready"], original)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_attack_and_replacement_withdrawals_block_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (ATTACK, REPLACEMENT):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Defender exile dependency withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, NAME)
                self.assertFalse(original["strict_capability_ready"], original["blockers"])
                other, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
