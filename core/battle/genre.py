"""
Genre families and the counter ring (Phase 4a/4b). Pure logic.

Five families. Each beats one, loses to one, neutral to two:

    HIP_HOP > POP > ROCK > ELECTRONIC > SOUL > HIP_HOP

NEUTRAL (no data / unmapped) and mirror matchups are neutral both ways.

Tag mapping: Last.fm tags are messy ("hip hop", "hip-hop", "rap", "trap"
must all land on HIP_HOP). Tags are normalised to lowercase alphanumerics
before lookup, so add dictionary keys in that normalised form.
"""
import re
from enum import Enum
from typing import Dict, Iterable, Optional

from core.battle.config import GENRE_COUNTER_MULT, GENRE_COUNTERED_MULT


class GenreFamily(str, Enum):
    HIP_HOP = "HIP_HOP"
    POP = "POP"
    ROCK = "ROCK"
    ELECTRONIC = "ELECTRONIC"
    SOUL = "SOUL"
    NEUTRAL = "NEUTRAL"

    @classmethod
    def parse(cls, value: Optional[str]) -> "GenreFamily":
        if isinstance(value, GenreFamily):
            return value
        key = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
        try:
            return cls(key)
        except ValueError:
            return cls.NEUTRAL


# family → the family it beats
GENRE_RING: Dict[GenreFamily, GenreFamily] = {
    GenreFamily.HIP_HOP: GenreFamily.POP,
    GenreFamily.POP: GenreFamily.ROCK,
    GenreFamily.ROCK: GenreFamily.ELECTRONIC,
    GenreFamily.ELECTRONIC: GenreFamily.SOUL,
    GenreFamily.SOUL: GenreFamily.HIP_HOP,
}

PLAYABLE_FAMILIES = tuple(GENRE_RING.keys())


def beats(attacker: GenreFamily, defender: GenreFamily) -> bool:
    return GENRE_RING.get(attacker) == defender


def genre_multiplier(attacker: GenreFamily, defender: GenreFamily) -> float:
    """Multiplier applied to `attacker`'s power when facing `defender`."""
    a, d = GenreFamily.parse(attacker), GenreFamily.parse(defender)
    if a == GenreFamily.NEUTRAL or d == GenreFamily.NEUTRAL or a == d:
        return 1.0
    if beats(a, d):
        return GENRE_COUNTER_MULT
    if beats(d, a):
        return GENRE_COUNTERED_MULT
    return 1.0


# ── tag → family mapping ────────────────────────────────────────────────────

_NORMALISE = re.compile(r"[^a-z0-9]+")


def normalize_tag(tag: str) -> str:
    """'Hip-Hop' → 'hiphop', 'R&B' → 'rb', 'Alt. Rock ' → 'altrock'."""
    return _NORMALISE.sub("", (tag or "").lower())


