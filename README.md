# MCP Crédit — laboratoire pour comprendre MCP (données factices)

Un serveur MCP d'exemple (simulation et brouillon de demande de crédit) **plus un mini-jeu** qui vérifie
si un modèle utilise les bons tools, resources et prompts. Tout est fictif et en mémoire.

## Contenu

| Fichier | Rôle |
|---|---|
| `server.py` | Serveur MCP : 5 tools, 2 resources, 3 prompts |
| `fake_db.py` | Fausse base (clients, offres, demandes) |
| `calllog.py` | Journalise chaque appel dans `calls.jsonl` : **l'arbitre du jeu** |
| `cases.json` | Les 12 cas de test (prompt, ce qui est attendu, ce qui est interdit) |
| `lab.py` | Le jeu : mode `auto` (modèle vLLM), mode `play` (Claude / ChatGPT), `report` |
| `mock_llm.py` | Faux modèle à base de règles, pour essayer le jeu sans GPU |
| `test_client.py` | Appelle tout le serveur à la main, sans modèle |

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python test_client.py          # vérifie que le serveur répond
```

> SDK Python `mcp` en version 2.x (`MCPServer` remplace `FastMCP` de la 1.x).

## 1. Brancher le serveur à Claude / ChatGPT

### A. Claude Desktop, en local (le plus simple, sans tunnel)

`claude_desktop_config.json` (Settings > Developer > Edit Config), puis redémarrer l'application :

```json
{
  "mcpServers": {
    "credit-demo": {
      "command": "/chemin/absolu/.venv/bin/python",
      "args": ["/chemin/absolu/mcp-credit-demo/server.py"],
      "env": { "CALL_LOG": "/chemin/absolu/mcp-credit-demo/calls.jsonl" }
    }
  }
}
```

La variable `CALL_LOG` garantit que le serveur écrit dans le même fichier que celui lu par `lab.py`.

### B. Claude Code

```bash
claude mcp add credit-demo --env CALL_LOG=/chemin/absolu/mcp-credit-demo/calls.jsonl \
  -- /chemin/absolu/.venv/bin/python /chemin/absolu/mcp-credit-demo/server.py
```

Puis `/mcp` dans une session pour vérifier la connexion.

### C. Claude web ou ChatGPT, via un tunnel HTTPS

Ces clients exigent une URL HTTPS publique (pas de stdio).

```bash
# terminal 1 : tunnel (affiche https://abc-123.trycloudflare.com)
cloudflared tunnel --url http://localhost:8000

# terminal 2 : serveur, en autorisant le domaine du tunnel
MCP_TRANSPORT=streamable-http PORT=8000 ALLOWED_HOSTS=abc-123.trycloudflare.com python server.py
```

- **Claude** : Customize > Connectors > « Add custom connector », URL `https://abc-123.trycloudflare.com/mcp`, authentification « No sign in ».
- **ChatGPT** : plan payant, Developer Mode activé, puis créer une app avec la même URL (menus variables selon les versions).
- Sans `ALLOWED_HOSTS`, le serveur répond **421** (protection anti DNS-rebinding du SDK).

> **Aucune authentification dans cette démo** : n'expose le tunnel que le temps du test, avec ces données factices.

## 2. Le mini-jeu

**Principe** : le serveur écrit dans `calls.jsonl` ce qui a *réellement* été appelé. `lab.py` compare ce journal
à `cases.json`. Pour chaque cas, jusqu'à 4 contrôles :

- ✔ **bons outils / primitives** : tout ce qui est attendu a été appelé (ou rien, pour `sans-tool`)
- ✔ **aucun outil interdit** : par exemple, ne pas créer de brouillon quand on demande juste une simulation
- ✔ **bons arguments** : `type_credit`, `montant`, `duree_mois`...
- ✔ **réponse correcte** : la réponse reprend le chiffre renvoyé par le tool (333 € de mensualité, 17 jours de rétractation)

Les 12 cas couvrent : choix du bon tool, extraction d'arguments, tool inutile, piège « simuler ≠ créer »,
erreur de tool, accès refusé, pression de l'utilisateur, un **prompt** MCP et une **resource** MCP.

```bash
python lab.py list                 # voir les cas
```

### Mode manuel : Claude ou ChatGPT

```bash
python lab.py play --label claude-desktop
```

Le script affiche un prompt à la fois : ouvre une **nouvelle conversation**, envoie le message, attends la
réponse, puis appuie sur Entrée. Il lit `calls.jsonl` et affiche le verdict. Refais l'exercice avec
`--label chatgpt`, `--label claude-code`, etc. (option `--only id1,id2` pour un sous-ensemble).

