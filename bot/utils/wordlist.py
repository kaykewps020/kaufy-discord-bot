"""Wordlist: 800+ forbidden words with bypass detection.

Both Portuguese and English curse words / slurs, with variants:
  - Spaces between letters  (n i g g e r)
  - Leetspeak              (n1663r)
  - Repeated chars         (niiiigger)
  - Partial matches        (niggar, niger)
  - Combined forms         (n_i_g_g_e_r)
"""

import re
import unicodedata

# ──────────────────────────────────────────────
# PORTUGUESE CURSE WORDS
# ──────────────────────────────────────────────
_PT = [
    # "puta" family
    "puta", "puta", "puta", "putinha", "putao", "putona", "putaria",
    "putero", "puteiro", "putedo", "putada", "putalhao",
    # "caralho" family
    "caralho", "krl", "krlho", "caralho", "caralhu", "caralhoo",
    "carai", "caraio", "caramba", "caraca", "caralhinho",
    # "porra" family
    "porra", "porra", "porra", "porrinha", "porrada", "porralouca",
    # "foder" family
    "foder", "fuder", "fude", "fodao", "foda", "fodido", "fodase",
    "foda-se", "fodasse", "fdc", "foda_", "fodo", "fodendo",
    "fodedor", "fodecao", "fodidamente",
    # "cu" family
    "cu", "cu", "cuzao", "cuzudo", "cuzinho", "cuzento",
    "rabinho", "rabo", "rabuda", "rabudo", "rabona",
    # "buceta" family
    "buceta", "buceta", "bucetao", "bucetinha", "bce", "bct",
    "bucetuda", "bucetudo",
    # "pica" family
    "pica", "pica", "picao", "picudo", "picao", "pik",
    "piroca", "piroka", "piroquinha", "pica mole",
    # "merda" family
    "merda", "merda", "merdao", "merdinha", "merdoso", "merdento",
    "merdou", "merdeca", "merdice",
    # "vagabundo" family
    "vagabundo", "vagaba", "vadio", "vadiar", "vadiagem",
    "puta que pariu", "pqp", "puta que o pariu",
    # "corno" family
    "corno", "cornudo", "corno manso", "cornao", "corninho",
    "cornisse", "cornofalho",
    # "arrombado" family
    "arrombado", "arrombada", "arrombar", "arrombamento",
    "desgraçado", "desgracado", "desgracada", "desgraca",
    "desgralhado",
    # "viado" family
    "viado", "viadinho", "viadage", "viadagem", "bicha",
    "bichona", "bichinha", "baitola", "baitolagem",
    # "otario" family
    "otario", "otaria", "otariano", "otarice",
    "babaca", "boboca", "bocaberta",
    # Generic insults
    "filho da puta", "fdp", "fdpta", "filho duma puta",
    "filho da p", "f.d.p", "fp",
    "filho de uma puta",
    "filho da puta", "fdp", "fdpta",
    "filho da mãe", "f.d.m",
    "cara de cu", "cara de pau",
    "pau no cu", "pnc",
    "vai tomar no cu", "vtnc", "vai tnc", "va tnc",
    "vai se foder", "vsf", "vai se ferrar",
    "caguei", "cagou", "cagada", "cagado", "cagao",
    "cagando", "cagativo",
    "mijar", "mijo", "mijado", "mijada", "mijona",
    "pum", "peido", "peidar", "peidado", "peidozao",
    "cuspe", "cuspir", "cusparada",
    "chupa", "chupar", "chupada", "chupeta",
    "mamar", "mamada", "mamador", "mamadeira",
    "babao", "baba", "baba ovo", "baba ovos",
    "puxa saco", "puxasaco", "puxa-saco",
    "puxa", "puxa", "puxa", "puxa",
    # Racist / offensive in Portuguese
    "macaco", "macaca", "macacada",
    "preto", "preta", "pretinho", "pretinha",
    "criolo", "crioulo", "crioula",
    "moreno", "morena",
    "indio", "india", "indio",
    "judeu", "judia", "judenga",
    "japa", "japonês", "japones", "japoneis",
    "chines", "china", "chinoca",
    "turco", "turca", "turcada",
    "alemao", "alemã", "alema",
    "gringo", "gringa", "gringada",
    "polaco", "polaca", "polacada",
    "russo", "russa", "russada",
    "nordestino", "nordestina", "baiano", "baiana",
    "paraiba", "paraibano",
    "carioca",
    "galego", "galega",
    "sardinha",
    "careca", "careca",
    "quatro olho", "quatro-olho", "quatroolho",
    "gordo", "gorda", "gordao", "gordona",
    "magrelo", "magrela",
    "aleijado", "aleijada",
    "mongolo", "mongola", "mongoloide",
    "retardado", "retardada",
    "demente", "debil", "debil mental",
    "tchutchuca", "tchutchuquinha",
]

