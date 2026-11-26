"""Native witnesses for announced support and a temporary combat-damage grant."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


ARENA = "Generic Training Arena"
BODY = "Generic Bound Body"
BASIC = "Generic Bound Plains"
PROFILE = "commander_review"
SUPPORT = "When this artifact enters, support X."
GRANT = (
    '{3}, {T}: Until end of turn, target creature gains "Whenever this '
    'creature deals combat damage to a player, draw a card for each kind '
    'of counter on it" and it can\'t be blocked this turn.'
)
CAPABILITIES = (
    "counter.producer.support_entry_x",
    "quantity_expression.source_counter_kind_count",
    "continuous.ability.temporary_quoted_combat_draw_unblockable",
)


class BlitzballStadiumCompilerTests(unittest.TestCase):
    def compile(self, text, registry, *, mana_cost="{X}{U}", type_line="Artifact"):
        record = CardRecord(
            oracle_id="fixture:training-arena-compiler",
            name="Generic Training Compiler",
            mana_cost=mana_cost,
            mana_value=1,
            type_line=type_line,
            oracle_text=text,
            power=None,
            toughness=None,
            loyalty=None,
            defense=None,
            colors=("U",),
            color_identity=("U",),
            keywords=(),
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={"commander": "legal"},
            faces=(),
            raw={},
        )
        return compile_oracle_card(record, capability_registry=registry, capability_profile=PROFILE)

    def test_closed_text_rejects_changed_damage_and_counter_predicates(self):
        registry = load_default_capability_registry()
        text = SUPPORT + "\n" + GRANT
        admitted = self.compile(text, registry)
        self.assertEqual("exact", admitted.status, admitted.material_residuals)
        for old, new in (
            ("deals combat damage", "deals damage"),
            ("to a player", "to a planeswalker"),
            ("each kind of counter on it", "each counter on a permanent you control"),
        ):
            with self.subTest(new=new):
                self.assertNotEqual("exact", self.compile(text.replace(old, new), registry).status)

    def test_each_new_authority_is_required_and_unrelated_effects_remain_exact(self):
        original = json.loads((ROOT / "quorune" / "rules" / "capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform" / "capability-evidence-declarations.json")
        known_test_ids = set(discover_tests(ROOT).values())
        for omitted in CAPABILITIES:
            with self.subTest(omitted=omitted):
                raw = deepcopy(original)
                row = next(cap for cap in raw["capabilities"] if cap["id"] == omitted)
                row.update(status="blocked", blockers=["Withheld for the authority regression witness"])
                registry = CapabilityRegistry(raw)
                index = build_index(
                    registry_value=raw,
                    declaration_source=declarations,
                    discovered_test_ids=known_test_ids,
                )
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", self.compile(SUPPORT + "\n" + GRANT, registry).status)
                old_support = self.compile("Support 2.", registry, mana_cost="{1}{U}", type_line="Sorcery")
                old_unblockable = self.compile(
                    "Target creature can't be blocked this turn.", registry, mana_cost="{U}", type_line="Instant"
                )
                self.assertEqual("exact", old_support.status, old_support.material_residuals)
                self.assertEqual("exact", old_unblockable.status, old_unblockable.material_residuals)


class BlitzballStadiumNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "training-arena.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "blitzball-stadium-witness-cards.json",
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Training arena witnesses", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry(BASIC, 30),
        ], [commander])

    def begin(self, game, source, *, active="A", mana=None):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active=active, mana=mana)
        self.checkpoint(game)

    def submit(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activation(self, game, seat, source):
        self.priority(game, seat)
        live = game.state.cards[source.object_id]
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row["id"].startswith(f"activate:{live.ref}:"))
        return action

    def cast_arena(self, game, source, x):
        payment = {"U": 1}
        if x:
            payment["C"] = x
        action = self.action(game, "A", source)
        command = {"action_id": action["id"], "x": x, "pay": "manual", "payment": payment}
        self.reject(game, "D", command)
        if x:
            self.reject(game, "A", {**command, "payment": {"U": 1, "C": x - 1}})
        self.submit(game, "A", command)
        spell = game.state.stack[-1]
        self.assertEqual((source.object_id, x, []), (spell.card_object_id, spell.x_value, spell.targets))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def test_announced_support_uses_paid_x_and_revalidates_targets(self):
        game = self.session(70983101)
        arena = self.add(game.engine, ARENA, zone="hand")
        own = self.add(game.engine, BODY, ref="training-own-eligible")
        enemy = self.add(game.engine, BODY, seat="B", ref="training-foreign-eligible")
        third = self.add(game.engine, BODY, seat="D", ref="training-third-eligible")
        land = self.add(game.engine, BASIC, ref="training-unqualified")
        hidden = self.add(game.engine, BODY, seat="C", zone="hand", ref="training-private-card")
        bounce = self.add(game.engine, "Generic Training Recall", seat="B", zone="hand")
        game.state.players["B"].mana_pool["U"] = 1
        self.begin(game, arena, mana={"C": 2, "U": 1})
        self.cast_arena(game, arena, 2)

        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[arena.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, arena))
        decision = game.packet("pilot:A", full=True)["decision"]
        schema = decision["ctx"]["target_schema"]
        self.assertEqual({own.ref, enemy.ref, third.ref}, set(schema["legal_refs"]))
        group, = schema["groups"]
        self.assertEqual((0, 2), (group["min"], group["max"]))
        self.assertNotIn(hidden.ref, json.dumps(decision))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])

        choose = {"action_id": "choose", "targets": [own.ref, enemy.ref]}
        self.reject(game, "B", choose)
        self.reject(game, "A", {**choose, "targets": [own.ref, enemy.ref, third.ref]})
        self.reject(game, "A", {**choose, "targets": [own.ref, own.ref]})
        self.reject(game, "A", {**choose, "targets": [land.ref]})

        game = self.replay(game, load=True)
        self.submit(game, "A", choose)
        before_incarnation = game.state.cards[enemy.object_id].logical_object_id
        self.cast(game, "B", bounce, [enemy.ref])
        self.resolve_top(game)
        live_enemy = game.state.cards[enemy.object_id]
        self.assertEqual("hand", live_enemy.zone)
        self.assertNotEqual(before_incarnation, live_enemy.logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1}, game.state.cards[own.object_id].counters)
        self.assertEqual({}, game.state.cards[enemy.object_id].counters)
        self.assertEqual({}, game.state.cards[third.object_id].counters)
        self.replay(game)

    def test_zero_support_resolves_without_counter_placement(self):
        game = self.session(70983102)
        arena = self.add(game.engine, ARENA, zone="hand")
        target = self.add(game.engine, BODY)
        self.begin(game, arena, mana={"U": 1})
        self.cast_arena(game, arena, 0)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, arena))
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[arena.object_id].zone)
        self.assertEqual({}, game.state.cards[target.object_id].counters)
        self.replay(game)

    def test_paid_cross_controller_grant_draws_per_live_counter_kind_and_replays(self):
        game = self.session(70983103)
        arena = self.add(game.engine, ARENA)
        target = self.add(game.engine, "Generic Training Shooter", seat="B", ref="training-recipient")
        other = self.add(game.engine, BODY, seat="B", ref="training-other-attacker")
        blocker = self.add(game.engine, BODY, seat="C", ref="training-defender")
        land = self.add(game.engine, BASIC, ref="training-ineligible-land")
        add_counter = self.add(game.engine, "Generic Bound Counter Sequence", seat="B", zone="hand")
        target.acquired_control_turn_count = other.acquired_control_turn_count = -1
        target.counters.update({"+1/+1": 2, "charge": 4})
        other.counters["oil"] = 3
        arena.counters["lore"] = 1
        game.state.players["B"].mana_pool["R"] = 1
        green_land = self.add(game.engine, "Generic Training Grove", seat="B")
        self.begin(game, arena, active="B", mana={"C": 3})
        offer = self.activation(game, "A", arena)
        legal = set(offer["target_schema"]["legal_refs"])
        self.assertTrue({target.ref, other.ref, blocker.ref}.issubset(legal))
        self.assertNotIn(land.ref, legal)

        activate = {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"}
        self.reject(game, "D", activate)
        self.reject(game, "A", {**activate, "targets": [land.ref]})
        self.reject(game, "A", {**activate, "pay": "manual", "payment": {"C": 2}})
        self.assertFalse(game.state.cards[arena.object_id].tapped)
        self.assertEqual(3, game.state.players["A"].mana_pool["C"])
        self.submit(game, "A", activate)
        self.assertTrue(game.state.cards[arena.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.reject(game, "A", activate)
        self.assertIsNone(self.resolve(game))

        life = game.state.players["C"].life
        hands_before_noncombat = self.hand_sizes(game)
        shot = self.activation(game, "B", target)
        self.submit(game, "B", {"action_id": shot["id"], "targets": ["C"], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 1, game.state.players["C"].life)
        self.assertEqual(hands_before_noncombat, self.hand_sizes(game))

        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attack = {"action_id": "attack", "attackers": {target.ref: "C", other.ref: "C"}}
        self.reject(game, "A", attack)
        self.submit(game, "B", attack)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal_blocks"][blocker.ref]
        self.assertIn(other.ref, options)
        self.assertNotIn(target.ref, options)
        self.reject(game, "C", {"action_id": "block", "blocks": {blocker.ref: target.ref}})
        self.submit(game, "C", {"action_id": "block", "blocks": {}})

        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")
        trigger, = game.state.stack
        self.assertEqual(("triggered_ability", target.object_id, "B"),
                         (trigger.kind, trigger.source_object_id, trigger.controller))
        self.assertEqual(life - 7, game.state.players["C"].life)
        game = self.replay(game, load=True)
        self.cast(game, "B", add_counter, [target.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[green_land.object_id].tapped)
        self.assertEqual({"+1/+1": 2, "charge": 4, "flying": 1}, game.state.cards[target.object_id].counters)
        before = self.hand_sizes(game)
        old = set(game.state.players["B"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**before, "B": before["B"] + 3}, self.hand_sizes(game))
        drawn = set(game.state.players["B"].zones["hand"]) - old
        self.assertEqual(3, len(drawn))
        for seat in "ACD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for object_id in drawn:
                self.assertNotIn(game.state.cards[object_id].ref, packet)
        self.replay(game)

    def test_granted_creature_combat_damage_to_planeswalker_does_not_draw(self):
        game = self.session(70983104)
        arena = self.add(game.engine, ARENA)
        target = self.add(game.engine, BODY, ref="training-walker-attacker")
        target.acquired_control_turn_count = -1
        target.counters.update({"+1/+1": 2, "charge": 1})
        walker = self.add(game.engine, "Generic Training Observer", seat="C")
        walker.counters["loyalty"] = 5
        self.begin(game, arena, mana={"C": 3})
        action = self.activation(game, "A", arena)
        self.submit(game, "A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        before = self.hand_sizes(game)
        self.attack(game, [(target, walker.ref)])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(1, game.state.cards[walker.object_id].counters.get("loyalty"))
        self.assertEqual(before, self.hand_sizes(game))
        self.assertEqual(0, self.trigger_count(game, target))
        self.replay(game)

    def test_pending_granted_combat_draw_uses_last_known_counter_kinds(self):
        game = self.session(70983105)
        arena = self.add(game.engine, ARENA)
        target = self.add(game.engine, BODY, ref="training-departing-recipient")
        target.acquired_control_turn_count = -1
        target.counters.update({"+1/+1": 2, "charge": 4})
        bounce = self.add(game.engine, "Generic Training Recall", zone="hand")
        self.add(game.engine, "Generic Training Springs")
        self.begin(game, arena, mana={"C": 3})
        action = self.activation(game, "A", arena)
        self.submit(game, "A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertIsNone(self.resolve(game))

        self.attack(game, [(target, "C")])
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual(1, self.trigger_count(game, target))
        old_incarnation = game.state.cards[target.object_id].logical_object_id
        self.cast(game, "A", bounce, [target.ref])
        self.resolve_top(game)
        current = game.state.cards[target.object_id]
        self.assertEqual(("hand", {}), (current.zone, current.counters))
        self.assertNotEqual(old_incarnation, current.logical_object_id)

        game = self.replay(game, load=True)
        before = self.hand_sizes(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**before, "A": before["A"] + 2}, self.hand_sizes(game))
        self.replay(game)

    @staticmethod
    def hand_sizes(game):
        return {seat: len(player.zones["hand"]) for seat, player in game.state.players.items()}
