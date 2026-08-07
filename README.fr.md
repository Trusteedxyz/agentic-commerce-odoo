[English](README.md) | [Español](README.es.md) | **Français** | [Deutsch](README.de.md)

# Trusteed Agentic Commerce pour Odoo

Permettez aux nouveaux acheteurs en ligne, les agents IA, d'effectuer des achats dans votre boutique de manière sûre et fiable grâce à Trusteed : le réseau qui instaure la confiance entre entreprises et agents.

- **Définissez vos règles commerciales** : qui vous autorisez à acheter, jusqu'à quel montant, quelles catégories vous ne souhaitez pas proposer aux agents, limites de prix, maintien des niveaux de stock pour vous protéger contre d'éventuels agents frauduleux, et plus encore.
- **Reçus inviolables** : nous générons des reçus signés électroniquement et cryptographiquement inviolables qui servent de preuve de la transaction réelle en cas de litige. Compatibles avec eIDAS (UE, Royaume-Uni) et eSIGN (États-Unis).
- **Analyses des agents** : consultez les statistiques d'achats des agents — combien ils dépensent, quels produits ils achètent, et à quelle fréquence.
- **Blocage des agents** : bloquez les agents potentiellement dangereux ou problématiques.
- **Monnaies numériques** : permet les achats en monnaies numériques grâce au protocole X402.
- **Transactions pair à pair** : permet le commerce direct entre agents et commerçants.

## Captures d'écran

| Trust Center | My Sales — Mes commandes | My Sales — AI Sales |
|---------------|-----------------------------|--------------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![Mes commandes](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| My Sales — Clés | My Sales — Audit | Paramètres |
|---------------------|----------------------|------------|
| ![Clés](screenshots/04-my-sales-keys.png) | ![Audit](screenshots/05-my-sales-audit.png) | ![Paramètres](screenshots/06-settings.png) |

| Assistant de configuration rapide |
|-----------------------------------------|
| ![Assistant](screenshots/07-quick-setup-wizard.png) |

Chaque transaction d'un agent génère un **reçu de confiance** signé cryptographiquement — un enregistrement inviolable (compatible eIDAS / eSIGN) répertorié sous **My Sales → AI Sales**. Les clés de signature et un journal d'audit complet sont disponibles dans le même menu **My Sales**, et l'écran **Trust Center** affiche le score de confiance global de votre boutique.

## Fonctionnalités

Trusteed regroupe un Trust Center, un registre de reçus signés et 5 outils agentiques natifs au sein d'un seul module Odoo.

- **Trust Center** — score de confiance de la boutique, reçus de confiance signés, clés de signature, journal d'audit
- **My Sales** — commandes, ventes aux agents IA, clés de signature, journal d'audit, tout dans un seul menu
- **5 outils IA natifs** exposés via `ir.actions.server` (`usage='ai_tool'`), découverts automatiquement par l'AI App / serveur MCP d'Odoo : `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2`
- **Badge de confiance sur la commande** — un badge de score de confiance calculé et injecté dans la vue kanban native `sale.order`
- **Pièce jointe automatique du reçu JWS** — les reçus de confiance signés sont automatiquement joints aux enregistrements `account.move` lors de leur validation
- **Support multi-société** — reconnexion silencieuse avec notification toast lors du changement de société active
- **Assistant de configuration rapide** — un parcours d'intégration en 4 étapes qui connecte votre instance Odoo à Trusteed
- **Comportement fail-closed par défaut** — appels sortants protégés contre le SSRF, HTTPS uniquement, l'application des règles n'autorise jamais silencieusement en cas de mauvaise configuration

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

## Installation

### Installation manuelle

1. **Téléchargez le `.zip` installable** depuis la dernière Release GitHub :
   [**⬇ trusteed-agentic-commerce-odoo-18.0.1.1.0.zip**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest/download/trusteed-agentic-commerce-odoo-18.0.1.1.0.zip)
   — ou parcourez toutes les versions sur la [page des Releases](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
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

```bash
docker compose -f e2e/docker/odoo-staging.yml up -d
```

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

## FAQ

**Quelles données sont envoyées ?** Uniquement ce que nécessitent les règles d'application et les reçus de confiance (montants de commande, pays, identité de l'agent). Aucune donnée de carte de paiement ne transite jamais par Trusteed. Toutes les communications utilisent HTTPS.

**Quels agents sont pris en charge ?** Tout agent connecté via un client compatible MCP qui appelle les 5 outils IA natifs exposés par ce module, y compris Claude Desktop et l'AI App / serveur MCP propre d'Odoo.

**Cela ralentit-il ma boutique ?** Non. L'application des règles s'exécute de manière synchrone uniquement à l'étape de transaction concernée, avec un comportement fail-closed par défaut plutôt qu'une autorisation globale par défaut.

**Puis-je l'installer sur Odoo Online (SaaS) ?** Non — Odoo Online n'autorise pas les modules tiers personnalisés. Utilisez Odoo.sh ou une installation on-premise.

## Journal des modifications

### 18.0.1.1.0

- **Correctif de sécurité** — la détection de rejeu du vérificateur de jetons d'agent reposait sur un `if nonce:`, si bien qu'un jeton omettant simplement le claim `nonce` échappait entièrement à la détection hors ligne. Le claim est désormais obligatoire (16 à 64 caractères, comme l'exige le schéma canonique du jeton) et un jeton qui en est dépourvu est rejeté — fail-closed, comme dans les connecteurs WooCommerce, PrestaShop et Magento.
- **Nouveauté** — l'addon déclare désormais quels signaux de panier cette installation sait projeter (`POST /api/v1/enforcement/capabilities`, signé en HMAC, envoyé depuis le hook post-init, c'est-à-dire précisément au moment où la version de l'addon change). Sans cela, une règle dont le signal n'arrive jamais renvoie `NO_SIGNAL` à chaque paiement : elle passe en silence, et le marchand voit une règle en ENFORCE qui ne bloque rien. La déclaration ne propage jamais d'erreur : un incident réseau ne peut pas faire échouer une installation ou une mise à jour.

### 18.0.1.0.0

- Première version publique : Trust Center, My Sales (commandes, ventes aux agents IA, clés, audit), assistant de configuration rapide, intégration dans les Paramètres, 5 outils IA natifs, badge de confiance sur la commande, pièce jointe automatique du reçu JWS, support multi-société.

## Support

- E-mail support : support@trusteed.xyz
- Issues GitHub : [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## Licence

MIT. Voir [LICENSE](LICENSE) pour le texte complet.
