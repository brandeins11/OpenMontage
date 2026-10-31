# Kopie von heinetzwerk scripts/ops/aussenprobe/sitecustomize.py (Stand bbe1a9b).
# Aenderungen zuerst dort, dann hierher uebernehmen — zwei Fassungen driften sonst.
# lauf-ergebnis: nicht zutreffend — Wächter fuer Testprozesse, kein Lauf.
"""Aussenprobe: faengt in Testprozessen ab, was auf dem Mini nach draussen wirken wuerde.

Wird ueber PYTHONPATH als `sitecustomize` geladen — in jedem Python-Prozess der Testsuite,
auch in Kindprozessen. Gestartet von `scripts/ops/aussenprobe.py`; allein ist er stumm.

**Anlass (31.10.2026).** Ein Testlauf auf dem Mini schickte echte Alarm-Mails: Dort liegen
Schluessel und echte Zustandsdateien, in Sandbox und CI nicht. Eine einmalige Probe fand
danach vier weitere Tests (ssh zur NAS, gh mit dem Token des Mini, zwei echte Protokolle).
Dieser Waechter macht die Probe dauerhaft.

Gemeldet wird je Versuch eine JSON-Zeile mit Test, Art und Ziel — **nie ein Inhalt**:
`netz` (Verbindung nach aussen), `dns` (Name statt Adresse), `loopback` (eigener Rechner,
mit Port), `programm` (echtes Programm aus der Liste unten), `git-push-remote` (Push auf ein
nicht lokales Remote), `schreibt-ins-home`. Netz, DNS und Programme werden vor der Wirkung
abgebrochen; Loopback, Push und Schreiben werden nur gemeldet.
"""
import builtins
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile

_LOG = os.environ.get("AUSSENPROBE_LOG")
_HOME = os.path.realpath(os.environ.get("HOME", "/"))
_ECHTHOME = os.path.realpath(os.environ.get("AUSSENPROBE_ECHTHOME", _HOME))
_LOKAL = ("127.0.0.1", "::1", "localhost")
_TMP = tuple(sorted({os.path.realpath(tempfile.gettempdir()), "/tmp", "/private/tmp",
                     "/var/folders", "/private/var/folders"}))
_PROGRAMME = ("osascript", "curl", "wget", "wrangler", "npx", "launchctl", "ssh", "scp",
              "rsync", "tmutil", "codex", "claude", "gh", "sendmail", "terminal-notifier",
              "shortcuts", "tailscale", "cloudflared", "rclone", "sudo", "psql", "pg_dump",
              "open", "security")
_test = {"id": "?"}


class AussenprobeGesperrt(OSError):
    """Ein Testprozess wollte nach draussen."""


def _melden(art, ziel):
    if not _LOG:
        return
    try:
        with _orig_open(_LOG, "a", encoding="utf-8") as f:
            # pytest-Tests laufen nicht durch TestCase.run; pytest nennt den Test in der Umgebung.
            test = _test["id"] if _test["id"] != "?" else \
                os.environ.get("PYTEST_CURRENT_TEST", "?").rsplit(" (", 1)[0]
            f.write(json.dumps({"test": test, "art": art, "ziel": str(ziel)[:200],
                                "test_home": _HOME != _ECHTHOME}) + "\n")
    except OSError:
        pass


def _ist_ip(host):
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in str(host) else socket.AF_INET, str(host))
        return True
    except (OSError, ValueError):
        return False


# --- Netz -------------------------------------------------------------------------------
_orig_connect = socket.socket.connect


def _connect(self, adresse):
    if self.family == getattr(socket, "AF_UNIX", -1):
        return _orig_connect(self, adresse)
    host = adresse[0] if isinstance(adresse, tuple) else adresse
    if str(host) in _LOKAL:
        _melden("loopback", adresse)
        return _orig_connect(self, adresse)
    _melden("netz", adresse)
    raise AussenprobeGesperrt("Aussenprobe: Verbindung nach aussen gesperrt")


socket.socket.connect = _connect
_orig_gai = socket.getaddrinfo


def _gai(host, *a, **k):
    if host is not None and str(host) not in _LOKAL and not _ist_ip(host):
        _melden("dns", host)
        raise socket.gaierror("Aussenprobe: Namensaufloesung gesperrt")
    return _orig_gai(host, *a, **k)