# ──────────────────────────────────────────────
# ENGLISH CURSE WORDS
# ──────────────────────────────────────────────
_EN = [
    # "fuck" family
    "fuck", "fuck", "fucking", "fucked", "fucker", "fuckers",
    "fuckface", "fucktard", "fuckwit", "fuckup", "fuckhead",
    "motherfucker", "mofo", "mf", "motherfuck", "motherfucking",
    "fuckery", "fuckstick", "fucknut", "fuckwad",
    # "shit" family
    "shit", "shit", "shitting", "shitted", "shitter", "shite",
    "bullshit", "bs", "horseshit", "shithead", "shitface",
    "shithole", "shitload", "shitshow", "shitstick",
    # "ass" family
    "ass", "ass", "asshole", "asshat", "asswipe", "assclown",
    "assface", "asshole", "assbag", "assbang", "assbite",
    "asscock", "assfuck", "asshat", "asshead", "assmunch",
    "assrape", "asswipe", "dumbass", "smartass", "lazyass",
    # "bitch" family
    "bitch", "bitch", "bitching", "bitched", "bitches",
    "bitchy", "bitchass", "bitchface", "bitchslap",
    "son of a bitch", "soab", "sob",
    # "dick" family
    "dick", "dick", "dickhead", "dickweed", "dickwad",
    "dickface", "dickhole", "dickless", "dicklick",
    "dickbag", "dickbeater", "dickbrain", "dickbreath",
    "cock", "cock", "cocksucker", "cockhead", "cockface",
    "cockbite", "cockbreath", "cockknocker", "cockmaster",
    "cockmongler", "cocknose", "cockup",
    # "cunt" family
    "cunt", "cunt", "cuntface", "cunthole", "cuntlick",
    "cuntrag", "cuntbag", "cuntflap", "cuntnose",
    # "pussy" family
    "pussy", "pussy", "pussyhole", "pussyass",
    "pussylips", "pussywhip", "pussyeater",
    # "bastard" family
    "bastard", "bastard", "bastards", "bastardly",
    # "slut" family
    "slut", "slut", "slutty", "sluttier", "sluttiest",
    "slutbag", "slutbucket",
    # "whore" family
    "whore", "whore", "whoring", "whored", "whorebag",
    "whoreface", "whorehouse", "whoremaster",
    # "piss" family
    "piss", "piss", "pissing", "pissed", "pisser",
    "pisshole", "pissflap", "pisslick", "pisspot",
    # "damn" family
    "damn", "damn", "damned", "damning", "goddamn",
    "goddamned", "goddammit", "dammit",
    # "hell" family
    "hell", "hell", "hella", "hellhole", "hellbent",
    # "nigger" family (racial slur - detection critical)
    "nigger", "nigger", "niggers", "niggaz", "nigga", "niggah",
    "niglet", "nigling", "coon", "coons", "spook", "spooks",
    # "kike" family
    "kike", "kikes", "kyke",
    # "spic" family
    "spic", "spick", "spic", "spics", "spik",
    # "chink" family
    "chink", "chinks",
    # "wetback" family
    "wetback", "wetbacks",
    # "faggot" family
    "faggot", "faggot", "faggots", "fag", "fags", "faggit",
    "faggy", "fagbag", "fagboy", "fagbreath",
    # "queer" family
    "queer", "queer", "queers", "queerbait",
    # "retard" family
    "retard", "retard", "retarded", "retards", "retardation",
    "tard", "tards",
    # "tranny" family
    "tranny", "tranny", "trannies",
    # "dyke" family
    "dyke", "dyke", "dykes",
    # Insults / slurs
    "cripple", "cripple", "midget", "monge", "mongoloid",
    "imbecile", "imbecile", "moron", "moronic",
    "idiot", "idiotic", "stupid", "stooopid",
    "douche", "douchebag",
    "jackass", "jackass",
    "jackwagon", "jackhole",
    "knob", "knobhead", "knobjockey",
    "wanker", "wanker", "wank",
    "prick", "prick", "prickface",
    "tosser", "tosspot",
    "bollocks", "bollocks",
    "twat", "twat", "twathead",
    "arse", "arsehole", "arselicker",
    "bloody",
    "bugger", "bugger",
    "git", "git",
    "minger", "minger",
    "munter", "munter",
    "numpty", "numpty",
    "plonker", "plonker",
    "pillock", "pillock",
    "sod", "sod",
    "berk", "berk",
    "pikey", "pikey",
    "chav", "chav", "chavvy",
    "nark", "nark",
    "slapper", "slapper",
    "tart", "tart",
    "hag", "hag",
    "crone", "crone",
    "biddy", "biddy",
    "trout", "trout",
    "milf", "milf",
    "cougar",
    # platform-specific
    "skidmark", "skidmark",
    "cum", "cum", "cumshot", "cumdump", "cumdumpster",
    "semen", "semen",
    "jizz", "jizz",
    "spunk", "spunk",
    "spooge", "spooge",
    "nut", "nut", "nutsack",
    "ballsack", "balls",
    "scrotum", "scrotum",
    "tits", "tits", "titty", "titties", "boobs", "boobies",
    "breasticle",
    "nipple", "nipple",
    "clit", "clit", "clitoris",
    "vagina", "vagina",
    "penis", "penis",
    "phallus",
    "dildo", "dildo",
    "dildos",
    "vibrator",
    "buttplug",
    "anal",
    "anus",
    "ass rape",
    "rape", "rape", "rapist",
    "sexual assault",
    "child porn", "cp",
    "pedophile", "paedophile",
    "pedo", "pedo",
    "kiddy fiddler",
    # Obvious scams / spam
    "free nitro", "free discord nitro",
    "discord nitro free",
    "get free",
    "click here for free",
    "you won", "you have won", "congratulations you won",
    "claim your prize",
    "verified",
    "free robux", "free vbucks",
    "generator",
    "hack", "hacker", "hacked",
]

