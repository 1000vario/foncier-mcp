# foncier-mcp

[![CI](https://github.com/1000vario/foncier-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/1000vario/foncier-mcp/actions/workflows/ci.yml)

English version: [README.en.md](README.en.md).

Vous posez à votre assistant une question sur un terrain ou un logement en France : quelle est la
parcelle, à quel prix se sont vendus les biens voisins, dans quelle zone du plan local d'urbanisme,
avec quel diagnostic de performance énergétique, sous quels risques naturels et industriels.
Ces réponses existent, elles sont publiques et gratuites, mais dispersées entre six services de
l'État, et un modèle de langage livré à lui-même les invente avec aplomb.

foncier-mcp est un serveur [MCP](https://modelcontextprotocol.io) qui donne à l'agent l'accès
direct à ces sources officielles : cadastre, demandes de valeurs foncières (DVF), Géoportail de
l'urbanisme, observatoire DPE de l'ADEME, Géorisques. Chaque valeur rendue porte sa source et la
date à laquelle elle a été lue. Quand une source n'a rien, le champ vaut `null` avec la raison
écrite. Le serveur ne comble jamais un vide par une estimation.

## Pour qui

- **Vous utilisez Claude Desktop, Claude Code ou Codex** et voulez poser vos questions sur une
  adresse ou une parcelle, en français, avec des réponses sourcées : le serveur tourne sur votre
  machine, sans compte ni clé, en dix minutes.
- **Vous construisez un agent** pour l'immobilier, le conseil foncier ou le diagnostic : sept outils
  typés, en lecture seule, un cache local daté, des erreurs typées, un jeu d'évaluation public.
- **Vous exercez un métier du foncier** et un agent vous assiste : il lit les mêmes sources que vous
  et dit quand il ne sait pas.

## Ce que l'agent reçoit, sur un exemple

Question posée à l'assistant : « Que sait-on de la parcelle 34172000HL0365 à Montpellier ? »
L'agent appelle `summarize_parcel` et reçoit en 357 ms, le cache étant en partie chaud, les
faits suivants, rendus le 2026-10-12, chaque champ portant sa propre date de lecture :

```text
Parcelle    Montpellier, section HL, numéro 365, 401 m2                  (IGN API Carto, cadastre)
Ventes DVF  11/01/2023 : 275 000 EUR, appartement 4 pièces de 93 m2 avec dépendance
            16/12/2025 : 116 000 EUR, appartement 2 pièces de 35 m2     (fichiers geo-dvf, data.gouv.fr)
Urbanisme   PLUi Montpellier Méditerranée Métropole, zone UA 1-1-1, « Tissu urbain de centralité
            de ville », hauteur maximale R+4 ; règlement nommé page 63, lien absent : null, raison
            écrite                                                        (Géoportail de l'urbanisme)
DPE         5 diagnostics à l'adresse BAN la plus proche, à 10 m ; le plus récent du 07/01/2026 :
            classe D, GES B, appartement de 108,5 m2 ; avertissement : vérifier que c'est le bon
            bâtiment                                                      (ADEME)
Risques     sismicité 2 faible, radon 1 faible, 36 arrêtés de catastrophe naturelle, territoire à
            risque d'inondation Montpellier-Lunel-Mauguio ; au point : argiles exposition moyenne,
            aucun zonage inondation à 100 m, 10 installations classées à 1 km (Géorisques)
```

Chaque ligne ci-dessus est un champ de la réponse JSON avec sa source, sa date de lecture et, le cas
échéant, sa raison d'absence. L'agent n'a rien à deviner : il rédige à partir de ces faits et peut
citer leur origine.

## Ce que le serveur refuse

- Pas de prix au mètre carré interpolé ni d'estimation : l'outil rend les mutations enregistrées.
- Pas de lien vers un règlement de zone tant que l'API le renvoie vide : le nom du fichier est
  donné, le lien reste `null`.
- Pas de DPE sans numéro de voie : l'outil dit qu'il ne sait pas.
- Pas de valeur sans source, pas de `null` sans raison : l'évaluation compte chaque écart comme
  une faute grave et échoue.

Le service hébergé est gratuit, avec une clé par courriel et 200 appels par jour. Un niveau payant
est conçu mais éteint, voir plus bas.

## Outils

| Outil | Entrée (un localisateur) | Rend |
|---|---|---|
| `geocode_address` | `address`, `limit` facultatif | meilleure correspondance, coordonnées, code INSEE, candidats |
| `find_parcel` | `address`, ou `lon` + `lat`, ou `parcel_id` | identifiant de parcelle, commune, section, numéro, surface, centroïde, géométrie ; un point d'adresse sur la voie prend la parcelle la plus proche à moins de 10 m, avec un avertissement |
| `get_parcel_sales` | localisateur, `radius_m` facultatif (0 à 2000), `years` | mutations DVF sur la parcelle, et alentour quand un rayon est donné, avec prix, date, bâtis, terrains |
| `get_zoning` | localisateur | document d'urbanisme, zone(s) à la parcelle, prescriptions, servitudes d'utilité publique ; les communes au RNU sont signalées comme telles |
| `get_energy_diagnostics` | `address` ou `ban_id` | diagnostics DPE à cette adresse (logements existants et neufs) |
| `get_risks` | localisateur ou `insee` | risques de la commune (inventaire, zone sismique, radon, arrêtés CatNat, territoires à risque d'inondation) et risques au point (retrait-gonflement des argiles, zonage inondation, cavités, mouvements de terrain, installations classées) |
| `summarize_parcel` | localisateur, `radius_m` facultatif | les quatre sections ci-dessus pour une parcelle, chacune indépendante : une source en panne est signalée, les autres répondent |

Un localisateur est exactement un de `parcel_id` (14 caractères, par exemple `34172000DS0638`),
`address`, ou le couple `lon` + `lat` (WGS84). Deux localisateurs à la fois sont refusés.

### Forme des réponses

```json
{
  "ok": true,
  "tool": "find_parcel",
  "input": {"lon": 3.8946, "lat": 43.5986},
  "data": {
    "parcel": {
      "value": {"id": "34172000DS0638", "code_insee": "34172", "commune": "Montpellier", "area_m2": 4553},
      "source": "apicarto_cadastre",
      "retrieved_at": "2026-10-11T17:12:03+00:00",
      "reason": null
    }
  },
  "warnings": [],
  "errors": [],
  "sources": {"apicarto_cadastre": {"name": "IGN API Carto, cadastre module", "url": "...", "licence": "..."}},
  "generated_at": "2026-10-11T17:12:03+00:00",
  "duration_ms": 343
}
```

Un champ sans valeur s'écrit `{"value": null, "source": "...", "retrieved_at": null, "reason": "pourquoi"}`.
Les erreurs typées portent un `code` : `invalid_input`, `not_found`, `not_covered` (par exemple DVF
en Alsace-Moselle), `source_unavailable` (à réessayer), `offline_cache_miss`, `unauthorized`,
`quota_exceeded`, `feature_disabled`.

## Installation en moins de dix minutes

Prérequis : Python 3.11 ou plus récent. Rien d'autre : ni base de données, ni clé pour les sources
publiques.

Option A, sans clone, avec [uv](https://docs.astral.sh/uv/) :

```bash
uvx --from git+https://github.com/1000vario/foncier-mcp foncier-mcp tools
```

Option B, avec pip :

```bash
pip install git+https://github.com/1000vario/foncier-mcp
foncier-mcp tools
```

Option C, depuis un clone, pour développer :

```bash
git clone https://github.com/1000vario/foncier-mcp
cd foncier-mcp
python -m venv .venv && . .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

Un appel sans aucun client MCP :

```bash
foncier-mcp call find_parcel --json '{"lon": 3.8946, "lat": 43.5986}'
```

### Claude Desktop

Dans `claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "foncier": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/1000vario/foncier-mcp", "foncier-mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add foncier -- uvx --from git+https://github.com/1000vario/foncier-mcp foncier-mcp
```

Pour le service HTTP hébergé, une fois la clé reçue :

```bash
claude mcp add --transport http foncier https://HOST/mcp --header "x-api-key: fm_..."
```

### Codex

Dans `~/.codex/config.toml` :

```toml
[mcp_servers.foncier]
command = "uvx"
args = ["--from", "git+https://github.com/1000vario/foncier-mcp", "foncier-mcp"]
```

### Tout client HTTP streamable

Point d'entrée `https://HOST/mcp`, réponses JSON, sans état. La clé passe dans l'en-tête
`x-api-key`. `GET /health` répond sans clé.

L'instance hébergée est `https://foncier.guillaume-couillard.com/mcp` ; son `/health` dit si elle
répond. La clé gratuite est envoyée par courriel, à la main, voir [Clés](#clés).

## Sources de données

Toutes les sources ont été lues le 2026-10-11, pages de documentation comprises. Les dates, limites
et licences ci-dessous sont ce qui a été observé ce jour-là, pas des promesses.

| Source | Sert à | Accès | Licence | Limites observées |
|---|---|---|---|---|
| Géocodeur de la Géoplateforme (IGN), `data.geopf.fr/geocodage` | `geocode_address`, géocodage inverse pour le DPE | HTTP JSON, index adresse et parcelle | Licence Ouverte 2.0 Etalab | `x-ratelimit-limit-second: 1`, `ratelimit-limit: 50`. L'ancien `api-adresse.data.gouv.fr` est déprécié (fin le 2026-01-31) et n'est pas utilisé |
| IGN API Carto, module cadastre | `find_parcel` | HTTP GeoJSON, par point ou par INSEE + section + numéro | Licence Ouverte 2.0 (cadastre Etalab) | jusqu'à 1000 objets par appel |
| IGN API Carto, module GPU (Géoportail de l'urbanisme) | `get_zoning` | `municipality`, `document`, `zone-urba`, `prescription-surf`, `assiette-sup-s`, `secteur-cc` | Licence Ouverte 2.0 | le champ `urlfic` des fichiers de règlement était vide sur chaque zone lue ce jour-là ; les liens de règlement valent `null`, le nom du fichier est gardé dans `regulation_file` |
| DVF, fichiers geo-dvf, `files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dep}/{insee}.csv` | `get_parcel_sales` | un CSV par commune et par année, 2021 à 2025, modifiés le 2026-05-18 | Licence Ouverte 2.0 | non couverts : départements 57, 67, 68 (Livre foncier) et 976. L'API DVF+ du Cerema était injoignable le 2026-10-11 ; les fichiers sont la source DVF unique |
| ADEME, `data.ademe.fr/data-fair`, jeux `dpe03existant` et `dpe02neuf` | `get_energy_diagnostics` | HTTP JSON, filtré sur l'identifiant d'adresse BAN | Licence Ouverte 2.0 | 600 requêtes par 60 s en anonyme ; rafraîchissement hebdomadaire |
| API Géorisques v1, `georisques.gouv.fr/api/v1` | `get_risks` | `gaspar/risques`, `zonage_sismique`, `radon`, `gaspar/catnat`, `gaspar/tri`, `rga`, `tri_zonage`, `cavites`, `mvt`, `installations_classees` | Licence Ouverte 2.0 | la page de documentation était géo-restreinte ce jour-là ; les points d'entrée ont été lus en direct. Le champ `libelle_tri` contient un tiret mal encodé que le serveur remplace |

Le serveur se limite lui-même par hôte et garde un cache local daté (sept jours par défaut, sous
`~/.cache/foncier-mcp`). Chaque réponse dit quand la donnée amont a été lue.

## Évals

```bash
python evals/run.py --live       # chaque cas contre les API publiques, cache vide
python evals/run.py --offline    # rejeu des 22 cas enregistrés, sans réseau, en CI
```

Le jeu de données ([evals/dataset.json](evals/dataset.json)) compte 40 parcelles publiques dans
12 départements (13, 23, 31, 33, 34, 35, 48, 55, 59, 67, 69, 75), urbaines, périurbaines et
rurales : 24 avec au moins une vente DVF, 14 sans aucune vente (bâtiments publics, mairies et
parcelles de village, dont 6 en commune au RNU sans document d'urbanisme numérisé), 2 dans un
département que DVF ne couvre pas. Aucune n'appartient à l'auteur ni à sa famille.

La vérité attendue est enregistrée par [evals/truth/record.py](evals/truth/record.py) depuis les
données ouvertes officielles, par un autre chemin que le serveur : fichiers GeoJSON du cadastre
Etalab avec un point-dans-polygone en Python pur pour la parcelle, fichiers CSV geo-dvf pour les
mutations, WFS de la Géoplateforme (`wfs_du:zone_urba`, `wfs_du:municipality`) pour le zonage. Deux
limites honnêtes : le contrôle DVF lit les mêmes fichiers que le serveur, il mesure donc le parsing
et le regroupement, pas un registre indépendant ; la vérité de zonage est prise au point
représentatif de la parcelle, une parcelle à cheval sur deux zones peut compter comme un échec.

Ce qui est mesuré : géocodage (bonne commune et moins de 150 m de la parcelle), parcelle trouvée
depuis un point et depuis une adresse, rappel et précision DVF par mutation, zéro vente rendue sur
les parcelles sans vente, refus dans les départements non couverts, libellé de zone, RNU annoncé
sans zonage, et l'absence d'invention : une valeur affirmée là où les sources n'ont rien, un `null`
sans raison ou une valeur sans source compte comme une faute grave et fait échouer la passe. La
latence est rapportée par outil.

<!-- EVAL_RESULTS_START -->
Mesuré le 2026-10-11 contre les API publiques en direct (`python evals/run.py --live`, cache vide),
40 cas, vérité enregistrée le 2026-10-11T17:40:21+00:00, durée totale 206 s. Rapport :
[evals/results/2026-10-11-live.md](evals/results/2026-10-11-live.md).

| Mesure | Réussis | Total | Taux |
|---|---:|---:|---:|
| geocode_address : bonne commune et moins de 150 m de la parcelle | 34 | 34 | 100,0 % |
| find_parcel depuis un point : parcelle du cadastre Etalab trouvée | 40 | 40 | 100,0 % |
| find_parcel depuis une adresse : parcelle attendue trouvée | 28 | 34 | 82,3 % |
| get_parcel_sales : mutations enregistrées rendues (par mutation) | 31 | 31 | 100,0 % |
| get_parcel_sales : mutations rendues bien enregistrées (par mutation) | 31 | 31 | 100,0 % |
| get_parcel_sales : parcelles sans vente rendues avec zéro vente | 14 | 14 | 100,0 % |
| get_parcel_sales : département non couvert refusé par not_covered | 2 | 2 | 100,0 % |
| get_zoning : libellé de zone du WFS présent parmi les zones rendues | 33 | 33 | 100,0 % |
| get_zoning : commune au RNU annoncée sans zonage, avec raison | 6 | 6 | 100,0 % |
| summarize_parcel : enveloppe correcte, chaque section répondue ou expliquée | 40 | 40 | 100,0 % |

Valeurs inventées : 0. Plantages hors erreurs typées : 0.
Latence de `summarize_parcel` sur 40 appels en direct : médiane 2206 ms, p95 3129 ms,
maximum 3610 ms.

Échecs de `find_parcel` depuis une adresse (6 sur 34) : `sale-33063-1` (parcelle voisine) ;
`sale-35051-1` (parcelle voisine, même vente enregistrée) ; `sale-23030-1` (parcelle voisine) ;
`sale-55463-1` (parcelle voisine) ; `public-31555` (place ouverte, aucune parcelle à moins de 10 m,
`null` rendu avec une raison) ; `public-35238` (parcelle voisine). Le point BAN tombe dans la
parcelle voisine, ou au plus près d'elle, ou au milieu d'une place. Le serveur rend ce que dit le
point, avec les candidates en avertissement, plutôt que de deviner la parcelle voulue.

Le rejeu hors ligne des 22 cas enregistrés (`python evals/run.py --offline`, lancé en CI à chaque
push) a donné 13/16 sur cette mesure et 100 % sur les autres, avec 0 valeur inventée et 0 défaut de
cache.
<!-- EVAL_RESULTS_END -->

Les rapports complets, cas par cas, sont dans [evals/results](evals/results). Une action GitHub
hebdomadaire rejoue la passe en direct et garde son rapport en artefact.

## Niveaux de service

Le serveur hébergé sert deux niveaux depuis un seul code :

| | Gratuit | Payant (conçu, éteint) |
|---|---|---|
| Accès | clé par courriel, en-tête `x-api-key` | clé par courriel |
| Quota | 200 appels par jour | 5000 appels par jour (`FONCIER_MCP_PAID_DAILY_QUOTA`) |
| Outils | les 7, dont `summarize_parcel` | les 7 plus `export_parcel_pdf` et `watch_parcel` |
| Paiement | aucun | aucun pour l'instant |

Le compteur et le contrôle de niveau sont implémentés et testés. Le niveau payant est derrière
`PAID_TIER_ENABLED=false` : drapeau éteint, aucune clé payante ne peut être émise, les clés
payantes existantes sont servies comme gratuites avec un avertissement, et les deux outils payants
ne sont même pas listés. Il reste éteint tant qu'une question juridique (le cumul de cette activité
avec le contrat de travail de l'auteur) n'est pas réglée, et les deux outils payants n'ont encore
aucune implémentation derrière eux. Allumer le drapeau est une décision de l'auteur, pas un détail
de déploiement.

En stdio (Claude Desktop, Claude Code, Codex lançant le serveur en local), il n'y a ni clé ni
quota : le compteur ne s'applique qu'aux requêtes HTTP.

### Clés

Pour obtenir une clé gratuite du service hébergé, ouvrez une issue « Clé gratuite » sur ce dépôt en
indiquant le courriel qui doit la recevoir. L'auteur l'émet et l'envoie à la main.

```bash
foncier-mcp keys issue --email quelquun@example.org    # affiche la clé une seule fois
foncier-mcp keys list
foncier-mcp keys revoke fm_abcdefgh
foncier-mcp keys export > keys-export.json             # empreintes seules, pour FONCIER_MCP_KEYS_JSON
```

Les clés sont stockées hachées (SHA-256) dans `FONCIER_MCP_KEYS_FILE` ; l'usage du jour dans
`FONCIER_MCP_USAGE_FILE`, remis à zéro à minuit UTC.

Un hôte sans disque persistant reçoit ses clés par `FONCIER_MCP_KEYS_JSON`, la sortie de
`keys export` placée dans une variable d'environnement secrète. Le magasin est alors en lecture
seule : `issue` et `revoke` refusent, et une révocation est un export suivi d'un redéploiement.
L'export contient des empreintes et des courriels, jamais une clé.

## Lancer le serveur HTTP

```bash
foncier-mcp serve --transport http --host 0.0.0.0 --port 8080
```

Environnement (voir [.env.example](.env.example)) :

| Variable | Défaut | Sens |
|---|---|---|
| `FONCIER_MCP_CACHE_DIR` | `~/.cache/foncier-mcp` | cache de réponses daté |
| `FONCIER_MCP_CACHE_TTL_HOURS` | `168` | durée de vie du cache |
| `FONCIER_MCP_OFFLINE` | `0` | `1` répond depuis le cache seulement |
| `FONCIER_MCP_REQUIRE_API_KEY` | `1` | les requêtes HTTP exigent une clé ; stdio jamais |
| `FONCIER_MCP_KEYS_FILE`, `FONCIER_MCP_USAGE_FILE` | `~/.config/foncier-mcp/...` | magasins de clés et d'usage |
| `FONCIER_MCP_KEYS_JSON` | vide | sortie de `keys export` pour un hôte sans disque ; quand elle est posée, le magasin de clés est en lecture seule et `FONCIER_MCP_KEYS_FILE` est ignoré |
| `FONCIER_MCP_FREE_DAILY_QUOTA` | `200` | quota du niveau gratuit |
| `PAID_TIER_ENABLED` | `false` | interrupteur du niveau payant, voir plus haut |
| `FONCIER_MCP_ALLOWED_HOSTS` | vide | hôtes acceptés, séparés par des virgules, contre le DNS rebinding ; vide accepte tout hôte, ce qu'un déploiement derrière un mandataire exige |

### Conteneur

```bash
docker build -t foncier-mcp .
docker run -p 8080:8080 -v foncier-data:/data foncier-mcp
curl http://localhost:8080/health
```

La CI construit l'image et contrôle `/health` à chaque push.

### Hébergement, mesuré le 2026-10-11

Cible : un hébergeur européen, en France pour la localité des données. Prix lus sur scaleway.com ce
jour-là, hors taxes, région Paris :

| Option | Prix mensuel lu | Adéquation |
|---|---|---|
| Scaleway Serverless Containers | 0 EUR dans la franchise (400 000 Go-s et 200 000 vCPU-s par mois), puis 0,000002 EUR par Go-s et 0,00001 EUR par vCPU-s | le niveau gratuit de ce service tient dans la franchise à 200 appels par jour et par clé pour une poignée de clés |
| Scaleway Instance STARDUST1-S | environ 0,43 EUR | plus petite VM, disponibilité limitée |
| Scaleway Instance DEV1-S | environ 6,55 EUR | confortable pour le conteneur et son cache |
| Scaleway Instance PLAY2-PICO | environ 10,42 EUR | alternative sur matériel plus récent |

Choix : Serverless Containers à fr-par, 256 Mo, une instance au plus, zéro au repos, derrière
`foncier.guillaume-couillard.com`. Pire cas si quelqu'un occupe l'instance unique tout le mois
(31 jours, 256 Mo, 140 mvCPU fixés explicitement parce que le défaut de Scaleway quand le client ne
fixe rien est 1000 mvCPU) : environ 2,3 EUR. Le compte, le moyen de paiement et le sous-domaine
appartiennent à l'auteur.

### Déploiement

L'image est construite par [publish-image.yml](.github/workflows/publish-image.yml) après une CI
verte sur `main` et poussée sur `ghcr.io/1000vario/foncier-mcp:latest` (linux/amd64, publique).
Scaleway la tire de là : ni registre Scaleway, ni secret Scaleway dans GitHub.

L'auteur déploie depuis un terminal Windows avec les scripts de [deploy/scaleway](deploy/scaleway) :

```powershell
.\deploy\scaleway\install-credentials.ps1 -Provider scaleway     # demande la clé, la stocke chiffrée (DPAPI)
.\deploy\scaleway\install-credentials.ps1 -Provider cloudflare   # facultatif, laisse le script créer le CNAME
foncier-mcp keys export > keys-export.json
.\deploy\scaleway\deploy.ps1 -KeysJsonPath .\keys-export.json
```

`deploy.ps1` crée ou met à jour l'espace de noms et le conteneur (port 8080, 256 Mo, 140 mvCPU,
min 0, max 1, public, `FONCIER_MCP_REQUIRE_API_KEY=1`, `PAID_TIER_ENABLED=false`,
`FONCIER_MCP_KEYS_JSON` en variable secrète ; `FONCIER_MCP_ALLOWED_HOSTS` reste vide parce que la
protection contre le DNS rebinding vise un serveur local, pas un point d'entrée public derrière le
mandataire de l'hébergeur), attend l'état `ready`, contrôle `/health` sur le point d'entrée
Scaleway, s'assure que le CNAME `foncier.guillaume-couillard.com` pointe vers ce point d'entrée
sans proxy pour que le défi HTTP-01 atteigne Scaleway, enregistre le domaine personnalisé et attend
son certificat. Il affiche des identifiants et des états, jamais une clé. Les mêmes étapes à la
main dans la console Scaleway sont dans [deploy/scaleway/README.md](deploy/scaleway/README.md).

Ce que cet hébergement ne donne pas :

- Pas de disque persistant. Le cache et le compteur d'usage vivent dans l'instance et disparaissent
  quand elle descend à zéro après une quinzaine de minutes sans requête. Le quota journalier vaut
  donc par instance chaude : une borne sur le coût et l'abus, pas un compte exact par clé et par
  jour. Un compteur durable demanderait Object Storage (Standard One Zone 0,00803 EUR par Go et par
  mois, Multi-AZ 0,01606 EUR, lus le 2026-10-11, sans franchise de stockage) et du code qui n'existe
  pas encore.
- Les clés sont en lecture seule dans le conteneur. Émettre ou révoquer, c'est `keys issue` ou
  `keys revoke` sur la machine de l'auteur, puis `keys export` et `deploy.ps1` à nouveau.
- Les requêtes atteignent les API publiques depuis les adresses de Scaleway avec la limitation par
  hôte du serveur ; tous les appelants la partagent.

## Sécurité

- Aucun secret dans le dépôt. Les clés sont hachées au repos. Les fichiers `.env` sont ignorés.
- Les scripts de déploiement gardent les clés de l'hébergeur chiffrées par Windows DPAPI sous le
  profil de l'auteur, appellent les API en TLS et n'affichent que des états et des identifiants.
- Avant chaque push, l'auteur lance un scan de secrets hors CI (`brain scan-secrets --repo`, un
  outil local) ; la CI lance le lint, les tests et les évals hors ligne.
- Le serveur est en lecture seule envers ses sources et ne leur envoie que les paramètres de
  recherche.

## Limites connues

- Paris, Lyon et Marseille : API Carto indexe leurs parcelles par la commune parente (75056, 69123,
  13055) plus un code d'arrondissement, alors que l'identifiant de parcelle garde le code INSEE de
  l'arrondissement (75111000AP0059). Le serveur traduit la recherche par identifiant et rend le code
  d'arrondissement, celui que les fichiers DVF et le GPU utilisent. Géorisques connaît les codes
  d'arrondissement de Paris mais pas ceux de Lyon et Marseille : leurs risques de commune sont lus
  pour la commune parente, et la réponse le dit dans un avertissement. Les deux trouvés par les
  évals en direct du 2026-10-11.
- Les points d'adresse de la BAN sont souvent sur l'axe de la voie, hors de toute parcelle.
  `find_parcel` prend alors la parcelle la plus proche à moins de 10 m et le dit dans un
  avertissement qui liste les candidates. Quand le point tombe dans une parcelle voisine, c'est la
  voisine qui est rendue : le tableau des évals compte ces cas comme des échecs.
- Le géocodeur inverse de la Géoplateforme rend l'adresse la plus proche, pas l'adresse dans la
  parcelle ; `get_energy_diagnostics` via `summarize_parcel` ne garde qu'une correspondance au
  numéro à moins de 30 m et le dit sinon.
- Les PDF de règlement des zones sont nommés mais pas liés, parce que l'API GPU a rendu des champs
  `urlfic` vides le jour de la lecture.
- DVF ne publie rien pour l'Alsace-Moselle (Livre foncier) ni Mayotte ; l'outil répond
  `not_covered`.
- Les libellés Géorisques arrivent tels que publiés, avec des champs parfois vides.
- Les taux du tableau d'évals valent pour le jour de la mesure ; les API publiques changent.

## Licence

Code : MIT, voir [LICENSE](LICENSE). Les données rendues par le serveur restent sous leur propre
licence, Licence Ouverte 2.0 Etalab pour chaque source listée ci-dessus, qui impose de nommer le
producteur : IGN, DGFiP (DVF), ADEME, BRGM et les ministères derrière Géorisques, et les
contributeurs du Géoportail de l'urbanisme.
