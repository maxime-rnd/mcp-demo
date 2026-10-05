"""Parcourt tous les tools, resources et prompts du serveur, sans Claude ni ChatGPT.

Usage : python test_client.py
"""

import asyncio
import json

from mcp.client import Client

from server import mcp


def titre(texte: str) -> None:
    print(f"\n{'=' * 8} {texte} {'=' * 8}")


def afficher(resultat) -> None:
    if resultat.is_error:
        print("ERREUR TOOL ->", resultat.content[0].text)
    elif resultat.structured_content is not None:
        print(json.dumps(resultat.structured_content, indent=2, ensure_ascii=False))
    else:
        print(resultat.content[0].text)


async def main() -> None:
    async with Client(mcp) as client:
        titre("TOOLS DISPONIBLES")
        for tool in (await client.list_tools()).tools:
            print(f"- {tool.name}: {(tool.description or '').splitlines()[0]}")

        titre("simuler_credit (auto, 18 000 EUR, 60 mois)")
        afficher(await client.call_tool("simuler_credit", {"montant": 18000, "duree_mois": 60, "type_credit": "auto"}))

        titre("consulter_capacite_emprunt (conso, 48 mois)")
        afficher(await client.call_tool("consulter_capacite_emprunt", {"type_credit": "conso", "duree_mois": 48}))

        titre("preparer_demande_credit (travaux, 12 000 EUR, 48 mois)")
        afficher(
            await client.call_tool(
                "preparer_demande_credit",
                {"montant": 12000, "duree_mois": 48, "type_credit": "travaux", "objet": "Rénovation salle de bain"},
            )
        )

        titre("lister_demandes")
        afficher(await client.call_tool("lister_demandes", {}))

        titre("statut_demande DEM-1003 (appartient à un autre client -> refus)")
        afficher(await client.call_tool("statut_demande", {"demande_id": "DEM-1003"}))

        titre("simuler_credit hors limites (erreur propre pour le LLM)")
        afficher(await client.call_tool("simuler_credit", {"montant": 100, "duree_mois": 60, "type_credit": "conso"}))

        titre("RESOURCES")
        for res in (await client.list_resources()).resources:
            print(f"- {res.uri}")
        contenu = await client.read_resource("docs://credit/conditions")
        print(contenu.contents[0].text)

        titre("PROMPTS")
        for prompt in (await client.list_prompts()).prompts:
            print(f"- {prompt.name}")
        prompt = await client.get_prompt("preparer_dossier_credit", {"projet": "achat d'une voiture", "montant": "15000"})
        print(prompt.messages[0].content.text)


if __name__ == "__main__":
    asyncio.run(main())
