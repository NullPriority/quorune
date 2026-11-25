from __future__ import annotations

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
from quorune.compiler.return_to_hand_templates import targeted_own_graveyard_return_to_hand_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_card_return import (
    DEATH_EXILE_ARRIVALS, DEATH_EXILE_THEN_RETURN_CAPABILITY, death_land_exile_condition, death_source_exile_effect,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_excluding_graveyard_returns import (
    SOURCE_EXCLUDING_GRAVEYARD_RETURN_CAPABILITY as CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FIXTURES = ROOT / "tests" / "fixtures"
PROFILE = "commander_review"
MYR = "Myr Retriever"
TREEFOLK = "Deadwood Treefolk"
ELF = "Elderfang Ritualist"
ATTENDANT = "Dutiful Attendant"
ALTAR = "Ashnod's Altar"
CARRION = "Carrion Thrash"
SYLVAN = "Sylvan Hierophant"
OPTIONAL_PRICE = "effect.choice.optional_fixed_mana_payment"
ELF_CARD = "Elvish Promenade"
PROVED = {
    TREEFOLK: ("types_any", ["creature"]),
    ATTENDANT: ("types_any", ["creature"]),
    ELF: ("subtypes_any", ["elf"]),
    "Morcant's Loyalist": ("subtypes_any", ["elf"]),
    "Junk Diver": ("types_any", ["artifact"]),
    MYR: ("types_any", ["artifact"]),
    "Workshop Assistant": ("types_any", ["artifact"]),
}


class SourceExcludingGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activated-self-transform-cards.json": {ALTAR, "Cremate", "Unsummon"},
            "activation-view-equivalence-cards.json": {"Ornithopter"},
            "additional-conditional-modifier-cards.json": {"Raise Dead"},
            "attached-destruction-set-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
            "fixed-behold-additional-cost-cards.json": {ELF_CARD},
            "permanent-control-exchange-cards.json": {"Avarice Totem"},
            "target-land-type-choice-cards.json": {"Cyclopean Giant"},
        }
        parts = [FIXTURES / "source-excluding-graveyard-return-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            cards = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, source
            ids = {row["oracle_id"] for row in cards}
            rulings = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps({**doc, "cards": cards, "rulings": rulings}), encoding="utf-8")
            parts.append(part)
        path = folder / "source-excluding-graveyard-returns.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source-excluding graveyard returns", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _start(self, game, cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana or {})

    def _sacrifice(self, game, source, altar):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        [offer] = [row for row in rows if row["id"].startswith(f"activate:{altar.ref}:")]
        accepted = game.act("pilot:A", {"action_id": offer["id"], "cost_cards": [source.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)

    def _offered_targets(self, game, expected):
        self.until(game, lambda g: g.state.pending_decision is not None and g.state.pending_decision.kind == "semantic.target")
        actions = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        [choose] = [action for action in actions if action["id"] == "choose"]
        self.assertEqual({card.ref for card in expected}, set(choose["target_schema"]["legal_refs"]))

    def _refuse(self, game, target):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_seven_exact_originals_keep_only_the_closed_source_excluding_trigger(self):
        for name, (predicate, values) in PROVED.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                report, registry = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                programs = registry.programs_for_oracle(card.oracle_id)
                program, = (p for p in programs if CAPABILITY in p.capability_dependencies)
                self.assertEqual({"zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
                                  "count": 1, "source_exclusion": True, predicate: values}, program.target_schema)
                self.assertEqual([{"op": "return_graveyard_card_to_owner_hand", "card": "$target.0"}], program.effects)
                self.assertEqual("permanent.enter" if name == TREEFOLK else "creature.dies.self", program.event)
                if name == TREEFOLK:
                    subscriptions = program.event_condition["event_subscriptions"]
                    self.assertEqual(["permanent.enter", "permanent.leave"], [row["event"] for row in subscriptions])
                    self.assertEqual({"zone.change", "step.begin", "counter.removed", "permanent.enter"},
                                     {value.event for value in programs})
                else:
                    self.assertIsNone(program.event_condition)
                for invalid_schema in (
                    {key: value for key, value in program.target_schema.items() if key != "source_exclusion"},
                    {**program.target_schema, "source_exclusion": 1},
                    {**program.target_schema, "owner_relation": "any"},
                    {**program.target_schema, "count": 2},
                    {**program.target_schema, "mana_value_max": 2},
                ):
                    changed = replace(program, target_schema=invalid_schema)
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile=PROFILE,
                    )["strict"], invalid_schema)
                for invalid_frame in (
                    replace(program, active_zone="graveyard", event="activate", event_condition=None),
                    replace(program, effects=[{"op": "note", "effects": program.effects}]),
                ):
                    result = bind_semantic_program_runtime(invalid_frame, capability_registry=self.registry, profile=PROFILE)
                    self.assertTrue(any("exact self-zone trigger and target" in blocker for blocker in result["blockers"]))
                deps = tuple(owner for owner in program.capability_dependencies if owner != CAPABILITY)
                undeclared = replace(program, capability_dependencies=deps,
                                     capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(undeclared, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        for text in (
            "Return another target creature card with mana value 2 or less from your graveyard to your hand.",
            "Return another target sorcery card from your graveyard to your hand.",
            "Return another target creature card from a graveyard to your hand.",
            "Return another target creature card from your graveyard to your hand. Draw a card.",
        ):
            self.assertIsNone(targeted_own_graveyard_return_to_hand_effect_template(text), text)

    def test_fresh_compiler_source_exclusion_omission_is_distinguished(self):
        card = self.db.lookup(MYR, fuzzy=False)

        def witness():
            report, registry = register_card(self.db, self.registry, MYR)
            self.assertTrue(report["strict_capability_ready"], report["blockers"])
            program, = registry.programs_for_oracle(card.oracle_id)
            self.assertIs(program.target_schema.get("source_exclusion"), True)
            self.assertIn(CAPABILITY, program.capability_dependencies)

        witness()
        with patch("quorune.compiler.return_to_hand_templates.source_excluding_graveyard_return_schema",
                   side_effect=lambda spec: spec.to_target_schema()):
            with self.assertRaises(AssertionError):
                witness()
        witness()

    def test_actual_myr_retriever_omits_its_departed_source_and_returns_the_other_artifact(self):
        game = self.session(60303111)
        source = self.add(game.engine, MYR)
        altar = self.add(game.engine, ALTAR)
        other = self.add(game.engine, "Ornithopter", zone="graveyard", ref="retrieve-eligible")
        foreign = self.add(game.engine, "Ornithopter", seat="B", zone="graveyard", ref="retrieve-foreign")
        private = self.add(game.engine, "Raise Dead", seat="C", zone="hand", ref="retrieve-private")
        self._start(game, (source, altar))
        self.checkpoint(game)
        self._sacrifice(game, source, altar)
        game = self.replay(game, load=True)
        self._offered_targets(game, (other,))
        self._refuse(game, source)
        self._refuse(game, foreign)
        self.target(game, "A", (other.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        for card in (source, foreign):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_attendant_with_only_the_source_in_its_graveyard_needs_no_target_choice(self):
        game = self.session(60303112)
        source = self.add(game.engine, ATTENDANT)
        altar = self.add(game.engine, ALTAR)
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard")
        self._start(game, (source, altar))
        self.checkpoint(game)
        cursor = len(game.state.events)
        self._sacrifice(game, source, altar)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        self.assertTrue(any(row.code == "stack.trigger.removed" for row in game.state.events[cursor:]))
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.replay(game, load=True)

    def test_actual_elf_trigger_returns_a_noncreature_elf_card_after_target_revalidation(self):
        game = self.session(60303113)
        source = self.add(game.engine, ELF)
        altar = self.add(game.engine, ALTAR)
        elf_card = self.add(game.engine, ELF_CARD, zone="graveyard", ref="elf-noncreature")
        nonelf = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="elf-wrong-subtype")
        self._start(game, (source, altar))
        self.assertIn("Sorcery", self.db.lookup(ELF_CARD, fuzzy=False).type_line)
        self.checkpoint(game)
        self._sacrifice(game, source, altar)
        self._offered_targets(game, (elf_card,))
        self._refuse(game, nonelf)
        self.target(game, "A", (elf_card.ref,))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[elf_card.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[nonelf.object_id].zone)
        self.replay(game, load=True)

    def test_actual_treefolk_enters_with_vanishing_and_uses_both_source_occurrences(self):
        game = self.session(60303114)
        source = self.add(game.engine, TREEFOLK, zone="hand")
        first = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="treefolk-entry-result")
        second = self.add(game.engine, "Ornithopter", zone="graveyard", ref="treefolk-departure-result")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self._start(game, (source, bounce), {"C": 5, "G": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(3, game.state.cards[source.object_id].counters["time"])
        self._offered_targets(game, (first, second))
        self.target(game, "A", (first.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self._offered_targets(game, (second,))
        self.target(game, "A", (second.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[second.object_id].zone)
        self.replay(game, load=True)

    def test_actual_departed_graveyard_target_fizzles_without_substituting_the_source(self):
        game = self.session(60303115)
        source = self.add(game.engine, MYR)
        altar = self.add(game.engine, ALTAR)
        target = self.add(game.engine, "Ornithopter", zone="graveyard", ref="retrieve-stale")
        response = self.add(game.engine, "Cremate", seat="B", zone="hand")
        self._start(game, (source, altar, response))
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        self._sacrifice(game, source, altar)
        self._offered_targets(game, (target,))
        self.target(game, "A", (target.ref,))
        self.cast(game, "B", response, [target.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_original_carrion_thrash_uses_one_paid_return_with_its_target_announced(self):
        original = self.db.lookup(CARRION, fuzzy=False)
        report, registry = register_card(self.db, self.registry, CARRION)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = registry.programs_for_oracle(original.oracle_id)
        self.assertEqual("creature.dies.self", program.event)
        self.assertIs(program.target_schema["source_exclusion"], True)
        effect, = program.effects
        self.assertEqual("offer_optional_mana_payment", effect["op"])
        self.assertEqual({"GENERIC": 2}, {key: value for key, value in effect["cost"].items() if value})
        self.assertEqual([{"op": "return_graveyard_card_to_owner_hand", "card": "$target.0"}], effect["effects"])
        self.assertTrue({CAPABILITY, OPTIONAL_PRICE} <= set(program.capability_dependencies))
        for fields in (
            {"cost": {**effect["cost"], "GENERIC": True}},
            {"player": "$target.0"},
            {"effects": [*effect["effects"], {"op": "draw", "player": "$controller", "count": 1}]},
        ):
            changed = replace(program, effects=[{**effect, **fields}])
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"], fields)
        with patch("quorune.rules.source_excluding_graveyard_returns._optional_source_death_payment_dependencies", return_value=()):
            omitted, _ = register_card(self.db, self.registry, CARRION)
            self.assertFalse(omitted["strict_capability_ready"], omitted)

    def test_actual_carrion_payment_follows_target_selection_and_resumes_pay_or_decline(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                game = self.session(60303120 + pay)
                source = self.add(game.engine, CARRION)
                altar = self.add(game.engine, ALTAR)
                target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="carrion-return")
                private = self.add(game.engine, "Raise Dead", seat="C", zone="hand", ref="carrion-private")
                self._start(game, (source, altar))
                self.checkpoint(game)
                self._sacrifice(game, source, altar)
                self._offered_targets(game, (target,))
                self._refuse(game, source)
                self.assertEqual(2, game.state.players["A"].mana_pool["C"])
                self.target(game, "A", (target.ref,))
                pending = self.resolve(game)
                self.assertEqual("semantic.choice", pending.kind)
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.assertEqual(2, game.state.players["A"].mana_pool["C"])
                game = self.replay(game, load=True)
                self.assertEqual(["pilot:A"], game.pending_principals())
                before = authoritative_state_hash(game.state)
                command = {"action_id": "choose", "pay": pay}
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if pay else "graveyard", game.state.cards[target.object_id].zone)
                self.assertEqual(0 if pay else 2, game.state.players["A"].mana_pool["C"])
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                for seat in "ABD":
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.replay(game, load=True)

    def test_actual_carrion_lost_target_never_offers_its_optional_payment(self):
        game = self.session(60303122)
        source = self.add(game.engine, CARRION)
        altar = self.add(game.engine, ALTAR)
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="carrion-lost-target")
        response = self.add(game.engine, "Cremate", seat="B", zone="hand")
        self._start(game, (source, altar, response))
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        self._sacrifice(game, source, altar)
        self._offered_targets(game, (target,))
        self.target(game, "A", (target.ref,))
        self.cast(game, "B", response, [target.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_verified_optional_payment_withdrawal_keeps_the_unpaid_myr_trigger(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == OPTIONAL_PRICE).update(
            status="blocked", blockers=["Optional fixed resolution price withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((OPTIONAL_PRICE,), profile=PROFILE)
        self.assertIn(f"status:{OPTIONAL_PRICE}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, CARRION)
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, MYR)
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_complete_sylvan_program_uses_committed_exile_then_one_owned_return(self):
        original = self.db.lookup(SYLVAN, fuzzy=False)
        report, registry = register_card(self.db, self.registry, SYLVAN)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = registry.programs_for_oracle(original.oracle_id)
        self.assertEqual("creature.dies", program.event)
        self.assertEqual(death_land_exile_condition(), program.event_condition)
        self.assertEqual([death_source_exile_effect(),
                          {"op": "return_graveyard_card_to_owner_hand", "card": "$target.0"}], program.effects)
        self.assertIs(program.target_schema["source_exclusion"], True)
        self.assertEqual("you", program.target_schema["owner_relation"])
        for altered in (
            replace(program, effects=program.effects[::-1]),
            replace(program, target_schema={**program.target_schema, "source_exclusion": False}),
            replace(program, event_condition=None),
            replace(program, effects=[{**program.effects[0], "death_exile_version": True}, program.effects[1]]),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != DEATH_EXILE_THEN_RETURN_CAPABILITY)
        omitted = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + DEATH_EXILE_THEN_RETURN_CAPABILITY, binding["blockers"])
        with patch("quorune.compiler.event_card_return_templates._source_death_exile_then_owned_return", return_value=None):
            residual, _ = register_card(self.db, self.registry, SYLVAN)
            self.assertFalse(residual["strict_capability_ready"], residual)

    def test_actual_sylvan_exiles_the_borrowed_source_before_returning_the_controllers_target(self):
        game = self.session(60303131)
        source = self.add(game.engine, SYLVAN, seat="B")
        altar = self.add(game.engine, ALTAR)
        totem = self.add(game.engine, "Avarice Totem")
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="sylvan-controller-target")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="sylvan-owner-decoy")
        private = self.add(game.engine, "Raise Dead", seat="C", zone="hand", ref="sylvan-private")
        self._start(game, (source, altar, totem), {"C": 5})
        self.checkpoint(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{totem.ref}:")]
        exchange = game.act("pilot:A", {"action_id": offer["id"], "targets": [source.ref],
                                        "pay": "manual", "payment": {"C": 5}})
        self.assertTrue(exchange.ok, exchange.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[source.object_id].controller)
        self.assertEqual("B", game.state.cards[source.object_id].owner)
        self._sacrifice(game, source, altar)
        self._offered_targets(game, (target,))
        self._refuse(game, foreign)
        item, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual("A", item.controller)
        self.assertEqual([{"object_id": source.object_id, "reference": source.ref,
                          "logical_object_id": game.state.cards[source.object_id].logical_object_id,
                          "zone": "graveyard"}], item.context[DEATH_EXILE_ARRIVALS])
        self.target(game, "A", (target.ref,))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        moved_source = game.state.cards[source.object_id]
        returned = game.state.cards[target.object_id]
        self.assertEqual(("exile", "hand"), (moved_source.zone, returned.zone))
        self.assertLess(moved_source.zone_timestamp, returned.zone_timestamp)
        self.assertIn(moved_source.object_id, game.state.players["B"].zones["exile"])
        self.assertIn(returned.object_id, game.state.players["A"].zones["hand"])
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_sylvan_distinguishes_source_departure_from_losing_its_target(self):
        for lost_source in (False, True):
            with self.subTest(lost_source=lost_source):
                game = self.session(60303132 + lost_source)
                source = self.add(game.engine, SYLVAN)
                altar = self.add(game.engine, ALTAR)
                target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="sylvan-revalidation")
                response = self.add(game.engine, "Cremate", seat="B", zone="hand")
                self._start(game, (source, altar, response))
                game.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(game)
                self._sacrifice(game, source, altar)
                self._offered_targets(game, (target,))
                self._refuse(game, source)
                self.target(game, "A", (target.ref,))
                removed = source if lost_source else target
                self.cast(game, "B", response, [removed.ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile" if lost_source else "graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual("hand" if lost_source else "exile", game.state.cards[target.object_id].zone)
                self.replay(game, load=True)

    def test_verified_sylvan_sequence_withdrawal_preserves_independent_death_results(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == DEATH_EXILE_THEN_RETURN_CAPABILITY).update(
            status="blocked", blockers=["Ordered death-source exile before owned return withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((DEATH_EXILE_THEN_RETURN_CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{DEATH_EXILE_THEN_RETURN_CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, SYLVAN)
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        for name in (MYR, "Cyclopean Giant"):
            retained, _ = register_card(self.db, registry, name)
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_verified_withdrawal_preserves_plain_owned_graveyard_return(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Source exclusion authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (MYR, TREEFOLK, ELF):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        retained, _ = register_card(self.db, registry, "Raise Dead")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