socket.getaddrinfo = _gai

# --- Programme -------------------------------------------------------------------------
_orig_popen_init = subprocess.Popen.__init__


def _attrappe(programm, env):
    """True, wenn das Programm nichts Echtes ausloesen kann: fehlt, oder liegt im Temp/Home."""
    pfad = programm if os.sep in programm else shutil.which(programm, path=(env or os.environ).get("PATH"))
    if not pfad or not os.path.exists(pfad):
        return True
    echt = os.path.realpath(pfad)
    return echt.startswith(_TMP) or echt.startswith(_HOME + os.sep)


def _git_pushes(argv):
    """(Argumente, Ordner aus `-C`) nach jedem `git … push` (Unterbefehl, nicht `git stash push`)."""
    for i, wort in enumerate(argv):
        if os.path.basename(wort) != "git":
            continue
        j, ordner = i + 1, None
        while j < len(argv) and argv[j].startswith("-"):
            if argv[j] == "-C" and j + 1 < len(argv):
                ordner = argv[j + 1]
            j += 2 if argv[j] in ("-c", "-C", "--git-dir", "--work-tree") else 1
        if j < len(argv) and argv[j] == "push":
            rest = []
            for w in argv[j + 1:]:
                if w in ("&&", "||", ";", "|"):
                    break
                rest.append(w)
            yield rest, ordner


def _push_ziel_lokal(rest, cwd, env):
    ziele = [a for a in rest if not a.startswith("-")]
    ziel = ziele[0] if ziele else "origin"
    if ziel.startswith(("/", ".", "file:")):
        return True
    try:
        url = subprocess.run(["git", "remote", "get-url", "--push", ziel], cwd=cwd, env=env,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return False
    return url.startswith(("/", ".", "file:"))


def _popen_init(self, args, *a, **k):
    if isinstance(args, (str, bytes)):
        argv = os.fsdecode(args).split()
    else:
        argv = [os.fsdecode(x) if isinstance(x, bytes) else str(x) for x in args]
    if argv:
        name = os.path.basename(argv[0])
        if name in _PROGRAMME and not _attrappe(argv[0], k.get("env")):
            _melden("programm", " ".join(argv))
            raise AussenprobeGesperrt("Aussenprobe: Programm gesperrt: " + name)
        for rest, ordner in _git_pushes(argv):
            cwd = os.path.join(k.get("cwd") or os.getcwd(), ordner) if ordner else k.get("cwd")
            if not _push_ziel_lokal(rest, cwd, k.get("env")):
                _melden("git-push-remote", " ".join(argv))
                break
    return _orig_popen_init(self, args, *a, **k)


subprocess.Popen.__init__ = _popen_init
_orig_system = os.system


def _system(befehl):
    _melden("programm", befehl)
    raise AussenprobeGesperrt("Aussenprobe: os.system gesperrt")


os.system = _system

# --- Schreiben ins Home ----------------------------------------------------------------
_orig_open = builtins.open


def _schreibt_ins_home(pfad):
    try:
        p = os.path.realpath(os.fsdecode(pfad))
    except (TypeError, ValueError):
        return
    if p.startswith(_HOME + os.sep) and "__pycache__" not in p:
        _melden("schreibt-ins-home", "~" + p[len(_HOME):])


def _open(datei, mode="r", *a, **k):
    if isinstance(datei, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+"):
        _schreibt_ins_home(datei)
    return _orig_open(datei, mode, *a, **k)


builtins.open = _open
io.open = _open
_orig_osopen = os.open


def _osopen(pfad, flags, *a, **k):
    if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
        _schreibt_ins_home(pfad)
    return _orig_osopen(pfad, flags, *a, **k)


os.open = _osopen
_orig_replace = os.replace


def _replace(quelle, ziel, *a, **k):
    _schreibt_ins_home(ziel)
    return _orig_replace(quelle, ziel, *a, **k)


os.replace = _replace

# --- Zuordnung zum laufenden Test ------------------------------------------------------
import unittest.case as _uc  # noqa: E402

_orig_run = _uc.TestCase.run


def _run(self, result=None):
    _test["id"] = self.id()
    try:
        return _orig_run(self, result)
    finally:
        _test["id"] = "?"


_uc.TestCase.run = _run
