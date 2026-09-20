[English](README.md) | [Español](README.es.md) | **Français** | [Deutsch](README.de.md)

# Trusteed Agentic Commerce pour Odoo

Les agents IA sont un nouveau type d'acheteur en ligne. Avec Trusteed, le réseau qui met en relation les entreprises et les agents, ils peuvent acheter dans votre boutique selon vos conditions.

- Définissez vos règles métier : qui peut acheter, jusqu'à quel montant, quelles catégories vous ne proposez pas aux agents, des limites de prix, des niveaux de stock qui vous protègent des agents frauduleux, et plus encore.
- Recevez des reçus signés. Chaque transaction produit un reçu signé cryptographiquement (JWS Ed25519), dont toute altération est détectable, et que vous pouvez utiliser comme preuve de l'achat en cas de litige. Il est aligné sur eIDAS (UE) et sur eSIGN (États-Unis). Le reçu est un candidat à un cachet électronique avancé, une posture technique que Trusteed déclare lui-même et non une certification par un tiers. Ce n'est **pas** un cachet qualifié : il n'y a aujourd'hui en production ni horodatage qualifié ni cachet de QTSP.
- Voyez ce que font les agents : combien ils dépensent, ce qu'ils achètent et à quelle fréquence.
- Bloquez les agents qui semblent dangereux ou qui posent problème.
- Acceptez des achats en monnaies numériques grâce au protocole X402.
- Laissez agents et marchands échanger directement, de pair à pair.
- Vérifiez votre Agent Readiness : une vue en direct qui indique si les agents IA peuvent acheter dans votre boutique dès aujourd'hui. Elle comporte trois vues indépendantes (ce que disent les autres, ce que vous promettez par rapport à ce que vous faites, ce que nous avons observé), et elle ne les fusionne jamais en une seule note.

## Captures d'écran

