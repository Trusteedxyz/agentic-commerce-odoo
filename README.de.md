[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | **Deutsch**

# Trusteed Agentic Commerce für Odoo

Ermöglichen Sie neuen Online-Käufern, den KI-Agenten, sichere und zuverlässige Einkäufe in Ihrem Shop dank Trusteed: dem Netzwerk, das Vertrauen zwischen Unternehmen und Agenten fördert.

- **Legen Sie Ihre Geschäftsregeln fest**: wen Sie zum Kauf zulassen, bis zu welchem Betrag, welche Kategorien Sie Agenten nicht anbieten möchten, Preisgrenzen, Lagerbestände zum Schutz vor potenziell betrügerischen Agenten und mehr.
- **Manipulationssichere Belege**: Wir erstellen elektronisch signierte und kryptografisch manipulationssichere Belege, die als Nachweis der tatsächlichen Transaktion im Streitfall dienen. Kompatibel mit eIDAS (EU, UK) und eSIGN (USA).
- **Agentenanalysen**: Sehen Sie Statistiken zu Agentenkäufen — wie viel sie ausgeben, welche Produkte sie kaufen und wie oft.
- **Agentensperrung**: Blockieren Sie potenziell gefährliche oder problematische Agenten.
- **Digitale Währungen**: Ermöglicht Käufe in digitalen Währungen dank des X402-Protokolls.
- **Peer-to-Peer-Transaktionen**: Ermöglicht direkten Handel zwischen Agenten und Händlern.

## Screenshots

| Trust Center | My Sales — Meine Bestellungen | My Sales — AI Sales |
|---------------|-----------------------------------|--------------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![Meine Bestellungen](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| My Sales — Schlüssel | My Sales — Audit | Einstellungen |
|---------------------------|----------------------|----------------|
| ![Schlüssel](screenshots/04-my-sales-keys.png) | ![Audit](screenshots/05-my-sales-audit.png) | ![Einstellungen](screenshots/06-settings.png) |

| Schnelleinrichtungs-Assistent |
|--------------------------------------|
| ![Assistent](screenshots/07-quick-setup-wizard.png) |

Jede Agenten-Transaktion erzeugt einen kryptografisch signierten **Trust Receipt** — einen manipulationssicheren Nachweis (eIDAS-/eSIGN-kompatibel), gelistet unter **My Sales → AI Sales**. Signierschlüssel und ein vollständiges Audit-Protokoll stehen im selben **My Sales**-Menü zur Verfügung, und der Bildschirm **Trust Center** zeigt den Gesamt-Vertrauenswert Ihres Shops.

## Funktionen

Trusteed vereint ein Trust Center, ein Verzeichnis signierter Belege und 5 native agentische Tools in einem einzigen Odoo-Addon.

- **Trust Center** — Vertrauenswert des Shops, signierte Trust Receipts, Signierschlüssel, Audit-Protokoll
- **My Sales** — Bestellungen, Verkäufe an KI-Agenten, Signierschlüssel, Audit-Protokoll, alles in einem Menü
- **5 native KI-Tools**, bereitgestellt über `ir.actions.server` (`usage='ai_tool'`), automatisch erkannt von Odoos AI App / MCP-Server: `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2`
- **Vertrauens-Badge an der Bestellung** — ein berechnetes Vertrauenswert-Badge, eingefügt in die native `sale.order`-Kanban-Ansicht
- **Automatischer JWS-Beleganhang** — signierte Trust Receipts werden `account.move`-Datensätzen beim Buchen automatisch angehängt
- **Multi-Unternehmen-Unterstützung** — stilles erneutes Bootstrapping mit Toast-Benachrichtigung bei Wechsel des aktiven Unternehmens
- **Schnelleinrichtungs-Assistent** — ein 4-stufiger Onboarding-Ablauf, der Ihre Odoo-Instanz mit Trusteed verbindet
- **Fail-closed als Standard** — SSRF-geschützte ausgehende Aufrufe, nur HTTPS, die Regeldurchsetzung erlaubt bei Fehlkonfiguration niemals stillschweigend

## Kompatibilität

| Komponente | Unterstützt |
|------------|-------------|
| Odoo | 18.0 (Community oder Enterprise) |
| Python | 3.10+ |
| Bereitstellung | Odoo.sh oder On-Premise-Installation — **nicht** verfügbar bei Odoo Online (SaaS), das benutzerdefinierte Drittanbieter-Module blockiert |

## Voraussetzungen

- Odoo 18.0, auf Odoo.sh oder einer On-Premise-Installation
- Python 3.10+
- Ein Trusteed-Konto — [kostenlos registrieren auf trusteed.xyz](https://trusteed.xyz)

## Installation

### Manuelle Installation

1. **Laden Sie die installierbare `.zip`** von der neuesten GitHub-Release herunter:
   [**⬇ trusteed-agentic-commerce-odoo-18.0.1.1.0.zip**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest/download/trusteed-agentic-commerce-odoo-18.0.1.1.0.zip)
   — oder durchsuchen Sie alle Versionen auf der [Releases-Seite](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
2. Entpacken Sie sie in Ihren Odoo-`addons_path` — der entpackte Ordner muss `trusteed` heißen (der technische Name des Addons).
3. Starten Sie Odoo neu: `systemctl restart odoo` (oder das Äquivalent für Ihre Bereitstellung).
4. Im Odoo-Back-Office: **Einstellungen → Entwicklermodus aktivieren**.
5. **Einstellungen → Apps → App-Liste aktualisieren**, dann nach "Trusteed" suchen und auf **Installieren** klicken.
6. Öffnen Sie das neue **Trusteed**-Menü — der Schnelleinrichtungs-Assistent öffnet sich automatisch.

### Aus dem Quellcode (Zip selbst erstellen)

```bash
git clone https://github.com/Trusteedxyz/agentic-commerce-odoo.git
cd agentic-commerce-odoo
bash bin/build-zip.sh   # erzeugt dist/trusteed-agentic-commerce-odoo-<version>.zip
```

### Docker / lokale Entwicklung

```bash
docker compose -f e2e/docker/odoo-staging.yml up -d
```

Dann in Odoo: **Einstellungen → Apps → Trusteed → Installieren**.

## Konfiguration

Der Schnelleinrichtungs-Assistent (**Trusteed → Quick Setup**) führt durch den gesamten Ablauf:

1. **Willkommen** — bestätigt die Voraussetzungen (ein Trusteed-Konto + ausgehendes HTTPS vom Odoo-Server).
2. **Verbinden** — öffnet das Trusteed-Portal unter [app.trusteed.xyz](https://app.trusteed.xyz), wo Sie zu **Store verbinden → Odoo** navigieren.
3. **Zugangsdaten** — fügen Sie Ihre **Merchant ID** und Ihr **Bootstrap Secret** ein (64 Hex-Zeichen).
4. **Test** — überprüft die Verbindung vor dem Abschluss.

Zugangsdaten können auch direkt unter **Einstellungen → Trusteed** eingegeben werden, ohne den Assistenten zu durchlaufen.

### Systemparameter

| `ir.config_parameter`-Schlüssel | Standard | Zweck |
|-------------------------------------|----------|-------|
| `trusteed.merchant_id` | _(leer)_ | Von Trusteed ausgestellte Merchant ID |
| `trusteed.bootstrap_secret` | _(leer)_ | 64-Hex-Zeichen-Secret, `password=True`, beschränkt auf `group_admin` |
| `trusteed.api_base` | `https://api.trusteed.xyz` | Endpunkt des Trusteed-Backends |

## Admin-Menüs

Nach der Installation erscheint ein **Trusteed**-Menü oberster Ebene im Odoo-Back-Office:

| Menü | Beschreibung |
|------|--------------|
| Quick Setup | 4-stufiger Onboarding-Assistent |
| Trust Center | Überblick über den Vertrauenswert des Shops |
| My Sales | Meine Bestellungen, AI Sales, Schlüssel, Audit — alles an einem Ort |
| Einstellungen | Merchant ID, Bootstrap Secret, API-Basis-URL und Toggles je Tool |

## FAQ

**Welche Daten werden gesendet?** Nur was Regeldurchsetzung und Trust Receipts benötigen (Bestellsummen, Land, Agentenidentität). Es laufen niemals Zahlungskartendaten über Trusteed. Die gesamte Kommunikation erfolgt über HTTPS.

**Welche Agenten werden unterstützt?** Jeder Agent, der über einen MCP-kompatiblen Client verbunden ist und die 5 nativen KI-Tools dieses Addons aufruft, einschließlich Claude Desktop und Odoos eigener AI App / MCP-Server.

**Verlangsamt es meinen Shop?** Nein. Die Regeldurchsetzung läuft synchron nur beim jeweiligen Transaktionsschritt, mit Fail-closed als Standard statt einer pauschalen Erlaubnis-Rückfallebene.

**Kann ich es auf Odoo Online (SaaS) installieren?** Nein — Odoo Online erlaubt keine benutzerdefinierten Drittanbieter-Module. Verwenden Sie Odoo.sh oder eine On-Premise-Installation.

## Änderungsprotokoll

### 18.0.1.1.0

- **Sicherheitsfix** — die Replay-Erkennung des Agent-Token-Verifizierers hing an einem `if nonce:`, sodass ein Token, das den `nonce`-Claim schlicht wegließ, die Offline-Replay-Erkennung vollständig umging. Der Claim ist jetzt verpflichtend (16–64 Zeichen, wie es das kanonische Token-Schema verlangt) und ein Token ohne ihn wird abgelehnt — fail-closed, wie in den Konnektoren für WooCommerce, PrestaShop und Magento.
- **Neu** — das Addon meldet jetzt, welche Warenkorb-Signale diese Installation projizieren kann (`POST /api/v1/enforcement/capabilities`, HMAC-signiert, aus dem Post-Init-Hook gesendet — genau dann ändert sich die Addon-Version). Ohne das liefert eine Regel, deren Signal nie eintrifft, bei jedem Checkout `NO_SIGNAL`: sie passiert stillschweigend, und der Händler sieht eine Regel in ENFORCE, die nichts blockiert. Die Meldung reicht niemals einen Fehler weiter — ein Netzwerkproblem darf keine Installation oder Aktualisierung zum Scheitern bringen.

### 18.0.1.0.0

- Erste öffentliche Version: Trust Center, My Sales (Bestellungen, Verkäufe an KI-Agenten, Schlüssel, Audit), Schnelleinrichtungs-Assistent, Integration in Einstellungen, 5 native KI-Tools, Vertrauens-Badge an der Bestellung, automatischer JWS-Beleganhang, Multi-Unternehmen-Unterstützung.

## Support

- Support-E-Mail: support@trusteed.xyz
- GitHub Issues: [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## Lizenz

MIT. Vollständiger Text siehe [LICENSE](LICENSE).
