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
from quorune.compiler import affected_player_sacrifice_templates
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.power_sacrifice_model import GREATEST_POWER_SACRIFICE_FIELD, GreatestPowerSacrificeSpec
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.sacrifice_prohibition_model import ResolvedSacrificeProhibition
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
DOOM = "Crackling Doom"
PAPALYMO = "Papalymo Totolymo"
ORIGINALS = {DOOM: False, PAPALYMO: True}
SACRIFICE = "choice.affected_player.fixed_sacrifice"
MAXIMUM = "quantity_expression.public_characteristic_aggregate"
HISTORY = "condition.history.fixed_public_query"
MANA_VALUE = "quantity_expression.public_query_effect_amount"
MANA_VALUE_EFFECT = (
    "Each opponent sacrifices a creature or planeswalker with the greatest mana value "
    "among creatures and planeswalkers they control."
)


class GreatestPowerAffectedSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    mana_value_spell = "Greatest Mana Sacrifice Witness"
    payment_creature = "Ancient Brontodon"
    wrong_color_creature = "Walking Corpse"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Shock", "Sol Ring"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse", "Hill Giant"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "greatest-power-affected-sacrifice-cards.json": set(ORIGINALS) | {"The Wanderer"},
        }
        pieces = []
        for filename, names in support.items():
            doc = json.loads((FIXTURES / filename).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(doc), encoding="utf-8")
            pieces.append(piece)
        synthetic = folder / "mana-value-sacrifice-witness.json"
        synthetic.write_text(json.dumps({"schema_version": 1, "cards": [{
            "oracle_id": "00000000-0000-4000-8000-000000009523",
            "name": cls.mana_value_spell, "layout": "normal", "mana_cost": "{2}{B}{B}",
            "cmc": 4, "type_line": "Instant", "colors": ["B"], "color_identity": ["B"],
            "legalities": {"commander": "legal", "duel": "legal"},
            "oracle_text": (
                "You may sacrifice a nontoken green creature rather than pay this spell's mana cost.\n"
                + MANA_VALUE_EFFECT
            ),
        }]}), encoding="utf-8")
        pieces.append(synthetic)
        path = folder / "opponent-greatest-power-sacrifices.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Greatest-power sacrifices", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_sacrifice(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        result, = [p for p in programs if p.effects and GREATEST_POWER_SACRIFICE_FIELD in p.effects[-1]]
        return card, result, programs

    def test_both_greatest_power_sacrifices_bind_complete_originals_and_distinct_history(self):
        for name, lost_life_only in ORIGINALS.items():
            with self.subTest(name=name):
                card, result, programs = self.original_sacrifice(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertIsNone(result.target_schema)
                self.assertEqual(GreatestPowerSacrificeSpec(lost_life_only).effect(),
                                 thaw_value(result.effects[-1]))
                self.assertLessEqual({SACRIFICE, MAXIMUM, "zone.change.destination_replacement"},
                                     set(result.capability_dependencies))
                self.assertEqual(lost_life_only, HISTORY in result.capability_dependencies)
                if lost_life_only:
                    self.assertEqual(("activate", "battlefield"), (result.event, result.active_zone))
                    self.assertIn("activation.source_zone_change.fixed", result.capability_dependencies)
                    self.assertTrue(any(p.event == "spell.cast" and len(p.effects) == 2 for p in programs))
                else:
                    self.assertEqual(("resolve", "stack"), (result.event, result.active_zone))
                    self.assertEqual(2, len(result.effects))
                    self.assertIn("damage.result.player_life", result.capability_dependencies)

    def test_greatest_power_sacrifice_shape_rejects_unsealed_scope_and_targeted_selection(self):
        _, original, _ = self.original_sacrifice(PAPALYMO)
        effect, = thaw_value(original.effects)
        for changes in (
            {"effects": [{**effect, "players": "all"}]},
            {"effects": [{**effect, "count": True}]},
            {"effects": [{**effect, GREATEST_POWER_SACRIFICE_FIELD: {"schema_version": True, "lost_life_only": True}}]},
            {"effects": [GreatestPowerSacrificeSpec().effect()]},
            {"effects": [{**effect, "actor": "$active"}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1}},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(original, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)

    def _options(self, game, seat):
        decision = game.state.pending_decision
        self.assertEqual(("choice.apnap", [seat]), (decision.kind, decision.actors))
        data = decision.payload_by_actor[seat]
        self.assertEqual(1, data["count"])
        return data

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + seat, command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose(self, game, seat, card):
        accepted = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref]})
        self.assertTrue(accepted.ok, accepted.summary)

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_greatest_mana_value_requires_the_exact_untargeted_spell_shape(self):
        card, result, programs = self.original_sacrifice(self.mana_value_spell)
        self.assertEqual("exact", compile_oracle_card(
            card, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)
        effect, = thaw_value(result.effects)
        self.assertEqual(GreatestPowerSacrificeSpec(mana_value=True), GreatestPowerSacrificeSpec.from_effect(effect))
        self.assertEqual({"metric": "mana_value", "schema_version": 3}, effect[GREATEST_POWER_SACRIFICE_FIELD])
        self.assertIsNone(result.target_schema)
        self.assertLessEqual({SACRIFICE, MANA_VALUE, "zone.change.destination_replacement"},
                             set(result.capability_dependencies))
        self.assertTrue(any(program.event == "cast.cost" for program in programs))
        for mutation in (
            replace(result, event="activate", active_zone="battlefield"),
            replace(result, effects=({**effect, "players": "all"},)),
            replace(result, effects=(effect, {"op": "draw", "count": 1})),
        ):
            binding = bind_semantic_program_runtime(mutation, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)
        deps = tuple(value for value in result.capability_dependencies if value != MANA_VALUE)
        mutation = replace(result, capability_dependencies=deps,
                           capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(mutation, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertTrue(any(MANA_VALUE in blocker for blocker in binding["blockers"]))
        for text in (
            MANA_VALUE_EFFECT.replace("Each opponent", "Each player"),
            MANA_VALUE_EFFECT.replace("greatest mana value", "least mana value"),
            MANA_VALUE_EFFECT.replace("creatures and planeswalkers", "permanents"),
        ):
            unsupported = compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=PROFILE,
            )
            self.assertTrue(unsupported.material_residuals)

    def test_mana_value_sacrifice_normal_cast_batches_off_turn_choices_and_replays(self):
        game = self.session(77134011)
        engine = game.engine
        spell = self.add(engine, self.mana_value_spell, zone="hand")
        copied = self.add(engine, "Ancient Brontodon")
        token_ref, = engine.create_token("C", name="", copy_of=copied.ref)
        token = engine._resolve_object("C", token_ref, zones={"battlefield"})
        low_c = self.add(engine, "Walking Corpse", seat="C", ref="mana-low-c")
        walker = self.add(engine, "The Wanderer", seat="B")
        creature = self.add(engine, "Hill Giant", seat="B")
        low_b = self.add(engine, "Walking Corpse", seat="B", ref="mana-low-b")
        protected = self.add(engine, "Ancient Brontodon", seat="D", ref="mana-protected-d")
        low_d = self.add(engine, "Walking Corpse", seat="D", ref="mana-low-d")
        commit_continuous_effect(game.state, ResolvedSacrificeProhibition(
            effect_id="test-protected-maximum", timestamp=engine._next_zone_timestamp(),
            subject=ContinuousObjectIdentity(protected.object_id, protected.logical_object_id),
        ))
        self.assertEqual(8, engine._effective_card_data(token)["mana_value"])
        self.assertEqual(4, engine._effective_card_data(creature)["mana_value"])
        self.assertEqual(4, engine._effective_card_data(walker)["mana_value"])
        self.assert_closed(game, spell)
        self.prepare(game, active="C", actor="C")
        game.state.players["A"].mana_pool.update({"C": 2, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        self.assertEqual([token.ref], self._options(game, "C")["options"])
        self._reject(game, "C", {"action_id": "choose", "cards": [low_c.ref]})
        self._choose(game, "C", token)
        tie = self._options(game, "B")
        self.assertEqual({walker.ref, creature.ref}, set(tie["options"]))
        self.assertEqual([token.ref], tie["prior_public_choices"]["C"])
        self.assertEqual("battlefield", self._zone(game, token))
        self._reject(game, "B", {"action_id": "choose", "cards": [low_b.ref]})
        self._reject(game, "C", {"action_id": "choose", "cards": [walker.ref]})
        game = self.replay(game, load=True)
        self._reject(game, "B", {"action_id": "choose", "cards": [token.ref]})
        self._choose(game, "B", walker)
        self.assertIsNone(self.resolve(game))
        batch, = (event for event in game.state.events if event.code == "choice.sacrifice")
        self.assertEqual((token.ref, walker.ref), batch.details["objects"])
        self.assertEqual("graveyard", self._zone(game, walker))
        self.assertTrue(token.object_id not in game.state.cards or self._zone(game, token) != "battlefield")
        self.assertEqual({"battlefield"}, {self._zone(game, card) for card in
                         (copied, creature, low_c, low_b, protected, low_d)})
        self.replay(game, load=True)

    def test_mana_value_sacrifice_alternative_cost_rejects_ineligible_prices_and_replays(self):
        game = self.session(77134012)
        engine = game.engine
        spell = self.add(engine, self.mana_value_spell, zone="hand")
        payment = self.add(engine, self.payment_creature)
        wrong_color = self.add(engine, self.wrong_color_creature)
        token_ref, = engine.create_token("A", name="", copy_of=payment.ref)
        opponent_price = self.add(engine, self.payment_creature, seat="B", ref="mana-foreign-price")
        absent = self.add(engine, self.payment_creature, zone="graveyard", ref="mana-graveyard-price")
        self.prepare(game)
        self.assert_closed(game, spell)
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        option, = (value for value in action["cost_options"] if value["id"] == "plain-alternative-ab1")
        self.assertEqual([payment.ref], option["choice_schema"]["sacrifice_cards"]["legal_refs"])
        command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
        for forbidden in (token_ref, wrong_color.ref, opponent_price.ref, absent.ref):
            self._reject(game, "A", {**command, "sacrifice_cards": [forbidden]})
        paid = game.act("pilot:A", {**command, "sacrifice_cards": [payment.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", self._zone(game, payment))
        self.assertEqual("battlefield", self._zone(game, opponent_price))
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        self.assertEqual([opponent_price.ref], self._options(game, "B")["options"])
        self._choose(game, "B", opponent_price)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self._zone(game, opponent_price))
        self.assertEqual("battlefield", self._zone(game, wrong_color))
        self.replay(game, load=True)

    def test_actual_doom_uses_current_power_per_seat_and_commits_after_ordered_public_choices(self):
        game = self.session(77134001)
        doom = self.add(game.engine, DOOM, zone="hand")
        prevention = self.add(game.engine, "The Wanderer", seat="B")
        first = self.add(game.engine, "Ancient Brontodon", seat="B")
        tied = self.add(game.engine, "Ancient Brontodon", seat="B", ref="other-highest-b")
        low = self.add(game.engine, "Walking Corpse", seat="B")
        increased = self.add(game.engine, "Walking Corpse", seat="C", ref="power-increased-c")
        unchanged = self.add(game.engine, "Walking Corpse", seat="C", ref="other-original-c")
        growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        secret = self.add(game.engine, "Sol Ring", seat="D", zone="hand", ref="private-sacrifice-d")
        for card in (doom, prevention, first, tied, low, increased, unchanged, growth):
            self.assert_closed(game, card)
        self.prepare(game, active="C", actor="C", mana={"G": 1})
        game.state.players["A"].mana_pool.update({"R": 1, "W": 1, "B": 1})
        self.checkpoint(game)
        self.cast(game, "A", doom)
        self.cast(game, "C", growth, [increased.ref])
        self.resolve_top(game)
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        before = self._options(game, "C")
        self.assertEqual([increased.ref], before["options"])
        self.assertEqual((40, 38, 38), tuple(game.state.players[seat].life for seat in "BCD"))
        self._reject(game, "C", {"action_id": "choose", "cards": [unchanged.ref]})
        self._choose(game, "C", increased)
        pending = self._options(game, "B")
        self.assertEqual({first.ref, tied.ref}, set(pending["options"]))
        self.assertIn(increased.ref, pending["prior_public_choices"]["C"])
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertEqual({"battlefield"}, {self._zone(game, card)
                                          for card in (first, tied, increased, unchanged, low)})
        self._reject(game, "B", {"action_id": "choose", "cards": [low.ref]})
        self._reject(game, "A", {"action_id": "choose", "cards": [tied.ref]})
        game = self.replay(game, load=True)
        self._choose(game, "B", tied)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard", "battlefield", "battlefield", "battlefield"),
            tuple(self._zone(game, card) for card in (increased, tied, first, low, unchanged)))
        self.assertEqual("battlefield", self._zone(game, prevention))
        self.replay(game, load=True)

    def test_actual_papalymo_uses_current_opponent_loss_even_after_regaining_life(self):
        game = self.session(77134002)
        source = self.add(game.engine, PAPALYMO)
        source.acquired_control_turn_count = -1
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        gain = self.add(game.engine, "Revitalize", seat="C", zone="hand")
        chosen = self.add(game.engine, "Ancient Brontodon", seat="C")
        low = self.add(game.engine, "Walking Corpse", seat="C")
        opponent = self.add(game.engine, "Ancient Brontodon", seat="B", ref="never-lost-life-b")
        unaffected = self.add(game.engine, "Walking Corpse", seat="D", ref="never-lost-life-d")
        for card in (source, shock, gain, chosen, low, opponent, unaffected):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"R": 1})
        game.state.players["A"].mana_pool["C"] = 4
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "B", shock, ["C"])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "C", gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(41, game.state.players["C"].life)
        self.priority(game, "A")
        action, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + source.ref + ":")]
        accepted = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", self._zone(game, source))
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        self.assertEqual([chosen.ref], self._options(game, "C")["options"])
        self._reject(game, "C", {"action_id": "choose", "cards": [low.ref]})
        game = self.replay(game, load=True)
        self._choose(game, "C", chosen)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self._zone(game, chosen))
        self.assertEqual({"battlefield"}, {self._zone(game, card) for card in (opponent, unaffected, low)})
        self.replay(game, load=True)

    def test_actual_papalymo_noncreature_cast_trigger_resolves_before_its_original_spell(self):
        game = self.session(77134003)
        source = self.add(game.engine, PAPALYMO)
        life = self.add(game.engine, "Revitalize", zone="hand")
        for card in (source, life):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", life)
        self.assertEqual(["spell", "triggered_ability"], [item.kind for item in game.state.stack])
        self.resolve_top(game)
        self.assertEqual((41, 39, 39, 39), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(44, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_omitting_exact_greatest_power_sacrifices_keeps_originals_partial(self):
        import re

        with patch.object(affected_player_sacrifice_templates, "_GREATEST_INSTRUCTION", re.compile(r"(?!)")):
            for name in ORIGINALS:
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)
            independent, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(independent["strict_capability_ready"], independent["blockers"])

    def test_genuine_sacrifice_maximum_and_history_withdrawals_keep_independent_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (SACRIFICE, MAXIMUM, HISTORY):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Affected-opponent sacrifice authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    result, _ = register_card(self.db, registry, name)
                    self.assertEqual(capability == HISTORY and name == DOOM,
                                     result["strict_capability_ready"], result)
                independent, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(independent["strict_capability_ready"], independent["blockers"])
