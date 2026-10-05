"""Fausse base de données en mémoire pour la démo.

Tout est fictif. Pour passer en "vrai", il suffit de remplacer le contenu de
ces fonctions par des appels à ton API / ta base (le reste du serveur ne change pas).
Les données sont perdues à chaque redémarrage du serveur.
"""

from datetime import date
from itertools import count

# --- Clients fictifs -------------------------------------------------------
CLIENTS: dict[str, dict] = {
    "CLI-001": {
        "prenom": "Camille",
        "nom": "Dupont",
        "revenus_mensuels": 3200.0,
        "charges_mensuelles": 650.0,  # loyer + crédits en cours
        "anciennete_ans": 6,
    },
    "CLI-002": {
        "prenom": "Karim",
        "nom": "Benali",
        "revenus_mensuels": 4800.0,
        "charges_mensuelles": 1900.0,
        "anciennete_ans": 2,
    },
}

# --- Grille d'offres fictive ----------------------------------------------
OFFRES: dict[str, dict] = {
    "conso": {
        "libelle": "Crédit consommation",
        "taux_nominal": 5.90,
        "montant_min": 500,
        "montant_max": 75_000,
        "duree_min": 6,
        "duree_max": 84,
        "frais_dossier_pct": 1.0,
    },
    "auto": {
        "libelle": "Crédit auto",
        "taux_nominal": 4.20,
        "montant_min": 3_000,
        "montant_max": 60_000,
        "duree_min": 12,
        "duree_max": 84,
        "frais_dossier_pct": 1.0,
    },
    "travaux": {
        "libelle": "Crédit travaux",
        "taux_nominal": 4.90,
        "montant_min": 1_500,
        "montant_max": 75_000,
        "duree_min": 12,
        "duree_max": 120,
        "frais_dossier_pct": 1.0,
    },
    "immo": {
        "libelle": "Crédit immobilier",
        "taux_nominal": 3.45,
        "montant_min": 50_000,
        "montant_max": 800_000,
        "duree_min": 120,
        "duree_max": 300,
        "frais_dossier_pct": 0.8,
    },
}

# --- Demandes de crédit (pré-remplies pour pouvoir tester le suivi) --------
DEMANDES: dict[str, dict] = {
    "DEM-1001": {
        "id": "DEM-1001",
        "client_id": "CLI-001",
        "type_credit": "auto",
        "montant": 18_000.0,
        "duree_mois": 60,
        "objet": "Achat véhicule d'occasion",
        "statut": "en_analyse",
        "cree_le": "2026-09-20",
    },
    "DEM-1002": {
        "id": "DEM-1002",
        "client_id": "CLI-001",
        "type_credit": "conso",
        "montant": 3_000.0,
        "duree_mois": 24,
        "objet": "Ordinateur portable",
        "statut": "accepte",
        "cree_le": "2026-06-11",
    },
    "DEM-1003": {
        "id": "DEM-1003",
        "client_id": "CLI-002",
        "type_credit": "immo",
        "montant": 250_000.0,
        "duree_mois": 240,
        "objet": "Résidence principale",
        "statut": "brouillon",
        "cree_le": "2026-09-30",
    },
}

_next_id = count(1004)


# --- Accès "base de données" ----------------------------------------------
def get_client(client_id: str) -> dict:
    if client_id not in CLIENTS:
        raise KeyError(f"Client inconnu : {client_id}")
    return CLIENTS[client_id]


def get_offre(type_credit: str) -> dict:
    if type_credit not in OFFRES:
        raise KeyError(f"Type de crédit inconnu : {type_credit}")
    return OFFRES[type_credit]


def list_demandes(client_id: str) -> list[dict]:
    return [d for d in DEMANDES.values() if d["client_id"] == client_id]


def get_demande(demande_id: str, client_id: str) -> dict | None:
    """Ne renvoie la demande que si elle appartient bien au client."""
    d = DEMANDES.get(demande_id)
    if d is None or d["client_id"] != client_id:
        return None
    return d


def create_demande(
    client_id: str, type_credit: str, montant: float, duree_mois: int, objet: str
) -> dict:
    demande_id = f"DEM-{next(_next_id)}"
    demande = {
        "id": demande_id,
        "client_id": client_id,
        "type_credit": type_credit,
        "montant": montant,
        "duree_mois": duree_mois,
        "objet": objet,
        "statut": "brouillon",
        "cree_le": date.today().isoformat(),
    }
    DEMANDES[demande_id] = demande
    return demande
