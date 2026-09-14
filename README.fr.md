[English](README.md) | [Español](README.es.md) | **Français** | [Deutsch](README.de.md)

# Trusteed Agentic Commerce pour Odoo

Permettez aux nouveaux acheteurs en ligne, les agents IA, d'effectuer des achats dans votre boutique de manière sûre et fiable grâce à Trusteed : le réseau qui instaure la confiance entre entreprises et agents.

- **Définissez vos règles commerciales** : qui vous autorisez à acheter, jusqu'à quel montant, quelles catégories vous ne souhaitez pas proposer aux agents, limites de prix, maintien des niveaux de stock pour vous protéger contre d'éventuels agents frauduleux, et plus encore.
- **Reçus inviolables** : nous générons des reçus signés cryptographiquement (JWS Ed25519) sur lesquels toute altération se voit, et qui servent de preuve de la transaction réelle en cas de litige. Ils sont conçus autour des concepts probatoires d'eIDAS (UE, Royaume-Uni) et d'eSIGN (États-Unis) — une signature de type avancé, **non** qualifiée : à ce jour, il n'y a en production ni horodatage qualifié ni cachet de QTSP.
- **Analyses des agents** : consultez les statistiques d'achats des agents — combien ils dépensent, quels produits ils achètent, et à quelle fréquence.
- **Blocage des agents** : bloquez les agents potentiellement dangereux ou problématiques.
- **Monnaies numériques** : permet les achats en monnaies numériques grâce au protocole X402.
- **Transactions pair à pair** : permet le commerce direct entre agents et commerçants.
- **Tableau de bord Agent Readiness** : vérifie en direct si les agents IA peuvent réellement acheter dès aujourd'hui dans votre boutique — trois vues indépendantes (ce que disent les autres, ce que vous promettez vs. ce que vous faites, ce que nous avons observé), sans les fusionner en un score inventé.

## Captures d'écran

| Trust Center | My Sales — Mes commandes | My Sales — AI Sales |
|---------------|-----------------------------|--------------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![Mes commandes](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| My Sales — Clés | My Sales — Audit | Paramètres |
|---------------------|----------------------|------------|
| ![Clés](screenshots/04-my-sales-keys.png) | ![Audit](screenshots/05-my-sales-audit.png) | ![Paramètres](screenshots/06-settings.png) |

| Assistant de configuration rapide | Agent Readiness |
|-----------------------------------------|------------------|
| ![Assistant](screenshots/07-quick-setup-wizard.png) | ![Agent Readiness](screenshots/08-agent-readiness.png) |

Chaque transaction d'un agent génère un **reçu de confiance** signé cryptographiquement — un enregistrement sur lequel toute altération se voit (JWS Ed25519, aligné sur les concepts probatoires d'eIDAS / eSIGN, sans horodatage qualifié) répertorié sous **My Sales → AI Sales**. Les clés de signature et un journal d'audit complet sont disponibles dans le même menu **My Sales**, et l'écran **Trust Center** affiche le score de confiance global de votre boutique.

## Fonctionnalités

Trusteed regroupe un Trust Center, un registre de reçus signés et 5 outils agentiques natifs au sein d'un seul module Odoo.

- **Trust Center** — score de confiance de la boutique, reçus de confiance signés, clés de signature, journal d'audit
- **My Sales** — commandes, ventes aux agents IA, clés de signature, journal d'audit, tout dans un seul menu
- **5 outils IA natifs** exposés via `ir.actions.server` (`usage='ai_tool'`) : `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2` — **les cinq ne sont pas tous opérationnels aujourd'hui**, voir [État des outils IA natifs](#état-des-outils-ia-natifs)
- **Badge de confiance sur la commande** — un badge de score de confiance calculé et injecté dans la vue kanban native `sale.order`
- **Pièce jointe automatique du reçu JWS** — les reçus de confiance signés sont automatiquement joints aux enregistrements `account.move` lors de leur validation
- **Support multi-société** — reconnexion silencieuse avec notification toast lors du changement de société active
- **Assistant de configuration rapide** — un parcours d'intégration en 4 étapes qui connecte votre instance Odoo à Trusteed
- **Comportement fail-closed par défaut** — appels sortants protégés contre le SSRF, HTTPS uniquement, l'application des règles n'autorise jamais silencieusement en cas de mauvaise configuration