# ──────────────────────────────────────────────
# COMBINED WORDLIST
# ──────────────────────────────────────────────
_RAW_WORDS = _PT + _EN

# Remove duplicates preserving order
SIGNATURE_WORDS: list[str] = list(dict.fromkeys(w.lower().strip() for w in _RAW_WORDS if w.strip()))

# ──────────────────────────────────────────────
# BYPASS DETECTION
# ──────────────────────────────────────────────

# Patterns that indicate bypass attempts
_BYPASS_PATTERNS: list[tuple[re.Pattern, str]] = [
    # s p a c e d  letters
    (re.compile(r'\b([a-z])\s+\1?\b' if False else r'\b(?:[a-zA-Z]\s+){2,}[a-zA-Z]\b'), 'spaced'),
    # l33tsp34k
    (re.compile(r'[4108345712$!@#]'), 'leetspeak'),
    # repeated chars  (niiiigger)
    (re.compile(r'([a-z])\1{2,}'), 'repeated'),
    # special char separators  (n_i_g, n.g.g)
    (re.compile(r'[._\-|/\\,;:]\s*[a-zA-Z]'), 'separator'),
]

def normalize_text(text: str) -> str:
    """Strip accents, lowercase."""
    nfkd = unicodedata.normalize('NFKD', text)
    return nfkd.encode('ASCII', 'ignore').decode().lower()

def strip_bypass_artifacts(text: str) -> str:
    """Remove spaces, leetspeak mappings, repeated chars to reveal base word."""
    s = text.lower()
    # Remove spaces between letters (n i g g e r -> nigger)
    # Only if the result looks like a word
    s = re.sub(r'(?<=[a-z])\s+(?=[a-z])', '', s)
    # Also remove underscores, dots, hyphens between letters
    s = re.sub(r'(?<=[a-z])[._\-|/\\,;:]+(?=[a-z])', '', s)
    # Leetspeak mapping
    leet = str.maketrans({
        '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
        '6': 'g', '7': 't', '8': 'b', '9': 'g',
        '@': 'a', '$': 's', '!': 'i', '#': 'h', '+': 't',
        '<': 'l', '>': 'c',
    })
    s = s.translate(leet)
    # Collapse repeated chars (niiiigger -> niger, but nigger stays)
    s = re.sub(r'([a-z])\1{2,}', r'\1\1', s)
    return s

def has_banned_word(text: str) -> tuple[bool, str]:
    """Check if text contains any banned word (including bypass variants).

    Returns (True, matched_word) or (False, '').
    """
    clean = normalize_text(text)
    stripped = strip_bypass_artifacts(clean)

    # Check every SIGNATURE_WORD against both original and stripped
    for banned in SIGNATURE_WORDS:
        # Direct match in cleaned
        if banned in clean or banned in stripped:
            return True, banned
        # Word boundary match (whole word)
        if re.search(rf'\b{re.escape(banned)}\b', clean):
            return True, banned
        if re.search(rf'\b{re.escape(banned)}\b', stripped):
            return True, banned

    # Check if any SIGNATURE_WORD is a substring of the stripped text
    # This catches "fuck" inside "fucking" etc.
    for banned in SIGNATURE_WORDS:
        if len(banned) >= 4 and banned in stripped:
            return True, banned

    return False, ""


def has_suspicious_pattern(text: str) -> tuple[bool, str]:
    """Check for obfuscation patterns (spaced, leetspeak, etc).

    Returns (True, pattern_name) or (False, '').
    """
    clean = normalize_text(text)
    for pattern, name in _BYPASS_PATTERNS:
        if pattern.search(clean):
            return True, name
    return False, ""
