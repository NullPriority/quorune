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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
REGENERATION = "permanent.regeneration.self_activation"
RANDOM = "zone.discard.fixed_random"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = ("Duskworker", "Corrupt Official")


class SourceBecomesBlockedResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Unsummon", "Island", "Murder"},
            "activation-restriction-material-cards.json": {"Cloudshift"},
        }
        parts = [FIXTURES / "source-becomes-blocked-result-cards.json"]
        for filename, names in support.items():
            selected = json.loads((FIXTURES / filename).read_bytes())
            selected["cards"] = [row for row in selected["cards"] if row["name"] in names]
            assert {row["name"] for row in selected["cards"]} == names
            ids = {row["oracle_id"] for row in selected["cards"]}
            selected["rulings"] = [row for row in selected.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / filename
            part.write_text(json.dumps(selected), encoding="utf-8")
            parts.append(part)
        db_path = folder / "blocked-attacker-results.sqlite3"
        build_fixture_database(parts, db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Blocked attacker results", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_bind_the_sealed_occurrence_and_keep_the_activated_siblings(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                bound, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                programs = semantics.programs_for_oracle(card.oracle_id)
                trigger, = (program for program in programs if program.event == "creature.becomes_blocked")
                activation, = (program for program in programs if program.event == "activate")
                self.assertEqual("battlefield", trigger.active_zone)
                self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, trigger.event_condition)
                self.assertIsNone(trigger.target_schema)
                if name == "Duskworker":
                    self.assertEqual([{"op": "regenerate", "card": "$source.zone_object"}], thaw_value(trigger.effects))
                    self.assertIn(REGENERATION, trigger.capability_dependencies)
                    self.assertEqual("{3}", activation.cost_schema["text"])
                    self.assertEqual(1, activation.effects[0]["power"])
                else:
                    self.assertEqual([{"op": "discard_random_cards", "actor": "$controller",
                        "players": ["$context.defending_player"], "count": 1}], thaw_value(trigger.effects))
                    self.assertIn(RANDOM, trigger.capability_dependencies)
                    self.assertEqual("{2}{B}", activation.cost_schema["text"])
                    self.assertEqual("regenerate", activation.effects[0]["op"])

    def test_random_discard_cannot_bind_a_different_event_scope_or_count(self):
        name = "Corrupt Official"
        card = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        trigger, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "creature.becomes_blocked")
        first, = thaw_value(trigger.effects)
        for changes in (
            {"event": "creature.becomes_blocked_by"},
            {"event": "creature.attacks"},
            {"event_condition": None},
            {"active_zone": "graveyard"},
            {"effects": [{**first, "count": 2}]},
            {"effects": [{**first, "players": ["$context.defending_player", "$controller"]}]},
        ):
            with self.subTest(changes=changes):
                changed = replace(trigger, **changes)
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

    def blocked(self, name, seed, *, reply=None, seat="B"):
        game = self.session(seed)
        source = self.add(game.engine, name)
        source.acquired_control_turn_count = -1
        blockers = [self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"blocked-witness-{i}") for i in range(2)]
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="other-private-hand")
        response = None
        if reply is not None:
            response = self.add(game.engine, reply, seat="A" if reply == "Cloudshift" else seat, zone="hand")
            mana = "Generic Bound Plains" if reply == "Cloudshift" else "Island"
            self.add(game.engine, mana, seat=response.controller)
            self.assert_closed(game, response)
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, seat)])
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {blocker.ref: source.ref for blocker in blockers}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        items = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(1, len(items))
        self.assertEqual("creature.becomes_blocked", items[0].context["event"])
        self.assertEqual(seat, items[0].context["defending_player"])
        return game, source, response, private

    def test_actual_duskworker_group_block_creates_one_shield_and_lethal_damage_consumes_it(self):
        game, source, _, _ = self.blocked("Duskworker", 76155410)
        self.assertEqual(0, game.state.cards[source.object_id].regeneration_shields)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertIn(source.object_id, game.state.combat.attackers)
        self.assertEqual(1, current.regeneration_shields)
        self.assertEqual(0, current.marked_damage)

        self.until(game, lambda s: s.state.pending_decision is not None
                   and s.state.pending_decision.kind == "combat.damage")
        blocker_id = game.state.combat.blockers[source.object_id][0]
        result = game.act("pilot:A", {"a": "dmg", "assignments": [
            {"source": source.ref, "target": game.state.cards[blocker_id].ref, "amount": 2},
        ]})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        current = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", True, 0, 0, None),
            (current.zone, current.tapped, current.marked_damage, current.regeneration_shields, current.attacking))
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_duskworker_cannot_regenerate_its_new_incarnation_after_blink(self):
        game, source, blink, _ = self.blocked("Duskworker", 76155411, reply="Cloudshift")
        original = source.logical_object_id
        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertNotEqual(original, current.logical_object_id)
        self.assertNotIn(source.object_id, game.state.combat.attackers)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[source.object_id].regeneration_shields)
        self.replay(game, load=True)

    def test_actual_official_discards_one_from_the_defender_after_its_source_leaves(self):
        game, source, bounce, private = self.blocked("Corrupt Official", 76155412, reply="Unsummon")
        for object_id in game.state.players["B"].zones["hand"]:
            ref = game.state.cards[object_id].ref
            for viewer in "ACD":
                self.assertNotIn(ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        before = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        grave_before = set(game.state.players["B"].zones["graveyard"])
        self.assertIsNone(self.resolve(game))
        discarded = before["B"].difference(game.state.players["B"].zones["hand"])
        self.assertEqual(1, len(discarded))
        self.assertEqual(discarded, set(game.state.players["B"].zones["graveyard"]) - grave_before)
        for viewer in "ACD":
            self.assertEqual(before[viewer], set(game.state.players[viewer].zones["hand"]))
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_departed_defender_leaves_the_random_discard_empty(self):
        game, _, _, _ = self.blocked("Corrupt Official", 76155413, seat="C")
        self.priority(game, "C")
        result = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn("C", game.engine.active_seats)
        before = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABD"}
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.game_over)
        self.assertFalse(game.state.stack)
        self.assertEqual(before, {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABD"})
        self.replay(game, load=True)

    def test_excluding_the_random_owner_blocks_the_compound_original(self):
        with patch("quorune.random_discard.random_discard_node_capabilities", return_value=()):
            result, _ = register_card(self.db, self.registry, "Corrupt Official")
            other, _ = register_card(self.db, self.registry, "Duskworker")
        self.assertFalse(result["strict_capability_ready"], result)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_result_owner_withdrawals_block_only_dependent_complete_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap, blocked in ((REGENERATION, ORIGINALS), (RANDOM, ("Corrupt Official",))):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Blocked-attacker consequence withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"], (name, ready["blockers"]))
                other, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