# Keys are normalised (see normalize_tag). Order inside the dict does not matter;
# the ORDER OF THE TAGS passed in does (Last.fm returns them by weight).
TAG_TO_FAMILY: Dict[str, GenreFamily] = {
    # HIP_HOP
    "hiphop": GenreFamily.HIP_HOP, "rap": GenreFamily.HIP_HOP, "trap": GenreFamily.HIP_HOP,
    "drill": GenreFamily.HIP_HOP, "grime": GenreFamily.HIP_HOP, "gangstarap": GenreFamily.HIP_HOP,
    "southernrap": GenreFamily.HIP_HOP, "undergroundhiphop": GenreFamily.HIP_HOP,
    "conscioushiphop": GenreFamily.HIP_HOP, "boombap": GenreFamily.HIP_HOP,
    "cloudrap": GenreFamily.HIP_HOP, "emorap": GenreFamily.HIP_HOP, "crunk": GenreFamily.HIP_HOP,
    "latintrap": GenreFamily.HIP_HOP, "reggaeton": GenreFamily.HIP_HOP, "afrobeats": GenreFamily.HIP_HOP,
    "afrobeat": GenreFamily.HIP_HOP, "dancehall": GenreFamily.HIP_HOP,
    # POP
    "pop": GenreFamily.POP, "dancepop": GenreFamily.POP, "electropop": GenreFamily.POP,
    "synthpop": GenreFamily.POP, "teenpop": GenreFamily.POP, "kpop": GenreFamily.POP,
    "jpop": GenreFamily.POP, "latinpop": GenreFamily.POP, "poprock": GenreFamily.POP,
    "indiepop": GenreFamily.POP, "artpop": GenreFamily.POP, "bubblegumpop": GenreFamily.POP,
    "singersongwriter": GenreFamily.POP, "country": GenreFamily.POP, "countrypop": GenreFamily.POP,
    "folk": GenreFamily.POP, "folkpop": GenreFamily.POP, "acoustic": GenreFamily.POP,
    "adultcontemporary": GenreFamily.POP, "boyband": GenreFamily.POP, "girlgroup": GenreFamily.POP,
    # ROCK
    "rock": GenreFamily.ROCK, "alternativerock": GenreFamily.ROCK, "altrock": GenreFamily.ROCK,
    "alternative": GenreFamily.ROCK, "indierock": GenreFamily.ROCK, "indie": GenreFamily.ROCK,
    "classicrock": GenreFamily.ROCK, "hardrock": GenreFamily.ROCK, "punk": GenreFamily.ROCK,
    "punkrock": GenreFamily.ROCK, "poppunk": GenreFamily.ROCK, "metal": GenreFamily.ROCK,
    "heavymetal": GenreFamily.ROCK, "metalcore": GenreFamily.ROCK, "grunge": GenreFamily.ROCK,
    "emo": GenreFamily.ROCK, "garagerock": GenreFamily.ROCK, "psychedelicrock": GenreFamily.ROCK,
    "progressiverock": GenreFamily.ROCK, "postpunk": GenreFamily.ROCK, "shoegaze": GenreFamily.ROCK,
    "britpop": GenreFamily.ROCK, "nurock": GenreFamily.ROCK, "numetal": GenreFamily.ROCK,
    "blues": GenreFamily.ROCK, "bluesrock": GenreFamily.ROCK, "rockabilly": GenreFamily.ROCK,
    "rocknroll": GenreFamily.ROCK, "rockandroll": GenreFamily.ROCK,
    # ELECTRONIC
    "electronic": GenreFamily.ELECTRONIC, "electronica": GenreFamily.ELECTRONIC,
    "edm": GenreFamily.ELECTRONIC, "house": GenreFamily.ELECTRONIC, "deephouse": GenreFamily.ELECTRONIC,
    "techhouse": GenreFamily.ELECTRONIC, "progressivehouse": GenreFamily.ELECTRONIC,
    "techno": GenreFamily.ELECTRONIC, "trance": GenreFamily.ELECTRONIC, "dubstep": GenreFamily.ELECTRONIC,
    "drumandbass": GenreFamily.ELECTRONIC, "dnb": GenreFamily.ELECTRONIC, "garage": GenreFamily.ELECTRONIC,
    "ukgarage": GenreFamily.ELECTRONIC, "futurebass": GenreFamily.ELECTRONIC, "electro": GenreFamily.ELECTRONIC,
    "dance": GenreFamily.ELECTRONIC, "eurodance": GenreFamily.ELECTRONIC, "ambient": GenreFamily.ELECTRONIC,
    "idm": GenreFamily.ELECTRONIC, "synthwave": GenreFamily.ELECTRONIC, "hyperpop": GenreFamily.ELECTRONIC,
    "bigroom": GenreFamily.ELECTRONIC, "hardstyle": GenreFamily.ELECTRONIC, "breakbeat": GenreFamily.ELECTRONIC,
    "chillout": GenreFamily.ELECTRONIC, "downtempo": GenreFamily.ELECTRONIC, "lofi": GenreFamily.ELECTRONIC,
    # SOUL
    "soul": GenreFamily.SOUL, "rb": GenreFamily.SOUL, "rnb": GenreFamily.SOUL, "randb": GenreFamily.SOUL,
    "rhythmandblues": GenreFamily.SOUL, "neosoul": GenreFamily.SOUL, "funk": GenreFamily.SOUL,
    "motown": GenreFamily.SOUL, "gospel": GenreFamily.SOUL, "jazz": GenreFamily.SOUL,
    "smoothjazz": GenreFamily.SOUL, "contemporaryrb": GenreFamily.SOUL, "alternativerb": GenreFamily.SOUL,
    "reggae": GenreFamily.SOUL, "disco": GenreFamily.SOUL, "quietstorm": GenreFamily.SOUL,
    "newjackswing": GenreFamily.SOUL, "urban": GenreFamily.SOUL, "afrosoul": GenreFamily.SOUL,
}


def family_from_tags(tags: Iterable[str]) -> GenreFamily:
    """First tag (in the given order) that maps to a family wins; else NEUTRAL."""
    for tag in tags or ():
        fam = TAG_TO_FAMILY.get(normalize_tag(tag))
        if fam:
            return fam
    return GenreFamily.NEUTRAL


def family_from_genre_string(genre: Optional[str]) -> GenreFamily:
    """
    AudioDB gives one free-text genre ("Hip-Hop", "Alternative Rock", "Pop/Rock").
    Try the whole string, then each '/', ',' or ' ' separated part, most specific first.
    """
    if not genre:
        return GenreFamily.NEUTRAL
    whole = TAG_TO_FAMILY.get(normalize_tag(genre))
    if whole:
        return whole
    parts = [p for p in re.split(r"[/,&+]| and ", genre) if p.strip()]
    fam = family_from_tags(parts)
    if fam != GenreFamily.NEUTRAL:
        return fam
    return family_from_tags(genre.split())


def resolve_family(track_tags: Optional[Iterable[str]] = None,
                   artist_tags: Optional[Iterable[str]] = None,
                   audiodb_genre: Optional[str] = None) -> GenreFamily:
    """Resolution chain (4a): Last.fm track tags → artist tags → AudioDB genre → NEUTRAL."""
    for tags in (track_tags, artist_tags):
        fam = family_from_tags(tags or ())
        if fam != GenreFamily.NEUTRAL:
            return fam
    return family_from_genre_string(audiodb_genre)
