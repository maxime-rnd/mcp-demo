"""Faux "modèle" compatible API OpenAI, à base de règles, pour essayer le jeu sans GPU.

    python mock_llm.py --port 8000
    python lab.py auto --model faux-modele

Ce n'est PAS un LLM : il repère des mots-clés et appelle les outils. Il a un défaut volontaire
(il crée un brouillon dès qu'on lui dit « accorde-moi le crédit »), pour que tu voies un échec
apparaître dans le jeu.
"""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONDITIONS_URI = "docs://credit/conditions"


def parse_amount(text: str) -> float | None:
    m = re.search(r"(\d[\d\s\u202f\u00a0]*)\s*(?:€|euros?)", text)
    return float(re.sub(r"\D", "", m.group(1))) if m else None


def parse_duration(text: str) -> int | None:
    if m := re.search(r"(\d+)\s*mois", text):
        return int(m.group(1))
    if m := re.search(r"(\d+)\s*ans", text):
        return int(m.group(1)) * 12
    return None


def parse_type(text: str) -> str:
    if re.search(r"voiture|auto", text):
        return "auto"
    if re.search(r"travaux|rénov", text):
        return "travaux"
    if re.search(r"immo|maison|appartement", text):
        return "immo"
    return "conso"


def plan(text: str, tool_names: set[str]) -> list[tuple[str, dict]]:
    t = text.lower()
    amount, months, kind = parse_amount(t), parse_duration(t), parse_type(t)
    if "rétractation" in t and "lire_ressource" in tool_names:
        return [("lire_ressource", {"uri": CONDITIONS_URI})]
    if m := re.search(r"dem-\d+", t):
        return [("statut_demande", {"demande_id": m.group(0).upper()})]
    if re.search(r"mes demandes|demandes de crédit en cours|où en sont", t):
        return [("lister_demandes", {})]
    if re.search(r"\bprépare\b|accorde", t):  # défaut volontaire : « accorde » déclenche aussi une création
        return [("preparer_demande_credit", {"montant": amount or 10000, "duree_mois": months or 48,
                                             "type_credit": kind, "objet": "demande via assistant"})]
    steps: list[tuple[str, dict]] = []
    if re.search(r"capacité|combien puis-je emprunter", t):
        steps.append(("consulter_capacite_emprunt", {"type_credit": kind, "duree_mois": months or 48}))
    if re.search(r"\bsimul", t):
        steps.append(("simuler_credit", {"montant": amount or 10000, "duree_mois": months or 48, "type_credit": kind}))
    if steps:
        return steps
    return []


def respond(body: dict) -> dict:
    messages = body["messages"]
    tool_names = {t["function"]["name"] for t in body.get("tools", [])}
    user_text = next(m["content"] for m in messages if m["role"] == "user")
    done = {tc["function"]["name"] for m in messages if m.get("tool_calls") for tc in m["tool_calls"]}
    todo = [(n, a) for n, a in plan(user_text, tool_names) if n not in done and n in tool_names]

    if todo:
        name, args = todo[0]
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{len(done)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        finish = "tool_calls"
    else:
        results = [m["content"] for m in messages if m["role"] == "tool"]
        content = ("D'après les outils : " + " | ".join(r[:300] for r in results)) if results else \
            "Le TAEG inclut les frais en plus du taux nominal (réponse de culture générale, sans outil)."
        message, finish = {"role": "assistant", "content": content}, "stop"
    return {"id": "mock", "object": "chat.completion", "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send({"data": [{"id": "faux-modele", "object": "model"}]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._send(respond(body))

    def log_message(self, *_) -> None:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    port = parser.parse_args().port
    print(f"Faux modèle sur http://127.0.0.1:{port}/v1  (Ctrl+C pour arrêter)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
