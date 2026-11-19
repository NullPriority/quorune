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
from quorune.compiler.event_player_results import EVENT_PLAYER_DAMAGE_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
EIDOLON = "Eidolon of the Great Revel"
PILLAR = "Pyrostatic Pillar"
VIPER = "Scalding Viper // Steam Clean"
ORIGINALS = (EIDOLON, PILLAR, VIPER)
SPELL_CAST = "trigger.event.normalized_spell_cast"
FIXTURES = ROOT / "tests" / "fixtures"
LIMIT = {"field": "mana_value", "op": "lte", "value": 3}
OPPONENT = {"field": "controller", "op": "ne", "value": "$source.controller"}


class SpellCastManaValueLimitTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "event-player-fixed-damage-cards.json": {*ORIGINALS, "Spellshock"},
            "cast-spell-mana-value-cards.json": {"Blaze"},
            "global-alternative-cost-cards.json": {"Burst Lightning"},
            "mana-value-destruction-cards.json": {"Daru Lancer"},
            "attached-cast-restriction-cards.json": {"Seal of Cleansing"},
            "ability-label-composition-cards.json": {"Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
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
        database = folder / "spell-cast-mana-value-limits.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed spell cast mana values", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (program for program in programs if program.event == "spell.cast")
        return card, trigger, programs

    def test_complete_originals_keep_inclusive_scope_and_adventure(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, trigger, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual("battlefield", trigger.active_zone)
                self.assertIsNone(trigger.target_schema)
                self.assertEqual({"all": [OPPONENT, LIMIT]} if name == VIPER else LIMIT,
                                 thaw_value(trigger.event_condition))
                self.assertEqual([{"op": "damage", "source": "$source",
                    "target": "$context.player", "amount": 1 if name == VIPER else 2}], thaw_value(trigger.effects))
                self.assertTrue({SPELL_CAST, EVENT_PLAYER_DAMAGE_CAPABILITY} <= set(trigger.capability_dependencies))
                if name == VIPER:
                    adventure, = (program for program in programs if program.event == "resolve")
                    self.assertIn("intrinsic_adventure_resolution_required", adventure.coverage)
                    self.assertEqual("bounce", adventure.effects[0]["op"])

    def test_malformed_upper_limits_and_widened_conditions_fail_closed(self):
        card, program, _ = self.original_programs(PILLAR)
        conditions = (
            {**LIMIT, "value": True},
            {**LIMIT, "value": 3.0},
            {**LIMIT, "value": -1},
            {**LIMIT, "value": "3"},
            {**LIMIT, "extra": True},
            {"all": [{**OPPONENT, "op": "eq"}, LIMIT]},
            {"all": [OPPONENT, LIMIT, LIMIT]},
            {"any": [LIMIT, OPPONENT]},
            {"not": LIMIT},
        )
        for condition in conditions:
            with self.subTest(condition=condition):
                result = bind_semantic_program_runtime(replace(program, event_condition=condition),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        detached = bind_semantic_program_runtime(replace(program, active_zone="graveyard", coverage=()),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(detached["strict"], detached)
        for old, new in (("a player casts", "you cast"), ("a spell", "a creature spell"),
                         ("3 or less", "X or less"), ("3 or less", "3 or fewer and you've gained life")):
            with self.subTest(text=new):
                ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def _cast(self, game, seat, card, targets=(), *, face=None, **cast_options):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        if face is None:
            matching = [row for row in actions if row["id"] == "cast:" + card.ref]
        else:
            matching = [row for row in actions if row.get("card") == card.ref and row.get("face") == face]
            cast_options["face"] = face
        self.assertEqual(1, len(matching), {
            "card": card.ref, "zone": game.state.cards[card.object_id].zone, "face": face,
            "mana": game.state.players[seat].mana_pool,
            "card_offers": [row["id"] for row in actions if row.get("card") == card.ref],
        })
        action = matching[0]
        command = {"action_id": action["id"], "targets": list(targets), "pay": "auto", **cast_options}
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        return next(item for item in game.state.stack if item.card_object_id == card.object_id)

    def _assert_triggers(self, game, caster, mana_value, sources):
        items = [item for item in game.state.stack if item.kind == "triggered_ability"]
        self.assertEqual({(card.object_id, card.controller) for card in sources},
                         {(item.source_object_id, item.controller) for item in items})
        for item in items:
            self.assertEqual(("spell.cast", caster, mana_value, []),
                             (item.context["event"], item.context["player"], item.context["mana_value"], item.targets))

    def test_actual_announced_x_at_boundary_and_pending_damage_after_source_departure(self):
        game = self.session(76156700)
        pillar = self.add(game.engine, PILLAR)
        eidolon = self.add(game.engine, EIDOLON, seat="C")
        seal = self.add(game.engine, "Seal of Cleansing", seat="B")
        at_limit = self.add(game.engine, "Blaze", zone="hand", ref="cast-limit-exact")
        over_limit = self.add(game.engine, "Blaze", zone="hand", ref="cast-limit-above")
        for card in (pillar, eidolon, seal, at_limit, over_limit):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 2, "C": 5})
        self.checkpoint(game)
        stack = self._cast(game, "A", at_limit, ["D"], x=2)
        self.assertEqual(2, stack.x_value)
        self._assert_triggers(game, "A", 3, (pillar, eidolon))

        self.priority(game, "B")
        action, = (row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + seal.ref + ":"))
        command = {"action_id": action["id"], "targets": [pillar.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[pillar.object_id].zone)
        self._assert_triggers(game, "A", 3, (pillar, eidolon))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([36, 40, 40, 38], [game.state.players[seat].life for seat in "ABCD"])

        stack = self._cast(game, "A", over_limit, ["D"], x=3)
        self.assertEqual(3, stack.x_value)
        self._assert_triggers(game, "A", 4, ())
        self.assertIsNone(self.resolve(game))
        self.assertEqual([36, 40, 40, 35], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_self_cast_morph_and_kicker_use_inherent_stack_characteristics(self):
        game = self.session(76156701)
        pillar = self.add(game.engine, PILLAR, seat="B")
        eidolon = self.add(game.engine, EIDOLON, zone="hand")
        hidden = self.add(game.engine, "Daru Lancer", zone="hand", ref="cast-limit-masked-card")
        kicked = self.add(game.engine, "Burst Lightning", zone="hand")
        for card in (pillar, eidolon, hidden, kicked):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 3, "C": 7})
        self.checkpoint(game)
        self._cast(game, "A", eidolon)
        self._assert_triggers(game, "A", 2, (pillar,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[eidolon.object_id].zone)
        self.assertEqual(38, game.state.players["A"].life)

        self.priority(game, "A")
        action = "cast-morph:" + hidden.ref
        self.assertIn(action, {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]})
        result = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        morph = next(item for item in game.state.stack if item.card_object_id == hidden.object_id)
        self.assertEqual(3, morph.context["mana_spent_total"])
        self._assert_triggers(game, "A", 0, (pillar, eidolon))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn("Daru Lancer", packet)
            self.assertNotIn(hidden.ref, packet)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[hidden.object_id].face_down)
        self.assertEqual(34, game.state.players["A"].life)

        spell = self._cast(game, "A", kicked, ["D"], cost_option="kicked")
        self.assertEqual(5, spell.context["mana_spent_total"])
        self._assert_triggers(game, "A", 1, (pillar, eidolon))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([30, 40, 40, 36], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_steam_clean_permits_the_creature_and_only_opponent_casts_trigger_it(self):
        game = self.session(76156702)
        viper = self.add(game.engine, VIPER, zone="hand")
        adventure_target = self.add(game.engine, "Grizzly Bears", seat="B", ref="cast-limit-adventure-target")
        own_target = self.add(game.engine, "Grizzly Bears", seat="B", ref="cast-limit-own-cast-target")
        own = self.add(game.engine, "Unsummon", zone="hand", ref="cast-limit-own-instant")
        opponent = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="cast-limit-opponent-instant")
        self.add(game.engine, "Island", seat="B")
        for card in (viper, own, opponent):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "U": 4, "C": 2})
        self.checkpoint(game)
        adventure = self._cast(game, "A", viper, [adventure_target.ref], face="Steam Clean")
        self.assertEqual("Steam Clean", adventure.chosen_face)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[adventure_target.object_id].zone)
        self.assertEqual("exile", game.state.cards[viper.object_id].zone)

        self._cast(game, "A", game.state.cards[viper.object_id], face="Scalding Viper")
        self.assertIsNone(self.resolve(game))
        viper = game.state.cards[viper.object_id]
        self.assertEqual("battlefield", viper.zone)
        self._cast(game, "A", own, [own_target.ref])
        self._assert_triggers(game, "A", 1, ())
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[own_target.object_id].zone)
        self._cast(game, "B", opponent, [viper.ref])
        self._assert_triggers(game, "B", 1, (viper,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[viper.object_id].zone)
        self.assertEqual([40, 39, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_omitting_the_exact_upper_limit_restores_the_three_cast_residuals(self):
        with patch("quorune.compiler.spell_cast_predicates._mana_value_limit_spell_cast_binding", return_value=None):
            for name in ORIGINALS:
                bound, _ = register_card(self.db, self.registry, name)
                self.assertFalse(bound["strict_capability_ready"], bound)
            other, _ = register_card(self.db, self.registry, "Spellshock")
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_actual_cast_and_fixed_caster_damage(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (SPELL_CAST, EVENT_PLAYER_DAMAGE_CAPABILITY):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Upper cast dependency withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                for name in ORIGINALS:
                    bound, _ = register_card(self.db, registry, name)
                    self.assertFalse(bound["strict_capability_ready"], bound)
                other, _ = register_card(self.db, registry, "Blaze")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
