from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.phase_occurrence_model import PHASE_IN_CAPABILITY, PHASE_IN_EVENT, SPELL_TARGET_PHASE_OUT_CAPABILITY, observed_subtype
from quorune.phasing_model import SPELL_TARGET_PHASE_OUT_SUBJECT, validate_phase_out_effect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.phase_occurrence_shapes import phase_in_token_runtime_dependencies, spell_target_phase_out_runtime_dependencies
from quorune.rules.target_announcements import TARGET_ANNOUNCEMENT_EVENT
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CARD = "King of the Oathbreakers"
PROFILE = "commander_review"


class SpellTargetAndPhaseInTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "spell-target-and-phase-in-cards.json": {CARD, "Undo"},
            "permanent-death-amount-cards.json": {"Silent-Chant Zubera"},
            "ability-kind-cost-modifier-cards.json": {"Bonesplitter"},
            "ability-source-counter-cards.json": {"Prodigal Pyromancer"},
            "ability-label-composition-cards.json": {"Unsummon"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "controlled-attacker-delayed-phase-out-cards.json": {"Time Warp"},
        }
        pieces = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in document["cards"]} == names, filename
            oracle_ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "spell-target-and-phase-in.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Spell target and phase-in", [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def test_whole_card_closes_both_original_triggers_and_rejects_forged_scope(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, registry = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = {p.event: p for p in registry.programs_for_oracle(card.oracle_id)}
        first, second = (programs[event] for event in (TARGET_ANNOUNCEMENT_EVENT, PHASE_IN_EVENT))
        self.assertIsNone(first.target_schema)
        self.assertIsNone(second.target_schema)
        self.assertEqual(SPELL_TARGET_PHASE_OUT_SUBJECT, validate_phase_out_effect(first.effects[0]))
        self.assertEqual("spirit", observed_subtype(first.event_condition, spell_target=True))
        self.assertEqual("spirit", observed_subtype(second.event_condition, spell_target=False))
        self.assertTrue(second.effects[0]["tapped"])
        for program, validator, capability in ((first, spell_target_phase_out_runtime_dependencies, SPELL_TARGET_PHASE_OUT_CAPABILITY),
                                                 (second, phase_in_token_runtime_dependencies, PHASE_IN_CAPABILITY)):
            with self.subTest(event=program.event):
                self.assertIn(capability, validator(program))
                with self.assertRaises(ValueError):
                    validator(replace(program, event_condition={}))
                deps = tuple(value for value in program.capability_dependencies if value != capability)
                forged = replace(program, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                closed = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(closed["strict"], closed)
        for old, new in (("the target of a spell", "the target of a spell or ability"),
                         ("phases in, create", "phases out, create"), ("a tapped 1/1", "a 2/2")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
        with patch("quorune.compiler.phase_occurrence_templates.phase_occurrence_binding_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)

    def start(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def order(self, game, *, put_on_top=None):
        while game.state.pending_decision is not None and game.state.pending_decision.kind == "trigger.order":
            items = [item for batch in game.state.pending_trigger_batches for item in batch.items]
            refs = [item.ref for item in sorted(items, key=lambda item: item.payload["context"].get("card") == put_on_top)]
            result = game.act(game.pending_principals()[0], {"action_id": "order", "triggers": refs})
            self.assertTrue(result.ok, result.summary)

    def triggers(self, game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability" and item.source_object_id == source.object_id]

    def test_actual_spell_targets_each_spirit_and_simultaneous_phase_in_creates_tapped_tokens(self):
        game = self.session(502104)
        king = self.add(game.engine, CARD)
        spirit = self.add(game.engine, "Silent-Chant Zubera")
        equipment = self.add(game.engine, "Bonesplitter")
        spell = self.add(game.engine, "Undo", zone="hand")
        next_turn = self.add(game.engine, "Time Warp", zone="hand")
        attach_objects(game.state.cards, equipment, spirit, source_timestamp=equipment.zone_timestamp, players=game.state.players)
        king.tapped = spirit.tapped = True
        identities = {card.object_id: card.logical_object_id for card in (king, spirit, equipment)}
        self.start(game, king, spell, next_turn, mana={"C": 5, "U": 6})
        self.cast(game, "A", spell, [king.ref, spirit.ref])
        self.order(game, put_on_top=king.ref)
        self.assertEqual({king.ref, spirit.ref}, {item.context.get("card") for item in self.triggers(game, king)})
        self.assertEqual(2, len(self.triggers(game, king)))
        self.assertTrue(all(not item.targets for item in self.triggers(game, king)))
        game = self.replay(game, load=True)
        self.assertEqual(king.ref, game.state.stack[-1].context.get("card"))
        self.resolve_top(game)
        self.assertTrue(game.state.cards[king.object_id].phased_out)
        self.assertFalse(game.state.cards[spirit.object_id].phased_out)
        self.resolve_top(game)
        self.assertTrue(all(game.state.cards[card.object_id].phased_out for card in (king, spirit, equipment)))
        self.assertTrue(game.state.cards[equipment.object_id].phasing_record.indirect)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.cast(game, "A", next_turn, ["A"])
        self.assertIsNone(self.resolve(game))
        self.finish_turn(game)
        self.until(game, lambda g: bool(self.triggers(g, king)) or getattr(g.state.pending_decision, "kind", None) == "trigger.order")
        self.order(game)
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertEqual({king.ref, spirit.ref}, {item.context.get("card") for item in self.triggers(game, king)})
        self.assertEqual(2, len(self.triggers(game, king)))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.assertFalse(any(game.state.cards[card.object_id].phased_out for card in (king, spirit, equipment)))
        self.assertTrue(all(not game.state.cards[card.object_id].tapped for card in (king, spirit)))
        self.assertEqual(identities, {oid: game.state.cards[oid].logical_object_id for oid in identities})
        self.assertEqual(spirit.object_id, game.state.cards[equipment.object_id].attached_to)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(2, len(tokens))
        for token in tokens:
            data = game.engine._effective_card_data(token)
            self.assertEqual(("A", True, "1", "1"), (token.controller, token.tapped, str(data["power"]), str(data["toughness"])))
            self.assertIn("flying", {keyword.casefold() for keyword in data["keywords"]})
            self.assertIn("spirit", game.engine._type_parts(data["type_line"])[1])
            self.assertEqual(["W"], list(data["colors"]))
        self.replay(game, load=True)

    def test_actual_ability_target_does_not_phase_but_opponent_spell_target_does(self):
        game = self.session(1157004)
        king = self.add(game.engine, CARD)
        source = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        spell = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        source.acquired_control_turn_count = -1
        game.state.players["B"].mana_pool.update(U=1)
        self.start(game, king, source, spell)
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [a for a in actions if a["id"].startswith(f"activate:{source.ref}:")]
        result = game.act("pilot:B", {"action_id": offer["id"], "targets": [king.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([], self.triggers(game, king))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[king.object_id].phased_out)
        self.assertEqual(1, game.state.cards[king.object_id].marked_damage)
        self.cast(game, "B", spell, [king.ref])
        trigger, = self.triggers(game, king)
        self.assertEqual(("spell", king.ref), (trigger.context.get("stack_kind"), trigger.context.get("card")))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[king.object_id].phased_out)
        self.assertEqual("battlefield", game.state.cards[king.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)
