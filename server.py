"""Serveur MCP de démo : assistant de demande de crédit (données factices).

Lancer en local (stdio)      : python server.py
Lancer en HTTP (distant)     : MCP_TRANSPORT=streamable-http python server.py
Debug avec l'Inspector       : mcp dev server.py
"""

import os
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

import fake_db as db
from calllog import logged

TypeCredit = Literal["conso", "auto", "travaux", "immo"]

# En production, cet identifiant vient du token OAuth de l'utilisateur connecté.
# Ici on le fixe via une variable d'environnement pour la démo.
CLIENT_ID = os.getenv("DEMO_CLIENT_ID", "CLI-001")

TAUX_ENDETTEMENT_MAX = 0.35

mcp = MCPServer(
    "credit-demo",
    log_level=os.getenv("MCP_LOG_LEVEL", "WARNING"),
    instructions=(
        "Tu aides un client à explorer et préparer une demande de crédit. "
        "Utilise toujours les outils pour obtenir chiffres et statuts : n'invente jamais "
        "un taux, une mensualité ou une décision. Tu peux simuler et préparer un "
        "BROUILLON de demande, mais tu ne peux ni accorder ni soumettre un crédit : "
        "la validation finale se fait par le client dans l'espace sécurisé de la banque. "
        "Précise que les résultats sont indicatifs et non contractuels."
    ),
)


# --------------------------------------------------------------------------
# Calculs (logique métier déterministe : le LLM ne calcule rien lui-même)
# --------------------------------------------------------------------------
def _mensualite(montant: float, taux_annuel_pct: float, duree_mois: int) -> float:
    r = taux_annuel_pct / 100 / 12
    if r == 0:
        return montant / duree_mois
    return montant * r / (1 - (1 + r) ** -duree_mois)


def _taeg_pct(montant: float, mensualite: float, duree_mois: int, frais: float) -> float:
    """TAEG indicatif : taux annuel qui égalise (montant - frais) et les mensualités."""
    net = montant - frais
    lo, hi = 0.0, 1.0  # taux mensuel entre 0 % et 100 %
    for _ in range(80):
        mid = (lo + hi) / 2
        pv = mensualite * (1 - (1 + mid) ** -duree_mois) / mid if mid > 0 else mensualite * duree_mois
        lo, hi = (mid, hi) if pv > net else (lo, mid)
    return round(((1 + lo) ** 12 - 1) * 100, 2)


def _verifier_bornes(type_credit: str, montant: float, duree_mois: int) -> dict:
    offre = db.get_offre(type_credit)
    if not offre["montant_min"] <= montant <= offre["montant_max"]:
        raise ToolError(
            f"Montant hors limites pour '{type_credit}' : "
            f"{offre['montant_min']} à {offre['montant_max']} €."
        )
    if not offre["duree_min"] <= duree_mois <= offre["duree_max"]:
        raise ToolError(
            f"Durée hors limites pour '{type_credit}' : "
            f"{offre['duree_min']} à {offre['duree_max']} mois."
        )
    return offre


def _simulation(type_credit: str, montant: float, duree_mois: int) -> dict:
    offre = _verifier_bornes(type_credit, montant, duree_mois)
    mensualite = _mensualite(montant, offre["taux_nominal"], duree_mois)
    frais = round(montant * offre["frais_dossier_pct"] / 100, 2)
    return {
        "type_credit": offre["libelle"],
        "montant": montant,
        "duree_mois": duree_mois,
        "taux_nominal_pct": offre["taux_nominal"],
        "mensualite": round(mensualite, 2),
        "frais_dossier": frais,
        "cout_total_credit": round(mensualite * duree_mois - montant + frais, 2),
        "taeg_indicatif_pct": _taeg_pct(montant, mensualite, duree_mois, frais),
        "avertissement": "Simulation indicative, non contractuelle.",
    }


# --------------------------------------------------------------------------
# TOOLS : actions que le LLM peut appeler
# --------------------------------------------------------------------------
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
@logged("tool")
def simuler_credit(montant: float, duree_mois: int, type_credit: TypeCredit) -> dict:
    """Simule un crédit : mensualité, taux, frais, coût total et TAEG indicatif.
    Ne crée rien et n'engage pas le client. type_credit : conso, auto, travaux ou immo."""
    return _simulation(type_credit, montant, duree_mois)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
