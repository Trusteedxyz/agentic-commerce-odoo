[English](README.md) | **Español** | [Français](README.fr.md) | [Deutsch](README.de.md)

# Trusteed Agentic Commerce para Odoo

Permite que los nuevos compradores online, los agentes de IA, realicen compras en tu tienda de forma segura y fiable gracias a Trusteed: la red que fomenta la confianza entre negocios y agentes.

- **Define tus reglas de negocio**: a quién permites comprar, hasta qué importe, qué categorías no quieres ofrecer a los agentes, límites de precio, mantén niveles de stock para protegerte de agentes potencialmente fraudulentos, y más.
- **Recibos a prueba de manipulación**: generamos recibos firmados electrónicamente y criptográficamente invulnerables que sirven como prueba de la transacción real en caso de disputa. Compatibles con eIDAS (UE, Reino Unido) y eSIGN (EE. UU.).
- **Analítica de agentes**: consulta estadísticas de compras de agentes — cuánto gastan, qué productos compran y con qué frecuencia.
- **Bloqueo de agentes**: bloquea agentes potencialmente peligrosos o problemáticos.
- **Monedas digitales**: habilita compras en monedas digitales gracias al protocolo X402.
- **Transacciones entre pares**: habilita comercio directo entre agentes y comerciantes.

## Capturas de pantalla

