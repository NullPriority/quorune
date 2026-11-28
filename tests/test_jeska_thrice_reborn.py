"""Real Jeska command-zone, loyalty payment and combat replay witnesses."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.commander_pairing import (
    PRINTED_COMMANDER_ELIGIBILITY_TEMPLATE, CommanderPairingError, validate_commander_pair,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.loyalty_damage import (
    LOYALTY_X_DAMAGE_FIELD, bind_loyalty_x_target_damage, loyalty_x_damage_instruction,
)
from quorune.zone_transitions import ZoneTransitionOwner
from quorune.target_combat_damage_multiplier_model import ResolvedTargetCombatDamageMultiplier


JESKA = "Jeska, Thrice Reborn"
PARTNER = "Rograkh, Son of Rohgahh"


class JeskaThriceRebornTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "jeska.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/counted-library-search-cards.json",
            ROOT / "tests/fixtures/ashiok-nightmare-weaver-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Jeska with partner",
            [DeckEntry("Jeska, Thrice Reborn", 1, "commander"), DeckEntry("Rograkh, Son of Rohgahh", 1, "commander"), DeckEntry("Mountain", 30)],
            [JESKA, PARTNER],
        )

    def commander(self, game, seat, name):
        return next(card for card in game.state.cards.values() if card.owner == seat and card.printed_name == name)

    def multipliers(self, game):
        return tuple(effect for effect in game.state.continuous_effects or ()
                     if isinstance(effect, ResolvedTargetCombatDamageMultiplier))

    def source(self, game, *, seat="A", loyalty=3, zone="battlefield"):
        card = self.add(game.engine, "Jeska, Thrice Reborn", seat=seat, zone=zone, ref=f"extra-jeska-{seat}")
        if zone == "battlefield":
            card.counters["loyalty"] = loyalty
            card.annotations["loyalty_initialized"] = True
        self.assert_closed(game, card)
        return card

    def main(self, game, seat, *, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active=seat, actor=seat, mana=mana)
        self.checkpoint(game)

    def submit(self, game, seat, action, **payload):
        result = game.act(f"pilot:{seat}", {"action_id": action, **payload})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, action, **payload):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", {"action_id": action, **payload})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate(self, game, seat, card, number, **payload):
        self.priority(game, seat)
        action = f"activate:{card.ref}:ab{number}"
        rows = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in rows})
        self.submit(game, seat, action, **payload)

    def next_main(self, game, seat):
        start = game.state.turn_sequence
        for _ in range(len(game.state.turn_order) + 1):
            self.until(game, lambda s: s.state.pending_decision is not None and (
                s.state.pending_decision.kind == "cleanup.discard"
                or (s.state.turn_sequence > start and s.state.active_player == seat
                    and s.state.phase == "precombat_main" and s.state.pending_decision.kind == "priority")
            ))
            decision = game.state.pending_decision
            if decision.kind == "priority":
                return
            actor, = decision.actors
            context = game.packet(f"pilot:{actor}", full=True)["decision"]["ctx"]
            self.submit(game, actor, "discard", cards=[row["id"] for row in context["hand"][:context["count"]]])
        self.fail(f"{seat} never reached their next main phase")

    def test_strict_source_and_mutated_instruction(self):
        record = self.db.lookup(JESKA)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertFalse(ir.material_residuals)

        def compile_without(ref):
            with patch(ref, return_value=None):
                return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        for producer in (
            "quorune.compiler.fixed_self_entry_counter_templates._counted_dynamic_amount_body",
            "quorune.compiler.loyalty_damage_templates.loyalty_damage_activation_template",
        ):
            with self.subTest(producer=producer):
                self.assertNotEqual("exact", compile_without(producer).status)
        introduced = replace(record, oracle_text=record.oracle_text.replace("up to three targets", "up to four targets"))
        self.assertNotEqual("exact", compile_oracle_card(introduced, capability_registry=self.registry).status)
        with self.assertRaisesRegex(ValueError, "announced X"):
            bind_loyalty_x_target_damage(None, {**loyalty_x_damage_instruction(), LOYALTY_X_DAMAGE_FIELD: True},
                                         SimpleNamespace(x_value=1))

    def test_own_and_partner_command_casts_count_and_hand_cast_does_not(self):
        game = self.session(10010701)
        partner = self.commander(game, "A", PARTNER)
        jeska = self.commander(game, "A", JESKA)
        hand = self.source(game, seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B", ref="jeska-hand-mana-red")
        for index in range(2):
            self.add(game.engine, "Mountain", seat="B", ref=f"jeska-hand-mana-{index}")
        self.main(game, "A", mana={"C": 2, "R": 1})
        self.cast_for(game, "A", partner)
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", jeska)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[jeska.object_id].counters["loyalty"])

        registry = game.engine.semantics
        current_programs = registry.programs_for_oracle

        def without_printed_permission(*args, **keywords):
            return tuple(program for program in current_programs(*args, **keywords)
                         if program.provenance.get("template_id") != PRINTED_COMMANDER_ELIGIBILITY_TEMPLATE)

        records = (self.db.lookup(JESKA), self.db.lookup(PARTNER))
        self.assertTrue(all(validate_commander_pair(self.db, registry, records)))
        with patch.object(registry, "programs_for_oracle", side_effect=without_printed_permission):
            with self.assertRaises(CommanderPairingError):
                validate_commander_pair(self.db, registry, records)

        self.next_main(game, "B")
        self.cast_for(game, "B", hand)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[hand.object_id].zone)
        self.replay(game, load=True)

    def test_native_minus_x_uses_equal_damage_and_last_known_source(self):
        game = self.session(10010702)
        source = self.source(game)
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        walker = self.add(game.engine, "Ashiok, Nightmare Weaver", seat="D")
        walker.counters["loyalty"] = 5
        walker.annotations["loyalty_initialized"] = True
        self.main(game, "A")
        action = f"activate:{source.ref}:ab3"
        self.reject(game, "A", action, x=4, targets=["C"])
        self.reject(game, "A", action, x=3, targets=["C", "C"])
        self.reject(game, "A", action, x=3, targets=["A", "B", "C", "D"])
        self.activate(game, "A", source, 3, x=3, targets=[creature.ref, "C", walker.ref])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(3, game.state.stack[-1].x_value)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual(37, game.state.players["C"].life)
        self.assertEqual(2, game.state.cards[walker.object_id].counters["loyalty"])
        self.replay(game, load=True)

    def test_native_minus_x_accepts_zero_targets_and_zero_amount(self):
        for targets, announced in (([], 1), (["C"], 0)):
            with self.subTest(targets=targets, announced=announced):
                game = self.session(10010703 + announced)
                source = self.source(game)
                self.main(game, "A")
                self.activate(game, "A", source, 3, x=announced, targets=targets)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40, game.state.players["C"].life)
                self.assertEqual(3 - announced, game.state.cards[source.object_id].counters["loyalty"])
                self.reject(game, "A", f"activate:{source.ref}:ab3", x=0, targets=[])
                self.replay(game, load=True)

    def test_native_trample_multiplier_stacks_and_preserves_blocker_damage(self):
        game = self.session(10010705)
        attacker = self.commander(game, "B", PARTNER)
        attacker = ZoneTransitionOwner(game.engine).move_card(attacker.object_id, "battlefield", log=False)
        attacker.counters["+1/+1"] = 6
        attacker.acquired_control_turn_count = -1
        source_a, source_b = (self.source(game, seat=seat) for seat in "AB")
        blocker = self.add(game.engine, "Grizzly Bears", seat="C")
        other_blocker = self.add(game.engine, "Grizzly Bears", seat="C", ref="second-menace-blocker")
        self.main(game, "A")
        self.activate(game, "A", source_a, 2, targets=[attacker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.multipliers(game)))
        self.next_main(game, "B")
        self.assertEqual("A", self.multipliers(game)[0].expires_on_next_turn_of)
        self.activate(game, "B", source_b, 2, targets=[attacker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"A", "B"}, {effect.expires_on_next_turn_of for effect in self.multipliers(game)})
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.attackers")
        self.submit(game, "B", "attack", attackers={attacker.ref: "C"})
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.blockers")
        self.submit(game, "C", "block", blocks={blocker.ref: attacker.ref, other_blocker.ref: attacker.ref})
        self.until(game, lambda s: s.state.pending_decision is not None
                   and s.state.pending_decision.kind in {"combat.damage", "replacement.order"})
        if game.state.pending_decision.kind == "combat.damage":
            self.submit(game, "B", "assign_damage", assignments=[
                {"source": attacker.ref, "target": blocker.ref, "amount": 2},
                {"source": attacker.ref, "target": other_blocker.ref, "amount": 2},
                {"source": attacker.ref, "target": "C", "amount": 2},
            ])
            self.until(game, lambda s: s.state.pending_decision is not None
                       and s.state.pending_decision.kind == "replacement.order")
        game = self.replay(game, load=True)
        self.assertEqual(["C"], list(game.state.pending_decision.actors))
        self.assertEqual(40, game.state.players["C"].life)
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        selection = {"replacement": options[0]["id"]}
        self.reject(game, "D", "choose", choices=selection)
        self.submit(game, "C", "choose", choices=selection)
        self.assertEqual(22, game.state.players["C"].life)
        self.assertEqual(18, game.state.players["C"].commander_damage_received[attacker.commander_designation_id])
        for member in (blocker, other_blocker):
            self.assertEqual("graveyard", game.state.cards[member.object_id].zone)
        self.replay(game, load=True)

    def test_native_multiplier_expires_at_the_granting_controllers_next_turn(self):
        game = self.session(10010706)
        source = self.source(game, seat="A")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        creature.acquired_control_turn_count = -1
        self.main(game, "A")
        self.activate(game, "A", source, 2, targets=[creature.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(creature.logical_object_id, self.multipliers(game)[0].target.logical_object_id)
        game = self.replay(game, load=True)
        self.next_main(game, "B")
        self.assertEqual(1, len(self.multipliers(game)))
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.attackers")
        self.submit(game, "B", "attack", attackers={creature.ref: "A"})
        self.until(game, lambda s: s.state.players["A"].life < 40)
        self.assertEqual(38, game.state.players["A"].life)
        self.next_main(game, "A")
        self.assertFalse(self.multipliers(game))
        self.replay(game, load=True)



class LegendarySorceryAnnouncedDamageTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "legendary-sorcery-announced-damage.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
            ROOT / "tests/fixtures/ashiok-nightmare-weaver-cards.json",
            ROOT / "tests/fixtures/legendary-sorcery-damage-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Legendary sorcery", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def actions(self, game, seat="A"):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def inferno(self, game):
        source = self.add(game.engine, "Jaya's Immolating Inferno", zone="hand")
        self.assert_closed(game, source)
        return source

    def test_legendary_enabler_is_required_only_at_cast_and_remaining_targets_each_take_x(self):
        game = self.session(2050401)
        source = self.inferno(game)
        legend = self.add(game.engine, "Rograkh, Son of Rohgahh", zone="hand")
        foe_legend = self.add(game.engine, "Rograkh, Son of Rohgahh", seat="C", ref="jaya-foreign-legend")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        first = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="jaya-saved-creature")
        second = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="jaya-remove-enabler")
        self.add(game.engine, "Island", seat="B", ref="jaya-bounce-island-1")
        self.add(game.engine, "Island", seat="B", ref="jaya-bounce-island-2")
        self.prepare(game, mana={"W": 1, "R": 4})
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        cast = {"action_id": "cast:" + source.ref, "x": 2, "targets": [target.ref, "C", "D"], "pay": "auto"}
        self.assertNotIn(cast["action_id"], {row["id"] for row in self.actions(game)})
        self.reject(game, "A", cast)
        self.cast(game, "A", legend)
        self.resolve_top(game)
        self.assertIn(cast["action_id"], {row["id"] for row in self.actions(game)})
        for refs in (["B", "B"], ["A", "B", "C", "D"]):
            self.reject(game, "A", {**cast, "targets": refs})
        self.act(game, "A", cast)
        self.assertEqual(2, game.state.stack[-1].x_value)
        self.cast(game, "B", first, [target.ref])
        self.resolve_top(game)
        self.cast(game, "B", second, [legend.ref])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[foe_legend.object_id].zone)
        self.assertEqual("hand", game.state.cards[legend.object_id].zone)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([40, 40, 38, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_legendary_planeswalker_enables_zero_choices_and_zero_announced_amount(self):
        for index, (targets, x) in enumerate((([], 2), (["C"], 0))):
            with self.subTest(targets=targets, x=x):
                game = self.session(2050402 + index)
                source = self.inferno(game)
                walker = self.add(game.engine, "Ashiok, Nightmare Weaver")
                walker.counters["loyalty"] = 5
                walker.annotations["loyalty_initialized"] = True
                self.prepare(game, mana={"R": 2 + x})
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                action = "cast:" + source.ref
                self.assertIn(action, {row["id"] for row in self.actions(game)})
                self.act(game, "A", {"action_id": action, "x": x, "targets": targets, "pay": "auto"})
                self.resolve_top(game)
                self.assertEqual([40]*4, [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)

    def test_copy_resolves_without_casting_or_controlling_a_legendary_permanent(self):
        game = self.session(2050404)
        source = self.inferno(game)
        self.add(game.engine, "Rograkh, Son of Rohgahh")
        copier = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        self.prepare(game, mana={"R": 4})
        game.state.players["B"].mana_pool["R"] = 2
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.act(game, "A", {"action_id": "cast:" + source.ref, "x": 2, "targets": ["C", "D"], "pay": "auto"})
        original = game.state.stack[-1]
        self.cast(game, "B", copier, [original.ref])
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (decision.kind, decision.actors))
        self.act(game, "B", {"action_id": "choose"})
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", 2, "B"), (copy.kind, copy.x_value, copy.controller))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 40, 36, 36], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_legendary_rule_marker_and_damage_authority_are_closed(self):
        from quorune.rules.announced_three_target_damage import CAPABILITY, FIELD
        from quorune.rules.legendary_sorcery import VERSION_FIELD, validate_version
        from quorune.model import GameState
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from test_controller_creature_keyword_absence import register_card

        value, registry = register_card(self.db, self.registry, "Jaya's Immolating Inferno")
        self.assertTrue(value["strict_capability_ready"], value["blockers"])
        source = self.db.lookup("Jaya's Immolating Inferno")
        (p,) = [program for program in registry.programs_for_oracle(source.oracle_id) if CAPABILITY in program.capability_dependencies]
        deps = tuple(cap for cap in p.capability_dependencies if cap != CAPABILITY)
        for changed in (
            replace(p, effects=[{**p.effects[0], FIELD: True}]),
            replace(p, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile="commander_review").to_dict()),
        ):
            report = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(report["strict"])
        for bad in (True, 2, "1"):
            with self.assertRaises(ValueError):
                validate_version(bad)
        old = self.session(2050405).state.to_dict()
        self.assertEqual(1, old.pop(VERSION_FIELD))
        self.assertEqual(old, GameState.from_dict(old).to_dict())


class TurnCreatureCombatMultiplierTests(InheritedNativeFixture, TestCase):
    BODY = ("All creatures lose trample until end of turn. If a creature would deal combat damage "
            "to a creature this turn, it deals double that damage to that creature instead.")
    NAMES = ("Turn combat spell", "Combat trampler", "Combat attacker", "Combat wall", "Combat haste")

    @classmethod
    def setUpClass(cls):
        import json

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name)
        contents = json.loads((ROOT / "tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        by_name = {card["name"]: card for card in contents["cards"]}
        additions = [
            ("Instant", cls.BODY, "{3}{R}", 4, None, None, []),
            ("Creature — Dinosaur", "Trample", "{4}{G}{G}", 6, "6", "6", ["Trample"]),
            ("Creature — Bear", "", "{1}{G}", 2, "2", "2", []),
            ("Creature — Wall", "Defender", "{1}{R}{R}", 3, "0", "8", ["Defender"]),
            ("Enchantment", "Creatures you control have haste.", "{2}{R}", 3, None, None, []),
        ]
        base = by_name["Generic Bound Body"]
        for i, (name, fields) in enumerate(zip(cls.NAMES, additions, strict=True), 1):
            kind, text, cost, mana, power, toughness, keywords = fields
            contents["cards"].append({**base, "id": f"00000000-0000-4000-8000-00000000870{i}",
                "oracle_id": f"00000000-0000-4000-8000-00000000880{i}", "name": name,
                "type_line": kind, "oracle_text": text, "mana_cost": cost, "cmc": mana,
                "power": power, "toughness": toughness, "keywords": keywords, "color_identity": [], "colors": []})
        fixture = path / "turn-combat.json"
        fixture.write_text(json.dumps(contents), encoding="utf-8")
        database = path / "turn-combat.sqlite3"
        build_fixture_database([fixture], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Turn combat", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_turn_combat_multiplier_requires_both_closed_instructions(self):
        card = self.db.lookup(self.NAMES[0], fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        good = compile_oracle_card(card, **options)
        self.assertEqual("exact", good.status, good.material_residuals)
        ability, = good.faces[0].nodes
        self.assertIsNone(ability.target_schema)
        self.assertEqual(2, ability.effects[1]["schema_version"])
        for changed in (card.oracle_text.replace("double", "triple"), card.oracle_text.replace("combat damage", "damage")):
            self.assertTrue(compile_oracle_card(replace(card, oracle_text=changed), **options).material_residuals)
        with patch("quorune.compiler.loyalty_damage_templates._TURN_CREATURE_COMBAT") as grammar:
            grammar.fullmatch.return_value = None
            self.assertTrue(compile_oracle_card(card, **options).material_residuals)

    def test_turn_combat_multiplier_doubles_creature_damage_and_replays_through_expiry(self):
        spell_name, trampler_name, attacker_name, wall_name, haste_name = self.NAMES
        game = self.session(61470220)
        spell = self.add(game.engine, spell_name, zone="hand")
        old = self.add(game.engine, trampler_name, ref="turn-rule-old-trampler")
        later = self.add(game.engine, trampler_name, zone="hand", ref="turn-rule-later-trampler")
        attacker = self.add(game.engine, attacker_name)
        wall = self.add(game.engine, wall_name, seat="B")
        self.add(game.engine, haste_name)
        hidden = self.add(game.engine, attacker_name, seat="D", zone="hand", ref="turn-rule-other-hand")
        for card in (old, attacker):
            card.acquired_control_turn_count = -1
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 9, "R": 4, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        effect, = [effect for effect in game.state.continuous_effects
                   if isinstance(effect, ResolvedTargetCombatDamageMultiplier)]
        self.assertEqual(2, effect.to_dict()["schema_version"])
        self.assertNotIn("trample", game.engine._combat_keywords(game.state.cards[old.object_id]))
        self.assertNotIn(hidden.ref, str(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.cast_for(game, "A", later)
        self.assertIsNone(self.resolve(game))
        current_later = game.state.cards[later.object_id]
        self.assertIn("trample", game.engine._combat_keywords(current_later))
        self.attack(game, [(game.state.cards[old.object_id], "B"), (game.state.cards[attacker.object_id], "C")])
        self.until(game, lambda game: game.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {wall.ref: [old.ref]}}
        before = authoritative_state_hash(game.state), len(game.commands)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, (authoritative_state_hash(game.state), len(game.commands)))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda game: game.state.step == "end_combat")
        self.assertEqual("graveyard", game.state.cards[wall.object_id].zone)
        self.assertEqual([40, 40, 38, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.until(game, lambda game: game.state.active_player == "B")
        self.assertFalse(any(isinstance(effect, ResolvedTargetCombatDamageMultiplier) for effect in game.state.continuous_effects))
        self.assertIn("trample", game.engine._combat_keywords(game.state.cards[old.object_id]))
        self.replay(game, load=True)
