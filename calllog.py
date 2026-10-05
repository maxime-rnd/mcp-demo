"""Journal des appels côté serveur : l'arbitre du mini-jeu.

Chaque utilisation d'un tool, d'une resource ou d'un prompt est ajoutée à `calls.jsonl`,
quel que soit le client (Claude, ChatGPT, modèle open source via vLLM...).
Le fichier est placé à côté de server.py par défaut (même chemin quel que soit le dossier
de lancement), ou à l'emplacement donné par la variable d'environnement CALL_LOG.
"""

import functools
import json
import os
import time
from pathlib import Path

LOG_PATH = Path(os.getenv("CALL_LOG") or Path(__file__).with_name("calls.jsonl"))


def log_call(kind: str, name: str, args: dict | None = None, ok: bool = True, error: str | None = None) -> None:
    entry = {"ts": time.time(), "kind": kind, "name": name, "args": args or {}, "ok": ok}
    if error:
        entry["error"] = error
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def logged(kind: str, name: str | None = None):
    """Décorateur à placer SOUS @mcp.tool / @mcp.resource / @mcp.prompt."""

    def decorator(fn):
        label = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # on journalise puis on laisse l'erreur remonter
                log_call(kind, label, kwargs, ok=False, error=str(exc))
                raise
            log_call(kind, label, kwargs, ok=True)
            return result

        return wrapper

    return decorator


def read_calls(since: float = 0.0) -> list[dict]:
    """Lit les appels journalisés après l'instant `since` (timestamp Unix)."""
    if not LOG_PATH.exists():
        return []
    calls = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry["ts"] > since:
            calls.append(entry)
    return calls