| Trust Center | My Sales → Mes commandes | My Sales → AI Sales |
|---------------|-----------------------------|--------------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![Mes commandes](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| My Sales → Clés | My Sales → Audit | Paramètres |
|---------------------|----------------------|------------|
| ![Clés](screenshots/04-my-sales-keys.png) | ![Audit](screenshots/05-my-sales-audit.png) | ![Paramètres](screenshots/06-settings.png) |

| Assistant de configuration rapide | Agent Readiness |
|-----------------------------------------|------------------|
| ![Assistant](screenshots/07-quick-setup-wizard.png) | ![Agent Readiness](screenshots/08-agent-readiness.png) |

Chaque transaction d'un agent génère un reçu de confiance signé, un enregistrement dont toute altération est détectable (JWS Ed25519, aligné sur eIDAS et sur eSIGN, sans horodatage qualifié) répertorié sous **My Sales → AI Sales**. Les clés de signature et un journal d'audit complet se trouvent dans le même menu **My Sales**, et l'écran **Trust Center** affiche le score de confiance global de votre boutique.

## Fonctionnalités

Trusteed réunit un Trust Center, un registre de reçus signés et 5 outils agentiques natifs dans un seul module Odoo.

- Trust Center : score de confiance de la boutique, reçus de confiance signés, clés de signature, journal d'audit.
- My Sales : commandes, ventes aux agents IA, clés de signature et journal d'audit, tout dans un seul menu.
- 5 outils IA natifs exposés via `ir.actions.server` (`usage='ai_tool'`) : `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2`. Les cinq ne sont pas tous opérationnels aujourd'hui, voir [État des outils IA natifs](#état-des-outils-ia-natifs).
- Badge de confiance sur la commande : un badge de score de confiance calculé, injecté dans la vue kanban native de `sale.order`.
- Pièce jointe automatique du reçu JWS : les reçus de confiance signés sont joints automatiquement aux enregistrements `account.move` lors de leur validation.
- Prise en charge multi-société : le module se reconfigure en silence et affiche une notification toast lorsque la société active change.
- Assistant de configuration rapide : un parcours d'intégration en 4 étapes qui connecte votre instance Odoo à Trusteed.
- Comportements par défaut fail-closed : appels sortants protégés contre le SSRF, HTTPS uniquement, et une application des règles qui n'autorise jamais en silence en cas de mauvaise configuration.

### État des outils IA natifs

Les cinq outils sont enregistrés à l'installation, mais un seul est à la fois activé par défaut et adossé à un point d'accès déployé. Les bascules se trouvent sous **Paramètres → Trusteed**.

| Outil | Par défaut | État aujourd'hui |
|-------|------------|------------------|
| `verify-agent-signature` | activé | **Opérationnel**: vérification de signature RFC 9421 |
| `dispatch-payment-acp` | **désactivé** | Fonctionne, mais en opt-in : paiement initié par l'agent, c'est vous qui l'activez |
| `dispatch-payment-x402` | **désactivé** | Fonctionne, mais en opt-in : paiement initié par l'agent, c'est vous qui l'activez |
| `sign-trust-receipt` | activé | **Aucun backend déployé pour l'instant**: se déclare toujours indisponible, quoi que dise la bascule. Les reçus sont toujours émis par le parcours de paiement et se lisent sous **My Sales → AI Sales** |
| `dispatch-payment-ap2` | **désactivé** | **Aucun backend déployé pour l'instant**: se déclare toujours indisponible, quoi que dise la bascule |

Activer une bascule ne rend jamais appelable un outil dépourvu de backend déployé. La bascule ne fait que mémoriser votre préférence pour le jour où ce backend arrivera.

**Découverte des outils.** `usage='ai_tool'` est pleinement pris en charge par l'AI App d'Odoo sur **Odoo 19.0**. Sur la série Odoo 18.x que vise ce module, le champ existe mais l'interface de découverte de l'AI App peut ne pas faire apparaître ces actions. Elles restent appelables par programmation (`env.ref(...).run()`).

## Compatibilité

| Composant | Compatible |
|-----------|------------|
| Odoo | 18.0 (Community ou Enterprise) |
| Python | 3.10+ |
| Déploiement | Odoo.sh ou installation on-premise; **non** disponible sur Odoo Online (SaaS), qui bloque les modules tiers personnalisés |

## Prérequis

- Odoo 18.0, sur Odoo.sh ou une installation on-premise
- Python 3.10+
- Un compte Trusteed ([inscrivez-vous gratuitement sur trusteed.xyz](https://trusteed.xyz))
- Applications Odoo (installées automatiquement comme dépendances) : `base`, `web`, `mail`, `sale`, `account`, `stock`, `sale_stock`
- Paquet Python `cryptography` : il est déclaré dans les `external_dependencies` du manifeste, et Odoo refuse d'installer le module sans lui. Il sert à vérifier la signature Ed25519 de l'instantané de règles, l'identité de l'agent selon la RFC 9421 et la canonicalisation JCS selon la RFC 8785. Installez-le avec `pip install cryptography` sur le serveur Odoo (sur Odoo.sh : ajoutez-le à votre `requirements.txt`).

## Installation

### Installation manuelle

1. **Téléchargez le `.zip` installable** depuis la dernière Release GitHub :
   [**⬇ Télécharger la dernière version**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest).
   Le `.zip` est joint à cette release. Les versions antérieures se trouvent sur la
   [page des Releases](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
2. Extrayez-le dans votre `addons_path` Odoo. Le dossier extrait doit s'appeler `trusteed`, le nom technique du module.
3. Redémarrez Odoo : `systemctl restart odoo` (ou l'équivalent pour votre déploiement).
4. Dans le Back Office Odoo : **Paramètres → Activer le mode développeur**.
5. **Paramètres → Applications → Mettre à jour la liste des applications**, puis recherchez « Trusteed » et cliquez sur **Installer**.
6. Ouvrez le nouveau menu **Trusteed**. L'assistant de configuration rapide s'ouvre automatiquement.

### Depuis les sources (compiler le zip vous-même)

```bash
git clone https://github.com/Trusteedxyz/agentic-commerce-odoo.git
cd agentic-commerce-odoo
bash bin/build-zip.sh   # génère dist/trusteed-agentic-commerce-odoo-<version>.zip
```

### Docker / développement local

Le fichier compose de préproduction que l'équipe utilise (`e2e/docker/odoo-staging.yml`) se trouve dans le monorepo de développement privé de Trusteed et ne fait pas partie de ce dépôt. Pour essayer le module dans Docker, lancez n'importe quel conteneur Odoo 18 standard (voir l'[image Odoo officielle](https://hub.docker.com/_/odoo)) et montez le dossier `trusteed` extrait dans un chemin qui figure dans l'`addons_path` de ce conteneur.

Puis dans Odoo : **Paramètres → Applications → Trusteed → Installer**.

## Configuration

L'assistant de configuration rapide (**Trusteed → Quick Setup**) vous guide tout au long du parcours :

1. **Bienvenue** : confirme les prérequis (un compte Trusteed et un accès HTTPS sortant depuis le serveur Odoo).
2. **Connexion** : ouvre le portail Trusteed sur [trusteed.xyz/dashboard](https://trusteed.xyz/dashboard), où vous allez sur **Connecter une boutique → Odoo**.
3. **Identifiants** : collez votre **Merchant ID** et votre **Bootstrap Secret** (64 caractères hexadécimaux).
4. **Test** : vérifie la connexion avant de terminer.

Vous pouvez aussi saisir les identifiants directement sous **Paramètres → Trusteed**, sans passer par l'assistant.

### Paramètres système

| Clé `ir.config_parameter` | Valeur par défaut | Rôle |
|-------------------------------|---------------------|----------|
| `trusteed.merchant_id` | _(vide)_ | Merchant ID délivré par Trusteed |
| `trusteed.bootstrap_secret` | _(vide)_ | Secret d'amorçage de 64 caractères hexadécimaux, `password=True`, réservé à `group_admin` |
| `trusteed.api_base` | `https://api.trusteed.xyz` | Point de terminaison du backend Trusteed |

## Menus d'administration

Après l'installation, un menu de premier niveau **Trusteed** apparaît dans le Back Office Odoo :

| Menu | Description |
|------|--------------|
| Quick Setup | Assistant d'intégration en 4 étapes |
| Trust Center | Aperçu du score de confiance de la boutique |
| My Sales | Mes commandes, AI Sales, Clés et Audit au même endroit |
| Paramètres | Merchant ID, Bootstrap Secret, URL de base de l'API et bascules par outil |

Le panneau d'administration est un bundle partagé avec les connecteurs WooCommerce et PrestaShop. Il contient donc aussi des sections que l'hôte Odoo n'expose pas : `inicio`, `mis-reglas`, `seguridad`, `agentes`, `payment-methods` et `merchant-center`. Les actions client d'Odoo ne montent que `trust-center` et `mis-ventas`, et rien dans ces deux pages ne renvoie vers les autres. Dans Odoo, ces sections sont inaccessibles, et le tableau ci-dessus est la liste complète de ce que vous pouvez ouvrir depuis le Back Office.

## Application des règles au paiement

Au-delà du Trust Center, le module installe une couche d'application des règles qui s'exécute sur les commandes de vente de la boutique elle-même (`models/sale_order_enforcement.py` et les fichiers qui l'entourent). Dans les grandes lignes :

- Elle intercepte la confirmation de la commande de vente par les trois voies d'entrée (l'`action_confirm` de l'interface et du RPC, la création d'une commande directement avec `state='sale'`, et l'écriture de `state='sale'` sur une commande existante), si bien qu'un client XML-RPC ou JSON-RPC sans interface ne peut pas contourner le contrôle.
- À chaque confirmation, elle récupère votre instantané de règles auprès de Trusteed, vérifie sa signature JWS Ed25519 et le garde en cache dans le processus pendant 5 minutes.
- Si un token d'agent est présent, il est vérifié hors ligne. Une décision BLOCK refuse la confirmation avec une erreur `trusteed:R0xx` qui nomme la règle déclenchée.
- Si l'instantané est indisponible, si le coupe-circuit est activé ou si quelque chose d'inattendu se produit, c'est le mode de repli configuré qui tranche : `strict` bloque, `balanced` et `permissive` laissent passer la commande. La confirmation ne plante jamais.
- Une action planifiée, « Trusteed CEL: Refresh Rule Snapshot » (`data/cron.xml`), préchauffe ce cache d'instantané toutes les 5 minutes, pour que la première commande de chaque intervalle ne paie pas la latence d'une récupération à froid. Vous la trouverez, et pourrez la désactiver, sous **Paramètres → Technique → Automatisation → Actions planifiées**.

## FAQ

**Quelles données sont envoyées ?** Uniquement ce dont ont besoin les règles d'application et les reçus de confiance : montants des commandes, pays et identité de l'agent. Aucune donnée de carte bancaire ne transite jamais par Trusteed. Toutes les communications passent par HTTPS.

**Quels agents sont pris en charge ?** Tout agent connecté par un client compatible MCP qui appelle les outils IA natifs exposés par ce module, y compris Claude Desktop. Deux réserves avant de vous appuyer dessus : aujourd'hui, seul `verify-agent-signature` est activé et opérationnel (voir [État des outils IA natifs](#état-des-outils-ia-natifs)), et l'AI App d'Odoo ne fait apparaître ces outils de façon fiable que sur Odoo 19.0. Sur 18.x, ils peuvent ne pas figurer dans son interface de découverte.

**Cela ralentit-il ma boutique ?** Non. L'application des règles ne s'exécute de façon synchrone qu'à l'étape de transaction concernée, avec des comportements par défaut fail-closed plutôt qu'un repli général qui autorise tout.

**Puis-je l'installer sur Odoo Online (SaaS) ?** Non. Odoo Online n'autorise pas les modules tiers personnalisés. Utilisez Odoo.sh ou une installation on-premise.

## Le tableau de bord de préparation agentique

**Les agents me trouvent-ils ?** est une page de votre panneau d'administration
qui répond à une seule question : lorsqu'un agent d'achat IA visite votre
boutique, obtient-il ce que vous croyez qu'il obtient ?

Elle n'affiche jamais de note unique. Trois colonnes, jamais moyennées, car elles
répondent à des questions différentes et peuvent légitimement se contredire :

| Colonne | Ce que c'est |
| --- | --- |
| **Ce que dit un tiers** | Le verdict d'un scanner externe, cité tel quel. Jamais réinterprété dans une échelle qui serait la nôtre : dès que nous convertissons la note d'un autre, nous corrigeons notre propre copie |
| **Ce que vous dites correspond-il à ce que vous faites ?** | 16 vérifications qui confrontent ce que votre boutique *annonce* à ce qu'elle *répond réellement*. C'est ce qu'aucun scanner externe ne peut faire : il faut vos identifiants |
| **Ce que nous avons vu** | Le trafic réel des agents sur la période choisie : quels agents sont venus, quels outils ils ont utilisés, jusqu'où ils sont allés et où ils ont échoué |

Une vérification qui n'a pas pu s'exécuter est signalée comme « non vérifiée »,
avec son motif. Elle n'est jamais écartée en silence ni comptée comme réussie.
« Nous n'avons pas pu regarder » et « nous avons regardé et tout allait bien »
sont deux réponses différentes, et la page indique laquelle s'applique.

### Ce que vérifie chaque contrôle

| Contrôle | Ce qu'il détecte |
| --- | --- |
| C1 | Vous annoncez des outils que votre boutique ne sert pas |
| C2 | Vous annoncez un protocole de paiement dont le point de terminaison ne répond pas |
| C3 | Le prix du catalogue n'est pas le prix facturé |
| C4 | Annoncé comme disponible alors qu'il ne l'est pas |
| C5 | Votre politique de retour dit des choses différentes selon l'endroit où on la lit |
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
| C16 | Le délai de livraison que vous promettez n'est pas celui que vous tenez |

Certains contrôles ont besoin de plus que vos réglages, et la page le dit au lieu
de laisser un vide :

- Nécessite une boutique connectée (C3, C4, C5, C14) : ces contrôles comparent
  avec votre catalogue réel, et sans identifiants il n'y a rien à comparer.
- Nécessite des commandes livrées (C16) : ce contrôle compare ce que vous
  promettez à ce que vous avez réellement tenu, ce qui est impossible sans
  historique.
- Rien à comparer cette fois : C12, par exemple, n'a rien à vérifier tant qu'un
  agent n'a pas réellement finalisé un achat. Ce n'est pas une mauvaise note.

Les contrôles s'exécutent une fois par jour et la page affiche le résultat avec
sa date, pour qu'un verdict d'hier ressemble à un verdict d'hier. Un « tout va
bien » mis en cache et présenté comme actuel serait exactement l'auto-illusion
que cette page existe pour débusquer.

## Journal des modifications

### 18.0.1.2.4: Renforcement de sécurité

- Renforcé : la vérification du token d'agent rejette désormais proprement toute entrée à la forme inattendue, au lieu de risquer une erreur interne. Ce n'est pas un bug exploitable ici : contrairement à d'autres plateformes de cette famille, aucun chemin ne laisse passer une commande à cause de cette erreur. Renforcé par cohérence après un signalement de sécurité sur le module PrestaShop. Voir [GHSA-2j2x-5q52-g48m](https://github.com/Trusteedxyz/agentic-commerce-prestashop/security/advisories/GHSA-2j2x-5q52-g48m).

### 18.0.1.2.3

- Nouveau : une vérification qui n'a pas pu s'exécuter indique maintenant pourquoi, dans l'un de quatre groupes (rien à faire, configuration nécessaire, en attente de données, ou l'une de nos propres vérifications a échoué), au lieu d'une liste unique de gris inexpliqués.
- Nouveau : le panneau affiche désormais lequel de nos serveurs a répondu à votre requête, sous la forme d'une courte étiquette opaque. C'est utile pour comparer ce que vous voyez ici avec ce que voit le support. Elle ne révèle jamais un nom d'hôte ni un nom de service.

### 18.0.1.2.2

- Nouveau : les Paramètres vous permettent maintenant de choisir les outils que votre boutique sert aux agents. Si vous n'avez jamais enregistré de liste, le panneau vous indique que ce que vous servez est l'ensemble de base livré avec la plateforme, et non un choix de votre part.
- Nouveau : un bouton pour relancer la vérification sans attendre le balayage quotidien, et le panneau mémorise ce qui a changé depuis l'exécution précédente.
- Modifié : nos propres pannes ne comptent plus comme des écarts de votre boutique. Le panneau les met à part, car vous ne pouvez rien y faire.

### 18.0.1.2.1

- Corrigé : la page de préparation agentique était livrée sans sa feuille de style, si bien que le panneau s'affichait sans mise en forme.
- Corrigé : le panneau pouvait afficher son interface dans une langue et le diagnostic dans une autre. La langue retenue accompagne désormais les textes au lieu d'être détectée deux fois.
- Corrigé : le panneau ne transmettait pas la langue de l'utilisateur Odoo, si bien que le diagnostic revenait dans la langue du navigateur et non dans la sienne.
- Nouveau : chaque constat comporte un lien vers l'endroit où le corriger, et les affirmations du marchand (la promesse de livraison et les autres) apparaissent avec les éléments qui étayent chacune.
- Modifié : une boutique sans aucune exécution s'affiche comme « en cours de vérification » au lieu de « vérifiée une fois par jour » : ouvrir le panneau lance déjà la première exécution en arrière-plan.

### 18.0.1.2.0

- Nouveau : tableau de bord de préparation agentique. *Les agents me trouvent-ils ?* arrive dans le panneau d'administration. Il confronte, en 16 vérifications, ce que votre boutique annonce à ce qu'elle répond réellement, et il affiche les seize, pas seulement celles qui échouent. Une vérification qui n'a pas pu s'exécuter indique pourquoi (boutique non connectée, aucune commande livrée pour l'instant, rien à comparer cette fois) au lieu de laisser un vide qui ressemble à une panne. Voir « Le tableau de bord de préparation agentique » ci-dessus.
- Corrigé : le diagnostic était rédigé en espagnol dans l'API et affiché tel quel, si bien qu'un marchand qui utilisait le panneau en anglais lisait des titres anglais au-dessus de constats en espagnol. Les vérifications émettent maintenant des codes neutres sur le plan de la langue, et le texte est composé au moment de servir, dans la langue que vous utilisez.
- Corrigé : la vérification C1 (« vous annoncez des outils que votre boutique ne sert pas ») comptait tout le catalogue public comme servi lorsqu'aucune liste d'outils n'était configurée, et signalait 46 sur 48 qui répondent alors que le serveur en sert 12. L'erreur allait dans le sens flatteur, celui-là même que ce panneau existe pour débusquer.
- Corrigé : la vérification C6 (« vous annoncez comme disponible quelque chose qui est désactivé ») signalait une capacité comme désactivée dès que son indicateur n'était pas défini, y compris pour les indicateurs activés par défaut. C'était une fausse alerte dans toutes les boutiques.

### 18.0.1.1.2

- Corrigé : le bundle du panneau d'administration (`static/src/js/admin-spa.js`) était livré non minifié : 869 Ko / 25 064 lignes au lieu des 490 Ko / 41 lignes que produit réellement la commande de build documentée (`pnpm run build:odoo`). Il fonctionnait quand même, mais sa provenance ne pouvait pas être vérifiée, faute de diff avec la source pour confirmer ce qu'il contenait. Reconstruit depuis la source. Le bundle compilé correspond maintenant caractère pour caractère à ce que produit la commande de build.
- Corrigé : `R047.customer-confirmation` (la règle qui demande à l'acheteur de confirmer une commande d'agent par un autre canal, e-mail ou SMS) n'avait pas de champ de formulaire dans le panneau d'administration : son seuil de montant existait dans le schéma mais ne pouvait être défini que par l'API. Corrigé aussi : l'affichage d'un nom de catégorie fourni par le marchand imprimait autour les délimiteurs bruts anti-injection de prompt (`<<<MERCHANT_CONTENT_START>>> … <<<MERCHANT_CONTENT_END>>>`) au lieu de les retirer pour l'affichage.

### 18.0.1.1.1

- Corrigé : `_DOCS_URL` envoyait les marchands vers `https://docs.trusteed.xyz/embed/odoo-onprem`, un hôte qui renvoie NXDOMAIN. Tout marchand qui suivait le lien de documentation dans l'application obtenait une erreur de navigateur au lieu du guide d'intégration. Le lien pointe maintenant vers `https://trusteed.xyz/en/integrations/odoo`.

### 18.0.1.1.0

- Correctif de sécurité : la détection de rejeu du vérificateur de tokens d'agent reposait sur un `if nonce:`, si bien qu'un token qui omettait simplement le claim `nonce` échappait entièrement à la détection de rejeu hors ligne. Le claim est désormais obligatoire (16 à 64 caractères, comme l'exige le schéma canonique du token) et un token qui en est dépourvu est rejeté. C'est du fail-closed, comme dans les connecteurs WooCommerce, PrestaShop et Magento.
- Ajout : le module signale désormais quels signaux de panier cette installation sait projeter (`POST /api/v1/enforcement/capabilities`, signé en HMAC, envoyé depuis le hook post-init, c'est-à-dire précisément au moment où la version du module change). Sans ce signalement, une règle dont le signal n'arrive jamais renvoie `NO_SIGNAL` à chaque paiement : elle laisse passer en silence, et le marchand voit une règle en ENFORCE qui ne bloque rien. Le signalement ne propage jamais d'erreur, si bien qu'une erreur réseau à cet endroit ne peut pas faire échouer une installation ni une mise à jour.

### 18.0.1.0.0

- Première version publique : Trust Center, Mis ventas (commandes, ventes IA, clés, audit), assistant de configuration rapide, intégration dans les Paramètres, 5 outils IA natifs, badge de confiance sur la commande, pièce jointe automatique du reçu JWS, prise en charge multi-société.

## Support

- E-mail support : support@trusteed.xyz
- Issues GitHub : [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## Licence

LGPL-3.0. Voir [LICENSE](LICENSE) pour le texte complet. Elle correspond à la licence déclarée dans `__manifest__.py`.
