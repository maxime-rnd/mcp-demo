"""Mini-jeu : le modèle utilise-t-il les bons tools / resources / prompts ?

L'arbitre est le serveur MCP : il journalise chaque appel dans calls.jsonl (voir calllog.py).
Le jeu compare ce journal à ce qui est attendu dans cases.json.

  python lab.py list                          # voir les cas de test
  python lab.py auto --model <nom> ...        # modèle exposé via vLLM (ou tout endpoint OpenAI-compatible)
  python lab.py play --label claude-desktop   # mode manuel : tu colles les prompts dans Claude / ChatGPT
  python lab.py report                        # tableau comparatif de tous les essais
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from calllog import LOG_PATH, read_calls

HERE = Path(__file__).parent
RESULTS_DIR = HERE / "results"

CHECK_LABELS = {
    "bons_outils": "bons outils / bonnes primitives",
    "pas_d_interdit": "aucun outil interdit",
    "bons_arguments": "bons arguments",
    "reponse": "réponse correcte",
}


# --------------------------------------------------------------------------
# Cas de test et notation
# --------------------------------------------------------------------------
def load_cases(only: str | None = None) -> list[dict]:
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    if only:
        wanted = [x.strip() for x in only.split(",")]
        unknown = [w for w in wanted if w not in {c["id"] for c in cases}]
        if unknown:
            sys.exit(f"Cas inconnu(s) : {', '.join(unknown)}  (voir : python lab.py list)")
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def _match(expected, actual) -> bool:
    try:
        return abs(float(expected) - float(actual)) < 1e-6
    except (TypeError, ValueError):
        return str(expected).strip().casefold() == str(actual).strip().casefold()


def evaluate(case: dict, calls: list[dict], answer: str | None = None) -> dict:
    """Retourne {nom_du_contrôle: True/False/None}. None = non évaluable automatiquement."""
    names = [f"{c['kind']}:{c['name']}" for c in calls]
    checks: dict = {}

    if case.get("expect_no_calls"):
        checks["bons_outils"] = not names
    elif case.get("expect"):
        checks["bons_outils"] = all(e in names for e in case["expect"])

    if case.get("forbid"):
        checks["pas_d_interdit"] = not any(f in names for f in case["forbid"])

    if case.get("args"):
        ok = True
        for key, wanted in case["args"].items():
            kind, name = key.split(":", 1)
            candidates = [c for c in calls if c["kind"] == kind and c["name"] == name]
            ok = ok and any(all(_match(v, c["args"].get(k)) for k, v in wanted.items()) for c in candidates)
        checks["bons_arguments"] = ok

    if case.get("answer_contains"):
        checks["reponse"] = None if answer is None else all(s.lower() in answer.lower() for s in case["answer_contains"])

    return checks


def status_of(checks: dict) -> str:
    values = [v for v in checks.values() if v is not None]
    return "pass" if values and all(values) else "fail"


def describe_calls(calls: list[dict]) -> str:
    if not calls:
        return "(aucun appel)"
    parts = []
    for c in calls:
        args = ", ".join(f"{k}={v}" for k, v in c["args"].items())
        flag = "" if c.get("ok", True) else "  [erreur]"
        parts.append(f"{c['kind']}:{c['name']}({args}){flag}")
    return "\n    ".join(parts)


def print_verdict(case: dict, calls: list[dict], checks: dict) -> None:
    print(f"  Appels observés :\n    {describe_calls(calls)}")
    for key, value in checks.items():
        mark = "✔" if value else ("?" if value is None else "✘")
        print(f"  {mark} {CHECK_LABELS[key]}")
    if case.get("note"):
        print(f"  ℹ {case['note']}")


def save_results(label: str, meta: dict, rows: list[dict]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{label}.json"
    path.write_text(
        json.dumps({"label": label, "date": datetime.now().isoformat(timespec="seconds"), **meta, "cases": rows},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def print_summary(rows: list[dict]) -> None:
    played = [r for r in rows if r["status"] != "skip"]
    passed = [r for r in played if r["status"] == "pass"]
    print(f"\nScore : {len(passed)}/{len(played)} cas réussis", end="")
    skipped = len(rows) - len(played)
    print(f"  ({skipped} ignoré(s))" if skipped else "")
    for r in played:
        if r["status"] == "fail":
            bad = [CHECK_LABELS[k] for k, v in r["checks"].items() if v is False]
            print(f"  ✘ {r['id']} : {', '.join(bad) or 'échec'}")


# --------------------------------------------------------------------------
# Mode automatique : modèle exposé en API OpenAI-compatible (vLLM, Ollama, LM Studio...)
# --------------------------------------------------------------------------
class EndpointError(Exception):
    """Le serveur d'inférence (vLLM...) est injoignable ou a refusé la requête."""


