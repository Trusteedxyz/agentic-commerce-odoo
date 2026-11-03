"""Payload del snapshot firmado — decodificación TRAS verificar la firma.

Plan 04 de reglas de checkout, Task 24. Módulo puro (sin Odoo): lo usa
``enforcement_snapshot.verify_jws_snapshot_result`` y lo ejecutan los tests
con la sección ``compressedSnapshot`` del fixture compartido
(packages/shared/src/enforcement/__fixtures__/platform-flow-conformance.json).

El servidor comprime con DEFLATE crudo (RFC 1951) todo snapshot cuyo JSON
pasa de 50 KB y lo marca con ``zip: "DEF"`` en la cabecera protegida. La
firma cubre los bytes COMPRIMIDOS: primero se verifica la firma sobre lo
recibido y, sólo si verifica, se llama aquí. Nunca se infla antes.

Contrato: sin ``zip`` ⇒ el JSON tal cual; ``zip == "DEF"`` exacto ⇒ inflar
con tope de :data:`MAX_INFLATED_BYTES`; cualquier otro ``zip``, un flujo
corrupto/truncado/vacío, pasar del tope o una salida que no sea un objeto
JSON ⇒ no utilizable.

Anti-bomba: ``zlib.decompressobj(-15).decompress(data, MAX + 1)``. El
parámetro ``max_length`` hace que zlib pare de producir salida al llegar a
ese tamaño y deje el resto de la entrada en ``unconsumed_tail``: la memoria
reservada nunca pasa de ``MAX + 1`` bytes, por grande que sea lo que declara
la bomba. Salida de más de ``MAX`` o cola sin consumir ⇒ por encima del
tope; flujo sin final (``eof`` falso) ⇒ corrupto o truncado.
"""

import json
import zlib
from typing import Optional

ZIP_DEFLATE = "DEF"
MAX_INFLATED_BYTES = 2 * 1024 * 1024

OK = "ok"
ZIP_UNSUPPORTED = "zip_unsupported"
INFLATE_FAILED = "inflate_failed"
INFLATE_OVER_CAP = "inflate_over_cap"
PAYLOAD_NOT_JSON = "payload_not_json"


def declares_zip(header: dict) -> bool:
    """¿Declara la cabecera algún ``zip``? (con él, todo fallo es «no utilizable»)."""
    return "zip" in header


def _inflate_capped(data: bytes) -> tuple[str, Optional[bytes]]:
    if not data:
        return INFLATE_FAILED, None
    inflater = zlib.decompressobj(-15)  # -15 = DEFLATE crudo, sin cabecera zlib/gzip
    try:
        out = inflater.decompress(data, MAX_INFLATED_BYTES + 1)
    except zlib.error:
        return INFLATE_FAILED, None
    if len(out) > MAX_INFLATED_BYTES or inflater.unconsumed_tail:
        return INFLATE_OVER_CAP, None
    if not inflater.eof:
        return INFLATE_FAILED, None
    return OK, out


def decode(header: dict, payload_bytes: bytes) -> tuple[str, Optional[dict]]:
    """``(OK o motivo, payload o None)`` de un payload CUYA FIRMA YA VERIFICÓ."""
    data = payload_bytes
    if declares_zip(header):
        if header["zip"] != ZIP_DEFLATE:
            return ZIP_UNSUPPORTED, None
        status, inflated = _inflate_capped(payload_bytes)
        if status != OK:
            return status, None
        data = inflated or b""
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return PAYLOAD_NOT_JSON, None
    if not isinstance(payload, dict):
        return PAYLOAD_NOT_JSON, None
    return OK, payload
