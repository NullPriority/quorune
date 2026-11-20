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
from quorune.damage import DamageError
from quorune.damage_prevention_permission import (
    TEMPORARY_DAMAGE_PROHIBITION_FIELD, UNPREVENTABLE_CAPABILITY,
    ResolvedDamagePreventionProhibition, temporary_damage_prevention_prohibition,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_gain_permission import (
    LIFE_GAIN_PROHIBITION_CAPABILITY, ResolvedLifeGainProhibition,
    temporary_life_gain_effect, temporary_opponent_life_gain_effect,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.damage_permissions import collect_damage_prevention_permissions
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAMES = ("Call In a Professional", "Flaring Pain", "Skullcrack")
FIXTURES = ROOT / "tests" / "fixtures"
MODELS = (ResolvedDamagePreventionProhibition, ResolvedLifeGainProhibition)


class TurnDamagePreventionProhibitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Plains", "Unsummon"},
            "ability-source-counter-cards.json": {"Shock"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "announced-x-public-damage-set-cards.json": {"Mending Hands"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "combat-prevention-source-scopes-cards.json": {"Flaring Pain"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "turn-damage-prevention-prohibition-cards.json": {"Boon of Safety", "Call In a Professional", "Skullcrack"},
        }
        parts = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "turn-damage-prevention-prohibition.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Prohibitions through cleanup", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return card, {p.event: p for p in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_originals_keep_ordered_effects_and_flashback(self):
        for name in NAMES:
            with self.subTest(name=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                spell = programs["resolve"]
                self.assertIn(UNPREVENTABLE_CAPABILITY, spell.capability_dependencies)
                if name == "Flaring Pain":
                    self.assertEqual({"resolve", "cast.cost"}, programs.keys())
                    self.assertEqual([temporary_damage_prevention_prohibition()], thaw_value(spell.effects))
                    self.assertIsNone(spell.target_schema)
                    self.assertIn("casting.flashback.fixed_mana", programs["cast.cost"].capability_dependencies)
                    continue
                self.assertEqual({"resolve"}, programs.keys())
                self.assertIn(LIFE_GAIN_PROHIBITION_CAPABILITY, spell.capability_dependencies)
                self.assertEqual([
                    temporary_life_gain_effect("any"), temporary_damage_prevention_prohibition(),
                    {"op": "damage", "source": "$source", "target": "$target.0", "amount": 3},
                ], thaw_value(spell.effects))
                expected = "damageable" if name == "Call In a Professional" else "player_or_planeswalker"
                self.assertEqual((expected, 1), (spell.target_schema["predicate"], spell.target_schema["count"]))

    def test_malformed_order_scope_target_and_journal_versions_fail_closed(self):
        _, flaring = self.original_programs("Flaring Pain")
        _, skullcrack = self.original_programs("Skullcrack")
        standalone, compound = flaring["resolve"], skullcrack["resolve"]
        prohibition, = thaw_value(standalone.effects)
        life, prevention, damage = thaw_value(compound.effects)
        schema = thaw_value(compound.target_schema)
        for program, changes in (
            (standalone, {"effects": [{**prohibition, TEMPORARY_DAMAGE_PROHIBITION_FIELD: True}]}),
            (standalone, {"effects": [{**prohibition, "source": "$source"}]}),
            (standalone, {"active_zone": "graveyard"}),
            (compound, {"effects": [prevention, life, damage]}),
            (compound, {"effects": [temporary_opponent_life_gain_effect(), prevention, damage]}),
            (compound, {"effects": [life, prevention, {**damage, "amount": True}]}),
            (compound, {"effects": [life, prevention, damage, life]}),
            (compound, {"target_schema": {**schema, "count": 2}}),
        ):
            with self.subTest(changes=changes):
                outcome = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(outcome["strict"], outcome)

        v1 = {"effect_kind": "temporary_life_gain_prohibition", "schema_version": 1,
              "effect_id": "LG1", "source_id": "ST1", "timestamp": 12,
              "controller": "A", "denied_players": ["B", "C"], "duration": "until_end_of_turn"}
        earlier = ResolvedLifeGainProhibition.from_dict(v1)
        self.assertEqual(v1, earlier.to_dict())
        global_rule = ResolvedLifeGainProhibition("LG2", "ST2", 13, "A", ("A", "B", "C"),
                                                  affected_player_relation="any")
        self.assertEqual(global_rule, ResolvedLifeGainProhibition.from_dict(global_rule.to_dict()))
        with self.assertRaises(ValueError):
            ResolvedLifeGainProhibition.from_dict({**v1, "schema_version": 2})
        damage_rule = ResolvedDamagePreventionProhibition("DP1", "ST1", 13)
        self.assertEqual(damage_rule, ResolvedDamagePreventionProhibition.from_dict(damage_rule.to_dict()))
        with self.assertRaises(ValueError):
            ResolvedDamagePreventionProhibition.from_dict({**damage_rule.to_dict(), "schema_version": True})

    def _lands(self, game, seat, names):
        for index, name in enumerate(names):
            self.add(game.engine, name, seat=seat, ref=f"prevention-{seat}-land-{index}")

    @staticmethod
    def _permissions(game):
        return tuple(effect for effect in game.state.continuous_effects if isinstance(effect, MODELS))

    @staticmethod
    def _shields(game, seat):
        return sorted(effect.remaining for effect in game.state.damage_prevention_shields if effect.subject.ref == seat)

    def _next_turn(self, game):
        turn = game.state.turn_sequence
        for _ in range(96):
            if game.state.turn_sequence > turn:
                return
            pending = game.state.pending_decision
            self.assertIsNotNone(pending)
            if pending.kind == "priority":
                command = {"action_id": "pass"}
            elif pending.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif pending.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            else:
                self.fail(f"Unexpected turn decision {pending.kind}")
            result = game.act(game.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.fail("Actual turn did not end")

    def test_actual_flaring_pain_flashback_keeps_finite_shields_and_expires(self):
        game = self.session(76812001)
        source = self.add(game.engine, "Flaring Pain", zone="graveyard")
        earlier, later = (self.add(game.engine, "Shock", zone="hand", ref="flaring-shock-" + part)
                          for part in ("earlier", "later"))
        first, second = (self.add(game.engine, "Mending Hands", seat="B", zone="hand", ref="flaring-shield-" + part)
                         for part in ("first", "second"))
        self._lands(game, "B", ("Plains", "Plains"))
        for card in (source, earlier, later, first, second):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 3})
        self.checkpoint(game)

        self.cast(game, "B", first, ["B"])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", earlier, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, [2]), (game.state.players["B"].life, self._shields(game, "B")))

        self.priority(game, "A")
        action = self.action(game, "A", source)
        self.assertIn("flashback", [rule["id"] for rule in action["cost_options"]])
        result = game.act("pilot:A", {"action_id": action["id"], "cost_option": "flashback", "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        prohibition, = self._permissions(game)
        self.assertIsInstance(prohibition, ResolvedDamagePreventionProhibition)

        self.cast(game, "B", second, ["B"])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", later, ["B"])
        self.resolve(game)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        effects = pending.payload_by_actor["B"]["options"]
        self.assertEqual(2, len(effects))
        game = self.replay(game, load=True)
        response = {"action_id": "choose", "replacement": effects[0]["id"]}
        accepted = game.act("pilot:B", response)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((38, [2, 4]), (game.state.players["B"].life, self._shields(game, "B")))
        self._next_turn(game)
        self.assertEqual((), self._permissions(game))
        self.assertFalse(collect_damage_prevention_permissions(game.engine).all_damage)
        self.replay(game, load=True)

    def test_actual_skullcrack_keeps_target_domain_and_blocks_both_players_gains(self):
        game = self.session(76812002)
        source = self.add(game.engine, "Skullcrack", zone="hand")
        body = self.add(game.engine, "Ancient Brontodon", seat="B")
        shield = self.add(game.engine, "Mending Hands", seat="B", zone="hand")
        own = self.add(game.engine, "Revitalize", zone="hand", ref="skull-life-own")
        other = self.add(game.engine, "Revitalize", seat="B", zone="hand", ref="skull-life-other")
        self._lands(game, "B", ("Plains", "Plains", "Plains"))
        for card in (source, body, shield, own, other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1, "W": 1})
        self.checkpoint(game)

        self.cast(game, "B", shield, ["B"])
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        action = self.action(game, "A", source)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": action["id"], "targets": [body.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", source, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((37, [4]), (game.state.players["B"].life, self._shields(game, "B")))
        life, prevention = self._permissions(game)
        self.assertIsInstance(life, ResolvedLifeGainProhibition)
        self.assertIsInstance(prevention, ResolvedDamagePreventionProhibition)
        self.assertLess(life.timestamp, prevention.timestamp)
        self.assertEqual(("A", "B", "C", "D"), life.denied_players)
        self.assertEqual(2, life.to_dict()["schema_version"])
        for seat, spell, expected in (("A", own, 40), ("B", other, 37)):
            old_hand = set(game.state.players[seat].zones["hand"])
            self.cast(game, seat, spell)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(expected, game.state.players[seat].life)
            card_id, = set(game.state.players[seat].zones["hand"]) - old_hand
            for observer in set("ABCD") - {seat}:
                self.assertNotIn(game.state.cards[card_id].ref, json.dumps(game.packet("pilot:" + observer, full=True)))
        self.replay(game, load=True)

    def _shield_counter_game(self, seed, damage_spell, mana):
        game = self.session(seed)
        source = self.add(game.engine, damage_spell, zone="hand")
        body = self.add(game.engine, "Ancient Brontodon", seat="B")
        boon = self.add(game.engine, "Boon of Safety", seat="B", zone="hand")
        self._lands(game, "B", ("Plains",))
        for card in (source, body, boon):
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        self.cast(game, "B", boon, [body.ref])
        self.resolve(game)
        choice = game.state.pending_decision
        self.assertEqual("semantic.choice", choice.kind)
        refs = choice.payload_by_actor["B"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertEqual(1, len(refs))
        response = {"action_id": "choose", "cards": {"top": refs, "bottom": []}}
        result = game.act("pilot:B", response)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.cards[body.object_id].counters["shield"])
        return game, source, body

    def test_actual_ordinary_shock_spends_existing_shield_counter(self):
        game, source, body = self._shield_counter_game(76812006, "Shock", {"R": 1})
        self.cast(game, "A", source, [body.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[body.object_id]
        self.assertEqual((0, 0), (current.marked_damage, current.counters.get("shield", 0)))
        self.assertEqual((), self._permissions(game))
        self.replay(game, load=True)

    def test_actual_call_in_a_professional_removes_shield_counter_without_prevention(self):
        game, source, body = self._shield_counter_game(76812003, "Call In a Professional", {"C": 2, "R": 1})
        self.cast(game, "A", source, [body.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[body.object_id]
        self.assertEqual(("battlefield", 3, 0), (current.zone, current.marked_damage, current.counters.get("shield", 0)))
        self.assertEqual(2, len(self._permissions(game)))
        self.replay(game, load=True)

    def test_actual_lost_only_target_gates_every_prohibition_and_damage(self):
        game = self.session(76812004)
        source = self.add(game.engine, "Call In a Professional", zone="hand")
        body = self.add(game.engine, "Ancient Brontodon", seat="B")
        escape = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        self._lands(game, "C", ("Island",))
        for card in (source, body, escape):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", source, [body.ref])
        self.cast(game, "C", escape, [body.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[body.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual((), self._permissions(game))
        self.assertEqual(0, game.state.cards[body.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_late_damage_failure_reverts_both_prior_permissions(self):
        game = self.session(76812005)
        source = self.add(game.engine, "Skullcrack", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", source, ["B"])

        def fail_after_permissions(*args, **kwargs):
            self.assertEqual(2, len(self._permissions(game)))
            raise DamageError("injected late damage failure")

        with patch("quorune.effect_runtime.damage_life_and_turns.resolve_damage_batch", side_effect=fail_after_permissions):
            for _ in range(12):
                before = authoritative_state_hash(game.state)
                result = game.act(game.pending_principals()[0], {"action_id": "pass"})
                if not result.ok:
                    self.assertIn("injected late damage failure", result.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    break
            else:
                self.fail("Injected failure was not reached")
        self.assertEqual((), self._permissions(game))
        self.assertEqual(40, game.state.players["B"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self._permissions(game)))
        self.assertEqual(37, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_disabling_exact_lowerer_restores_the_three_original_residuals(self):
        with patch("quorune.compiler.damage_permission_templates.turn_damage_prevention_template", return_value=None):
            for name in NAMES:
                original, _ = register_card(self.db, self.registry, name)
                self.assertFalse(original["strict_capability_ready"], original)
            unrelated, _ = register_card(self.db, self.registry, "Mending Hands")
            self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])

    def test_verified_withdrawals_require_damage_and_life_permission_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (UNPREVENTABLE_CAPABILITY, LIFE_GAIN_PROHIBITION_CAPABILITY):
            with self.subTest(capability=capability):
                raw = deepcopy(source)
                next(row for row in raw["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Turn damage or life permission dependency withdrawn"])
                registry = CapabilityRegistry(raw)
                index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in NAMES:
                    original, _ = register_card(self.db, registry, name)
                    expected = name == "Flaring Pain" and capability == LIFE_GAIN_PROHIBITION_CAPABILITY
                    self.assertEqual(expected, original["strict_capability_ready"], (name, original["blockers"]))
                unrelated, _ = register_card(self.db, registry, "Mending Hands")
                self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])
