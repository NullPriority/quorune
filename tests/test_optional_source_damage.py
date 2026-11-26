"""Native choices for announced-player damage and its printed fallback."""

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
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.optional_source_damage import (
    OPTIONAL_LAND_CONTROLLER_DAMAGE_CAPABILITY,
    OPTIONAL_SOURCE_DAMAGE_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
from test_controller_creature_keyword_absence import register_card


class OptionalSourceDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-damage.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "greatest-power-affected-sacrifice-cards.json",
            fixtures / "optional-source-damage-cards.json",
            fixtures / "ward-discard-replay-cards.json",
            fixtures / "event-player-modifier-cards.json",
            fixtures / "target-land-controller-choice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Optional source damage", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def test_actual_blazing_uses_the_new_creature_controller_even_if_the_accepted_damage_is_prevented(self):
        game = self.session(61127245)
        spell = self.add(game.engine, "Blazing Salvo", zone="hand")
        creature = self.add(game.engine, "Ornithopter", seat="B")
        steal = self.add(game.engine, "Act of Aggression", seat="C", zone="hand")
        protection = self.add(game.engine, "Solitary Confinement", seat="C")
        for card in (spell, creature, steal, protection):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"R": 1})
        game.state.players["C"].mana_pool["R"] = 5
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [creature.ref])
        self.priority(game, "C")
        offered = self.action(game, "C", steal)
        mana_only, = (option for option in offered["cost_options"] if not option.get("additional_life"))
        transfer = game.act("pilot:C", {"action_id": offered["id"], "cost_option": mana_only["id"],
                                         "targets": [creature.ref], "pay": "auto"})
        self.assertTrue(transfer.ok, transfer.summary)
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[creature.object_id].controller)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        lives = tuple(game.state.players[seat].life for seat in "BC")
        command = {"action_id": "choose", "choice": "accept_damage"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:C", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(lives, tuple(game.state.players[seat].life for seat in "BC"))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_actual_molten_offers_the_original_stack_controller_its_exact_damage_or_counter(self):
        record = self.db.lookup("Molten Influence", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Molten Influence")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        opened = replace(program, target_schema={
            key: value for key, value in program.target_schema.items() if key != "types_any"
        })
        self.assertFalse(bind_semantic_program_runtime(
            opened, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.optional_source_damage_templates._optional_target_controller_damage",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

        for target_name, targets, mana, response, expected in (
            ("Lightning Bolt", ["A"], {"R": 1}, "decline_damage", (40, 40)),
            ("Lightning Bolt", ["A"], {"R": 1}, "accept_damage", (37, 36)),
            ("Volcanic Fallout", [], {"R": 3}, "decline_damage", (38, 38)),
        ):
            with self.subTest(target=target_name, response=response):
                game = self.session(61127246)
                spell = self.add(game.engine, "Molten Influence", zone="hand")
                target = self.add(game.engine, target_name, seat="B", zone="hand")
                for card in (spell, target):
                    self.assert_closed(game, card)
                self.prepare(game, actor="A", active="A", mana={"R": 2})
                game.state.players["B"].mana_pool.update(mana)
                self.checkpoint(game)
                self.cast_for(game, "B", target, targets)
                self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
                result = game.act("pilot:B", {"action_id": "choose", "choice": response})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(expected, tuple(game.state.players[seat].life for seat in "AB"))
                self.replay(game, load=True)


    def scene(self, seed, name, *, jump_start=False, prevent=False):
        game = self.session(seed)
        spell = self.add(game.engine, name, zone="graveyard" if jump_start else "hand", ref="OPTIONAL-DAMAGE")
        witness = None
        if prevent:
            witness = self.add(game.engine, "The Wanderer", seat="B", ref="NONCOMBAT-PREVENTION")
            self.assert_closed(game, witness)
        discard = self.add(game.engine, "Generic Bound Plains", zone="hand", ref="CAST-DISCARD")
        hidden = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="UNRELATED-HAND")
        self.assert_closed(game, spell)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 2, "R": 1} if name == "Risk Factor" else {"R": 2})
        self.checkpoint(game)
        return game, spell, discard, hidden

    def choice(self, game, seat, hidden):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [seat]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor[seat]["legal_actions"][0]["choice_schema"]
        self.assertEqual(["accept_damage", "decline_damage"], schema["legal_values"])
        for other in set("ABCD") - {seat}:
            packet = game.packet("pilot:" + other, full=True)
            self.assertIsNone(packet["decision"])
            if other != "D":
                self.assertNotIn(hidden.ref, json.dumps(packet))

    def commit(self, game, seat, answer):
        rejected = next(other for other in "ABCD" if other != seat)
        command = {"action_id": "choose", "choice": answer}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + rejected, command).ok)
        self.assertFalse(game.act("pilot:" + seat, {**command, "choice": "draw_or_discard_another"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_risk_factor_acceptance_suppresses_the_draw_even_when_damage_is_prevented(self):
        for index, prevent in enumerate((False, True)):
            with self.subTest(prevented=prevent):
                game, spell, _, hidden = self.scene(2072901 + index, "Risk Factor", prevent=prevent)
                self.cast_for(game, "A", spell, ["B"])
                self.choice(game, "B", hidden)
                start_life = game.state.players["B"].life
                before_draw = tuple(game.state.players["A"].zones["hand"])
                game = self.replay(game, load=True)
                self.commit(game, "B", "accept_damage")
                self.assertEqual(start_life - (0 if prevent else 4), game.state.players["B"].life)
                self.assertEqual(before_draw, tuple(game.state.players["A"].zones["hand"]))
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_original_jump_start_rejects_self_then_pays_and_exiles_after_the_declined_damage(self):
        game, spell, discard, hidden = self.scene(2072903, "Risk Factor", jump_start=True)
        offer = self.action(game, "A", spell)
        self.assertIn("jump-start", [option["id"] for option in offer["cost_options"]])
        command = {"action_id": offer["id"], "targets": ["C"], "cost_option": "jump-start",
                   "discard_cards": [discard.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {**command, "targets": ["A"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[discard.object_id].zone)
        self.choice(game, "C", hidden)
        old_life = game.state.players["C"].life
        old_hand = len(game.state.players["A"].zones["hand"])
        game = self.replay(game, load=True)
        self.commit(game, "C", "decline_damage")
        self.assertEqual(old_hand + 3, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(old_life, game.state.players["C"].life)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_skullscorch_decline_discards_two_from_only_the_original_target(self):
        game, spell, _, hidden = self.scene(2072904, "Skullscorch")
        self.cast_for(game, "A", spell, ["C"])
        self.choice(game, "C", hidden)
        original = set(game.state.players["C"].zones["hand"])
        self.assertGreater(len(original), 2)
        unrelated = set(game.state.players["D"].zones["hand"])
        life = game.state.players["C"].life
        game = self.replay(game, load=True)
        self.commit(game, "C", "decline_damage")
        discarded = original - set(game.state.players["C"].zones["hand"])
        self.assertEqual(2, len(discarded))
        self.assertTrue(discarded <= set(game.state.players["C"].zones["graveyard"]))
        self.assertEqual(unrelated, set(game.state.players["D"].zones["hand"]))
        self.assertEqual(life, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_skullscorch_allows_the_caster_as_target_and_accepting_keeps_the_hand(self):
        game, spell, _, hidden = self.scene(2072905, "Skullscorch")
        self.cast_for(game, "A", spell, ["A"])
        self.choice(game, "A", hidden)
        hand = tuple(game.state.players["A"].zones["hand"])
        life = game.state.players["A"].life
        self.commit(game, "A", "accept_damage")
        self.assertEqual(hand, tuple(game.state.players["A"].zones["hand"]))
        self.assertEqual(life - 4, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_originals_require_the_reviewed_alternate_result_and_choice_authority(self):
        risk = self.db.lookup("Risk Factor", fuzzy=False)
        skull = self.db.lookup("Skullscorch", fuzzy=False)
        def compile_card(card, registry=self.registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        for card in (risk, skull):
            ir = compile_card(card)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertTrue(any(OPTIONAL_SOURCE_DAMAGE_CAPABILITY in node.capability_dependencies
                                for face in ir.faces for node in face.nodes))
        for card, old, new in (
            (risk, "Target opponent", "Target player"),
            (risk, "have Risk Factor", "have another spell"),
            (skull, "at random", "of their choice"),
            (skull, "that player has", "your opponent has"),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_card(changed).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        selected = next(row for row in raw["capabilities"] if row["id"] == OPTIONAL_SOURCE_DAMAGE_CAPABILITY)
        selected.update(status="blocked", blockers=["Withhold the reviewed damage choice."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertTrue(all(compile_card(card, registry).status != "exact" for card in (risk, skull)))
        with patch("quorune.compiler.optional_source_damage_templates.optional_source_damage_template", return_value=None):
            self.assertTrue(all(compile_card(card).status != "exact" for card in (risk, skull)))

    def setup_land_choice(self, seed, action, *, prevent=False):
        game=self.session(seed)
        spell=self.add(game.engine,"Generic Land Bargain",zone="hand",ref="LAND-CHOICE-SPELL")
        land=self.add(game.engine,"Skyline Cascade",seat="B",ref="TARGET-NONBASIC")
        basic=self.add(game.engine,"Generic Bound Plains",seat="B",ref="CONTROL-ELIGIBILITY")
        response=self.add(game.engine,action,seat="C",zone="hand",ref="RESPONSE-SPELL")
        hidden=self.add(game.engine,"Generic Bound Plains",seat="D",zone="hand",ref="UNRELATED-PRIVATE-HAND")
        for card in (spell,land,basic,response):
            self.assert_closed(game,card)
        if prevent:
            ward=self.add(game.engine,"The Wanderer",seat="C",ref="PREVENT-ACCEPTED-DAMAGE")
            self.assert_closed(game,ward)
        game.state.config.semantic_policy="trusted_only"
        self.prepare(game,mana={"C":2,"R":2})
        game.state.players["C"].mana_pool.update(C=5,R=2,G=1)
        self.checkpoint(game)
        offer=self.action(game,"A",spell)
        rejected=game.act("pilot:A",{"action_id":offer["id"],"targets":[basic.ref],"pay":"auto"})
        self.assertFalse(rejected.ok)
        self.cast_for(game,"A",spell,[land.ref])
        self.cast_for(game,"C",response,[land.ref])
        self.resolve_top(game)
        return game, land, hidden

    def test_current_controller_owns_each_legal_resolution_branch(self):
        scenarios=(("accept_damage",False),("accept_damage",True),("decline_damage",False))
        for i,(choice,prevent) in enumerate(scenarios):
            with self.subTest(choice=choice,prevent=prevent):
                game,land,hidden=self.setup_land_choice(608020541+i,"Word of Seizing",prevent=prevent)
                self.assertEqual("C",game.state.cards[land.object_id].controller)
                self.choice(game,"C",hidden)
                before=authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B",{"action_id":"choose","choice":choice}).ok)
                self.assertEqual(before,authoritative_state_hash(game.state))
                initial={seat:row.life for seat,row in game.state.players.items()}
                game=self.replay(game,load=True)
                self.commit(game,"C",choice)
                hp=initial["C"]-(4 if choice=="accept_damage" and not prevent else 0)
                self.assertEqual({**initial,"C":hp},{seat:row.life for seat,row in game.state.players.items()})
                expected="graveyard" if choice=="decline_damage" else "battlefield"
                self.assertEqual(expected,game.state.cards[land.object_id].zone)
                if expected=="graveyard":
                    self.assertIn(land.object_id,game.state.players["B"].zones["graveyard"])
                self.replay(game,load=True)

    def test_removed_target_does_not_open_any_damage_choice(self):
        game,land,_=self.setup_land_choice(608020549,"Generic Land Removal")
        self.assertEqual("graveyard",game.state.cards[land.object_id].zone)
        before={seat:row.life for seat,row in game.state.players.items()}
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before,{seat:row.life for seat,row in game.state.players.items()})
        self.replay(game,load=True)

    def test_land_controller_choice_requires_the_original_target_and_owner(self):
        card = self.db.lookup("Generic Land Bargain", fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            )

        admitted = compile_card(card)
        self.assertEqual("exact", admitted.status, admitted.material_residuals)
        node, = [node for face in admitted.faces for node in face.nodes
                 if OPTIONAL_LAND_CONTROLLER_DAMAGE_CAPABILITY in node.capability_dependencies]
        self.assertEqual(["basic"], node.target_schema["supertypes_none"])
        self.assertIn("resolution.target_controller_information", node.capability_dependencies)
        self.assertIn("permanent.destroy.effect", node.capability_dependencies)
        for old, new in (
            ("target nonbasic land", "target land"),
            ("its controller", "its owner"),
            ("this spell", "this land"),
        ):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_card(altered).status)
        with patch("quorune.compiler.optional_source_damage_templates.targeted_destruction_effect_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)
