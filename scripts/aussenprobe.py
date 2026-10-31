#!/usr/bin/env python3
"""aussenprobe.py — wirkt ein Test nach draussen, wenn echte Schluessel da sind?

Anlass (31.10.2026): In heinetzwerk schickten Tests auf dem Mac mini echte Alarm-Mails —
dort liegen Schluessel, in CI und Sandbox nicht. Hier liegen die Schluessel in `.env`, und
gesetzte Umgebungsvariablen haben Vorrang. Die Probe setzt deshalb fuer jeden Namen aus
`.env.example` einen Platzhalter und laesst die Suite mit dem Waechter
`scripts/aussenprobe/sitecustomize.py` laufen. Ein Test, der dann ein bezahltes API, ein
fremdes Programm (npx, curl, …) oder einen festen Port des eigenen Rechners anspricht oder
ins Home schreibt, ist ein Befund. Testfehler meldet `make test`, nicht diese Probe.

Ausnahmen mit Grund: `scripts/aussenprobe_ausnahmen.json`. Gemeldet wird das Ziel, nie ein
Inhalt. Exit 0: kein Befund. Exit 1: Befund oder eine Ausnahme, die nichts mehr deckt.
"""
import collections
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
WAECHTER = Path(__file__).resolve().parent / "aussenprobe"
AUSNAHMEN = Path(__file__).resolve().parent / "aussenprobe_ausnahmen.json"
FESTE_PORTS_UNTER = 20000


def platzhalter_env(home):
    namen = re.findall(r"^([A-Z][A-Z0-9_]+)=", (WURZEL / ".env.example").read_text(), re.M)
    env = {n: "probe-platzhalter" for n in namen}
    env.update(GOOGLE_APPLICATION_CREDENTIALS=str(Path(home) / "kein-dienstkonto.json"),
               VIDEO_GEN_LOCAL_ENABLED="false")
    return env


def befunde(zeilen, ausnahmen):
    gebraucht, aus = set(), []
    for z in zeilen:
        if z.get("test_home"):
            continue
        if z["art"] == "loopback":
            m = re.search(r",\s*(\d+)\)", z["ziel"])
            if m and int(m.group(1)) >= FESTE_PORTS_UNTER:
                continue
        deckend = [i for i, a in enumerate(ausnahmen)
                   if a["art"] == z["art"] and z["test"].startswith(a["test"])
                   and a.get("ziel", "") in z["ziel"]]
        if deckend:
            gebraucht.update(deckend)
        else:
            aus.append(z)
    return aus, [a for i, a in enumerate(ausnahmen) if i not in gebraucht]


def main():
    ausnahmen = json.loads(AUSNAHMEN.read_text())["ausnahmen"]
    kaputt = [a for a in ausnahmen if len(a.get("grund", "")) < 20]
    with tempfile.TemporaryDirectory(prefix="aussenprobe-") as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        log = Path(tmp) / "treffer.jsonl"
        env = dict(os.environ, **platzhalter_env(home))
        env.update(HOME=str(home), AUSSENPROBE_ECHTHOME=str(home), AUSSENPROBE_LOG=str(log),
                   PYTHONPATH=os.pathsep.join(filter(None, [str(WAECHTER), env.get("PYTHONPATH")])),
                   PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                           cwd=WURZEL, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        stand = (r.stdout.strip().splitlines() or ["?"])[-1]
        zeilen = [json.loads(z) for z in log.read_text().splitlines()] if log.exists() else []
    aus, unbenutzt = befunde(zeilen, ausnahmen)
    print("Aussenprobe: Suite %s · %d Ereignisse, %d Befunde" % (stand, len(zeilen), len(aus)))
    je = collections.defaultdict(set)
    for b in aus:
        je[(b["art"], b["test"])].add(b["ziel"])
    for (art, test), ziele in sorted(je.items()):
        print("  BEFUND %-17s %s — %s" % (art, test, sorted(ziele)[0]))
    for a in kaputt:
        print("  FEHLER Ausnahme ohne Grund (>= 20 Zeichen): %s" % a.get("test"))
    for a in unbenutzt:
        print("  FEHLER Ausnahme deckt nichts mehr — streichen: %s / %s" % (a["test"], a["art"]))
    return 1 if (aus or kaputt or unbenutzt) else 0


if __name__ == "__main__":
    sys.exit(main())
