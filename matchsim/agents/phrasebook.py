"""
Football phrasebook: local expressions the Narrator and Localizer may use so commentary
sounds native rather than translated.

Each expression is tied to a situation the stats engine can actually detect ("use_when").
Agents may only use an expression when the moment's data matches that situation, so an idiom
never implies something that did not happen. Expressions needing data we don't have (for
example where in the goal a shot went) are left out on purpose.

Idea inspired by LEaF Translations' guide to football idioms; descriptions are our own.
"""

PHRASEBOOK = {
    "en": [
        {"phrase": "parking the bus", "use_when": "a leading team drops deep / pressing_drop while leading late on"},
        {"phrase": "put it on a plate", "use_when": "an assist that set up an easy, high-xG chance"},
        {"phrase": "fox in the box", "use_when": "a striker scores from close range inside the box"},
        {"phrase": "a clean sheet", "use_when": "a team or goalkeeper concedes no goals (full time only)"},
        {"phrase": "the 12th man", "use_when": "the home crowd, for a home-team surge or late goal"},
        {"phrase": "throwing the kitchen sink at it", "use_when": "a trailing team surges forward late (pressing_surge or momentum while trailing)"},
    ],
    "fr": [
        {"phrase": "coude à coude", "use_when": "the score is level and the game is balanced"},
        {"phrase": "fermer la boutique", "use_when": "a leading team drops deep to protect the result"},
        {"phrase": "le gardien sauve les meubles", "use_when": "a goalkeeper keeps making saves for a team under pressure"},
        {"phrase": "une frappe de mule", "use_when": "a very powerful shot (shot_speed)"},
    ],
    "de": [
        {"phrase": "Bombenschuss", "use_when": "a very powerful or long-range shot (shot_speed, long distance)"},
        {"phrase": "Gras fressen", "use_when": "a team fights for every ball: pressing surge, chasing the game"},
        {"phrase": "Leitwolf", "use_when": "the player of the match or the standout leader"},
        {"phrase": "Bananenflanke", "use_when": "a curling cross that leads to a chance (pass_type cross)"},
    ],
    "es": [
        {"phrase": "¡Vaya pepinazo!", "use_when": "a thunderous shot from distance (shot_speed)"},
        {"phrase": "golazo", "use_when": "an outstanding goal: low xG, long distance or very fast shot"},
        {"phrase": "el cancerbero", "use_when": "the goalkeeper, especially after several saves"},
        {"phrase": "galáctico", "use_when": "a standout star performance (player of the match)"},
    ],
    "it": [
        {"phrase": "catenaccio", "use_when": "a team locks down defensively to protect a lead"},
        {"phrase": "un gran gol", "use_when": "an outstanding goal"},
        {"phrase": "il regista", "use_when": "the deep playmaker controlling the passing"},
    ],
    "pt": [
        {"phrase": "golaço", "use_when": "an outstanding goal"},
        {"phrase": "o maestro", "use_when": "the key playmaker: most chances created or passes"},
        {"phrase": "jogar por uma bola", "use_when": "a game where one moment will decide it: level score late on"},
    ],
    "nl": [
        {"phrase": "de bus parkeren", "use_when": "a leading team drops deep"},
        {"phrase": "een knal", "use_when": "a very powerful shot"},
    ],
}


def phrases_for(languages):
    """Phrasebook entries for the given language codes (others: the model uses its own knowledge)."""
    return {lang: PHRASEBOOK[lang] for lang in languages if lang in PHRASEBOOK}
