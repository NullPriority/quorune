"""Native cast-linked copies count only the actually sacrificed creatures."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import StateInvariantError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.casting.optional_creature_payments import SACRIFICE_CREDIT_CAPABILITY
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.paid_sacrifice_copy import (
    CAPABILITY, PAYMENT_FIELD, SEMANTIC_KEY, fixed_creature_copy_price, validated_paid_sacrifice_copy_trigger,
)
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class PaidSacrificeCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "paid-sacrifice-copy.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/paid-sacrifice-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid sacrifice copy", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_closed_price_and_required_authority(self):
        source = self.db.lookup("Plumb the Forbidden")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(source, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ability, = ir.faces[0].nodes
        self.assertEqual(["draw", "lose_life"], [effect["op"] for effect in ability.effects])
        self.assertIn(CAPABILITY, ability.capability_dependencies)
        self.assertEqual(101, fixed_creature_copy_price(101).required_count(None))
        for invalid in (True, 0, -1):
            with self.assertRaises(ValueError):
                fixed_creature_copy_price(invalid)
        for old, new in (("one or more creatures", "two artifacts"), ("each creature", "each artifact"),
                         ("draw a card and lose 1 life", "draw two cards and lose 1 life")):
            revised = replace(source, oracle_text=source.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(revised, **options).status)
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        target = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        target.update(status="blocked", blockers=["Test-only paid copy authority withdrawal"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        generated = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        known = {row["test_id"] for row in generated["declarations"]}
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(source, **{**options, "capability_registry": registry}).status)

    def game(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        spell = self.add(game.engine, "Plumb the Forbidden", zone="hand")
        self.assert_closed(game, spell)
        return game, spell

    def test_unpaid_cast_has_no_trigger_and_resolves_one_body(self):
        game, spell = self.game(6051601)
        self.add(game.engine, "Grizzly Bears", ref="available-unpaid-creature")
        self.prepare(game, mana={"B": 1, "C": 1})
        self.checkpoint(game)
        hand_before = len(game.state.players["A"].zones["hand"])
        action = self.action(game, "A", spell)
        self.assertTrue(any("+creature-sacrifice-copy:1" in item["id"] for item in action["cost_options"]))
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.settle(game)
        self.assertEqual(hand_before, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(39, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_extus_sacrifice_credit_pays_commander_tax_and_replays_avatar(self):
        name = "Extus, Oriq Overlord // Awaken the Blood Avatar"
        self.deck = DeckDefinition("Avatar price", [
            DeckEntry(name, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [name])
        for index, (previous_casts, colorless) in enumerate(((3, 4), (0, 0))):
            with self.subTest(previous_casts=previous_casts):
                game = self.session(6051620 + index)
                game.state.config.semantic_policy = "trusted_only"
                player = game.state.players["A"]
                spell = game.state.cards[player.zones["command"][0]]
                self.assert_closed(game, spell)
                player.commander_casts[spell.oracle_id] = previous_casts
                offerings = [self.add(game.engine, "Grizzly Bears", ref=f"avatar-price-{i}") for i in range(4)]
                opponents = {
                    seat: [self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"avatar-{seat}-{i}") for i in range(2)]
                    for seat in "BC"
                } if previous_casts else {}
                self.prepare(game, mana={"C": colorless, "B": 1, "R": 1})
                self.checkpoint(game)
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action, option = next(
                    (row, choice) for row in actions if row.get("card") == spell.ref
                    for choice in row.get("cost_options", ())
                    if choice["id"].endswith("+creature-sacrifice-credit:4")
                )
                requirements = option["requirements"]
                self.assertEqual((colorless, 1, 1),
                                 (requirements["GENERIC"], requirements["B"], requirements["R"]))
                self.assertCountEqual(
                    [obj.ref for obj in offerings], option["choice_schema"]["sacrifice_cards"]["legal_refs"],
                )
                request = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
                before = authoritative_state_hash(game.state)
                illegal = [offerings[0].ref] * 4
                self.assertFalse(game.act("pilot:A", {**request, "sacrifice_cards": illegal}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                paid = game.act("pilot:A", {**request, "sacrifice_cards": [obj.ref for obj in offerings]})
                self.assertTrue(paid.ok, paid.summary)
                self.assertTrue(all(game.state.cards[obj.object_id].zone == "graveyard" for obj in offerings))
                self.assertEqual(previous_casts + 1, game.state.players["A"].commander_casts[spell.oracle_id])
                for seat, bodies in opponents.items():
                    self.until(game, lambda s: s.state.pending_decision.kind == "choice.apnap")
                    self.assertEqual([seat], list(game.state.pending_decision.actors))
                    if seat == "C":
                        self.assertEqual("battlefield", game.state.cards[opponents["B"][0].object_id].zone)
                        game = self.replay(game, load=True)
                    chosen = game.act("pilot:" + seat, {"action_id": "choose", "cards": [bodies[0].ref]})
                    self.assertTrue(chosen.ok, chosen.summary)
                self.until(game, lambda s: s.state.pending_decision.kind == "state.commander_zone")
                returned = game.act("pilot:A", {"action_id": "command"})
                self.assertTrue(returned.ok, returned.summary)
                self.assertEqual("command", game.state.cards[spell.object_id].zone)
                self.settle(game)
                for sacrificed, kept in opponents.values():
                    self.assertEqual("graveyard", game.state.cards[sacrificed.object_id].zone)
                    self.assertEqual("battlefield", game.state.cards[kept.object_id].zone)
                avatars = [
                    game.state.cards[object_id] for object_id in game.state.players["A"].zones["battlefield"]
                    if game.state.cards[object_id].is_token
                ]
                self.assertEqual(1, len(avatars), [event.code for event in game.state.events[-30:]])
                avatar, = avatars
                characteristics = game.engine._effective_card_data(avatar)
                self.assertIn("Avatar", characteristics["type_line"])
                self.assertCountEqual(["B", "R"], characteristics["colors"])
                self.assertEqual(
                    (3, 6), tuple(game.engine._numeric_stat(avatar.object_id, stat) for stat in ("power", "toughness")),
                )
                self.assertIn("haste", game.engine._combat_keywords(avatar))
                life_before = {seat: game.state.players[seat].life for seat in "ABCD"}
                self.attack(game, [(avatar, "B")])
                self.assertEqual(1, self.trigger_count(game, avatar))
                self.resolve_top(game)
                for seat, life in life_before.items():
                    self.assertEqual(life - (3 if seat != "A" else 0), game.state.players[seat].life)
                self.replay(game, load=True)

    def test_extus_sacrifice_credit_is_closed_to_the_exact_typed_price(self):
        from quorune.compiler.creature_sacrifice_credit import creature_sacrifice_credit_template

        source = self.db.lookup("Extus, Oriq Overlord // Awaken the Blood Avatar")
        result = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        spell, avatar = result.faces[1].nodes
        self.assertIn(SACRIFICE_CREDIT_CAPABILITY, spell.capability_dependencies)
        self.assertEqual(["choose_cards_apnap", "create_token"], [effect["op"] for effect in spell.effects])
        self.assertEqual("$controller", spell.effects[1]["controller"])
        self.assertEqual("creature.attacks", avatar.event)
        price = source.faces[1]["oracle_text"].splitlines()[0]
        self.assertIsNotNone(creature_sacrifice_credit_template(price))
        for unsupported in (price.replace("{2}", "{3}"), price.replace("creatures", "permanents")):
            self.assertIsNone(creature_sacrifice_credit_template(unsupported))
        with patch("quorune.compiler.creature_sacrifice_credit.creature_sacrifice_credit_template", return_value=None), \
                patch("quorune.compiler.spell_additional_cost_nodes.creature_sacrifice_credit_template", return_value=None):
            withdrawn = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", withdrawn.status)

    def order_triggers(self, game):
        while game.state.pending_decision is not None and game.state.pending_decision.kind == "trigger.order":
            self.assertEqual(["A"], list(game.state.pending_decision.actors))
            packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
            result = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in packet["triggers"]]})
            self.assertTrue(result.ok, result.summary)

    def paid_scene(self, seed):
        game, spell = self.game(seed)
        paid_ability = self.add(game.engine, "Archmage Emeritus", ref="plumb-sacrificed-magecraft")
        remaining_ability = self.add(game.engine, "Archmage Emeritus", ref="plumb-surviving-magecraft")
        creature = self.add(game.engine, "Grizzly Bears", ref="plumb-other-price")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="plumb-invalid-price")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.assert_closed(game, remaining_ability)
        self.prepare(game, mana={"B": 1, "C": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        option = next(row for row in action["cost_options"] if "+creature-sacrifice-copy:2" in row["id"])
        command = {"action_id": action["id"], "cost_option": option["id"],
                   "sacrifice_cards": [paid_ability.ref, creature.ref], "pay": "auto"}
        for invalid in ([paid_ability.ref, foreign.ref], [creature.ref, creature.ref]):
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:A", {**command, "sacrifice_cards": invalid}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        before_hand = set(game.state.players["A"].zones["hand"])
        paid_ids = [paid_ability.logical_object_id, creature.logical_object_id]
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[paid_ability.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.order_triggers(game)
        triggers = [item for item in game.state.stack if item.kind == "triggered_ability"]
        self.assertEqual({spell.object_id, remaining_ability.object_id}, {item.source_object_id for item in triggers})
        trigger, = [item for item in triggers if item.semantic_key == SEMANTIC_KEY]
        self.assertCountEqual(paid_ids, trigger.context[PAYMENT_FIELD]["paid_incarnations"])
        self.assertEqual(2, trigger.context["copy_count"])
        return game, spell, counter, trigger, before_hand, remaining_ability

    def test_countered_spell_keeps_two_ordinary_copies_and_only_surviving_magecraft(self):
        game, spell, counter, trigger, before_hand, remaining_ability = self.paid_scene(6051602)
        changed = replace(trigger, context={**trigger.context, "copy_count": 1})
        with self.assertRaises(StateInvariantError):
            validated_paid_sacrifice_copy_trigger(changed)
        original = next(item for item in game.state.stack if item.card_object_id == spell.object_id)
        self.assertEqual(original.ref, trigger.context["copy_template"]["source_spell_ref"])
        self.cast_for(game, "B", counter, [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertTrue(any(item.ref == trigger.ref for item in game.state.stack))
        game = self.replay(game, load=True)
        for _ in range(3):
            if sum(item.kind == "spell_copy" for item in game.state.stack) == 2:
                break
            self.resolve_top(game)
            self.order_triggers(game)
        copies = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual(2, len(copies))
        self.assertEqual({original.semantic_key}, {item.semantic_key for item in copies})
        self.assertEqual({"A"}, {item.controller for item in copies})
        affected = [item for item in game.state.stack if item.kind == "triggered_ability"]
        self.assertGreaterEqual(len(affected), 2)
        self.assertEqual({remaining_ability.object_id}, {item.source_object_id for item in affected})
        self.settle(game)
        arrived = set(game.state.players["A"].zones["hand"]) - before_hand
        self.assertEqual(5, len(arrived))
        self.assertEqual(38, game.state.players["A"].life)
        for seat in "BCD":
            view = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for object_id in arrived:
                self.assertNotIn(game.state.cards[object_id].ref, view)
        self.replay(game, load=True)

    def test_copy_trigger_omission_breaks_the_native_paid_count_witness(self):
        with patch("quorune.rules.paid_sacrifice_copy.paid_sacrifice_copy_trigger_items", return_value=()):
            with self.assertRaises(AssertionError):
                self.paid_scene(6051603)
