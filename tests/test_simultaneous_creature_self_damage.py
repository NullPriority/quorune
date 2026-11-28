"""Resolve the printed all-creature self-damage spells through the canonical batch."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import creature_power_damage_templates
from quorune.creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_CAPABILITY, CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_SELF_DAMAGE_SET_CAPABILITY, CREATURE_SELF_DAMAGE_SET_DEPENDENCIES,
    creature_self_damage_set_effect, validate_creature_self_damage_set,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import capability_covered_mechanics, load_default_capability_registry
from quorune.rules.creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SOLAR = "Solar Blaze"
WAVE = "Wave of Reckoning"
ORIGINALS = (SOLAR, WAVE)
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class SimultaneousCreatureSelfDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-public-state-cards.json": {"Disfigure", "Giant Growth", "Grizzly Bears", "Unsummon"},
            "announced-x-public-damage-set-cards.json": {"Furnace of Rath", "Dictate of the Twin Gods"},
            "creature-power-multiple-cards.json": {"Vampire Nighthawk"},
            "damage-death-exile-cards.json": {"Wall of Omens"},
            "prepared-power-damage-cards.json": {"Justice Strike", "White Knight"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "simultaneous-creature-self-damage-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "simultaneous-creature-self-damage.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Simultaneous creature self-damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @contextmanager
    def damage_events(self, game, creatures):
        seen = []
        dispatch = game.engine._dispatch_semantic_event

        def observe(event, context, **fields):
            if event == "damage.dealt":
                marks = tuple(game.state.cards[card.object_id].marked_damage for card in creatures)
                seen.append((dict(context), marks))
            return dispatch(event, context, **fields)

        with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
            yield seen

    def _assert_single_own_source_batch(self, events, creatures):
        ids = {card.object_id for card in creatures}
        self.assertEqual(ids, {event["source_object_id"] for event, _marks in events})
        self.assertEqual(len(ids), len(events))
        self.assertEqual(1, len({event["event_id"] for event, _marks in events}))
        for event, _marks in events:
            self.assertEqual(event["source_object_id"], event["target_object_id"])
            self.assertEqual(event["source_logical_object_id"], event["target_logical_object_id"])
            self.assertEqual(event["source_controller"], event["target_controller"])
            self.assertFalse(event["combat"])

    def test_exact_untargeted_wire_rejects_other_scopes_and_keeps_existing_interactions(self):
        payload = creature_self_damage_set_effect()
        mechanics = (CREATURE_POWER_DAMAGE_MECHANIC, "cr-120-damage")
        validate_creature_self_damage_set(payload)
        required = fixed_creature_power_damage_node_capabilities(
            effects=(payload,), target_schema=None, mechanic_ids=mechanics,
        )
        self.assertEqual(CREATURE_SELF_DAMAGE_SET_DEPENDENCIES, required)
        self.assertEqual(required, typed_result_amount_runtime_dependencies(payload))
        self.assertIn(CREATURE_POWER_DAMAGE_MECHANIC, capability_covered_mechanics(required))
        self.assertFalse(fixed_creature_power_damage_node_capabilities(
            effects=(payload,), target_schema={"count": 1}, mechanic_ids=mechanics,
        ))
        self.assertFalse(fixed_creature_power_damage_node_capabilities(
            effects=(payload, {"op": "draw"}), target_schema=None, mechanic_ids=mechanics,
        ))
        for mutation in (
            dict(payload, schema_version=5.0), dict(payload, schema_version=4),
            dict(payload, source="$source"), dict(payload, power_multiplier=2),
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                typed_result_amount_runtime_dependencies(mutation)
        for text in (
            "Each tapped creature deals damage to itself equal to its power.",
            "Each creature you control deals damage to itself equal to its power.",
            "Each creature deals damage to itself equal to twice its power.",
            "Each creature deals damage to itself equal to its power. Draw a card.",
        ):
            self.assertIsNone(creature_power_damage_templates.fixed_creature_power_damage_effect_template(
                text, card_name="Unsupported creature self-damage",
            ))

    def test_complete_originals_share_the_set_and_justice_strike_retains_its_target(self):
        for name in ORIGINALS:
            card = self.db.lookup(name, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            report, semantics = register_card(self.db, self.registry, name)
            self.assertTrue(report["strict_capability_ready"], report["blockers"])
            self.assertEqual("capability_closed", report["trust_basis"])
            program, = semantics.programs_for_oracle(card.oracle_id)
            self.assertIsNone(program.target_schema)
            self.assertIn(CREATURE_SELF_DAMAGE_SET_CAPABILITY, program.capability_dependencies)
            effect, = program.effects
            validate_creature_self_damage_set(effect)
        report, semantics = register_card(self.db, self.registry, "Justice Strike")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        justice = self.db.lookup("Justice Strike", fuzzy=False)
        old, = semantics.programs_for_oracle(justice.oracle_id)
        self.assertIn(CREATURE_POWER_DAMAGE_CAPABILITY, old.capability_dependencies)
        self.assertEqual("$target.0", old.effects[0]["target"])
        self.assertNotIn("schema_version", old.effects[0])

    def test_paid_wave_applies_own_deathtouch_and_lifelink_and_omits_nonpositive_power(self):
        game = self.session(6496150)
        wave = self.add(game.engine, WAVE, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="A")
        vampire = self.add(game.engine, "Vampire Nighthawk", seat="B")
        zero = self.add(game.engine, "Wall of Omens", seat="C")
        negative = self.add(game.engine, "Wall of Omens", seat="D", ref="self-damage-negative")
        reduce = self.add(game.engine, "Disfigure", seat="D", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="self-damage-private")
        self.assert_closed(game, vampire)
        self.prepare(game, mana={"C": 4, "W": 1})
        game.state.players["D"].mana_pool["B"] = 1
        self.checkpoint(game)

        self.cast(game, "A", wave)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "D", reduce, [negative.ref])
        self.resolve_top(game)
        current = game.engine._numeric_stat(game.state.cards[negative.object_id].object_id, "power")
        self.assertEqual(-2, current)
        game = self.replay(game, load=True)
        life = game.state.players["B"].life
        with self.damage_events(game, (bear, vampire)) as events:
            self.assertIsNone(self.resolve(game))
        self._assert_single_own_source_batch(events, (bear, vampire))
        self.assertEqual([(2, 2), (2, 2)], [marks for _event, marks in events])
        self.assertEqual([2, 2], [event["amount"] for event, _marks in events])
        for body in (bear, vampire):
            self.assertEqual("graveyard", game.state.cards[body.object_id].zone)
        for body in (zero, negative):
            self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
            self.assertEqual(0, game.state.cards[body.object_id].marked_damage)
        self.assertEqual(life + 2, game.state.players["B"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_paid_solar_captures_live_power_and_replays_each_affected_controllers_choice(self):
        game = self.session(6496160)
        solar = self.add(game.engine, SOLAR, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="A")
        enlightened = self.add(game.engine, "Burrenton Forge-Tender", seat="B")
        furnace = self.add(game.engine, "Furnace of Rath", seat="C")
        dictate = self.add(game.engine, "Dictate of the Twin Gods", seat="D")
        growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        for card in (enlightened, furnace, dictate):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1, "W": 1})
        game.state.players["C"].mana_pool["G"] = 1
        self.checkpoint(game)

        self.cast(game, "A", solar)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "C", growth, [bear.ref])
        self.resolve_top(game)
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        with self.damage_events(game, (bear, enlightened)) as events:
            for seat in ("A", "B"):
                self.assertEqual(["pilot:" + seat], game.pending_principals())
                self.assertEqual([], events)
                self.assertTrue(all(game.state.cards[card.object_id].marked_damage == 0 for card in (bear, enlightened)))
                options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"]
                self.assertEqual(2, len(options))
                command = {"action_id": "choose", "replacement": options[-1]["id"]}
                unchanged = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:D", command).ok)
                self.assertFalse(game.act("pilot:" + seat, {**command, "replacement": "unissued"}).ok)
                self.assertEqual(unchanged, authoritative_state_hash(game.state))
                chosen = game.act("pilot:" + seat, command)
                self.assertTrue(chosen.ok, chosen.summary)
            self.assertIsNone(self.resolve(game))
        self._assert_single_own_source_batch(events, (bear, enlightened))
        amounts = {event["source_controller"]: (event["assigned_amount"], event["amount"]) for event, _marks in events}
        self.assertEqual({"A": (5, 20), "B": (1, 4)}, amounts)
        self.assertEqual([(20, 4), (20, 4)], [marks for _event, marks in events])
        for card in (bear, enlightened):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_compiler_omission_restores_both_unrepresented_whole_spell_results(self):
        with patch.object(creature_power_damage_templates, "_creature_self_damage_set_template", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertEqual("spell_effect", residual.kind)
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_self_damage_set_authority_withdrawal_keeps_targeted_justice_strike(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == CREATURE_SELF_DAMAGE_SET_CAPABILITY).update(
            status="blocked", blockers=["Scoped simultaneous creature self-damage withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CREATURE_SELF_DAMAGE_SET_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CREATURE_SELF_DAMAGE_SET_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        targeted, _ = register_card(self.db, registry, "Justice Strike")
        self.assertTrue(targeted["strict_capability_ready"], targeted["blockers"])



    def test_ignition_native_live_power_other_creatures_opponents_lifelink_and_replay(self):
        game = self.session(12020001)
        spell = self.add(game.engine, "Chandra's Ignition", zone="hand")
        source = self.add(game.engine, "Vampire Nighthawk", ref="ignition-source")
        ally = self.add(game.engine, "Grizzly Bears", ref="ignition-ally")
        foreign = self.add(game.engine, "Wall of Omens", seat="C", ref="ignition-foreign")
        protected = self.add(game.engine, "White Knight", seat="B", ref="ignition-protected")
        land = self.add(game.engine, "Generic Bound Plains", seat="D", ref="ignition-land")
        boost = self.add(game.engine, "Giant Growth", seat="C", zone="hand", ref="ignition-boost")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="ignition-private")
        for card in (spell, source, protected):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 2})
        game.state.players["C"].mana_pool["G"] = 1
        self.checkpoint(game)

        offer = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"] == "cast:" + spell.ref)
        before = authoritative_state_hash(game.state)
        wrong = {"action_id": offer["id"], "targets": [foreign.ref], "pay": "auto"}
        self.assertFalse(game.act("pilot:A", wrong).ok)
        self.assertFalse(game.act("pilot:B", {**wrong, "targets": [source.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [source.ref])
        self.cast_for(game, "C", boost, [source.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(source.object_id, "power"))
        game = self.replay(game, load=True)
        life = {seat: player.life for seat, player in game.state.players.items()}
        with self.damage_events(game, (ally, foreign)) as events:
            self.assertIsNone(self.resolve(game))
        self.assertEqual(5, len(events))
        self.assertEqual(1, len({event["event_id"] for event, _ in events}))
        for event, marks in events:
            self.assertEqual((5, 5), marks)
            self.assertEqual(source.object_id, event["source_object_id"])
            self.assertEqual("A", event["source_controller"])
            self.assertEqual(5, event["amount"])
            self.assertFalse(event["combat"])
        self.assertEqual(life["A"] + 25, game.state.players["A"].life)
        for seat in "BCD":
            self.assertEqual(life[seat] - 5, game.state.players[seat].life)
        for card in (source, protected, land):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
            self.assertEqual(0, game.state.cards[card.object_id].marked_damage)
        for card in (ally, foreign):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_ignition_illegal_source_whole_spell_fails_and_zero_power_deals_nothing(self):
        for remove_target in (True, False):
            with self.subTest(remove_target=remove_target):
                game = self.session(12020002 + int(remove_target))
                spell = self.add(game.engine, "Chandra's Ignition", zone="hand")
                source = self.add(game.engine, "Grizzly Bears" if remove_target else "Wall of Omens")
                other = self.add(game.engine, "Vampire Nighthawk", seat="B")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game, mana={"C": 3, "R": 2})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                life = {seat: player.life for seat, player in game.state.players.items()}
                self.cast_for(game, "A", spell, [source.ref])
                if remove_target:
                    self.cast_for(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                with self.damage_events(game, (source, other)) as events:
                    self.assertIsNone(self.resolve(game))
                self.assertEqual([], events)
                self.assertEqual(life, {seat: player.life for seat, player in game.state.players.items()})
                self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
                self.replay(game, load=True)

    def test_ignition_runtime_ownership_rejects_bad_target_domains_and_missing_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.creature_power_damage_model import TARGET_POWER_DAMAGE_COHORT_CAPABILITY

        card = self.db.lookup("Chandra's Ignition", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report)
        spell, = semantics.programs_for_oracle(card.oracle_id)
        schema = deepcopy(spell.target_schema)
        schema["groups"][0]["controller_relation"] = "any"
        invalid = bind_semantic_program_runtime(replace(spell, target_schema=schema),
                                               capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(invalid["strict"], invalid)
        effect, = spell.effects
        for changed in ({**effect, "schema_version": 8.0}, {**effect, "source": "$source"},
                        {**effect, "target": "$target.1"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                typed_result_amount_runtime_dependencies(changed)
        capabilities = tuple(cap for cap in spell.capability_dependencies if cap != TARGET_POWER_DAMAGE_COHORT_CAPABILITY)
        missing = replace(spell, capability_dependencies=capabilities,
                          capability_closure=self.registry.closure(capabilities, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + TARGET_POWER_DAMAGE_COHORT_CAPABILITY, result["blockers"])

    def test_ignition_closed_grammar_and_producer_omission_mutation(self):
        from test_fixed_public_alternative_costs import card_record
        from quorune.creature_power_damage_model import TARGET_POWER_DAMAGE_COHORT_KIND

        text = (
            "Target creature you control deals damage equal to its power to each other creature and each opponent."
        )
        for changed in (text.replace("you control ", ""), text.replace("each other", "each"),
                        text.replace("each opponent", "each player"), text.replace("its power", "twice its power")):
            with self.subTest(changed=changed):
                self.assertIsNone(creature_power_damage_templates.fixed_creature_power_damage_effect_template(
                    changed, card_name="Target-power fixture",
                ))

        def witness():
            ir = compile_oracle_card(card_record(text, type_line="Sorcery"),
                                     capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(TARGET_POWER_DAMAGE_COHORT_KIND, node.effects[0]["kind"])

        witness()
        with patch.object(creature_power_damage_templates, "target_power_damage_cohort_effect",
                          return_value={"op": "unrepresented"}):
            with self.assertRaises(AssertionError):
                witness()

if __name__ == "__main__":
    unittest.main()


PREDATION_ORACLE = (
    "For each creature your opponents control, create a 4/4 green Phyrexian Beast creature token. "
    "Each of those tokens fights a different one of those creatures."
)


class CreatedTokenFightNativeTests(InheritedNativeFixture, unittest.TestCase):
    damage_events = SimultaneousCreatureSelfDamageTests.damage_events

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Plains", "Generic Bound Body", "Generic Bound Growth",
            },
            "creature-power-multiple-cards.json": {"Vampire Nighthawk"},
            "recipient-control-cards.json": {"Serra Ascendant"},
            "attacking-token-result-cards.json": {"Parallel Lives"},
            "announced-populate-cards.json": {"Soul Warden"},
            "announced-x-public-damage-set-cards.json": {"Furnace of Rath", "Dictate of the Twin Gods"},
        }
        parts = [FIXTURES / "simultaneous-creature-self-damage-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "created-token-fights.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Created token fights", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 25),
        ], ["Generic Bound Commander"])

    def start(self, game):
        source = self.add(game.engine, "Ezuri's Predation", zone="hand", ref="predation-spell")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "G": 3})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertEqual([], game.state.stack[-1].targets)
        return source

    @staticmethod
    def beasts(game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Phyrexian Beast Token"]

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_predation_pairs_original_tokens_in_one_fight_batch(self):
        game = self.session(7011401)
        engine = game.engine
        multiplier = self.add(engine, "Parallel Lives", ref="predation-doubler")
        observer = self.add(engine, "Soul Warden", ref="predation-entry-observer")
        nighthawk = self.add(engine, "Vampire Nighthawk", seat="B", ref="predation-lifelink")
        ascendant = self.add(engine, "Serra Ascendant", seat="B", ref="predation-threshold")
        third = self.add(engine, "Generic Bound Body", seat="C", ref="predation-other")
        own = self.add(engine, "Generic Bound Body", ref="predation-own")
        private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="predation-private")
        game.state.players["B"].life = 29
        for card in (multiplier, observer, ascendant):
            self.assert_closed(game, card)
        self.assertEqual(1, engine._numeric_stat(ascendant.object_id, "power"))
        source = self.start(game)
        outcome = self.resolve(game)
        self.assertIsNotNone(outcome)
        self.assertEqual("choice.apnap", outcome.kind)
        self.assertEqual([source.object_id], [item.card_object_id for item in game.state.stack])
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual(6, len(self.beasts(game)))
        self.assertTrue(all(card.marked_damage == 0 for card in (nighthawk, ascendant, third)))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        fighters = context["fighters"]
        self.assertEqual(3, len(fighters))
        self.assertEqual({nighthawk.ref, ascendant.ref, third.ref}, set(context["opponents"]))
        self.assertNotIn(own.ref, context["opponents"])
        self.assertNotIn(private.ref, json.dumps(context))
        extras = [card for card in self.beasts(game) if card.ref not in fighters]
        self.assertEqual(3, len(extras))
        pairs = dict(zip(fighters, (nighthawk.ref, ascendant.ref, third.ref), strict=True))
        command = {"action_id": "choose", "fight_pairs": pairs}
        game = self.replay(game, load=True)
        self.reject(game, "B", command)
        self.reject(game, "A", {"action_id": "choose", "fight_pairs": dict.fromkeys(fighters, nighthawk.ref)})
        self.reject(game, "A", {"action_id": "choose", "fight_pairs": {**pairs, fighters[-1]: own.ref}})
        self.reject(game, "A", {"action_id": "choose", "fight_pairs": {extras[0].ref: nighthawk.ref,
                             fighters[1]: ascendant.ref, fighters[2]: third.ref}})
        watched = [nighthawk, ascendant, third]
        watched.extend(next(card for card in self.beasts(game) if card.ref == ref) for ref in fighters)
        with self.damage_events(game, watched) as events:
            result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(6, len(events))
        self.assertEqual(1, len({event["event_id"] for event, _marks in events}))
        self.assertTrue(all(not event["combat"] for event, _marks in events))
        self.assertTrue(all(marks == (4, 4, 4, 2, 1, 2) for _event, marks in events))
        self.assertEqual(32, game.state.players["B"].life)
        self.assertEqual(6, game.engine._numeric_stat(ascendant.object_id, "power"))
        self.assertEqual("battlefield", game.state.cards[ascendant.object_id].zone)
        for extra in extras:
            self.assertEqual(0, game.state.cards[extra.object_id].marked_damage)
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        refs = [row["id"] for row in ctx["triggers"]]
        self.assertEqual(6, len(refs))
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": refs})
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(46, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_predation_empty_scope_has_no_creation_or_choice(self):
        game = self.session(7011402)
        self.add(game.engine, "Generic Bound Body", ref="predation-own-only")
        self.start(game)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.beasts(game))
        self.replay(game, load=True)

    def test_predation_commits_the_only_pair(self):
        game = self.session(7011405)
        opponent = self.add(game.engine, "Generic Bound Body", seat="B")
        self.start(game)
        with self.damage_events(game, (opponent,)) as events:
            self.assertIsNone(self.resolve(game))
        beast, = self.beasts(game)
        self.assertEqual(2, beast.marked_damage)
        self.assertEqual(4, game.state.cards[opponent.object_id].marked_damage)
        self.assertEqual(2, len(events))
        self.assertEqual(1, len({event["event_id"] for event, _marks in events}))
        self.replay(game, load=True)

    def test_predation_retains_pairs_across_damage_replacement_choices(self):
        game = self.session(7011404)
        left = self.add(game.engine, "Generic Bound Body", seat="B", ref="predation-left")
        right = self.add(game.engine, "Generic Bound Body", seat="C", ref="predation-right")
        for name, seat in (("Furnace of Rath", "C"), ("Dictate of the Twin Gods", "D")):
            self.assert_closed(game, self.add(game.engine, name, seat=seat))
        self.start(game)
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        fighters = self.beasts(game)
        self.assertEqual(2, len(fighters))
        pairs = dict(zip((card.ref for card in fighters), (left.ref, right.ref), strict=True))
        chosen = game.act("pilot:A", {"action_id": "choose", "fight_pairs": pairs})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        watched = [left, right, *fighters]
        actors = []
        with self.damage_events(game, watched) as events:
            for _ in watched:
                decision = game.state.pending_decision
                self.assertEqual("replacement.order", decision.kind)
                seat, = decision.actors
                actors.append(seat)
                self.assertEqual([], events)
                self.assertEqual(2, len(self.beasts(game)))
                self.assertTrue(all(game.state.cards[card.object_id].marked_damage == 0 for card in watched))
                options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"]
                self.assertEqual(2, len(options))
                command = {"action_id": "choose", "replacement": options[0]["id"]}
                self.reject(game, "D", command)
                chosen = game.act("pilot:" + seat, command)
                self.assertTrue(chosen.ok, chosen.summary)
            self.assertIsNone(self.resolve(game))
        self.assertCountEqual(["A", "A", "B", "C"], actors)
        self.assertEqual(4, len(events))
        self.assertEqual(1, len({event["event_id"] for event, _marks in events}))
        self.assertTrue(all(marks == (16, 16, 8, 8) for _event, marks in events))
        self.replay(game, load=True)

    def test_predation_strict_shape_and_runtime_withdrawal(self):
        from dataclasses import replace
        from test_granted_skulk import record
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.created_token_fights import CAPABILITY, created_token_fight_node_capabilities

        source = record(PREDATION_ORACLE, "Sorcery")
        ir = compile_oracle_card(source, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(PREDATION_ORACLE)), (node.span.start, node.span.end))
        self.assertIn(CAPABILITY, node.capability_dependencies)
        effect, = node.effects
        for variant in ({**effect, "created_token_fights": True}, {**effect, "token": {"name": "Treasure"}},
                        {**effect, "players": "all"}):
            self.assertEqual((), created_token_fight_node_capabilities((variant,), None, None))
        self.assertEqual((), created_token_fight_node_capabilities(node.effects, {"count": 1}, None))
        for text in (
            PREDATION_ORACLE.replace("your opponents control", "you control"),
            PREDATION_ORACLE.replace("a different one of", "each of"),
            PREDATION_ORACLE.replace("a 4/4", "two 4/4"),
        ):
            altered = compile_oracle_card(replace(source, oracle_text=text), capability_registry=self.registry)
            self.assertNotIn(CAPABILITY, {cap for face in altered.faces for row in face.nodes for cap in row.capability_dependencies})
        game = self.session(7011403)
        spell = self.add(game.engine, "Ezuri's Predation", zone="hand")
        program, = game.engine.semantics.programs_for_oracle(spell.oracle_id)
        deps = tuple(value for value in program.capability_dependencies if value != CAPABILITY)
        mutant = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])
