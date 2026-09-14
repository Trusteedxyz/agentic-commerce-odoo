[English](README.md) | **Español** | [Français](README.fr.md) | [Deutsch](README.de.md)

# Trusteed Agentic Commerce para Odoo

Permite que los nuevos compradores online, los agentes de IA, realicen compras en tu tienda de forma segura y fiable gracias a Trusteed: la red que fomenta la confianza entre negocios y agentes.

- **Define tus reglas de negocio**: a quién permites comprar, hasta qué importe, qué categorías no quieres ofrecer a los agentes, límites de precio, mantén niveles de stock para protegerte de agentes potencialmente fraudulentos, y más.
- **Recibos a prueba de manipulación**: generamos recibos firmados criptográficamente (JWS Ed25519) en los que cualquier alteración queda a la vista, y que sirven como evidencia de la transacción real en caso de disputa. Están diseñados en torno a los conceptos probatorios de eIDAS (UE, Reino Unido) y eSIGN (EE. UU.) — una firma electrónica de tipo avanzado, **no** cualificada: hoy no hay sello de tiempo cualificado ni sello de un QTSP en producción.
- **Analítica de agentes**: consulta estadísticas de compras de agentes — cuánto gastan, qué productos compran y con qué frecuencia.
- **Bloqueo de agentes**: bloquea agentes potencialmente peligrosos o problemáticos.
- **Monedas digitales**: habilita compras en monedas digitales gracias al protocolo X402.
- **Transacciones entre pares**: habilita comercio directo entre agentes y comerciantes.
- **Panel de preparación para agentes**: comprueba en vivo si los agentes de IA pueden comprar hoy en tu tienda — tres vistas independientes (lo que dicen otros, lo que prometes vs. lo que haces, lo que hemos observado), sin fusionarlas en una sola puntuación inventada.

## Capturas de pantalla