### État des outils IA natifs

Les cinq outils sont enregistrés à l'installation, mais un seul est à la fois activé par défaut et adossé à un point d'accès déployé. Les bascules se trouvent sous **Paramètres → Trusteed**.

| Outil | Par défaut | État aujourd'hui |
|-------|------------|------------------|
| `verify-agent-signature` | activé | **Opérationnel** — vérification de signature RFC 9421 |
| `dispatch-payment-acp` | **désactivé** | Fonctionne, mais sur opt-in : paiement initié par l'agent, c'est vous qui l'activez |
| `dispatch-payment-x402` | **désactivé** | Fonctionne, mais sur opt-in : paiement initié par l'agent, c'est vous qui l'activez |
| `sign-trust-receipt` | activé | **Aucun backend déployé à ce jour** — se déclare toujours indisponible, quoi que dise la bascule. Les reçus continuent d'être émis par le parcours de paiement et se consultent sous **My Sales → AI Sales** |
| `dispatch-payment-ap2` | **désactivé** | **Aucun backend déployé à ce jour** — se déclare toujours indisponible, quoi que dise la bascule |

Activer une bascule ne rend jamais appelable un outil sans backend déployé ; la bascule ne fait que conserver votre préférence pour le jour où ce backend existera.

**Découverte des outils.** `usage='ai_tool'` est pleinement pris en charge par l'AI App d'Odoo sur **Odoo 19.0**. Sur la série Odoo 18.x visée par ce module, le champ existe mais l'interface de découverte de l'AI App peut ne pas faire apparaître ces actions — elles restent appelables par le code (`env.ref(...).run()`).

## Compatibilité

| Composant | Compatible |
|-----------|------------|
| Odoo | 18.0 (Community ou Enterprise) |
| Python | 3.10+ |
| Déploiement | Odoo.sh ou installation on-premise — **non** disponible sur Odoo Online (SaaS), qui bloque les modules tiers personnalisés |

## Prérequis