@logged("tool")
def consulter_capacite_emprunt(type_credit: TypeCredit, duree_mois: int) -> dict:
    """Estime la mensualité maximale supportable (35 % d'endettement) et le montant
    empruntable correspondant pour le client connecté."""
    client = db.get_client(CLIENT_ID)
    montant_min = db.get_offre(type_credit)["montant_min"]
    offre = _verifier_bornes(type_credit, montant_min, duree_mois)  # valide la durée
    revenus, charges = client["revenus_mensuels"], client["charges_mensuelles"]
    mensualite_max = max(0.0, revenus * TAUX_ENDETTEMENT_MAX - charges)
    r = offre["taux_nominal"] / 100 / 12
    montant_max = mensualite_max * (1 - (1 + r) ** -duree_mois) / r if mensualite_max else 0.0
    return {
        "revenus_mensuels": revenus,
        "charges_mensuelles": charges,
        "taux_endettement_actuel_pct": round(charges / revenus * 100, 1),
        "mensualite_max_recommandee": round(mensualite_max, 2),
        "montant_max_indicatif": round(min(montant_max, offre["montant_max"]), -2),
        "avertissement": "Estimation indicative, hors étude complète du dossier.",
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
@logged("tool")
def preparer_demande_credit(
    montant: float, duree_mois: int, type_credit: TypeCredit, objet: str
) -> dict:
    """Crée un BROUILLON de demande de crédit pour le client connecté. La demande n'est
    pas soumise : le client doit la relire et la valider via le lien retourné."""
    simulation = _simulation(type_credit, montant, duree_mois)  # valide les bornes
    demande = db.create_demande(CLIENT_ID, type_credit, montant, duree_mois, objet)
    return {
        "demande_id": demande["id"],
        "statut": demande["statut"],
        "simulation": simulation,
        "lien_finalisation": f"https://banque-demo.example/demandes/{demande['id']}/finaliser",
        "prochaine_etape": "Le client doit ouvrir le lien, compléter ses justificatifs et signer.",
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
@logged("tool")
def statut_demande(demande_id: str) -> dict:
    """Retourne le statut d'une demande de crédit du client connecté (ex. DEM-1001)."""
    demande = db.get_demande(demande_id, CLIENT_ID)
    if demande is None:
        raise ToolError(f"Aucune demande '{demande_id}' pour ce client.")
    return demande


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
@logged("tool")
def lister_demandes() -> list[dict]:
    """Liste toutes les demandes de crédit du client connecté."""
    return db.list_demandes(CLIENT_ID)


# --------------------------------------------------------------------------
# RESOURCES : données en lecture (utilisées surtout par Claude ; ChatGPT
# ne les consomme pas à ce jour)
# --------------------------------------------------------------------------
@mcp.resource("credit://offres", mime_type="application/json")
@logged("resource", "credit://offres")
def grille_offres() -> dict:
    """Grille des offres de crédit (taux, montants et durées autorisés)."""
    return db.OFFRES


@mcp.resource("docs://credit/conditions", mime_type="text/plain")
@logged("resource", "docs://credit/conditions")
def conditions_generales() -> str:
    """Conditions générales fictives du crédit."""
    return (
        "CONDITIONS (FICTIVES)\n"
        "- Taux d'endettement maximum recommandé : 35 %.\n"
        "- Délai de rétractation : 17 jours après acceptation de l'offre (valeur fictive de démo).\n"
        "- Toute demande est soumise à l'étude du dossier et à l'accord de la banque.\n"
    )


# --------------------------------------------------------------------------
# PROMPTS : modèles de requêtes déclenchables par l'utilisateur
# (dans Claude, ils apparaissent via le menu "+" / "Add from…")
# --------------------------------------------------------------------------
@mcp.prompt(title="Préparer un dossier de crédit")
@logged("prompt")
def preparer_dossier_credit(projet: str, montant: float) -> str:
    return (
        f"Je souhaite financer le projet suivant : {projet}, pour environ {montant:.0f} €.\n"
        "1. Choisis le type de crédit le plus adapté.\n"
        "2. Vérifie ma capacité d'emprunt.\n"
        "3. Simule 2 ou 3 durées et présente-les dans un tableau.\n"
        "4. Recommande une option en une phrase, puis demande-moi confirmation avant "
        "de préparer le brouillon de demande."
    )


@mcp.prompt(title="Comparer plusieurs durées")
@logged("prompt")
def comparer_durees(type_credit: str, montant: float) -> str:
    return (
        f"Compare un crédit {type_credit} de {montant:.0f} € sur 3 durées pertinentes "
        "(mensualité, coût total, TAEG) et explique le compromis mensualité / coût."
    )


@mcp.prompt(title="Suivi de mes demandes")
@logged("prompt")
def suivi_demandes() -> str:
    return (
        "Liste mes demandes de crédit, résume l'état de chacune en une ligne et dis-moi "
        "quelle est la prochaine action que je dois faire, le cas échéant."
    )


if __name__ == "__main__":
    if os.getenv("MCP_TRANSPORT") == "streamable-http":
        extra_hosts = [
            h.strip()
            for h in os.getenv("ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]

        mcp.run(
            "streamable-http",
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "7860")),
            transport_security=TransportSecuritySettings(
                allowed_hosts=[
                    "127.0.0.1:*",
                    "localhost:*",
                    "0.0.0.0:*",
                    *extra_hosts,
                ],
            ),
        )
    else:
        mcp.run()
