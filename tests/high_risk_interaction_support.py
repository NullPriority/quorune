from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import unittest

from common import DB_PATH
from quorune.card_programs import CardProgram, bind_card_program_runtime
from quorune.card_programs.adapters import (
    compile_best_available_card_program,
)
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.unlock_frontier import analyze_card_unlocks
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


Pair = tuple[str, str]


@dataclass(frozen=True, slots=True)
class _Witness:
    name: str
    type_line: str
    oracle_text: str
    mana_cost: str = ""
    keywords: tuple[str, ...] = ()
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None


_WITNESSES = {
    "floating-shield": _Witness(
        "Floating Shield",
        "Enchantment — Aura",
        "Enchant creature\n"
        "As this Aura enters, choose a color.\n"
        "Enchanted creature has protection from the chosen color. This "
        "effect doesn't remove this Aura.\n"
        "Sacrifice this Aura: Target creature gains protection from the "
        "chosen color until end of turn.",
        "{2}{W}",
        ("Enchant",),
    ),
    "etchings-of-the-chosen": _Witness(
        "Etchings of the Chosen",
        "Enchantment",
        "As this enchantment enters, choose a creature type.\n"
        "Creatures you control of the chosen type get +1/+1.\n"
        "{1}, Sacrifice a creature of the chosen type: Target creature you "
        "control gains indestructible until end of turn. (Damage and effects "
        'that say "destroy" don\'t destroy it.)',
        "{1}{W}{B}",
    ),
    "aether-tunnel": _Witness(
        "Aether Tunnel",
        "Enchantment — Aura",
        "Enchant creature\n"
        "Enchanted creature gets +1/+0 and can't be blocked.",
        "{1}{U}",
        ("Enchant",),
    ),
    "typed-floating-shield": _Witness(
        "Typed Floating Shield Fixture",
        "Enchantment — Aura",
        "Enchant creature or Vehicle\n"
        "As this Aura enters, choose a color.\n"
        "Enchanted creature has protection from the chosen color. This "
        "effect doesn't remove this Aura.\n"
        "Sacrifice this Aura: Target creature gains protection from the "
        "chosen color until end of turn.",
        "{2}{W}",
        ("Enchant",),
    ),
    "typed-aether-tunnel": _Witness(
        "Typed Aether Tunnel Fixture",
        "Enchantment — Aura",
        "Enchant creature or Vehicle\n"
        "Enchanted creature gets +1/+0 and can't be blocked.",
        "{1}{U}",
        ("Enchant",),
    ),
    "starforged-sword": _Witness(
        "Starforged Sword",
        "Artifact — Equipment",
        "Gift a tapped Fish (You may promise an opponent a gift as you cast "
        "this spell. If you do, when it enters, they create a tapped 1/1 blue "
        "Fish creature token.)\n"
        "When this Equipment enters, if the gift was promised, attach this "
        "Equipment to target creature you control.\n"
        "Equipped creature gets +3/+3 and loses flying.\n"
        "Equip {3}",
        "{4}",
        ("Gift", "Equip"),
    ),
    "junk-jet": _Witness(
        "Junk Jet",
        "Artifact — Equipment",
        "When this Equipment enters, create a Junk token.\n"
        "{3}, Sacrifice another artifact: Double equipped creature's power "
        "until end of turn.\n"
        "Equip {1}",
        "{1}{R}",
        ("Equip", "Double"),
    ),
    "soratami-cloud-chariot": _Witness(
        "Soratami Cloud Chariot",
        "Artifact",
        "{2}: Target creature you control gains flying until end of turn.\n"
        "{2}: Prevent all combat damage that would be dealt to and dealt by "
        "target creature you control this turn.",
        "{5}",
    ),
    "prismatic-circle": _Witness(
        "Prismatic Circle",
        "Enchantment",
        "Cumulative upkeep {1}\n"
        "As this enchantment enters, choose a color.\n"
        "{1}: The next time a source of your choice of the chosen color would "
        "deal damage to you this turn, prevent that damage.",
        "{2}{W}",
        ("Cumulative upkeep",),
    ),
    "fixed-life-upkeep-prevention": _Witness(
        "Fixed-Life Upkeep Prevention Fixture",
        "Enchantment",
        "Cumulative upkeep—Pay 2 life.\n"
        "As this enchantment enters, choose a color.\n"
        "{1}: The next time a source of your choice of the chosen color would "
        "deal damage to you this turn, prevent that damage.",
        "{2}{W}",
        ("Cumulative upkeep",),
    ),
    "bewitching-leechcraft": _Witness(
        "Bewitching Leechcraft",
        "Enchantment — Aura",
        "Enchant creature\n"
        "When this Aura enters, tap enchanted creature.\n"
        "Enchanted creature has \"If this creature would untap during your "
        "untap step, remove a +1/+1 counter from it instead. If you do, "
        "untap it.\" (Otherwise, it doesn't untap.)",
        "{2}{U}",
        ("Enchant",),
    ),
    "tangle-kelp": _Witness(
        "Tangle Kelp",
        "Enchantment — Aura",
        "Enchant creature\n"
        "When this Aura enters, tap enchanted creature.\n"
        "Enchanted creature doesn't untap during its controller's untap "
        "step if it attacked during its controller's last turn.",
        "{U}",
        ("Enchant",),
    ),
    "pemmins-aura": _Witness(
        "Pemmin's Aura",
        "Enchantment — Aura",
        "Enchant creature\n"
        "{U}: Untap enchanted creature.\n"
        "{U}: Enchanted creature gains flying until end of turn.\n"
        "{U}: Enchanted creature gains shroud until end of turn. (It can't "
        "be the target of spells or abilities.)\n"
        "{1}: Enchanted creature gets +1/-1 or -1/+1 until end of turn.",
        "{1}{U}{U}",
        ("Enchant",),
    ),
    "sleep-cursed-faerie": _Witness(
        "Sleep-Cursed Faerie",
        "Creature — Faerie Wizard",
        "Flying, ward {2}\n"
        "This creature enters tapped with three stun counters on it. (If it "
        "would become untapped, remove a stun counter from it instead.)\n"
        "{1}{U}: Untap this creature.",
        "{U}",
        ("Flying", "Ward"),
        "2",
        "1",
    ),
    "spike-weaver": _Witness(
        "Spike Weaver",
        "Creature — Spike",
        "This creature enters with three +1/+1 counters on it.\n"
        "{2}, Remove a +1/+1 counter from this creature: Put a +1/+1 "
        "counter on target creature.\n"
        "{1}, Remove a +1/+1 counter from this creature: Prevent all combat "
        "damage that would be dealt this turn.",
        "{2}{G}{G}",
        power="0",
        toughness="0",
    ),
    "tekuthal": _Witness(
        "Tekuthal, Inquiry Dominus",
        "Legendary Creature — Phyrexian Horror",
        "Flying\n"
        "If you would proliferate, proliferate twice instead.\n"
        "{1}{U/P}{U/P}, Remove three counters from among other artifacts, "
        "creatures, and planeswalkers you control: Put an indestructible "
        "counter on Tekuthal. ({U/P} can be paid with either {U} or 2 life.)",
        "{2}{U}{U}",
        ("Flying", "Proliferate"),
        "3",
        "5",
    ),
    "zabaz": _Witness(
        "Zabaz, the Glimmerwasp",
        "Legendary Artifact Creature — Insect",
        "Modular 1\n"
        "If a modular triggered ability would put one or more +1/+1 counters "
        "on a creature you control, that many plus one +1/+1 counters are put "
        "on it instead.\n"
        "{R}: Destroy target artifact you control.\n"
        "{W}: Zabaz gains flying until end of turn.",
        "{1}",
        ("Modular",),
        "0",
        "0",
    ),
    "jaya": _Witness(
        "Jaya, Venerated Firemage",
        "Legendary Planeswalker — Jaya",
        "If another red source you control would deal damage to a permanent "
        "or player, it deals that much damage plus 1 to that permanent or "
        "player instead.\n"
        "−2: Jaya deals 2 damage to any target.",
        "{4}{R}",
        loyalty="5",
    ),
    "jaya-task-mage": _Witness(
        "Jaya Ballard, Task Mage",
        "Legendary Creature — Human Spellshaper",
        "{R}, {T}, Discard a card: Destroy target blue permanent.\n"
        "{1}{R}, {T}, Discard a card: Jaya Ballard deals 3 damage to any "
        "target. A creature dealt damage this way can't be regenerated "
        "this turn.\n"
        "{5}{R}{R}, {T}, Discard a card: Jaya Ballard deals 6 damage to "
        "each creature and each player.",
        "{1}{R}{R}",
        power="2",
        toughness="2",
    ),
    "decode-transmissions": _Witness(
        "Decode Transmissions",
        "Sorcery",
        "You draw two cards and lose 2 life.\n"
        "Void — If a nonland permanent left the battlefield this turn or a "
        "spell was warped this turn, instead you draw two cards and each "
        "opponent loses 2 life.",
        "{2}{B}",
        ("Void",),
    ),
    "damage-result-and-coin-replacement": _Witness(
        "Damage Result and Coin Replacement Fixture",
        "Creature — Human Wizard",
        "{T}: This creature deals 1 damage to any target.\n"
        "If you would flip a coin, instead flip two coins and ignore one.",
        "{1}{R}",
        power="2",
        toughness="2",
    ),
    "alhammarrets-archive": _Witness(
        "Alhammarret's Archive",
        "Legendary Artifact",
        "If you would gain life, you gain twice that much life instead.\n"
        "If you would draw a card except the first one you draw in each of "
        "your draw steps, draw two cards instead.",
        "{5}",
    ),
    "kor-haven": _Witness(
        "Kor Haven",
        "Legendary Land",
        "{T}: Add {C}.\n"
        "{1}{W}, {T}: Prevent all combat damage that would be dealt by target "
        "attacking creature this turn.",
    ),
    "game-trail": _Witness(
        "Game Trail",
        "Land",
        "As this land enters, you may reveal a Mountain or Forest card from "
        "your hand. If you don't, this land enters tapped.\n"
        "{T}: Add {R} or {G}.",
    ),
    "rancid-earth": _Witness(
        "Rancid Earth",
        "Sorcery",
        "Destroy target land.\n"
        "Threshold — If there are seven or more cards in your graveyard, "
        "instead destroy that land and this spell deals 1 damage to each "
        "creature and each player.",
        "{1}{B}{B}",
        ("Threshold",),
    ),
    "cleansing-meditation": _Witness(
        "Cleansing Meditation",
        "Sorcery",
        "Destroy all enchantments.\n"
        "Threshold — If there are seven or more cards in your graveyard, "
        "instead destroy all enchantments, then return all cards in your "
        "graveyard destroyed this way to the battlefield.",
        "{1}{W}{W}",
        ("Threshold",),
    ),
    "legacy-weapon": _Witness(
        "Legacy Weapon",
        "Legendary Artifact",
        "{W}{U}{B}{R}{G}: Exile target permanent.\n"
        "If Legacy Weapon would be put into a graveyard from anywhere, reveal "
        "Legacy Weapon and shuffle it into its owner's library instead.",
        "{7}",
    ),
    "dauthi-voidwalker": _Witness(
        "Dauthi Voidwalker",
        "Creature — Dauthi Rogue",
        "Shadow\n"
        "If a card would be put into an opponent's graveyard from anywhere, "
        "instead exile it with a void counter on it.\n"
        "{T}, Sacrifice this creature: Choose an exiled card an opponent owns "
        "with a void counter on it. You may play it this turn without paying "
        "its mana cost.",
        "{B}{B}",
        ("Shadow",),
        "3",
        "2",
    ),
    "heartless-pillage": _Witness(
        "Heartless Pillage",
        "Sorcery",
        "Target opponent discards two cards.\n"
        "Raid — If you attacked this turn, create a Treasure token.",
        "{2}{B}",
    ),
    "snow-day": _Witness(
        "Snow Day",
        "Instant",
        "Tap up to two target creatures. Those creatures don't untap during "
        "their controller's next untap step.\n"
        "Draw two cards, then discard a card.",
        "{4}{U}{U}",
    ),
    "descend-upon-the-sinful": _Witness(
        "Descend upon the Sinful",
        "Sorcery",
        "Exile all creatures.\n"
        "Delirium — Create a 4/4 white Angel creature token with flying if "
        "there are four or more card types among cards in your graveyard.",
        "{4}{W}{W}",
    ),
    "sphinxs-insight": _Witness(
        "Sphinx's Insight",
        "Instant",
        "Draw two cards.\n"
        "Addendum — If you cast this spell during your main phase, you gain "
        "2 life.",
        "{2}{W}{U}",
        ("Addendum",),
    ),
    "electrolyze": _Witness(
        "Electrolyze",
        "Instant",
        "Electrolyze deals 2 damage divided as you choose among one or two "
        "targets.\nDraw a card.",
        "{1}{U}{R}",
    ),
    "cunning-strike": _Witness(
        "Cunning Strike",
        "Instant",
        "Cunning Strike deals 2 damage to target creature and 2 damage to target "
        "player or planeswalker.\nDraw a card.",
        "{3}{U}{R}",
    ),
    "blur": _Witness(
        "Blur",
        "Instant",
        "Exile target creature you control, then return that card to the "
        "battlefield under its owner's control.\nDraw a card.",
        "{2}{U}",
    ),
    "madblind-mountain": _Witness(
        "Madblind Mountain",
        "Land — Mountain",
        "This land enters tapped.\n"
        "{R}, {T}: Shuffle your library. Activate only if you control two or "
        "more red permanents.",
    ),
    "wintermoon-mesa": _Witness(
        "Wintermoon Mesa",
        "Land",
        "This land enters tapped.\n"
        "{T}: Add {C}.\n"
        "{2}, {T}, Sacrifice this land: Tap two target lands.",
    ),
    "ebony-fly": _Witness(
        "Ebony Fly",
        "Artifact",
        "This artifact enters tapped.\n"
        "{T}: Add {C}.\n"
        "{4}: Roll a d6. Until end of turn, you may have this artifact become "
        "an X/X Insect artifact creature with flying, where X is the result.\n"
        "Whenever this artifact attacks, another target attacking creature "
        "gains flying until end of turn.",
        "{2}",
    ),
    "rasputin": _Witness(
        "Rasputin Dreamweaver",
        "Legendary Creature — Human Wizard",
        "Rasputin enters with seven dream counters on it.\n"
        "Remove a dream counter from Rasputin: Add {C}.\n"
        "Remove a dream counter from Rasputin: Prevent the next 1 damage that "
        "would be dealt to Rasputin this turn.\n"
        "At the beginning of your upkeep, if Rasputin started the turn "
        "untapped, put a dream counter on it.\n"
        "Rasputin can't have more than seven dream counters on it.",
        "{4}{W}{U}",
        power="4",
        toughness="1",
    ),
    "chromatic-armor": _Witness(
        "Chromatic Armor",
        "Enchantment — Aura",
        "Enchant creature\n"
        "As this Aura enters, choose a color.\n"
        "This Aura enters with a sleight counter on it.\n"
        "Prevent all damage that would be dealt to enchanted creature by "
        "sources of the last chosen color.\n"
        "{X}: Put a sleight counter on this Aura and choose a color. X is "
        "the number of sleight counters on this Aura.",
        "{1}{W}{U}",
        ("Enchant",),
    ),
    "mourners-shield": _Witness(
        "Mourner's Shield",
        "Artifact",
        "Imprint — When this artifact enters, you may exile target card "
        "from a graveyard.\n"
        "{2}, {T}: Prevent all damage that would be dealt this turn by a "
        "source of your choice that shares a color with the exiled card.",
        "{4}",
        ("Imprint",),
    ),
    "ovinomancer": _Witness(
        "Ovinomancer",
        "Creature — Human Wizard",
        "When this creature enters, sacrifice it unless you return three basic "
        "lands you control to their owner's hand.\n"
        "{T}, Return this creature to its owner's hand: Destroy target "
        "creature. It can't be regenerated. That creature's controller "
        "creates a 0/1 green Sheep creature token.",
        "{2}{U}",
        power="0",
        toughness="1",
    ),
    "kirtars-wrath": _Witness(
        "Kirtar's Wrath",
        "Sorcery",
        "Destroy all creatures. They can't be regenerated.\n"
        "Threshold — If there are seven or more cards in your graveyard, "
        "instead destroy all creatures, then create two 1/1 white Spirit "
        "creature tokens with flying. Creatures destroyed this way can't be "
        "regenerated.",
        "{4}{W}{W}",
        ("Threshold",),
    ),
    "avatar-of-woe": _Witness(
        "Avatar of Woe",
        "Creature — Avatar",
        "If there are ten or more creature cards total in all graveyards, "
        "this spell costs {6} less to cast.\n"
        "Fear (This creature can't be blocked except by artifact creatures "
        "and/or black creatures.)\n"
        "{T}: Destroy target creature. It can't be regenerated.",
        "{6}{B}{B}",
        ("Fear",),
        "6",
        "5",
    ),
    "gideon-oathsworn": _Witness(
        "Gideon, the Oathsworn",
        "Legendary Planeswalker — Gideon",
        "Whenever you attack with two or more non-Gideon creatures, put a "
        "+1/+1 counter on each of those creatures.\n"
        "+2: Until end of turn, Gideon becomes a 5/5 white Soldier creature "
        "that's still a planeswalker. Prevent all damage that would be dealt "
        "to him this turn. (He can't attack if he was cast this turn.)\n"
        "−9: Exile Gideon and each creature your opponents control.",
        "{4}{W}{W}",
        loyalty="4",
    ),
    "gideon-ally-of-zendikar": _Witness(
        "Gideon, Ally of Zendikar",
        "Legendary Planeswalker — Gideon",
        "+1: Until end of turn, Gideon becomes a 5/5 Human Soldier Ally "
        "creature with indestructible that's still a planeswalker. Prevent "
        "all damage that would be dealt to him this turn.\n"
        "0: Create a 2/2 white Knight Ally creature token.\n"
        "−4: You get an emblem with \"Creatures you control get +1/+1.\"",
        "{2}{W}{W}",
        loyalty="4",
    ),
    "gideon-jura": _Witness(
        "Gideon Jura",
        "Legendary Planeswalker — Gideon",
        "+2: During target opponent's next turn, creatures that player "
        "controls attack Gideon Jura if able.\n"
        "−2: Destroy target tapped creature.\n"
        "0: Until end of turn, Gideon Jura becomes a 6/6 Human Soldier "
        "creature that's still a planeswalker. Prevent all damage that "
        "would be dealt to him this turn.",
        "{3}{W}{W}",
        loyalty="6",
    ),
    "gideon-champion-of-justice": _Witness(
        "Gideon, Champion of Justice",
        "Legendary Planeswalker — Gideon",
        "+1: Put a loyalty counter on Gideon, Champion of Justice for each "
        "creature target opponent controls.\n"
        "0: Until end of turn, Gideon, Champion of Justice becomes an "
        "indestructible Human Soldier creature with power and toughness "
        "each equal to the number of loyalty counters on him. He's still a "
        "planeswalker. Prevent all damage that would be dealt to him this "
        "turn.\n"
        "−15: Exile all other permanents.",
        "{2}{W}{W}",
        loyalty="4",
    ),
    "oko-trickster": _Witness(
        "Oko, the Trickster",
        "Legendary Planeswalker — Oko",
        "+1: Put two +1/+1 counters on up to one target creature you "
        "control.\n"
        "0: Until end of turn, Oko becomes a copy of target creature you "
        "control. Prevent all damage that would be dealt to him this turn.\n"
        "−7: Until end of turn, each creature you control has base power "
        "and toughness 10/10 and gains trample.",
        "{4}{G}{U}",
        loyalty="4",
    ),
    "serras-hymn": _Witness(
        "Serra's Hymn",
        "Enchantment",
        "At the beginning of your upkeep, you may put a verse counter on "
        "this enchantment.\n"
        "Sacrifice this enchantment: Prevent the next X damage that would "
        "be dealt this turn to any number of targets, divided as you choose, "
        "where X is the number of verse counters on this enchantment.",
        "{W}",
    ),
    "vile-requiem": _Witness(
        "Vile Requiem",
        "Enchantment",
        "At the beginning of your upkeep, you may put a verse counter on "
        "this enchantment.\n"
        "{1}{B}, Sacrifice this enchantment: Destroy up to X target nonblack "
        "creatures, where X is the number of verse counters on this "
        "enchantment. They can't be regenerated.",
        "{2}{B}{B}",
    ),
    "runesword": _Witness(
        "Runesword",
        "Artifact",
        "{3}, {T}: Target attacking creature gets +2/+0 until end of turn. "
        "When that creature leaves the battlefield this turn, sacrifice this "
        "artifact. If the creature deals damage to a creature this turn, the "
        "creature dealt damage can't be regenerated this turn. If a creature "
        "dealt damage by the targeted creature would die this turn, exile that "
        "creature instead.",
        "{6}",
    ),
    "kindred-discovery": _Witness(
        "Kindred Discovery",
        "Enchantment",
        "As this enchantment enters, choose a creature type.\n"
        "Whenever a creature you control of the chosen type enters or attacks, "
        "draw a card.",
        "{3}{U}{U}",
    ),
    "teferis-moat": _Witness(
        "Teferi's Moat",
        "Enchantment",
        "As this enchantment enters, choose a color.\n"
        "Creatures of the chosen color without flying can't attack you.",
        "{3}{W}{U}",
    ),
    "hallowed-healer": _Witness(
        "Hallowed Healer",
        "Creature — Human Cleric",
        "{T}: Prevent the next 2 damage that would be dealt to any target "
        "this turn.\n"
        "Threshold — {T}: Prevent the next 4 damage that would be dealt to "
        "any target this turn. Activate only if there are seven or more "
        "cards in your graveyard.",
        "{2}{W}",
        power="1",
        toughness="1",
    ),
    "winds-of-qal-sisma": _Witness(
        "Winds of Qal Sisma",
        "Instant",
        "Prevent all combat damage that would be dealt this turn.\n"
        "Ferocious — If you control a creature with power 4 or greater, "
        "instead prevent all combat damage that would be dealt this turn by "
        "creatures your opponents control.",
        "{1}{G}",
        ("Ferocious",),
    ),
}