| Trust Center | Mis ventas — Mis pedidos | Mis ventas — AI Sales |
|---------------|-----------------------------|--------------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![Mis pedidos](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| Mis ventas — Claves | Mis ventas — Auditoría | Ajustes |
|------------------------|----------------------------|---------|
| ![Claves](screenshots/04-my-sales-keys.png) | ![Auditoría](screenshots/05-my-sales-audit.png) | ![Ajustes](screenshots/06-settings.png) |

| Asistente de configuración rápida |
|----------------------------------------|
| ![Asistente](screenshots/07-quick-setup-wizard.png) |

Cada transacción de un agente genera un **recibo de confianza** firmado criptográficamente — un registro a prueba de manipulación (compatible con eIDAS / eSIGN) listado en **Mis ventas → AI Sales**. Las claves de firma y un registro de auditoría completo están disponibles en el mismo menú **Mis ventas**, y la pantalla **Trust Center** muestra la puntuación de confianza global de tu tienda.

## Funcionalidades

Trusteed consolida un Trust Center, un libro de recibos firmados y 5 herramientas agénticas nativas en un único addon de Odoo.

- **Trust Center** — puntuación de confianza de la tienda, recibos de confianza firmados, claves de firma, registro de auditoría
- **Mis ventas** — pedidos, ventas a agentes de IA, claves de firma, registro de auditoría, todo en un mismo menú
- **5 herramientas de IA nativas** expuestas vía `ir.actions.server` (`usage='ai_tool'`), descubiertas automáticamente por la AI App / servidor MCP de Odoo: `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2`
- **Insignia de confianza en el pedido** — una insignia de puntuación de confianza calculada e inyectada en la vista kanban nativa de `sale.order`
- **Adjuntado automático del recibo JWS** — los recibos de confianza firmados se adjuntan automáticamente a los registros `account.move` al validarlos
- **Soporte multiempresa** — reconexión silenciosa con notificación toast cuando cambia la empresa activa
- **Asistente de configuración rápida** — un flujo de incorporación de 4 pasos que conecta tu instancia de Odoo con Trusteed
- **Comportamiento fail-closed por defecto** — llamadas salientes protegidas contra SSRF, solo HTTPS, la aplicación de reglas nunca permite en silencio si está mal configurada

## Compatibilidad

| Componente | Compatible |
|------------|------------|
| Odoo | 18.0 (Community o Enterprise) |
| Python | 3.10+ |
| Despliegue | Odoo.sh o instalación on-premise — **no** disponible en Odoo Online (SaaS), que bloquea módulos de terceros personalizados |

## Requisitos

- Odoo 18.0, en Odoo.sh o una instalación on-premise
- Python 3.10+
- Una cuenta Trusteed — [regístrate gratis en trusteed.xyz](https://trusteed.xyz)

## Instalación

### Instalación manual

1. **Descarga el `.zip` instalable** desde la última Release de GitHub:
   [**⬇ trusteed-agentic-commerce-odoo-18.0.1.1.0.zip**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest/download/trusteed-agentic-commerce-odoo-18.0.1.1.0.zip)
   — o consulta todas las versiones en la [página de Releases](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
2. Extráelo en tu `addons_path` de Odoo — la carpeta extraída debe llamarse `trusteed` (es el nombre técnico del addon).
3. Reinicia Odoo: `systemctl restart odoo` (o el equivalente en tu despliegue).
4. En el Back Office de Odoo: **Ajustes → Activar el modo desarrollador**.
5. **Ajustes → Aplicaciones → Actualizar lista de aplicaciones**, luego busca "Trusteed" y pulsa **Instalar**.
6. Abre el nuevo menú **Trusteed** — el asistente de configuración rápida se abre automáticamente.

### Desde el código fuente (compilar el zip tú mismo)

```bash
git clone https://github.com/Trusteedxyz/agentic-commerce-odoo.git
cd agentic-commerce-odoo
bash bin/build-zip.sh   # genera dist/trusteed-agentic-commerce-odoo-<version>.zip
```

### Docker / desarrollo local

```bash
docker compose -f e2e/docker/odoo-staging.yml up -d
```

Luego en Odoo: **Ajustes → Aplicaciones → Trusteed → Instalar**.

## Configuración

El asistente de configuración rápida (**Trusteed → Quick Setup**) guía todo el flujo:

1. **Bienvenida** — confirma los requisitos previos (una cuenta Trusteed + salida HTTPS desde el servidor de Odoo).
2. **Conectar** — abre el Portal de Trusteed en [app.trusteed.xyz](https://app.trusteed.xyz), donde navegas a **Conectar una tienda → Odoo**.
3. **Credenciales** — pega tu **Merchant ID** y tu **Bootstrap Secret** (64 caracteres hexadecimales).
4. **Prueba** — verifica la conectividad antes de finalizar.

Las credenciales también se pueden introducir directamente en **Ajustes → Trusteed**, sin pasar por el asistente.

### Parámetros del sistema

| Clave `ir.config_parameter` | Valor por defecto | Propósito |
|-------------------------------|--------------------|-----------|
| `trusteed.merchant_id` | _(vacío)_ | Merchant ID emitido por Trusteed |
| `trusteed.bootstrap_secret` | _(vacío)_ | Secreto de 64 caracteres hexadecimales, `password=True`, restringido a `group_admin` |
| `trusteed.api_base` | `https://api.trusteed.xyz` | Endpoint del backend de Trusteed |

## Menús de administración

Tras la instalación, aparece un menú de nivel superior **Trusteed** en el Back Office de Odoo:

| Menú | Descripción |
|------|-------------|
| Quick Setup | Asistente de incorporación de 4 pasos |
| Trust Center | Resumen de la puntuación de confianza de la tienda |
| Mis ventas | Mis pedidos, AI Sales, Claves, Auditoría — todo en un mismo lugar |
| Ajustes | Merchant ID, Bootstrap Secret, URL base de la API y toggles por herramienta |

## Preguntas frecuentes

**¿Qué datos se envían?** Solo lo que requieren las reglas de aplicación y los recibos de confianza (importes de pedido, país, identidad del agente). Ningún dato de tarjeta de pago pasa nunca por Trusteed. Toda la comunicación usa HTTPS.

**¿Qué agentes son compatibles?** Cualquier agente conectado a través de un cliente compatible con MCP que llame a las 5 herramientas de IA nativas que expone este addon, incluyendo Claude Desktop y la propia AI App / servidor MCP de Odoo.

**¿Ralentiza mi tienda?** No. La aplicación de reglas se ejecuta de forma síncrona solo en el paso de transacción correspondiente, con comportamiento fail-closed por defecto en lugar de permitir todo por defecto.

**¿Puedo instalarlo en Odoo Online (SaaS)?** No — Odoo Online no permite módulos de terceros personalizados. Usa Odoo.sh o una instalación on-premise.

## Historial de cambios

### 18.0.1.1.0

- **Corrección de seguridad** — la detección de repetición del verificador de tokens de agente colgaba de un `if nonce:`, así que un token que simplemente OMITÍA el claim `nonce` se saltaba entera la detección offline. El claim es ahora obligatorio (de 16 a 64 caracteres, como exige el esquema canónico del token) y un token sin él se rechaza — fail-closed, igual que en los conectores de WooCommerce, PrestaShop y Magento.
- **Novedad** — el addon informa ahora de qué señales de carrito sabe proyectar esta instalación (`POST /api/v1/enforcement/capabilities`, firmado con HMAC, enviado desde el hook post-init, que es justo cuando cambia la versión del addon). Sin eso, una regla cuya señal no llega devuelve `NO_SIGNAL` en cada compra: pasa en silencio, y el comerciante ve una regla en ENFORCE que no bloquea nada. El reporte nunca propaga un fallo: un error de red ahí no puede tumbar una instalación ni una actualización.

### 18.0.1.0.0

- Primera versión pública: Trust Center, Mis ventas (pedidos, ventas a agentes de IA, claves, auditoría), asistente de configuración rápida, integración en Ajustes, 5 herramientas de IA nativas, insignia de confianza en el pedido, adjuntado automático de recibo JWS, soporte multiempresa.

## Soporte

- Correo de soporte: support@trusteed.xyz
- Issues de GitHub: [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## Licencia

MIT. Ver [LICENSE](LICENSE) para el texto completo.