def result_text(result) -> str:
    if getattr(result, "structured_content", None) is not None:
        return json.dumps(result.structured_content, ensure_ascii=False)
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def run_case_auto(case: dict, args: argparse.Namespace, features: set[str]) -> dict:
    start = time.time()
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "server.py")],
        env={**os.environ, "CALL_LOG": str(LOG_PATH)},
        cwd=HERE,
    )
    answer = ""
    endpoint_error = None
    async with Client(params) as client:  # un serveur neuf par cas : état isolé
        tools = []
        for t in (await client.list_tools()).tools:
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
            tools.append({"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": schema}})

        system = "" if args.no_instructions else (client.instructions or "")
        if "resources" in features:
            resources = (await client.list_resources()).resources
            listing = "\n".join(f"- {r.uri} : {r.description or r.name}" for r in resources)
            system += f"\n\nRessources disponibles (à lire avec l'outil lire_ressource) :\n{listing}"
            tools.append({
                "type": "function",
                "function": {
                    "name": "lire_ressource",
                    "description": "Lit le contenu d'une ressource du serveur à partir de son URI.",
                    "parameters": {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
                },
            })

        if case["mode"] == "prompt":  # l'hôte (ici le script) joue le rôle de l'utilisateur qui choisit le prompt
            spec = case["mcp_prompt"]
            rendered = await client.get_prompt(spec["name"], spec["arguments"])
            user_text = rendered.messages[0].content.text
        else:
            user_text = case["prompt"]

        messages = ([{"role": "system", "content": system.strip()}] if system.strip() else []) + [
            {"role": "user", "content": user_text}
        ]

        async with httpx.AsyncClient(timeout=args.timeout) as http:
            for _ in range(args.max_steps):
                payload = {"model": args.model, "messages": messages, "temperature": args.temperature, "max_tokens": 1024}
                if tools:
                    payload.update(tools=tools, tool_choice="auto")
                try:
                    resp = await http.post(
                        f"{args.base_url.rstrip('/')}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {args.api_key}"},
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    endpoint_error = f"HTTP {exc.response.status_code} : {exc.response.text[:300]}"
                    break
                except httpx.HTTPError as exc:
                    endpoint_error = f"{type(exc).__name__} : {exc}"
                    break
                msg = resp.json()["choices"][0]["message"]
                answer = msg.get("content") or answer
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    break
                messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    raw = tc["function"].get("arguments") or "{}"
                    try:
                        call_args = json.loads(raw) if isinstance(raw, str) else raw
                        if name == "lire_ressource":
                            res = await client.read_resource(call_args["uri"])
                            output = res.contents[0].text
                        else:
                            res = await client.call_tool(name, call_args)
                            output = result_text(res)
                    except Exception as exc:  # appel mal formé, outil inconnu... : le modèle le voit
                        output = f"Erreur : {exc}"
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", name), "content": output})

    if endpoint_error:
        raise EndpointError(endpoint_error)
    calls = read_calls(start)
    checks = evaluate(case, calls, answer)
    return {"id": case["id"], "status": status_of(checks), "checks": checks, "answer": answer, "calls": calls}


async def run_auto(args: argparse.Namespace) -> None:
    features = {f.strip() for f in args.features.split(",")}
    rows = []
    print(f"Modèle : {args.model}  |  endpoint : {args.base_url}  |  fonctions de l'hôte : {', '.join(sorted(features))}\n")
    for case in load_cases(args.only):
        needs = case.get("needs", "tools")
        print(f"▶ {case['id']} — {case['titre']}")
        if needs not in features:
            print(f"  – ignoré : l'hôte n'expose pas « {needs} » (c'est justement une limite à observer)\n")
            rows.append({"id": case["id"], "status": "skip", "checks": {}, "calls": []})
            continue
        try:
            row = await run_case_auto(case, args, features)
        except EndpointError as exc:
            sys.exit(f"\nImpossible d'interroger {args.base_url}\n  {exc}\n(le serveur d'inférence est-il lancé ? "
                     "vLLM a-t-il --enable-auto-tool-choice et --tool-call-parser ?)")
        print_verdict(case, row["calls"], row["checks"])
        print()
        rows.append(row)
    print_summary(rows)
    label = args.label or args.model.split("/")[-1]
    path = save_results(label, {"model": args.model, "mode": "auto", "features": sorted(features)}, rows)
    print(f"\nRésultats enregistrés : {path.relative_to(HERE)}")


# --------------------------------------------------------------------------
# Mode manuel : Claude Desktop / Claude Code / Claude web / ChatGPT
# --------------------------------------------------------------------------
def render_prompt_text(spec: dict) -> str:
    from server import mcp  # import tardif : seulement utile en mode manuel

    async def go() -> str:
        async with Client(mcp) as c:
            rendered = await c.get_prompt(spec["name"], spec["arguments"])
            return rendered.messages[0].content.text

    return asyncio.run(go())


def ask_yes_no(question: str) -> bool:
    while True:
        reply = input(f"  {question} (o/n) ").strip().lower()
        if reply in ("o", "n"):
            return reply == "o"


def run_play(args: argparse.Namespace) -> None:
    cases = load_cases(args.only)
    rows = []
    print(f"Mode manuel — journal des appels : {LOG_PATH}")
    print("Le serveur MCP doit être connecté au client que tu testes (Claude Desktop, ChatGPT...).")
    print("Ouvre une NOUVELLE conversation pour chaque cas, puis suis les instructions.\n")
    for i, case in enumerate(cases, 1):
        print(f"━━ Cas {i}/{len(cases)} · {case['id']} — {case['titre']}")
        if case["mode"] == "prompt":
            spec = case["mcp_prompt"]
            text = render_prompt_text(spec)
            print(f"  Si ton client gère les prompts MCP : sélectionne « {spec['name']} » avec {spec['arguments']}.")
            print("  Sinon (ex. ChatGPT) : colle ce texte dans le chat :\n")
            print("    " + text.replace("\n", "\n    ") + "\n")
        else:
            print("  Envoie ce message :\n")
            print(f"    « {case['prompt']} »\n")
            if case["mode"] == "resource":
                print("  (Si ton client ne propose pas la resource au modèle, observe ce qu'il fait : c'est le but.)")
        start = time.time()
        reply = input("  [Entrée] quand la réponse est terminée, [s] pour passer : ").strip().lower()
        if reply == "s":
            rows.append({"id": case["id"], "status": "skip", "checks": {}, "calls": []})
            print()
            continue
        calls = read_calls(start)
        checks = evaluate(case, calls)
        if checks.get("reponse", False) is None:
            wanted = " / ".join(case["answer_contains"])
            checks["reponse"] = ask_yes_no(f"La réponse du modèle mentionne-t-elle bien « {wanted} » ?")
        print_verdict(case, calls, checks)
        print()
        rows.append({"id": case["id"], "status": status_of(checks), "checks": checks, "calls": calls})
    print_summary(rows)
    path = save_results(args.label, {"model": args.label, "mode": "manuel"}, rows)
    print(f"\nRésultats enregistrés : {path.relative_to(HERE)}")


# --------------------------------------------------------------------------
# Rapport comparatif
# --------------------------------------------------------------------------
def run_report(_: argparse.Namespace) -> None:
    files = sorted(RESULTS_DIR.glob("*.json")) if RESULTS_DIR.exists() else []
    if not files:
        sys.exit("Aucun résultat : lance d'abord « auto » ou « play ».")
    runs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    labels = [r["label"] for r in runs]
    by_label = {r["label"]: {c["id"]: c["status"] for c in r["cases"]} for r in runs}
    cases = load_cases()
    width = max(len(c["id"]) for c in cases) + 2
    col = max(len(label) for label in labels) + 2
    print(" " * width + "".join(label.ljust(col) for label in labels))
    marks = {"pass": "✔", "fail": "✘", "skip": "–"}
    for case in cases:
        print(case["id"].ljust(width) + "".join(marks.get(by_label[l].get(case["id"], "skip"), "–").ljust(col) for l in labels))
    print("-" * (width + col * len(labels)))
    totals = []
    for label in labels:
        played = [s for s in by_label[label].values() if s != "skip"]
        totals.append(f"{played.count('pass')}/{len(played)}".ljust(col))
    print("score".ljust(width) + "".join(totals))


def run_list(_: argparse.Namespace) -> None:
    for c in load_cases():
        print(f"{c['id']:<22} [{c['mode']}]  {c['titre']}")


# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="affiche les cas de test").set_defaults(fn=run_list)
    sub.add_parser("report", help="tableau comparatif des essais").set_defaults(fn=run_report)

    auto = sub.add_parser("auto", help="teste un modèle exposé en API OpenAI-compatible (vLLM...)")
    auto.add_argument("--model", required=True, help="nom du modèle tel que servi (ex. google/gemma-4-E4B-it)")
    auto.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1"))
    auto.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", "EMPTY"))
    auto.add_argument("--features", default="tools,resources,prompts",
                      help="fonctions MCP que l'hôte (ce script) propose au modèle. Ex. « tools » pour imiter ChatGPT")
    auto.add_argument("--no-instructions", action="store_true", help="n'envoie pas les « instructions » du serveur au modèle")
    auto.add_argument("--label", help="nom de l'essai dans le rapport (défaut : nom du modèle)")
    auto.add_argument("--only", help="ids de cas séparés par des virgules")
    auto.add_argument("--temperature", type=float, default=0.0)
    auto.add_argument("--max-steps", type=int, default=6)
    auto.add_argument("--timeout", type=float, default=120.0)
    auto.set_defaults(fn=lambda a: asyncio.run(run_auto(a)))

    play = sub.add_parser("play", help="mode manuel pour Claude / ChatGPT")
    play.add_argument("--label", required=True, help="nom de l'essai (ex. claude-desktop, chatgpt)")
    play.add_argument("--only", help="ids de cas séparés par des virgules")
    play.set_defaults(fn=run_play)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
