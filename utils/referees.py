"""
Referee name unification. API-Sports has no referee id — the referee is a
free-text field whose format varies for the same person, even within one
league and season ("M. Oliver", "Michael Oliver, England", and since late
Sept 2026 "Michael Oliver"). Exact-name lookups therefore split one referee's
history into pieces; these helpers group the variants under one key.

Key = (first initial, surname), accent-free, country dropped. A key whose
variants carry two or more *different* explicit countries is treated as a
clash between different people and is not merged.
"""
import re
import unicodedata

_PARTICLES = {'de', 'da', 'do', 'dos', 'das', 'del', 'la', 'van', 'von', 'der', 'den', 'di', 'le'}


def _parse(name: str):
    """'Michael Oliver, England' -> (['michael', 'oliver'], 'england')."""
    base, _, country = name.partition(',')
    ascii_name = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode().lower()
    tokens = [t for t in re.split(r'[\s.]+', ascii_name) if t]
    return tokens, (country.strip().lower() or None)


def _candidates(tokens):
    """Possible (initial, surname) keys. Abbreviated names ('l. e. santander aguirre')
    give exactly one; full names give one per possible surname token."""
    if len(tokens) < 2:
        return []
    if len(tokens[0]) == 1:
        surname = next((t for t in tokens[1:] if len(t) > 1 and t not in _PARTICLES), None)
        return [(tokens[0], surname)] if surname else []
    return [(tokens[0][0], t) for t in tokens[1:] if len(t) > 1 and t not in _PARTICLES]


def build_referee_keys(names) -> tuple[dict, set]:
    """
    Maps every referee name to its key. Abbreviated forms anchor which token is
    the surname: 'Luis Enrique Santander Aguirre' goes to ('l', 'santander')
    because 'L. Santander' exists. Returns ({name: key or None}, clashing_keys).
    """
    parsed = {n: _parse(n) for n in names if n}
    anchors = {cands[0] for tokens, _ in parsed.values()
               if tokens and len(tokens[0]) == 1 and (cands := _candidates(tokens))}

    keys = {}
    for name, (tokens, _) in parsed.items():
        cands = _candidates(tokens)
        anchored = next((c for c in cands if c in anchors), None)
        # No abbreviated anchor: a two-word full name is unambiguous ('Daniel Siebert');
        # longer ones could be either surname, so they stay unmerged.
        keys[name] = anchored or (cands[0] if len(tokens) == 2 and cands else None)

    countries = {}
    for name, key in keys.items():
        country = parsed[name][1]
        if key and country:
            countries.setdefault(key, set()).add(country)
    clashing = {k for k, cs in countries.items() if len(cs) > 1}
    return keys, clashing
