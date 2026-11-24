"""Pinned CR 508.5a/608.2h: one defender, current creatures, one damage batch."""

from copy import deepcopy
from dataclasses import replace
import json
from itertools import permutations
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_damage_set_model import DefendingCreatureDamageGroup, FixedDamageSetError, FixedDamageSetSpec
from quorune.fixed_damage_set import snapshot_fixed_damage_set
from quorune.oracle_ir import compile_oracle_card
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.defending_creature_damage import (
    DEFENDING_CREATURE_DAMAGE_CAPABILITY as CAPABILITY,
    defending_creature_damage_effect,
)
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
from quorune.semantic_runtime.fixed_damage_set_handlers import FixedDamageSetHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"
ORIGINALS = FIXTURES / "defending-creature-damage-cards.json"
SOURCE_ATTACK = "trigger.event.normalized_self_attack"
NAMES = ("Swathcutter Giant", "Ronin Cliffrider", "Gouged Zealot", "Lord of Shatterskull Pass")


class DefendingCreatureDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [card for card in basic["cards"] if card["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "basic.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "defending-creature-damage.sqlite3"
        build_fixture_database([base, ORIGINALS], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defending-player damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_originals_compile_complete_siblings_and_exact_untargeted_attack_result(self):
        for name in NAMES:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                attacks = [program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "creature.attacks"]
                self.assertEqual(1, len(attacks))
                program, = attacks
                self.assertIsNone(program.target_schema)
                self.assertIn(CAPABILITY, program.capability_dependencies)
                self.assertIn(SOURCE_ATTACK, program.capability_dependencies)
                effect, = program.effects
                if name == "Ronin Cliffrider":
                    self.assertEqual("offer_optional_effect", effect["op"])
                    effect, = effect["effects"]
                self.assertEqual(defending_creature_damage_effect(6 if name == "Lord of Shatterskull Pass" else 1), effect)

    def test_versioned_group_rejects_partial_and_coerced_payloads_without_changing_old_groups(self):
        group = DefendingCreatureDamageGroup("B")
        payload = FixedDamageSetSpec((group,)).to_dict()
        self.assertEqual(payload, FixedDamageSetSpec.from_dict(payload).to_dict())
        for changes in ({"schema_version": True}, {"schema_version": 2}, {"player": ""},
                {"controller": "B"}, {"query": {"types_all": ["creature"]}}):
            with self.subTest(changes=changes), self.assertRaises(FixedDamageSetError):
                FixedDamageSetSpec.from_dict({"groups": [{**group.to_dict(), **changes}]})
        for legacy in ({"kind": "players", "relation": "opponents"},
                {"kind": "permanents", "controller_relation": "opponents", "target_controller": None,
                 "query": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).canonical_dict()}):
            self.assertEqual({"groups": [legacy]}, FixedDamageSetSpec.from_dict({"groups": [legacy]}).to_dict())
        context = ReadOnlyHandlerContext.from_sequences(actor="A", default_reason="attack",
            seats="ABCD", active_seats="ACD", apnap_order="ACD")
        handler = FixedDamageSetHandler()
        effect = defending_creature_damage_effect(1)
        effect["source"] = "original-source"
        for player in ("foreign", "$context.defending_player"):
            with self.subTest(player=player), self.assertRaises(SemanticNodeError):
                handler.lower({**effect, "groups": [DefendingCreatureDamageGroup(player).to_dict()]}, context)
        intent, = handler.lower({**effect, "groups": [group.to_dict()]}, context).intents
        self.assertEqual("B", intent.spec.groups[0].player)

    def test_raw_program_requires_exact_source_attack_and_cannot_omit_the_damage_owner(self):
        card = self.db.lookup(NAMES[0], fuzzy=False)
        _, semantics = register_card(self.db, self.registry, card.name)
        program, = [p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "creature.attacks"]
        effect, = program.effects
        for changed in (replace(program, event="permanent.enter.self"), replace(program, active_zone="graveyard"),
                replace(program, event_condition=None), replace(program, target_schema={"count": 1}),
                replace(program, effects=[{**effect, "groups": [{**effect["groups"][0], "player": "$controller"}]}]),
                replace(program, effects=[{**effect, "groups": [{**effect["groups"][0], "schema_version": True}]}]),
                replace(program, effects=[{**effect, "then": [{"op": "draw", "player": "$controller", "count": 1}]}])):
            with self.subTest(changed=changed.to_dict()):
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
        deps = [dep for dep in program.capability_dependencies if dep != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])
        for text in (
            "Whenever another creature attacks, it deals 1 damage to each creature defending player controls.",
            "Whenever this creature attacks, it deals X damage to each creature defending player controls.",
            "Whenever this creature attacks, it deals 1 damage to each artifact defending player controls.",
            "Whenever this creature attacks, it deals 1 damage to each creature each opponent controls.",
            "Whenever this creature attacks, it deals 1 damage to each creature defending player controls. Rewrite history.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                    capability_registry=self.registry, capability_profile=PROFILE).status)

    def test_recipient_permutations_preserve_current_controller_types_and_known_absent_defender(self):
        def row(ref, controller, *, types=("creature",), phased=False, zone="battlefield"):
            return ObjectQueryResult(object_id="physical:" + ref, logical_object_id="logical:" + ref,
                ref=ref, printed_name="Recipient", owner="A", controller=controller, zone=zone,
                types=types, phased_out=phased)

        rows = (row("stolen", "B"), row("foreign", "C"), row("artifact", "B", types=("artifact",)),
            row("phased", "B", phased=True), row("hand", "B", zone="hand"), row("second", "B"))
        spec = FixedDamageSetSpec((DefendingCreatureDamageGroup("B"),))
        for order in permutations(rows[:3]):
            query = SimpleNamespace(fixed_damage_active_seats=lambda: tuple("ABCD"),
                fixed_damage_apnap_order=lambda: tuple("ABCD"), fixed_damage_object_rows=lambda actor: (*order, *rows[3:]))
            result = snapshot_fixed_damage_set(query, actor="A", spec=spec)
            self.assertEqual(("second", "stolen"), tuple(recipient.ref for recipient in result.recipients))
        query.fixed_damage_active_seats = lambda: tuple("ACD")
        query.fixed_damage_apnap_order = lambda: tuple("ACD")
        self.assertEqual((), snapshot_fixed_damage_set(query, actor="A", spec=spec).recipients)

    def board(self, name=NAMES[0], *, seed=50851201):
        game = self.session(seed)
        source = self.add(game.engine, name, zone="hand")
        self.add(game.engine, "Fervor")
        own = self.add(game.engine, "Grizzly Bears", ref="own-combat-lane")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="defender-first")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="defender-second")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="other-defender-creature")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="private-damage-secret")
        return game, source, own, first, second, other, artifact, private

    def pay_source(self, game, source, mana):
        self.assert_closed(game, source)
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        action = self.action(game, "A", source)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def assert_damage(self, game, damaged, unaffected, amount=1):
        for card in damaged:
            self.assertEqual(amount, game.state.cards[card.object_id].marked_damage,
                "Retained defender's current creatures must receive the printed damage")
        for card in unaffected:
            self.assertEqual(0, game.state.cards[card.object_id].marked_damage)
        aggregate, = [event for event in game.state.events if event.code == "effect.damage.fixed_set"]
        self.assertEqual(len(damaged), aggregate.details["recipient_count"])
        self.assertEqual(len(damaged), len(aggregate.details["damage_events"]))
        for seat in "ABCD":
            self.assertEqual(40, game.state.players[seat].life)

    def assert_private_replay(self, game, private):
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.object_id, packet)
            if not any(card.object_id != private.object_id and card.printed_name == private.printed_name
                    and card.zone in {"battlefield", "graveyard", "exile", "stack", "command"}
                    for card in game.state.cards.values()):
                self.assertNotIn(private.printed_name, packet)
        return self.replay(game, load=True)

    def test_actual_swathcutter_retains_one_defender_and_reads_current_creatures_after_source_departure(self):
        game, source, own, first, second, other, artifact, private = self.board()
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        flash = self.add(game.engine, "Village Bell-Ringer", seat="B", zone="hand")
        for name, ref in (("Island", "bounce-island"), ("Plains", "flash-white"),
                ("Plains", "flash-generic-one"), ("Plains", "flash-generic-two")):
            self.add(game.engine, name, seat="B", ref=ref)
        self.pay_source(game, source, {"C": 4, "R": 1, "W": 1})
        self.assertIn("vigilance", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.attack(game, [(source, "B"), (own, "C")])
        trigger, = game.state.stack
        self.assertEqual(("B", [], source.object_id),
            (trigger.context["defending_player"], trigger.targets, trigger.source_object_id))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.cast(game, "B", flash)
        self.resolve_top(game)
        self.resolve_top(game)
        game = self.assert_private_replay(game, private)
        self.assertIsNone(self.resolve(game))
        self.assert_damage(game, (first, second, flash), (own, other, artifact))
        self.assert_private_replay(game, private)

    def test_actual_ronin_optional_acceptance_and_decline_survive_pending_reload(self):
        for accept in (False, True):
            with self.subTest(accept=accept):
                game, source, own, first, second, other, artifact, private = self.board(NAMES[1], seed=50851210 + accept)
                self.pay_source(game, source, {"C": 3, "R": 2})
                self.attack(game, [(source, "B"), (own, "C")])
                pending = self.resolve(game)
                self.assertEqual("semantic.choice", pending.kind)
                game = self.assert_private_replay(game, private)
                command = {"action_id": "choose", "choice": "apply" if accept else "decline"}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                selected = game.act("pilot:A", command)
                self.assertTrue(selected.ok, selected.summary)
                self.assertIsNone(self.resolve(game))
                if accept:
                    self.assert_damage(game, (first, second), (own, other, artifact))
                else:
                    self.assertFalse([event for event in game.state.events if event.code == "effect.damage.fixed_set"])
                    self.assertTrue(all(game.state.cards[card.object_id].marked_damage == 0
                        for card in (first, second, own, other)))
                self.assert_private_replay(game, private)

    def test_actual_gouged_zealot_checks_own_graveyard_types_at_attack_and_resolution(self):
        for mode in ("qualified", "foreign", "exiled"):
            with self.subTest(mode=mode):
                game, source, own, first, second, other, artifact, private = self.board(NAMES[2], seed=50851220 + len(mode))
                for index, name in enumerate(("Grizzly Bears", "Sol Ring", "Shock", "Act of Treason")):
                    self.add(game.engine, name, seat="B" if mode == "foreign" else "A",
                        zone="graveyard", ref="delirium-type-" + str(index))
                crypt = self.add(game.engine, "Tormod's Crypt", seat="B")
                self.pay_source(game, source, {"C": 3, "R": 1})
                self.assertIn("reach", game.engine._combat_keywords(game.state.cards[source.object_id]))
                self.attack(game, [(source, "B"), (own, "C")])
                self.assertEqual(int(mode != "foreign"), self.trigger_count(game, source))
                if mode == "foreign":
                    self.assertEqual(0, game.state.cards[first.object_id].marked_damage)
                    self.assert_private_replay(game, private)
                    continue
                if mode == "exiled":
                    self.priority(game, "B")
                    action = next(row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                        if row["id"].startswith("activate:" + crypt.ref + ":"))
                    activated = game.act("pilot:B", {"action_id": action["id"], "targets": ["A"], "pay": "auto"})
                    self.assertTrue(activated.ok, activated.summary)
                    self.assertEqual("graveyard", game.state.cards[crypt.object_id].zone)
                    self.resolve_top(game)
                    self.assertEqual([], game.state.players["A"].zones["graveyard"])
                game = self.assert_private_replay(game, private)
                self.assertIsNone(self.resolve(game))
                if mode == "qualified":
                    self.assert_damage(game, (first, second), (own, other, artifact))
                else:
                    self.assertEqual(0, game.state.cards[first.object_id].marked_damage)
                    self.assertEqual(0, game.state.cards[second.object_id].marked_damage)
                    self.assertFalse([event for event in game.state.events if event.code == "effect.damage.fixed_set"])
                self.assert_private_replay(game, private)

    def test_actual_lord_pays_level_up_and_keeps_queued_damage_after_level_counter_removal(self):
        for levels in (5, 6):
            with self.subTest(levels=levels):
                game, source, own, first, second, other, artifact, private = self.board(NAMES[3], seed=50851230 + levels)
                hexmage = self.add(game.engine, "Vampire Hexmage", seat="B")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 3 + levels, "R": 1 + levels})
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(3, game.engine._numeric_stat(source.object_id, "power"))
                for level in range(1, levels + 1):
                    self.priority(game, "A")
                    action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                        if row["id"].startswith("activate:" + source.ref + ":"))
                    activated = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                    self.assertTrue(activated.ok, activated.summary)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(level, game.state.cards[source.object_id].counters.get("level", 0))
                    self.assertEqual(6, game.engine._numeric_stat(source.object_id, "power"))
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                self.attack(game, [(source, "B"), (own, "C")])
                self.assertEqual(int(levels == 6), self.trigger_count(game, source))
                if levels == 5:
                    self.assert_private_replay(game, private)
                    continue
                self.priority(game, "B")
                action = next(row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"].startswith("activate:" + hexmage.ref + ":"))
                activated = game.act("pilot:B", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"})
                self.assertTrue(activated.ok, activated.summary)
                self.assertEqual("graveyard", game.state.cards[hexmage.object_id].zone)
                self.resolve_top(game)
                self.assertEqual(0, game.state.cards[source.object_id].counters.get("level", 0))
                self.assertEqual(3, game.engine._numeric_stat(source.object_id, "power"))
                game = self.assert_private_replay(game, private)
                self.assertIsNone(self.resolve(game))
                self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (first, second)))
                self.assertEqual(0, game.state.cards[other.object_id].marked_damage)
                self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
                aggregate, = [event for event in game.state.events if event.code == "effect.damage.fixed_set"]
                self.assertEqual(2, aggregate.details["recipient_count"])
                self.assertEqual(6, aggregate.details["assigned_amount"])
                self.assert_private_replay(game, private)

    def test_actual_planeswalker_attack_retains_defender_while_departed_source_uses_new_controller_lifelink(self):
        game, source, own, first, second, other, artifact, private = self.board(seed=50851240)
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        aura = self.add(game.engine, "Lifelink", zone="hand")
        control = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Vedalken Orrery", seat="B")
        for name, ref in (("Island", "control-bounce-island"), ("Mountain", "control-red"),
                ("Mountain", "control-generic-one"), ("Mountain", "control-generic-two")):
            self.add(game.engine, name, seat="B", ref=ref)
        self.assert_closed(game, source)
        self.assert_closed(game, aura)
        self.prepare(game, mana={"C": 4, "R": 1, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"W": 1})
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", aura, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertIn("lifelink", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.attack(game, [(source, walker.ref), (own, "C")])
        trigger, = game.state.stack
        self.assertEqual(("A", "B"), (trigger.controller, trigger.context["defending_player"]))
        self.cast(game, "B", control, [source.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.assert_private_replay(game, private)
        self.assertIsNone(self.resolve(game))
        for card in (first, second):
            self.assertEqual(1, game.state.cards[card.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[other.object_id].marked_damage)
        self.assertEqual(3, game.state.cards[walker.object_id].counters["loyalty"])
        self.assertEqual({"A": 40, "B": 42, "C": 40, "D": 40},
            {seat: player.life for seat, player in game.state.players.items()})
        self.assert_private_replay(game, private)

    def test_wrong_combat_lane_mutation_is_killed_by_the_actual_original(self):
        original = DefendingCreatureDamageGroup.affected_set

        def redirect(group):
            return original(replace(group, player="C"))

        with patch.object(DefendingCreatureDamageGroup, "affected_set", redirect):
            with self.assertRaisesRegex(AssertionError, "printed damage"):
                self.test_actual_swathcutter_retains_one_defender_and_reads_current_creatures_after_source_departure()

    def test_verified_withdrawals_preserve_independent_attack_and_damage_cards(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for missing in (CAPABILITY, "damage.batch.fixed_set", SOURCE_ATTACK):
            with self.subTest(missing=missing):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == missing).update(
                    status="blocked", blockers=["Defending damage owner withdrawn"])
                registry = CapabilityRegistry(rows)
                evidence = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
                self.assertIn("status:" + missing + ":blocked", registry.closure((missing,), profile=PROFILE).blockers)
                for name in (NAMES[0], "Grizzly Bears", "Shock"):
                    admitted, _ = register_card(self.db, registry, name)
                    self.assertEqual(name != NAMES[0], admitted["strict_capability_ready"], (name, admitted["blockers"]))