- Odoo 18.0, sur Odoo.sh ou une installation on-premise
- Python 3.10+
- Un compte Trusteed — [inscrivez-vous gratuitement sur trusteed.xyz](https://trusteed.xyz)
- **Applications Odoo** (installées automatiquement comme dépendances) : `base`, `web`, `mail`, `sale`, `account`, `stock`, `sale_stock`
- **Paquet Python `cryptography`** — déclaré dans les `external_dependencies` du manifeste ; sans lui, Odoo refuse d'installer le module. Il sert à vérifier la signature Ed25519 de l'instantané de règles, l'identité de l'agent (RFC 9421) et la canonicalisation JCS (RFC 8785). Installez-le avec `pip install cryptography` sur le serveur Odoo (sur Odoo.sh : ajoutez-le à votre `requirements.txt`).

## Installation

### Installation manuelle

1. **Téléchargez le `.zip` installable** depuis la dernière Release GitHub :
   [**⬇ Télécharger la dernière version**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest)
   — le `.zip` est joint à cette release. Les versions antérieures sont sur la
   [page des Releases](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
2. Extrayez-le dans votre `addons_path` Odoo — le dossier extrait doit s'appeler `trusteed` (c'est le nom technique du module).
3. Redémarrez Odoo : `systemctl restart odoo` (ou l'équivalent pour votre déploiement).
4. Dans le Back Office Odoo : **Paramètres → Activer le mode développeur**.
5. **Paramètres → Applications → Mettre à jour la liste des applications**, puis recherchez "Trusteed" et cliquez sur **Installer**.
6. Ouvrez le nouveau menu **Trusteed** — l'assistant de configuration rapide s'ouvre automatiquement.

### Depuis les sources (compiler le zip vous-même)

```bash
git clone https://github.com/Trusteedxyz/agentic-commerce-odoo.git
cd agentic-commerce-odoo
bash bin/build-zip.sh   # génère dist/trusteed-agentic-commerce-odoo-<version>.zip
```

### Docker / développement local

Le fichier compose utilisé par l'équipe pour la préproduction (`e2e/docker/odoo-staging.yml`) réside dans le monorepo de développement privé de Trusteed et ne fait **pas** partie de ce dépôt. Pour essayer le module dans Docker, lancez n'importe quel conteneur Odoo 18 standard — voir l'[image Odoo officielle](https://hub.docker.com/_/odoo) — et montez le dossier `trusteed` extrait dans un chemin figurant dans l'`addons_path` de ce conteneur.

Puis dans Odoo : **Paramètres → Applications → Trusteed → Installer**.

## Configuration

L'assistant de configuration rapide (**Trusteed → Quick Setup**) guide tout le parcours :

1. **Bienvenue** — confirme les prérequis (un compte Trusteed + sortie HTTPS depuis le serveur Odoo).
2. **Connexion** — ouvre le Portail Trusteed sur [trusteed.xyz/dashboard](https://trusteed.xyz/dashboard), où vous naviguez vers **Connecter une boutique → Odoo**.
3. **Identifiants** — collez votre **Merchant ID** et votre **Bootstrap Secret** (64 caractères hexadécimaux).
4. **Test** — vérifie la connectivité avant de terminer.

Les identifiants peuvent également être saisis directement dans **Paramètres → Trusteed**, sans passer par l'assistant.

### Paramètres système

| Clé `ir.config_parameter` | Valeur par défaut | Objectif |
|-------------------------------|---------------------|----------|
| `trusteed.merchant_id` | _(vide)_ | Merchant ID émis par Trusteed |
| `trusteed.bootstrap_secret` | _(vide)_ | Secret de 64 caractères hexadécimaux, `password=True`, réservé à `group_admin` |
| `trusteed.api_base` | `https://api.trusteed.xyz` | Point de terminaison du backend Trusteed |

## Menus d'administration

Après l'installation, un menu de premier niveau **Trusteed** apparaît dans le Back Office Odoo :

| Menu | Description |
|------|--------------|
| Quick Setup | Assistant d'intégration en 4 étapes |
| Trust Center | Aperçu du score de confiance de la boutique |
| My Sales | Mes commandes, AI Sales, Clés, Audit — tout au même endroit |
| Paramètres | Merchant ID, Bootstrap Secret, URL de base de l'API et bascules par outil |

Le panneau d'administration est un bundle partagé avec les connecteurs WooCommerce et PrestaShop : il contient donc aussi des sections que l'hôte Odoo n'expose pas (`inicio`, `mis-reglas`, `seguridad`, `agentes`, `payment-methods` et `merchant-center`). Les actions client Odoo ne montent que `trust-center` et `mis-ventas`, et rien à l'intérieur de ces deux pages ne renvoie vers les autres — sous Odoo, ces sections sont donc inaccessibles, et le tableau ci-dessus constitue la liste complète de ce que vous pouvez ouvrir depuis le Back Office.

## Application des règles au paiement

Au-delà du Trust Center, le module installe une couche d'application des règles qui agit sur les commandes de vente de la boutique elle-même (`models/sale_order_enforcement.py` et les fichiers qui l'entourent). Dans les grandes lignes :

- Elle intercepte la **confirmation de la commande de vente** par les trois voies d'entrée — l'`action_confirm` de l'interface et du RPC, la création d'une commande directement avec `state='sale'`, et l'écriture de `state='sale'` sur une commande existante — afin qu'un client XML-RPC ou JSON-RPC sans interface ne puisse pas contourner le contrôle.
- À chaque confirmation, elle télécharge votre **instantané de règles** depuis Trusteed, vérifie sa signature JWS Ed25519 et le met en cache dans le processus pendant 5 minutes.
- Si un jeton d'agent est présent, il est vérifié hors ligne ; une décision **BLOCK** refuse la confirmation avec une erreur `trusteed:R0xx` nommant la règle déclenchée.
- Si l'instantané est indisponible, si le coupe-circuit est actif ou si quelque chose d'inattendu survient, c'est le **mode de repli** configuré qui décide : `strict` bloque, `balanced` et `permissive` laissent passer la commande. La confirmation ne plante jamais.
- Une action planifiée, **« Trusteed CEL: Refresh Rule Snapshot »** (`data/cron.xml`), préchauffe ce cache **toutes les 5 minutes** pour que la première commande de chaque intervalle ne paie pas la latence du téléchargement à froid. Vous la trouverez, et pourrez la désactiver, sous **Paramètres → Technique → Automatisation → Actions planifiées**.

## FAQ

**Quelles données sont envoyées ?** Uniquement ce que nécessitent les règles d'application et les reçus de confiance (montants de commande, pays, identité de l'agent). Aucune donnée de carte de paiement ne transite jamais par Trusteed. Toutes les communications utilisent HTTPS.

**Quels agents sont pris en charge ?** Tout agent connecté via un client compatible MCP qui appelle les outils IA natifs exposés par ce module, y compris Claude Desktop. Deux réserves à connaître avant de bâtir dessus : aujourd'hui, seul `verify-agent-signature` est activé et opérationnel (voir [État des outils IA natifs](#état-des-outils-ia-natifs)), et l'AI App d'Odoo ne fait apparaître ces outils de façon fiable que sur Odoo 19.0 — sur 18.x, ils peuvent ne pas figurer dans son interface de découverte.

**Cela ralentit-il ma boutique ?** Non. L'application des règles s'exécute de manière synchrone uniquement à l'étape de transaction concernée, avec un comportement fail-closed par défaut plutôt qu'une autorisation globale par défaut.

**Puis-je l'installer sur Odoo Online (SaaS) ?** Non — Odoo Online n'autorise pas les modules tiers personnalisés. Utilisez Odoo.sh ou une installation on-premise.

## Le tableau de bord de préparation agentique

**Les agents me trouvent-ils ?** est une page de votre panneau d'administration
qui répond à une seule question : lorsqu'un agent d'achat IA visite votre
boutique, obtient-il ce que vous croyez qu'il obtient ?

Aucune note unique n'est affichée. Trois colonnes, jamais moyennées, car elles
répondent à des questions différentes et peuvent légitimement se contredire :

| Colonne | Ce que c'est |
| --- | --- |
| **Ce que dit un tiers** | Le verdict d'un scanner externe, cité tel quel. Jamais réinterprété dans une échelle qui serait la nôtre : dès que l'on convertit la note d'un autre, on corrige sa propre copie |
| **Ce que vous dites correspond-il à ce que vous faites ?** | 16 vérifications qui confrontent ce que votre boutique **annonce** à ce qu'elle **répond réellement**. C'est la partie qu'aucun scanner externe ne peut faire : elle exige vos identifiants |
| **Ce que nous avons vu passer** | Le trafic agentique réel sur la période choisie : quels agents sont venus, quels outils ils ont utilisés, jusqu'où ils sont allés et où ils ont échoué |

Une vérification qui n'a pas pu être faite est signalée comme **non vérifiée**,
avec son motif. Elle n'est jamais écartée en silence ni comptée comme réussie.
« Nous n'avons pas pu regarder » et « nous avons regardé et tout allait bien »
sont deux réponses distinctes, et la page indique laquelle s'applique.

### Ce que vérifie chaque contrôle

| Contrôle | Ce qu'il détecte |
| --- | --- |
| C1 | Vous annoncez des outils que votre boutique ne sert pas |
| C2 | Vous annoncez un protocole de paiement dont le point de terminaison ne répond pas |
| C3 | Le prix du catalogue n'est pas le prix facturé |
| C4 | Annoncé disponible alors que ce n'est pas le cas |
| C5 | Votre politique de retour dit des choses différentes selon la source |
| C6 | Vous annoncez comme disponible quelque chose qui est désactivé |
| C7 | Des règles activées qui ne peuvent pas agir faute de données |
| C8 | Vos règles observent mais ne bloquent pas |
| C9 | La méthode d'identification que vous annoncez ne fonctionne pas |
| C10 | Un agent peut acheter n'importe quel montant sans votre confirmation |
| C11 | Le point de vente utilise des règles expirées |
| C12 | Des opérations sans reçu signé |
| C13 | Des adresses annoncées qui ne fonctionnent pas |
| C14 | Les agents voient des données périmées de votre boutique |
| C15 | Des justificatifs d'identité sur le point d'expirer |
| C16 | Le délai de livraison promis n'est pas celui que vous tenez |

Certains contrôles ont besoin de plus que vos réglages, et la page le dit au lieu
de laisser un vide :

- **Nécessite une boutique connectée** (C3, C4, C5, C14) : ils comparent avec
  votre catalogue réel, et sans identifiants il n'y a rien à comparer.
- **Nécessite des commandes livrées** (C16) : il compare ce que vous promettez à
  ce que vous avez réellement tenu, ce qui est impossible sans historique.
- **Rien à comparer cette fois** : C12, par exemple, n'a rien à vérifier tant
  qu'un agent n'a pas réellement finalisé un achat. Ce n'est pas un échec.

Les contrôles s'exécutent une fois par jour et la page affiche le résultat **avec
sa date**, pour qu'un verdict d'hier ressemble à un verdict d'hier. Un « tout va
bien » mis en cache et présenté comme actuel serait exactement l'auto-illusion
que cette page existe pour débusquer.

## Journal des modifications

### 18.0.1.2.4 — Renforcement de sécurité

- Renforcé : la vérification du jeton d'agent rejette désormais proprement toute entrée à la forme inattendue, au lieu de risquer une erreur interne. Ce n'est pas une faille exploitable ici — contrairement à d'autres plateformes de cette famille, aucun chemin ne laisse passer une commande à cause de cette erreur — renforcé par cohérence après un signalement de sécurité concernant le module PrestaShop. Voir [GHSA-2j2x-5q52-g48m](https://github.com/Trusteedxyz/agentic-commerce-prestashop/security/advisories/GHSA-2j2x-5q52-g48m).

### 18.0.1.2.3

- Nouveau : lorsqu'une vérification n'a pas pu s'exécuter, le panneau explique désormais ce qui la débloquerait — rien à faire, configuration nécessaire, en attente de données, ou l'une de nos propres vérifications a échoué — au lieu d'une liste plate de gris inexpliqués.
- Nouveau : le panneau indique désormais quel serveur a répondu à votre requête, une étiquette courte et opaque. Utile pour comparer ce que vous voyez ici avec ce que voit le support ; elle ne révèle jamais un nom d'hôte ou de service.

### 18.0.1.2.2

- Nouveau : les Réglages vous permettent désormais de choisir les outils que votre boutique propose aux agents. Si vous n'avez jamais enregistré de liste, le panneau vous indique que ce qui est proposé est l'ensemble de base fourni par la plateforme, et non votre choix.
- Nouveau : un bouton pour relancer la vérification sans attendre le balayage quotidien, et le panneau retient ce qui a changé depuis la vérification précédente.
- Modifié : nos propres pannes ne comptent plus comme des incohérences de votre boutique. Le panneau les sépare, car vous n'y pouvez rien.

### 18.0.1.2.1

- Corrigé : la page de disponibilité pour les agents était publiée sans sa feuille de style, le panneau s'affichait donc sans mise en forme.
- Corrigé : le panneau pouvait afficher son interface dans une langue et le diagnostic dans une autre. La langue résolue accompagne désormais les textes au lieu d'être détectée deux fois.
- Corrigé : le panneau ne transmettait pas la langue de l'utilisateur Odoo, le diagnostic revenait donc dans la langue du navigateur et non la sienne.
- Nouveau : chaque constat renvoie vers l'endroit où le corriger, et les affirmations du marchand — le délai de livraison et les autres — apparaissent avec les éléments qui les étayent.
- Modifié : une boutique sans aucune vérification affiche « vérification en cours » au lieu de « vérifié une fois par jour » : ouvrir le panneau déclenche déjà la première vérification en arrière-plan.

### 18.0.1.2.0

- **Nouveau — tableau de bord de préparation agentique.** *Les agents me trouvent-ils ?* arrive dans le panneau d'administration. Il confronte ce que votre boutique annonce à ce qu'elle répond réellement, en **16 vérifications**, et les affiche toutes les seize, pas seulement celles qui échouent. Une vérification impossible indique **pourquoi** (boutique non connectée, aucune commande livrée pour l'instant, rien à comparer cette fois) au lieu de laisser un vide qui ressemble à une panne. Voir « Le tableau de bord de préparation agentique » ci-dessus.
- **Corrigé** — le diagnostic était rédigé en espagnol dans l'API et affiché tel quel : un marchand utilisant le panneau en anglais lisait des titres anglais au-dessus de constats espagnols. Les vérifications émettent désormais des codes neutres et le texte est composé au moment de servir, dans votre langue.
- **Corrigé** — la vérification C1 (« vous annoncez des outils que votre boutique ne sert pas ») considérait tout le catalogue public comme servi en l'absence de liste configurée : elle annonçait 46 sur 48 alors que le serveur en sert 12. L'erreur allait dans le sens flatteur, précisément celui que ce tableau de bord doit débusquer.
- **Corrigé** — la vérification C6 (« vous annoncez comme disponible quelque chose qui est désactivé ») signalait une capacité comme désactivée dès que son indicateur n'était pas défini, y compris pour ceux activés par défaut. C'était une fausse alerte sur toutes les boutiques.

### 18.0.1.1.2

- **Corrigé** — le bundle du panneau d'administration (`static/src/js/admin-spa.js`) était distribué non minifié : 869 Ko / 25 064 lignes au lieu des 490 Ko / 41 lignes que produit réellement la commande de build documentée (`pnpm run build:odoo`). Il fonctionnait quand même, mais sa provenance ne pouvait pas être vérifiée. Reconstruit depuis la source ; le bundle compilé correspond désormais caractère pour caractère à ce que produit la commande.
- **Corrigé** — `R047.customer-confirmation` (la règle qui demande à l'acheteur de confirmer une commande d'agent par un canal distinct, courriel ou SMS) n'avait pas de champ de formulaire dans le panneau d'administration : son seuil de montant existait dans le schéma mais ne pouvait être défini que via l'API. Également corrigé : l'affichage d'une catégorie marchande imprimait les délimiteurs anti-injection (`<<<MERCHANT_CONTENT_START>>> … <<<MERCHANT_CONTENT_END>>>`) autour, au lieu de les retirer pour l'affichage.

### 18.0.1.1.1

- **Corrigé** — `_DOCS_URL` envoyait le marchand vers `https://docs.trusteed.xyz/embed/odoo-onprem`, un hôte qui renvoie NXDOMAIN. Tout marchand suivant le lien de documentation intégré recevait une erreur de navigateur au lieu du guide d'intégration. Pointe désormais vers `https://trusteed.xyz/en/integrations/odoo`.

### 18.0.1.1.0

- **Correctif de sécurité** — la détection de rejeu du vérificateur de jetons d'agent reposait sur un `if nonce:`, si bien qu'un jeton omettant simplement le claim `nonce` échappait entièrement à la détection hors ligne. Le claim est désormais obligatoire (16 à 64 caractères, comme l'exige le schéma canonique du jeton) et un jeton qui en est dépourvu est rejeté — fail-closed, comme dans les connecteurs WooCommerce, PrestaShop et Magento.
- **Nouveauté** — l'addon déclare désormais quels signaux de panier cette installation sait projeter (`POST /api/v1/enforcement/capabilities`, signé en HMAC, envoyé depuis le hook post-init, c'est-à-dire précisément au moment où la version de l'addon change). Sans cela, une règle dont le signal n'arrive jamais renvoie `NO_SIGNAL` à chaque paiement : elle passe en silence, et le marchand voit une règle en ENFORCE qui ne bloque rien. La déclaration ne propage jamais d'erreur : un incident réseau ne peut pas faire échouer une installation ou une mise à jour.

### 18.0.1.0.0

- Première version publique : Trust Center, My Sales (commandes, ventes aux agents IA, clés, audit), assistant de configuration rapide, intégration dans les Paramètres, 5 outils IA natifs, badge de confiance sur la commande, pièce jointe automatique du reçu JWS, support multi-société.

## Support

- E-mail support : support@trusteed.xyz
- Issues GitHub : [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## Licence

LGPL-3.0. Voir [LICENSE](LICENSE) pour le texte complet — correspond à la licence déclarée dans `__manifest__.py`.