Pour les cas « prompt » et « resource », observe ce que fait chaque client : c'est précisément ce que
le jeu veut te montrer (voir « Ce qu'on peut / ne peut pas faire » ci-dessous).

### Mode automatique : un modèle open source via vLLM

1. Lancer le modèle (exemples d'après la recette vLLM de Gemma 4 ; adapte selon ta machine) :

```bash
# modèle léger pour commencer
vllm serve google/gemma-4-E4B-it --enable-auto-tool-choice --tool-call-parser gemma4
# ou le 31B (plusieurs GPU, ou version quantifiée)
vllm serve google/gemma-4-31B-it --enable-auto-tool-choice --tool-call-parser gemma4
```

   Pour un autre modèle, choisis le `--tool-call-parser` adapté à sa famille (voir la doc vLLM sur le tool calling).
   Sans parseur, le tool calling ne fonctionne pas.

2. Lancer le jeu :

```bash
python lab.py auto --model google/gemma-4-E4B-it --base-url http://localhost:8000/v1
```

`lab.py` joue le rôle de l'**hôte MCP** : il lance `server.py`, présente les tools au modèle au format OpenAI,
exécute ses appels et lui renvoie les résultats. Variantes utiles :

| Option | Effet |
|---|---|
| `--features tools` | l'hôte ne propose **que** les tools (comme ChatGPT) : les cas prompt/resource sont ignorés |
| `--no-instructions` | n'envoie pas les « instructions » du serveur au modèle : mesure leur utilité |
| `--temperature 0.7` | voir la variabilité du modèle |
| `--only a,b` | sous-ensemble de cas |
| `--label nom` | nom de l'essai dans le rapport |

Ça fonctionne aussi avec tout serveur à API compatible OpenAI (Ollama, LM Studio, llama.cpp).

**Sans GPU**, pour voir le jeu tourner : `python mock_llm.py --port 8100` puis
`python lab.py auto --model faux-modele --base-url http://127.0.0.1:8100/v1`.
Le faux modèle échoue volontairement au cas `pression`, pour que tu voies un ✘.

### Comparer

```bash
python lab.py report
```

```
                     claude-desktop  chatgpt  gemma-4-E4B-it
simuler-auto         ✔               ✔        ✔
...
prompt-dossier       ✔               ✘        ✔
```

Si un cas échoue sur vLLM, regarde d'abord si c'est le modèle, le parseur d'appels d'outils ou le serveur MCP
(les réponses brutes sont dans `results/<label>.json`).

## 3. Ce qu'on peut / ne peut pas faire

| Primitive | Qui décide de l'utiliser ? | Constat |
|---|---|---|
| **Tools** | le **modèle** | Supportés partout. C'est ce qui compte pour une première version. |
| **Resources** | l'**application** (ou l'utilisateur) | Pas un choix du modèle par défaut. ChatGPT ne les consomme pas (d'après les docs consultées). À vérifier par client avec le cas `resource-conditions`. |
| **Prompts** | l'**utilisateur** | Modèles de requêtes à déclencher soi-même. ChatGPT ne les consomme pas (d'après les docs consultées) ; pour les autres clients, vérifie avec le cas `prompt-dossier`. |
| Sampling, elicitation, roots | le serveur / le client | Fonctions avancées, support inégal : ChatGPT ne les consomme pas ; à tester client par client. |

Limites générales d'un serveur MCP :

- Il **répond**, il n'initie rien : pas de message spontané vers l'utilisateur ni vers le modèle.
- Il ne peut **pas forcer** le modèle à appeler un tool, ni à bien relayer le résultat : les descriptions et le schéma sont tes seuls leviers.
- Il ne voit **ni la conversation ni le raisonnement** du modèle, seulement les appels qu'il reçoit.
- Il ne sait **pas qui est l'utilisateur** sans authentification (ici simulé par `DEMO_CLIENT_ID`).
- Tout ce que renvoie un tool est lu par le fournisseur du modèle : à traiter comme une sortie de données.
- Les résultats de tools sont des **données non fiables** pour le modèle (risque d'injection) : à garder en tête quand les données viennent de sources externes.

## 4. Ajouter tes propres cas

Dans `cases.json`, une entrée :

```json
{
  "id": "mon-cas",
  "titre": "Description courte",
  "mode": "chat",
  "prompt": "Le message envoyé au modèle",
  "expect": ["tool:simuler_credit"],
  "forbid": ["tool:preparer_demande_credit"],
  "args": {"tool:simuler_credit": {"type_credit": "auto"}},
  "answer_contains": ["333"],
  "note": "Ce que ce cas apprend"
}
```

Primitives : `tool:<nom>`, `resource:<uri>`, `prompt:<nom>`. `mode` : `chat`, `prompt` (ajouter `mcp_prompt`) ou
`resource` (ajouter `"needs": "resources"`). `"expect_no_calls": true` exige qu'aucun appel n'ait lieu.

## 5. Pour passer en « vrai »

1. Remplacer le contenu de `fake_db.py` par tes appels à ta base / API.
2. Récupérer `CLIENT_ID` depuis un token OAuth validé, pas depuis une variable d'environnement.
3. Mettre OAuth 2.1 devant le transport HTTP.
4. Garder `calls.jsonl` (ou un vrai système de logs) : c'est aussi ton audit des appels.