| Trust Center | Mis ventas — Mis pedidos | Mis ventas — AI Sales |
|---------------|-----------------------------|--------------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![Mis pedidos](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| Mis ventas — Claves | Mis ventas — Auditoría | Ajustes |
|------------------------|----------------------------|---------|
| ![Claves](screenshots/04-my-sales-keys.png) | ![Auditoría](screenshots/05-my-sales-audit.png) | ![Ajustes](screenshots/06-settings.png) |

| Asistente de configuración rápida | Agent Readiness |
|----------------------------------------|------------------|
| ![Asistente](screenshots/07-quick-setup-wizard.png) | ![Agent Readiness](screenshots/08-agent-readiness.png) |

Cada transacción de un agente genera un **recibo de confianza** firmado criptográficamente — un registro en el que cualquier alteración queda a la vista (JWS Ed25519, alineado con los conceptos probatorios de eIDAS / eSIGN, sin sello de tiempo cualificado) listado en **Mis ventas → AI Sales**. Las claves de firma y un registro de auditoría completo están disponibles en el mismo menú **Mis ventas**, y la pantalla **Trust Center** muestra la puntuación de confianza global de tu tienda.

## Funcionalidades

Trusteed consolida un Trust Center, un libro de recibos firmados y 5 herramientas agénticas nativas en un único addon de Odoo.

- **Trust Center** — puntuación de confianza de la tienda, recibos de confianza firmados, claves de firma, registro de auditoría
- **Mis ventas** — pedidos, ventas a agentes de IA, claves de firma, registro de auditoría, todo en un mismo menú
- **5 herramientas de IA nativas** expuestas vía `ir.actions.server` (`usage='ai_tool'`): `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2` — **no las cinco funcionan hoy**, ver [Estado de las herramientas de IA nativas](#estado-de-las-herramientas-de-ia-nativas)
- **Insignia de confianza en el pedido** — una insignia de puntuación de confianza calculada e inyectada en la vista kanban nativa de `sale.order`
- **Adjuntado automático del recibo JWS** — los recibos de confianza firmados se adjuntan automáticamente a los registros `account.move` al validarlos
- **Soporte multiempresa** — reconexión silenciosa con notificación toast cuando cambia la empresa activa
- **Asistente de configuración rápida** — un flujo de incorporación de 4 pasos que conecta tu instancia de Odoo con Trusteed
- **Comportamiento fail-closed por defecto** — llamadas salientes protegidas contra SSRF, solo HTTPS, la aplicación de reglas nunca permite en silencio si está mal configurada

### Estado de las herramientas de IA nativas

Las cinco herramientas se registran al instalar, pero solo una está activada por defecto y respaldada por un endpoint desplegado. Los interruptores están en **Ajustes → Trusteed**.

| Herramienta | Por defecto | Estado hoy |
|-------------|-------------|------------|
| `verify-agent-signature` | activada | **Operativa** — verificación de firma RFC 9421 |
| `dispatch-payment-acp` | **desactivada** | Funciona, pero es opt-in: pago iniciado por el agente, la activas tú |
| `dispatch-payment-x402` | **desactivada** | Funciona, pero es opt-in: pago iniciado por el agente, la activas tú |
| `sign-trust-receipt` | activada | **Sin backend desplegado todavía** — se reporta siempre como no disponible, diga lo que diga el interruptor. Los recibos los sigue emitiendo el flujo de compra y se leen en **Mis ventas → AI Sales** |
| `dispatch-payment-ap2` | **desactivada** | **Sin backend desplegado todavía** — se reporta siempre como no disponible, diga lo que diga el interruptor |

Activar un interruptor nunca hace invocable una herramienta sin backend desplegado; el interruptor solo guarda tu preferencia para cuando ese backend exista.

**Descubrimiento de herramientas.** `usage='ai_tool'` está plenamente soportado por la AI App de Odoo en **Odoo 19.0**. En la serie Odoo 18.x a la que apunta este addon el campo existe, pero la interfaz de descubrimiento de la AI App puede no mostrar estas acciones — siguen siendo invocables por código (`env.ref(...).run()`).

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
- **Aplicaciones de Odoo** (se instalan automáticamente como dependencias): `base`, `web`, `mail`, `sale`, `account`, `stock`, `sale_stock`
- **Paquete Python `cryptography`** — declarado en `external_dependencies` del manifiesto; sin él Odoo se niega a instalar el addon. Se usa para verificar la firma Ed25519 del snapshot de reglas, la identidad del agente (RFC 9421) y la canonicalización JCS (RFC 8785). Instálalo con `pip install cryptography` en el servidor de Odoo (en Odoo.sh: añádelo a tu `requirements.txt`).

## Instalación

### Instalación manual

1. **Descarga el `.zip` instalable** desde la última Release de GitHub:
   [**⬇ Descargar la última versión**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest)
   — el `.zip` va adjunto a esa release. Las versiones anteriores están en la
   [página de Releases](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
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

El fichero compose que usa el equipo para staging (`e2e/docker/odoo-staging.yml`) vive en el monorepo de desarrollo privado de Trusteed y **no** forma parte de este repositorio. Para probar el addon en Docker, arranca cualquier contenedor estándar de Odoo 18 — ver la [imagen oficial de Odoo](https://hub.docker.com/_/odoo) — y monta la carpeta `trusteed` extraída en una ruta incluida en el `addons_path` de ese contenedor.

Luego en Odoo: **Ajustes → Aplicaciones → Trusteed → Instalar**.

## Configuración

El asistente de configuración rápida (**Trusteed → Quick Setup**) guía todo el flujo:

1. **Bienvenida** — confirma los requisitos previos (una cuenta Trusteed + salida HTTPS desde el servidor de Odoo).
2. **Conectar** — abre el Portal de Trusteed en [trusteed.xyz/dashboard](https://trusteed.xyz/dashboard), donde navegas a **Conectar una tienda → Odoo**.
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

El panel de administración es un bundle compartido con los conectores de WooCommerce y PrestaShop, así que contiene también secciones que el host de Odoo no expone: `inicio`, `mis-reglas`, `seguridad`, `agentes`, `payment-methods` y `merchant-center`. Las acciones cliente de Odoo montan únicamente `trust-center` y `mis-ventas`, y nada dentro de esas dos páginas enlaza a las demás — así que en Odoo esas secciones son inalcanzables, y la tabla de arriba es la lista completa de lo que puedes abrir desde el Back Office.

## Aplicación de reglas en la compra

Además del Trust Center, el addon instala una capa de aplicación de reglas que actúa sobre los propios pedidos de venta de la tienda (`models/sale_order_enforcement.py` y los ficheros a su alrededor). A grandes rasgos:

- Intercepta la **confirmación del pedido de venta** por las tres vías de entrada — el `action_confirm` de la interfaz y de RPC, crear un pedido directamente con `state='sale'`, y escribir `state='sale'` sobre uno existente — para que un cliente XML-RPC o JSON-RPC sin interfaz no pueda saltarse la comprobación.
- En cada confirmación descarga tu **snapshot de reglas** desde Trusteed, verifica su firma JWS Ed25519 y lo cachea en el proceso durante 5 minutos.
- Si hay un token de agente, se verifica sin salir a la red; una decisión **BLOCK** rechaza la confirmación con un error `trusteed:R0xx` que nombra la regla que ha saltado.
- Si el snapshot no está disponible, el interruptor de emergencia está activo o pasa algo inesperado, decide el **modo de reserva** configurado: `strict` bloquea, `balanced` y `permissive` dejan pasar el pedido. Nunca tumba la confirmación.
- Una acción planificada, **«Trusteed CEL: Refresh Rule Snapshot»** (`data/cron.xml`), calienta esa caché **cada 5 minutos** para que el primer pedido de cada intervalo no pague la latencia de la descarga en frío. Puedes encontrarla y desactivarla en **Ajustes → Técnico → Automatización → Acciones planificadas**.

## Preguntas frecuentes

**¿Qué datos se envían?** Solo lo que requieren las reglas de aplicación y los recibos de confianza (importes de pedido, país, identidad del agente). Ningún dato de tarjeta de pago pasa nunca por Trusteed. Toda la comunicación usa HTTPS.

**¿Qué agentes son compatibles?** Cualquier agente conectado a través de un cliente compatible con MCP que llame a las herramientas de IA nativas que expone este addon, incluyendo Claude Desktop. Dos matices que conviene saber antes de planificar sobre esto: hoy solo `verify-agent-signature` está activada y operativa (ver [Estado de las herramientas de IA nativas](#estado-de-las-herramientas-de-ia-nativas)), y la propia AI App de Odoo solo muestra estas herramientas de forma fiable en Odoo 19.0 — en 18.x pueden no aparecer en su interfaz de descubrimiento.

**¿Ralentiza mi tienda?** No. La aplicación de reglas se ejecuta de forma síncrona solo en el paso de transacción correspondiente, con comportamiento fail-closed por defecto en lugar de permitir todo por defecto.

**¿Puedo instalarlo en Odoo Online (SaaS)?** No — Odoo Online no permite módulos de terceros personalizados. Usa Odoo.sh o una instalación on-premise.

## El panel de preparación agéntica

**¿Me encuentran los agentes?** es una página dentro de tu panel de
administración que responde a una sola pregunta: cuando un agente de compra con
IA visita tu tienda, ¿se encuentra lo que tú crees que se encuentra?

Nunca enseña una nota única. Tres columnas, sin promediar, porque responden a
preguntas distintas y pueden contradecirse con toda legitimidad:

| Columna | Qué es |
| --- | --- |
| **Lo que dice un tercero** | El veredicto de un escáner externo, citado tal cual. Nunca reinterpretado a una escala nuestra: en cuanto reescalas la nota de otro, estás corrigiendo tu propio examen |
| **¿Coincide lo que dices con lo que haces?** | 16 comprobaciones que contrastan lo que tu tienda **anuncia** con lo que **responde de verdad**. Esta es la parte que ningún escáner externo puede hacer: necesita tus credenciales |
| **Lo que hemos visto pasar** | Tráfico agéntico real en la ventana elegida: qué agentes llegaron, qué herramientas usaron, hasta dónde llegaron y dónde fallaron |

Una comprobación que no se ha podido hacer se informa como **sin comprobar**,
con el motivo. Nunca se descarta en silencio ni se cuenta como aprobado. «No
hemos podido mirar» y «hemos mirado y está bien» son respuestas distintas, y la
página dice cuál de las dos es.

### Qué mira cada comprobación

| Comprobación | Qué detecta |
| --- | --- |
| C1 | Anuncias herramientas que tu tienda no sirve |
| C2 | Anuncias un protocolo de compra cuyo endpoint no responde |
| C3 | El precio del catálogo no es el que se cobra |
| C4 | Se anuncia disponible lo que no lo está |
| C5 | Tu política de devoluciones dice cosas distintas según dónde se mire |
| C6 | Anuncias como disponible algo que está apagado |
| C7 | Reglas activadas que no pueden actuar por falta de datos |
| C8 | Tus reglas observan pero no bloquean |
| C9 | La forma de identificarse que anuncias no funciona |
| C10 | Un agente puede comprar cualquier importe sin tu confirmación |
| C11 | El punto de venta usa reglas caducadas |
| C12 | Operaciones sin comprobante firmado |
| C13 | Direcciones anunciadas que no funcionan |
| C14 | Los agentes ven datos desfasados de tu tienda |
| C15 | Credenciales de identidad a punto de caducar |
| C16 | El plazo de entrega que prometes no es el que cumples |

Algunas comprobaciones necesitan algo más que tu configuración para ejecutarse, y
la página lo dice en vez de dejar un hueco:

- **Necesita tu tienda conectada** (C3, C4, C5, C14): comparan contra tu catálogo
  real, y sin credenciales no hay con qué comparar.
- **Necesita pedidos entregados** (C16): compara lo que prometes con lo que has
  cumplido de verdad, y eso no se puede sin historial.
- **Esta vez no había nada que comparar**: por ejemplo, C12 no tiene nada que
  mirar hasta que un agente haya completado una compra. Eso no es un suspenso.

Las comprobaciones se ejecutan una vez al día y la página enseña el resultado
**con su fecha**, para que un veredicto de ayer se vea como un veredicto de ayer.
Un «todo bien» guardado y presentado como actual sería justo el autoengaño que
esta página existe para cazar.

## Historial de cambios

### 18.0.1.2.4 — Refuerzo de seguridad

- Endurecido: la verificación del token del agente ahora rechaza de forma limpia cualquier entrada con forma inesperada, en vez de arriesgarse a un error interno. No es un fallo explotable aquí — a diferencia de otras plataformas de esta familia, ningún camino deja pasar un pedido por ese error — endurecido por consistencia tras un aviso de seguridad contra el módulo de PrestaShop. Ver [GHSA-2j2x-5q52-g48m](https://github.com/Trusteedxyz/agentic-commerce-prestashop/security/advisories/GHSA-2j2x-5q52-g48m).

### 18.0.1.2.3

- Nuevo: cuando una comprobación no se pudo ejecutar, el panel ahora explica qué la desbloquearía — nada que hacer, hay que configurar algo, falta esperar datos, o ha fallado una comprobación nuestra — en vez de una lista plana de grises sin explicar.
- Nuevo: el panel ahora muestra qué servidor nuestro respondió a tu petición, una etiqueta corta y opaca. Útil para comparar lo que ves aquí con lo que ve soporte; nunca revela un nombre de host o de servicio.

### 18.0.1.2.2

- Nuevo: en Ajustes puedes elegir qué herramientas sirve tu tienda a los agentes. Si nunca has guardado una lista, el panel te dice que lo que sirve es el conjunto básico que trae la plataforma, y no una elección tuya.
- Nuevo: un botón para volver a comprobar sin esperar al barrido diario, y el panel recuerda qué ha cambiado desde la comprobación anterior.
- Cambiado: nuestras propias averías dejan de contarse como incoherencias de tu tienda. El panel las separa, porque no puedes hacer nada con ellas.

### 18.0.1.2.1

- Corregido: la página de disponibilidad para agentes se publicaba sin su hoja de estilos, así que el panel salía sin formato.
- Corregido: el panel podía mostrar la carcasa en un idioma y el diagnóstico en otro. El idioma resuelto viaja ahora junto a los textos, en vez de detectarse dos veces por separado.
- Corregido: el panel no pasaba el idioma del usuario de Odoo, así que el diagnóstico salía en el idioma del navegador y no en el suyo.
- Nuevo: cada hallazgo lleva un enlace a donde se corrige, y las afirmaciones del comercio —el plazo de entrega y las demás— aparecen con el respaldo que tiene cada una.
- Cambiado: una tienda sin ninguna comprobación todavía se lee como «comprobando» en lugar de «se comprueba una vez al día»: abrir el panel ya lanza la primera comprobación en segundo plano.

### 18.0.1.2.0

- **Nuevo — panel de preparación agéntica.** *¿Me encuentran los agentes?* llega al panel de administración. Contrasta lo que tu tienda anuncia con lo que responde de verdad, en **16 comprobaciones**, y las enseña las dieciséis, no sólo las que fallan. Una comprobación que no se ha podido hacer dice **por qué** (tienda sin conectar, todavía sin pedidos entregados, nada que comparar esta vez) en vez de dejar un hueco que se lee como avería. Ver «El panel de preparación agéntica» más arriba.
- **Corregido** — el diagnóstico se escribía en castellano dentro de la API y se mostraba tal cual, así que un comerciante con el panel en inglés leía encabezados en inglés y hallazgos en castellano. Las comprobaciones emiten ahora códigos neutros de idioma y el texto se compone al servirlo, en el idioma que estés usando.
- **Corregido** — la comprobación C1 («anuncias herramientas que tu tienda no sirve») daba por servido el catálogo público entero cuando no había lista de herramientas configurada: informaba de 46 de 48 respondiendo cuando el servidor sirve 12. Fallaba en la dirección aduladora, que es justo la que este panel existe para cazar.
- **Corregido** — la comprobación C6 («anuncias como disponible algo que está apagado») daba una capacidad por apagada siempre que su bandera no estuviera puesta, incluso en banderas que están encendidas por defecto. Era una falsa alarma en todas las tiendas.

### 18.0.1.1.2

- **Corregido** — el bundle del panel de administración (`static/src/js/admin-spa.js`) se distribuía sin minificar: 869 KB / 25.064 líneas en vez de los 490 KB / 41 líneas que produce el comando de build documentado (`pnpm run build:odoo`). Funcionaba igual, pero su procedencia no se podía verificar. Reconstruido desde la fuente; ahora coincide carácter a carácter con lo que produce el comando.
- **Corregido** — `R047.customer-confirmation` (la regla que pide al comprador confirmar un pedido de agente por un canal aparte, correo o SMS) no tenía campo en el panel de administración: su umbral de importe existía en el esquema pero solo se podía configurar por API. Corregido también: al mostrar una categoría del comercio, se imprimían los delimitadores anti-inyección (`<<<MERCHANT_CONTENT_START>>> … <<<MERCHANT_CONTENT_END>>>`) alrededor en vez de quitarlos para la visualización.

### 18.0.1.1.1

- **Corregido** — `_DOCS_URL` enviaba al comercio a `https://docs.trusteed.xyz/embed/odoo-onprem`, un host que devuelve NXDOMAIN. Cualquier comercio que siguiera el enlace de documentación desde el módulo recibía un error del navegador en vez de la guía de integración. Ahora apunta a `https://trusteed.xyz/en/integrations/odoo`.

### 18.0.1.1.0

- **Corrección de seguridad** — la detección de repetición del verificador de tokens de agente colgaba de un `if nonce:`, así que un token que simplemente OMITÍA el claim `nonce` se saltaba entera la detección offline. El claim es ahora obligatorio (de 16 a 64 caracteres, como exige el esquema canónico del token) y un token sin él se rechaza — fail-closed, igual que en los conectores de WooCommerce, PrestaShop y Magento.
- **Novedad** — el addon informa ahora de qué señales de carrito sabe proyectar esta instalación (`POST /api/v1/enforcement/capabilities`, firmado con HMAC, enviado desde el hook post-init, que es justo cuando cambia la versión del addon). Sin eso, una regla cuya señal no llega devuelve `NO_SIGNAL` en cada compra: pasa en silencio, y el comerciante ve una regla en ENFORCE que no bloquea nada. El reporte nunca propaga un fallo: un error de red ahí no puede tumbar una instalación ni una actualización.

### 18.0.1.0.0

- Primera versión pública: Trust Center, Mis ventas (pedidos, ventas a agentes de IA, claves, auditoría), asistente de configuración rápida, integración en Ajustes, 5 herramientas de IA nativas, insignia de confianza en el pedido, adjuntado automático de recibo JWS, soporte multiempresa.

## Soporte

- Correo de soporte: support@trusteed.xyz
- Issues de GitHub: [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## Licencia

LGPL-3.0. Ver [LICENSE](LICENSE) para el texto completo — coincide con la licencia declarada en `__manifest__.py`.