def _pair(left: str, right: str) -> Pair:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


DESTROY_DAMAGE_PREVENTION_PAIR = _pair(
    "capability.permanent.destroy.effect",
    "residual.replacement.damage-prevention",
)

DESTROY_REGENERATION_PAIR = _pair(
    "capability.permanent.destroy.effect",
    "residual.replacement.regeneration",
)

PREVENTION_AND_REPLACEMENT_PAIRS = (
    _pair(
        "capability.damage.prevention.persistent_amount",
        "residual.replacement.damage-prevention",
    ),
    _pair(
        "capability.damage.prevention.persistent_amount",
        "residual.replacement.replacement-applicability",
    ),
    _pair(
        "capability.damage.prevention.persistent_amount",
        "residual.replacement.self-replacement-and-prevention-ordering",
    ),
)

TOKEN_AND_DAMAGE_PREVENTION_PAIR = _pair(
    "capability.token.creation.fixed_definition",
    "residual.replacement.damage-prevention",
)

FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS = (
    _pair(
        "capability.counter.producer.fixed_self_entry",
        "residual.replacement.damage-prevention",
    ),
    _pair(
        "capability.counter.producer.fixed_self_entry",
        "residual.replacement.replacement-applicability",
    ),
    _pair(
        "capability.counter.producer.fixed_self_entry",
        "residual.replacement.self-replacement-and-prevention-ordering",
    ),
)

TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS = (
    _pair(
        "capability.attachment.reference.current_or_lki",
        "residual.continuous_layer.affected-player-ordering",
    ),
    _pair(
        "capability.attachment.reference.current_or_lki",
        "residual.continuous_layer.continuous-effect-layers-and-dependencies",
    ),
    _pair(
        "capability.attachment.reference.current_or_lki",
        "residual.duration.until-end-of-turn",
    ),
    _pair(
        "capability.permanent.tap.effect",
        "residual.replacement.replacement-applicability",
    ),
    _pair(
        "capability.permanent.tap.effect",
        "residual.replacement.self-replacement-and-prevention-ordering",
    ),
    _pair(
        "capability.permanent.untap.effect",
        "residual.replacement.replacement-applicability",
    ),
    _pair(
        "capability.permanent.untap.effect",
        "residual.replacement.self-replacement-and-prevention-ordering",
    ),
)


ATTACHMENT_AND_CONTINUOUS_PAIRS = (
    _pair("capability.attachment.aura.simple_object", "residual.continuous_layer.affected-player-ordering"),
    _pair("capability.attachment.aura.simple_object", "residual.continuous_layer.continuous-effect-layers-and-dependencies"),
    _pair("capability.attachment.aura.simple_object", "residual.duration.until-end-of-turn"),
    _pair("capability.attachment.aura.simple_object", "residual.static_clause.broader-evasion-and-group-constraints"),
    _pair("capability.attachment.aura.simple_object", "residual.static_clause.conditional-declaration-predicates"),
    _pair("capability.attachment.aura.simple_object", "residual.static_clause.temporary-declaration-restrictions"),
    _pair("capability.attachment.equip.fixed_mana", "residual.continuous_layer.affected-player-ordering"),
    _pair("capability.attachment.equip.fixed_mana", "residual.continuous_layer.continuous-effect-layers-and-dependencies"),
    _pair("capability.attachment.equip.fixed_mana", "residual.duration.until-end-of-turn"),
    _pair("capability.continuous.resolution.fixed_characteristics_until_end_of_turn", "residual.replacement.damage-prevention"),
)

TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS = tuple(
    _pair("capability.attachment.aura.typed_restriction", residual)
    for residual in (
        "residual.continuous_layer.affected-player-ordering",
        "residual.continuous_layer.continuous-effect-layers-and-dependencies",
        "residual.duration.until-end-of-turn",
        "residual.static_clause.broader-evasion-and-group-constraints",
        "residual.static_clause.conditional-declaration-predicates",
        "residual.static_clause.temporary-declaration-restrictions",
    )
)

EFFECT_AND_REPLACEMENT_PAIRS = (
    _pair("capability.counter.producer.cumulative_upkeep_fixed_mana", "residual.replacement.damage-prevention"),
    _pair("capability.counter.producer.cumulative_upkeep_fixed_mana", "residual.replacement.replacement-applicability"),
    _pair("capability.counter.producer.cumulative_upkeep_fixed_mana", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.counter.producer.fixed_effect", "residual.replacement.damage-prevention"),
    _pair("capability.counter.producer.fixed_effect", "residual.replacement.replacement-applicability"),
    _pair("capability.counter.producer.fixed_effect", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.counter.producer.modular", "residual.replacement.replacement-applicability"),
    _pair("capability.counter.producer.modular", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.damage.result.multitype_permanent", "residual.replacement.replacement-applicability"),
    _pair("capability.damage.result.multitype_permanent", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.damage.result.player_life", "residual.replacement.replacement-applicability"),
    _pair("capability.damage.result.player_life", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.life.change.effect", "residual.replacement.replacement-applicability"),
    _pair("capability.life.change.effect", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.life.gain.replacement.static_multiplier", "residual.replacement.replacement-applicability"),
    _pair("capability.life.gain.replacement.static_multiplier", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.mana.activated.fixed_output", "residual.replacement.damage-prevention"),
    _pair("capability.mana.activated.fixed_output", "residual.replacement.replacement-applicability"),
    _pair("capability.mana.activated.fixed_output", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.permanent.destroy.effect", "residual.replacement.replacement-applicability"),
    _pair("capability.permanent.destroy.effect", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.permanent.destroy.fixed_set", "residual.replacement.replacement-applicability"),
    _pair("capability.permanent.destroy.fixed_set", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.permanent.exile.effect", "residual.replacement.replacement-applicability"),
    _pair("capability.permanent.exile.effect", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.permanent.indestructible.ordinary", "residual.replacement.replacement-applicability"),
    _pair("capability.permanent.indestructible.ordinary", "residual.replacement.self-replacement-and-prevention-ordering"),
    _pair("capability.counter.producer.fixed_permanent_target_set_effect", "residual.replacement.damage-prevention"),
    _pair("capability.counter.producer.fixed_effect", "residual.replacement.regeneration"),
    _pair("capability.counter.producer.optional_fixed_event_trigger", "residual.replacement.damage-prevention"),
    _pair("capability.counter.producer.optional_fixed_event_trigger", "residual.replacement.regeneration"),
    _pair("capability.counter.producer.cumulative_upkeep_fixed_life", "residual.replacement.damage-prevention"),
    _pair("capability.counter.producer.cumulative_upkeep_fixed_life", "residual.replacement.replacement-applicability"),
    _pair("capability.counter.producer.cumulative_upkeep_fixed_life", "residual.replacement.self-replacement-and-prevention-ordering"),
    DESTROY_DAMAGE_PREVENTION_PAIR,
    DESTROY_REGENERATION_PAIR,
)

FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS = tuple(
    _pair(capability, "residual.replacement.regeneration")
    for capability in (
        "capability.damage.amount.positive",
        "capability.damage.batch.fixed_set",
        "capability.damage.result.multitype_permanent",
        "capability.damage.result.player_life",
    )
)

ZONE_AND_CHOICE_PAIRS = (
    _pair("capability.zone.change.destination_replacement", "residual.target_or_choice.target-predicate"),
    _pair("capability.zone.draw.library_to_hand", "residual.target_or_choice.conditional-effect"),
    _pair("capability.zone.draw.library_to_hand", "residual.target_or_choice.divided-damage-allocation"),
    _pair("capability.zone.draw.library_to_hand", "residual.target_or_choice.multiple-damage-recipients"),
    _pair("capability.zone.draw.library_to_hand", "residual.target_or_choice.multiple-targets"),
    _pair("capability.zone.draw.library_to_hand", "residual.target_or_choice.target-predicate"),
    _pair("capability.zone.entry.tapped_state", "residual.target_or_choice.conditional-effect"),
    _pair("capability.zone.entry.tapped_state", "residual.target_or_choice.multiple-targets"),
    _pair("capability.zone.entry.tapped_state", "residual.target_or_choice.random-outcome"),
    _pair("capability.zone.change.destination_replacement", "residual.target_or_choice.conditional-effect"),
    _pair("capability.zone.change.destination_replacement", "residual.target_or_choice.multiple-targets"),
    _pair("capability.zone.move.fixed_public_set", "residual.target_or_choice.conditional-effect"),
    _pair("capability.zone.move.fixed_public_set", "residual.target_or_choice.target-predicate"),
)

PUBLIC_SET_AND_CHOICE_PAIRS = ZONE_AND_CHOICE_PAIRS[-2:]

COST_AND_REPLACEMENT_PAIRS = tuple(
    _pair(cost, replacement)
    for cost in (
        "residual.activated_cost.complete-alternate-additional-cost-grammar",
        "residual.activated_cost.restricted-payment-predicates",
    )
    for replacement in (
        "residual.replacement.damage-prevention",
        "residual.replacement.regeneration",
        "residual.replacement.replacement-applicability",
        "residual.replacement.self-replacement-and-prevention-ordering",
    )
)

CONTINUOUS_AND_REPLACEMENT_PAIRS = tuple(
    _pair(continuous, replacement)
    for continuous in (
        "residual.continuous_layer.affected-player-ordering",
        "residual.continuous_layer.continuous-effect-layers-and-dependencies",
        "residual.duration.until-end-of-turn",
    )
    for replacement in (
        "residual.replacement.damage-prevention",
        "residual.replacement.regeneration",
        "residual.replacement.replacement-applicability",
        "residual.replacement.self-replacement-and-prevention-ordering",
    )
)

TRIGGER_AND_REPLACEMENT_PAIRS = tuple(
    _pair(trigger, replacement)
    for trigger in (
        "residual.event_binding.intervening-if-and-reflexive-trigger-grammar",
        "residual.event_binding.normalized-event-binding",
    )
    for replacement in (
        "residual.replacement.damage-prevention",
        "residual.replacement.regeneration",
        "residual.replacement.replacement-applicability",
        "residual.replacement.self-replacement-and-prevention-ordering",
    )
)

DECLARATION_AND_REPLACEMENT_PAIRS = tuple(
    _pair(replacement, declaration)
    for replacement in (
        "residual.replacement.replacement-applicability",
        "residual.replacement.self-replacement-and-prevention-ordering",
    )
    for declaration in (
        "residual.static_clause.broader-evasion-and-group-constraints",
        "residual.static_clause.conditional-declaration-predicates",
        "residual.static_clause.temporary-declaration-restrictions",
    )
)

ALL_HIGH_RISK_BOUNDARY_PAIRS = tuple(
    sorted(
        {
            *ATTACHMENT_AND_CONTINUOUS_PAIRS,
            *TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS,
            *EFFECT_AND_REPLACEMENT_PAIRS,
            *FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS,
            *ZONE_AND_CHOICE_PAIRS,
            *COST_AND_REPLACEMENT_PAIRS,
            *CONTINUOUS_AND_REPLACEMENT_PAIRS,
            *TRIGGER_AND_REPLACEMENT_PAIRS,
            *DECLARATION_AND_REPLACEMENT_PAIRS,
            *PREVENTION_AND_REPLACEMENT_PAIRS,
            TOKEN_AND_DAMAGE_PREVENTION_PAIR,
            *FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS,
            *TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS,
        }
    )
)


_PAIR_WITNESS: dict[Pair, str] = {}


def _bind(witness: str, *pairs: Pair) -> None:
    for pair in pairs:
        if pair in _PAIR_WITNESS:
            raise AssertionError(f"duplicate high-risk witness pair: {pair}")
        _PAIR_WITNESS[pair] = witness


_bind("floating-shield", *ATTACHMENT_AND_CONTINUOUS_PAIRS[:3])
_bind("aether-tunnel", *ATTACHMENT_AND_CONTINUOUS_PAIRS[3:6])
_bind(
    "typed-floating-shield",
    *TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS[:3],
)
_bind(
    "typed-aether-tunnel",
    *TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS[3:],
)
_bind("starforged-sword", *ATTACHMENT_AND_CONTINUOUS_PAIRS[6:8])
_bind("junk-jet", ATTACHMENT_AND_CONTINUOUS_PAIRS[8])
_bind("soratami-cloud-chariot", ATTACHMENT_AND_CONTINUOUS_PAIRS[9])
_bind("prismatic-circle", *EFFECT_AND_REPLACEMENT_PAIRS[:3])
_bind("serras-hymn", EFFECT_AND_REPLACEMENT_PAIRS[3])
_bind("tekuthal", *EFFECT_AND_REPLACEMENT_PAIRS[4:6])
_bind("zabaz", *EFFECT_AND_REPLACEMENT_PAIRS[6:8])
_bind("damage-result-and-coin-replacement", *EFFECT_AND_REPLACEMENT_PAIRS[8:12])
_bind("decode-transmissions", *EFFECT_AND_REPLACEMENT_PAIRS[12:14])
_bind("alhammarrets-archive", *EFFECT_AND_REPLACEMENT_PAIRS[14:16])
_bind("kor-haven", EFFECT_AND_REPLACEMENT_PAIRS[16])
_bind("game-trail", *EFFECT_AND_REPLACEMENT_PAIRS[17:19])
_bind("rancid-earth", *EFFECT_AND_REPLACEMENT_PAIRS[19:21])
_bind("cleansing-meditation", *EFFECT_AND_REPLACEMENT_PAIRS[21:23])
_bind("legacy-weapon", *EFFECT_AND_REPLACEMENT_PAIRS[23:25])
_bind("tekuthal", *EFFECT_AND_REPLACEMENT_PAIRS[25:27])
_bind("oko-trickster", EFFECT_AND_REPLACEMENT_PAIRS[27])
_bind("vile-requiem", EFFECT_AND_REPLACEMENT_PAIRS[28])
_bind("serras-hymn", EFFECT_AND_REPLACEMENT_PAIRS[29])
_bind("vile-requiem", EFFECT_AND_REPLACEMENT_PAIRS[30])
_bind(
    "fixed-life-upkeep-prevention",
    *EFFECT_AND_REPLACEMENT_PAIRS[31:34],
)
_bind("gideon-jura", DESTROY_DAMAGE_PREVENTION_PAIR)
_bind("jaya-task-mage", DESTROY_REGENERATION_PAIR)
_bind(
    "jaya-task-mage",
    *FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS,
)
_bind("dauthi-voidwalker", ZONE_AND_CHOICE_PAIRS[0])
_bind("sphinxs-insight", ZONE_AND_CHOICE_PAIRS[1])
_bind("electrolyze", ZONE_AND_CHOICE_PAIRS[2], ZONE_AND_CHOICE_PAIRS[4])
_bind("cunning-strike", ZONE_AND_CHOICE_PAIRS[3])
_bind("blur", ZONE_AND_CHOICE_PAIRS[5])
_bind("madblind-mountain", ZONE_AND_CHOICE_PAIRS[6])
_bind("wintermoon-mesa", ZONE_AND_CHOICE_PAIRS[7])
_bind("ebony-fly", ZONE_AND_CHOICE_PAIRS[8])
_bind("heartless-pillage", ZONE_AND_CHOICE_PAIRS[9])
_bind("snow-day", ZONE_AND_CHOICE_PAIRS[10])
_bind("descend-upon-the-sinful", PUBLIC_SET_AND_CHOICE_PAIRS[0])
_bind("gideon-champion-of-justice", PUBLIC_SET_AND_CHOICE_PAIRS[1])
_bind(
    "rasputin",
    COST_AND_REPLACEMENT_PAIRS[0],
    COST_AND_REPLACEMENT_PAIRS[4],
)
_bind(
    "ovinomancer",
    COST_AND_REPLACEMENT_PAIRS[1],
    COST_AND_REPLACEMENT_PAIRS[5],
)
_bind("etchings-of-the-chosen", *COST_AND_REPLACEMENT_PAIRS[2:4])
_bind("etchings-of-the-chosen", *COST_AND_REPLACEMENT_PAIRS[6:8])
_bind("prismatic-circle", CONTINUOUS_AND_REPLACEMENT_PAIRS[0])
_bind("kirtars-wrath", CONTINUOUS_AND_REPLACEMENT_PAIRS[1])
_bind("floating-shield", *CONTINUOUS_AND_REPLACEMENT_PAIRS[2:4])
_bind("mourners-shield", CONTINUOUS_AND_REPLACEMENT_PAIRS[4])
_bind("avatar-of-woe", CONTINUOUS_AND_REPLACEMENT_PAIRS[5])
_bind("floating-shield", *CONTINUOUS_AND_REPLACEMENT_PAIRS[6:8])
_bind("gideon-oathsworn", CONTINUOUS_AND_REPLACEMENT_PAIRS[8])
_bind("runesword", CONTINUOUS_AND_REPLACEMENT_PAIRS[9])
_bind("floating-shield", *CONTINUOUS_AND_REPLACEMENT_PAIRS[10:12])
_bind("rasputin", TRIGGER_AND_REPLACEMENT_PAIRS[0], TRIGGER_AND_REPLACEMENT_PAIRS[4])
_bind("ovinomancer", TRIGGER_AND_REPLACEMENT_PAIRS[1], TRIGGER_AND_REPLACEMENT_PAIRS[5])
_bind("kindred-discovery", *TRIGGER_AND_REPLACEMENT_PAIRS[2:4])
_bind("kindred-discovery", *TRIGGER_AND_REPLACEMENT_PAIRS[6:8])
_bind("teferis-moat", *DECLARATION_AND_REPLACEMENT_PAIRS)
_bind("hallowed-healer", PREVENTION_AND_REPLACEMENT_PAIRS[0])
_bind("winds-of-qal-sisma", *PREVENTION_AND_REPLACEMENT_PAIRS[1:])
_bind("gideon-ally-of-zendikar", TOKEN_AND_DAMAGE_PREVENTION_PAIR)
_bind("rasputin", FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS[0])
_bind("chromatic-armor", *FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS[1:])
_bind(
    "bewitching-leechcraft",
    TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS[0],
    *TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS[3:5],
)
_bind("tangle-kelp", TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS[1])
_bind("pemmins-aura", TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS[2])
_bind("sleep-cursed-faerie", *TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS[5:])

if set(_PAIR_WITNESS) != set(ALL_HIGH_RISK_BOUNDARY_PAIRS):
    raise AssertionError("high-risk witness map is incomplete")


def _record(key: str) -> CardRecord:
    witness = _WITNESSES[key]
    ordinal = tuple(sorted(_WITNESSES)).index(key) + 1
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{ordinal:012d}",
        name=witness.name,
        mana_cost=witness.mana_cost,
        mana_value=0.0,
        type_line=witness.type_line,
        oracle_text=witness.oracle_text,
        power=witness.power,
        toughness=witness.toughness,
        loyalty=witness.loyalty,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=witness.keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def _observed_piece_ids(row: dict) -> set[str]:
    observed: set[str] = set()
    for ability in row["abilities"]:
        blockers = ability.get("blockers", {})
        observed.update(
            f"capability.{capability_id}"
            for capability_id in blockers.get("capability_ids", ())
        )
        observed.update(
            "residual." + family_id.replace(":", ".", 1)
            for family_id in blockers.get("canonical_family_ids", ())
        )
    return observed


def assert_high_risk_boundary_pairs(
    case: unittest.TestCase,
    pairs: Iterable[Pair],
    *,
    database: CardDatabase | None = None,
) -> None:
    """Prove each residual pair stays blocked at runtime admission."""

    expected = tuple(pairs)
    case.assertEqual(len(expected), len(set(expected)))
    registry = load_default_capability_registry()
    owned_database = database is None
    db = database or CardDatabase(DB_PATH)
    analyzed: dict[
        str, tuple[dict, CardProgram | None, dict | None, str | None]
    ] = {}
    try:
        for pair in expected:
            key = _PAIR_WITNESS[pair]
            analysis = analyzed.get(key)
            if analysis is None:
                record = _record(key)
                oracle_ir = compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                program_error = None
                try:
                    program = compile_best_available_card_program(
                        db,
                        record,
                        semantic_registry=SemanticRegistry(),
                        capability_registry=registry,
                        capability_profile="commander_review",
                    )
                except (KeyError, ValueError) as exc:
                    program = None
                    program_error = str(exc)
                row = analyze_card_unlocks(
                    oracle_ir,
                    program=program,
                    program_error=program_error,
                    capabilities=registry,
                    profile="commander_review",
                )
                binding = (
                    bind_card_program_runtime(
                        program,
                        capability_registry=registry,
                        profile="commander_review",
                    )
                    if program is not None
                    else None
                )
                analysis = (row, program, binding, program_error)
                analyzed[key] = analysis
            row, program, binding, program_error = analysis
            observed = _observed_piece_ids(row)
            with case.subTest(pair=pair, witness=row["card_name"]):
                case.assertLessEqual(set(pair), observed)
                case.assertEqual("residual", row["card_program_status"])
                case.assertIsNone(row["hard_construction_failure"])
                case.assertIsNone(program_error)
                case.assertIsNotNone(program)
                case.assertIsNotNone(binding)
                if program is None or binding is None:
                    case.fail("Residual witness did not reach runtime admission")
                case.assertEqual(
                    "unresolved",
                    program.trust_closure["trust_basis"],
                )
                case.assertFalse(
                    program.trust_closure["strict_capability_ready"]
                )
                case.assertFalse(binding["strict_capability_ready"])
                case.assertFalse(binding["compatible_ready"])
                case.assertIn("trust_basis:unresolved", binding["blockers"])
                for piece_id in pair:
                    if piece_id.startswith("capability."):
                        capability_id = piece_id.removeprefix("capability.")
                        capability = registry.capability(capability_id)
                        case.assertIsNotNone(capability)
                        case.assertEqual("trusted", capability["status"])
                        case.assertTrue(
                            any(
                                capability_id
                                in ability.get("blockers", {}).get(
                                    "capability_ids", ()
                                )
                                for ability in row["abilities"]
                            )
                        )
                    else:
                        family_id = piece_id.removeprefix("residual.").replace(
                            ".", ":", 1
                        )
                        case.assertTrue(
                            any(
                                ability["status"] != "exact"
                                and family_id
                                in ability.get("blockers", {}).get(
                                    "canonical_family_ids", ()
                                )
                                for ability in row["abilities"]
                            )
                        )
    finally:
        if owned_database:
            db.close()


__all__ = [
    "ALL_HIGH_RISK_BOUNDARY_PAIRS",
    "ATTACHMENT_AND_CONTINUOUS_PAIRS",
    "CONTINUOUS_AND_REPLACEMENT_PAIRS",
    "COST_AND_REPLACEMENT_PAIRS",
    "DECLARATION_AND_REPLACEMENT_PAIRS",
    "DESTROY_DAMAGE_PREVENTION_PAIR",
    "DESTROY_REGENERATION_PAIR",
    "EFFECT_AND_REPLACEMENT_PAIRS",
    "FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS",
    "FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS",
    "PREVENTION_AND_REPLACEMENT_PAIRS",
    "PUBLIC_SET_AND_CHOICE_PAIRS",
    "TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS",
    "TOKEN_AND_DAMAGE_PREVENTION_PAIR",
    "TRIGGER_AND_REPLACEMENT_PAIRS",
    "TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS",
    "ZONE_AND_CHOICE_PAIRS",
    "assert_high_risk_boundary_pairs",
]
