"""Native evidence for one origin-scoped, independently subscribed cast or entry trigger."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler import fixed_public_multi_event_bindings as pairs
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from quorune.trigger_discovery import semantic_event_condition_matches
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FALDORN = "Faldorn, Dread Wolf Herald"
LOST = "The Lost and the Damned"
PROFILE = "commander_review"
MULTI_EVENT = "trigger.subscription.fixed_multi_event"
ENTRY = "trigger.event.normalized_zone_change"
CAST = "trigger.event.normalized_spell_cast"
FIXTURES = ROOT / "tests" / "fixtures"


class ControlledOriginDualTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Sol Ring"},
            "all-kind-counter-cards.json": {"Reverberate"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "flashback-cards.json": {"Think Twice"},
            "forest-dryad-x-token-cards.json": {"Awaken the Woods"},
            "noncreature-subtype-quantity-cards.json": {"Lava Spike"},
            "query-bounded-library-search-cards.json": {"Explosive Vegetation", "Forest"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = [FIXTURES / "controlled-origin-dual-trigger-cards.json"]
        for name, names in support.items():
            record = json.loads((FIXTURES / name).read_bytes())
            record["cards"] = [row for row in record["cards"] if row["name"] in names]
            assert names == {row["name"] for row in record["cards"]}, name
            ids = {row["oracle_id"] for row in record["cards"]}
            record["rulings"] = [row for row in record.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(record), encoding="utf-8")
            pieces.append(part)
        path = folder / "controlled-origin-dual-trigger.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled origin trigger", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _offer(self, game, seat, prefix):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith(prefix)]
        return action

    def _submit(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _tokens(self, game, subtype, seat="A"):
        tokens = []
        for card in game.state.cards.values():
            if card.zone != "battlefield" or not card.is_token or card.controller != seat:
                continue
            data = game.engine._effective_card_data(card)
            _, subtypes, _ = game.engine._type_parts(data["type_line"])
            if subtype in subtypes:
                tokens.append(data)
        return tokens

    def _assert_tokens(self, game, subtype, quantity, color, size):
        tokens = self._tokens(game, subtype)
        self.assertEqual(quantity, len(tokens))
        self.assertTrue(all(row["colors"] == [color] and (int(row["power"]), int(row["toughness"])) == (size, size)
                            for row in tokens))

    def _order_own_land_triggers(self, game, source):
        choice = game.state.pending_decision
        if choice is not None and choice.kind == "trigger.order":
            self.assertEqual(["A"], list(choice.actors))
            items = choice.payload_by_actor["A"]["triggers"]
            self.assertEqual(2, len(items))
            self._submit(game, "A", {"action_id": "order", "triggers": [row["id"] for row in items]})
        self.assertEqual(2, self.trigger_count(game, source))

    def test_both_complete_originals_keep_both_subscriptions_and_the_separate_activation(self):
        for name, subtype, color, count in ((FALDORN, "Wolf", "G", 2), (LOST, "Spawn", "R", 3)):
            with self.subTest(name=name):
                card, programs = self._original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                spec, = [p for p in programs if p.event == "spell.cast"]
                subs = FixedEventSubscriptionSet.from_condition(spec.event_condition)
                self.assertEqual(("spell.cast", "land.enter"), tuple(row.event for row in subs.subscriptions))
                self.assertEqual(subs.subscriptions[0].condition, subs.subscriptions[1].condition)
                self.assertLessEqual({MULTI_EVENT, ENTRY, CAST}, set(spec.capability_dependencies))
                effect, = spec.effects
                self.assertEqual(("create_token", 1, subtype + " Token"),
                                 (effect["op"], effect["quantity"], effect["name"]))
                definition = effect["characteristics"]
                self.assertEqual([color], list(definition["colors"]))
                self.assertEqual((str(count), str(count)), (definition["power"], definition["toughness"]))
                self.assertEqual(name == FALDORN, any(program.event.startswith("activate") for program in programs))

    def test_joint_origin_excludes_mixed_duplicate_and_nonentry_arms_and_open_results(self):
        spell = "you cast a spell from exile"
        entry = "a land you control enters from exile"
        fixed = "create a 2/2 green Wolf creature token."
        for first, second, body in (
            (spell, entry.replace("exile", "anywhere other than your hand"), fixed),
            (spell, spell, fixed),
            (spell, "you play a land from exile", fixed),
            (spell, "a land an opponent controls enters from exile", fixed),
            (spell, entry, "create two 2/2 green Wolf creature tokens."),
            (spell, entry, "create a 2/2 green Wolf creature token with flying."),
            (spell, entry, fixed + " Then draw a card."),
        ):
            with self.subTest(first=first, second=second, body=body):
                self.assertIsNone(pairs.fixed_public_multi_event_binding_spec(f"Whenever {first} or {second}, {body}"))

        clause = ("Whenever a land you control enters from anywhere other than your hand "
                  "or you cast a spell from anywhere other than your hand, " + fixed)
        paired = pairs.fixed_public_multi_event_binding_spec(clause)
        game = self.session(3701500)
        source = self.add(game.engine, LOST)
        for subscription in FixedEventSubscriptionSet.from_condition(paired.condition).subscriptions:
            for facts, expected in (
                ({"controller": "A", "from": "hand", "owner": "A"}, False),
                ({"controller": "A", "from": "hand", "owner": "B"}, True),
                ({"controller": "A", "from": "hand"}, False),
                ({"controller": "A", "from": "outside"}, True),
                ({"controller": "A", "from": "graveyard"}, True),
                ({"controller": "B", "from": "graveyard"}, False),
                ({"controller": "A", "owner": "B"}, False),
            ):
                with self.subTest(event=subscription.event, facts=facts):
                    self.assertEqual(expected, semantic_event_condition_matches(
                        game.engine, subscription.condition, source=source, context=facts))

    def test_actual_faldorn_pays_to_exile_then_uses_the_same_allowed_card_as_land_or_spell(self):
        for index, name in enumerate(("Forest", "Lava Spike")):
            with self.subTest(name=name):
                game = self.session(3701510 + index)
                source = self.add(game.engine, FALDORN)
                source.acquired_control_turn_count = -1
                card = self.add(game.engine, name, zone="library", ref="faldorn-known-top")
                library = game.state.players["A"].zones["library"]
                library.remove(card.object_id)
                library.append(card.object_id)
                discard = self.add(game.engine, "Sol Ring", zone="hand", ref="faldorn-paid-discard")
                self.prepare(game, mana={"C": 1, "R": 1})
                self.checkpoint(game)
                self.assertNotIn(card.ref, json.dumps(game.packet("pilot:B", full=True)))
                offer = self._offer(game, "A", f"activate:{source.ref}:")
                self._submit(game, "A", {"action_id": offer["id"], "cost_cards": [discard.ref],
                                          "pay": "manual", "payment": {"C": 1}})
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertEqual("graveyard", game.state.cards[discard.object_id].zone)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", game.state.cards[card.object_id].zone)
                self.assertEqual(0, self.trigger_count(game, source))
                game = self.replay(game, load=True)
                if name == "Forest":
                    play = self._offer(game, "A", f"play-land:{card.ref}")
                    self._submit(game, "A", {"action_id": play["id"]})
                    self.assertEqual(0, game.state.players["A"].land_plays_remaining)
                else:
                    self.cast_for(game, "A", card, ["B"])
                    stack = game.state.stack[-2]
                    self.assertEqual(card.object_id, stack.card_object_id)
                    self.assertEqual(40, game.state.players["B"].life)
                    self.assertEqual(0, len(self._tokens(game, "wolf")))
                self.assertEqual(1, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if name == "Forest" else "graveyard", game.state.cards[card.object_id].zone)
                self.assertEqual(40 if name == "Forest" else 37, game.state.players["B"].life)
                self._assert_tokens(game, "wolf", 1, "G", 2)
                self.replay(game, load=True)

    def test_actual_lost_counts_each_library_placed_land_and_skips_its_controllers_hand(self):
        game = self.session(3701520)
        source = self.add(game.engine, LOST)
        other = self.add(game.engine, FALDORN)
        hand = self.add(game.engine, "Forest", zone="hand", ref="own-hand-land")
        spell = self.add(game.engine, "Explosive Vegetation", zone="hand")
        choices = [self.add(game.engine, "Forest", zone="library", ref=f"placed-from-library-{i}") for i in range(2)]
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        play = self._offer(game, "A", f"play-land:{hand.ref}")
        self._submit(game, "A", {"action_id": play["id"]})
        self.assertFalse(game.state.stack)
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self.cast(game, "A", spell)
        self.assertEqual(0, self.trigger_count(game, source))
        decision = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, list(decision.actors)))
        for seat in "BCD":
            self.assertNotIn(choices[0].ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        self._submit(game, "A", {"action_id": "choose", "search_cards": [c.ref for c in choices]})
        self.assertTrue(all(game.state.cards[c.object_id].zone == "battlefield" and game.state.cards[c.object_id].tapped
                            for c in choices))
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self._order_own_land_triggers(game, source)
        self.assertEqual(0, self.trigger_count(game, other))
        self.assertIsNone(self.resolve(game))
        self._assert_tokens(game, "spawn", 2, "R", 3)
        self._assert_tokens(game, "wolf", 0, "G", 2)
        self.replay(game, load=True)

    def test_actual_lost_counts_each_own_land_token_and_skips_the_opponents_source(self):
        game = self.session(3701530)
        source = self.add(game.engine, LOST)
        opponent = self.add(game.engine, LOST, seat="B", ref="lost-opposing-source")
        woods = self.add(game.engine, "Awaken the Woods", zone="hand")
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)
        offer = self._offer(game, "A", f"cast:{woods.ref}")
        self._submit(game, "A", {"action_id": offer["id"], "x": 2, "targets": [], "pay": "auto"})
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual(2, len(self._tokens(game, "dryad")))
        self._order_own_land_triggers(game, source)
        self.assertEqual(0, self.trigger_count(game, opponent))
        self.assertIsNone(self.resolve(game))
        self._assert_tokens(game, "spawn", 2, "R", 3)
        self.assertEqual([], self._tokens(game, "spawn", "B"))
        self.replay(game, load=True)

    def test_actual_lost_counts_its_graveyard_cast_once_through_copy_and_opposing_cast(self):
        game = self.session(3701540)
        source = self.add(game.engine, LOST)
        other = self.add(game.engine, FALDORN)
        own = self.add(game.engine, "Think Twice", zone="graveyard", ref="covered-flashback")
        enemy = self.add(game.engine, "Think Twice", seat="B", zone="graveyard", ref="opposing-flashback")
        copy = self.add(game.engine, "Reverberate", zone="hand")
        private = self.add(game.engine, "Sol Ring", seat="C", zone="hand", ref="unused-private-sol")
        self.prepare(game, mana={"C": 2, "U": 1, "R": 2})
        game.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        for seat, card in (("A", own), ("B", enemy)):
            offer = self._offer(game, seat, f"cast:{card.ref}")
            option, = [row["id"] for row in offer["cost_options"] if row["id"] == "flashback"]
            self._submit(game, seat, {"action_id": offer["id"], "cost_option": option,
                                       "pay": "manual", "payment": {"C": 2, "U": 1}})
            self.assertEqual(1, self.trigger_count(game, source))
            self.assertEqual(0, self.trigger_count(game, other))
        original, = [item for item in game.state.stack if item.card_object_id == own.object_id]
        self.cast_for(game, "A", copy, [original.ref])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(0, self.trigger_count(game, other))
        self.assertIsNone(self.resolve(game))
        self._assert_tokens(game, "spawn", 1, "R", 3)
        self._assert_tokens(game, "wolf", 0, "G", 2)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_omitting_the_origin_pair_returns_both_complete_originals_to_one_residual(self):
        with patch.object(pairs, "_entry_or_cast_origin_spec", return_value=None):
            for name in (FALDORN, LOST):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertEqual("trigger", residual.kind)
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_subscription_authority_withdrawal_denies_both_complete_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for omitted in (MULTI_EVENT, ENTRY, CAST):
            with self.subTest(omitted=omitted):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == omitted).update(
                    status="blocked", blockers=["Scoped dual origin event authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((omitted,), profile=PROFILE)
                self.assertIn(f"status:{omitted}:blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in (FALDORN, LOST):
                    report, _ = register_card(self.db, registry, name)
                    self.assertFalse(report["strict_capability_ready"], report)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
