"""Cliente HTTP para a API publica do onibus.info (Joinville/SC).

A API nao exige autenticacao: basta enviar cabecalhos de navegador.
Os dados de GTFS (linhas, paradas, quadros de horario) sao estaticos,
entao usamos um cache em memoria com TTL para nao repetir chamadas caras.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

BASE = "https://onibus.info"
TZ = ZoneInfo("America/Sao_Paulo")

_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": BASE,
    "referer": f"{BASE}/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
}

# TTL (segundos) por prefixo de rota. Rotas nao listadas nao sao cacheadas.
_CACHE_TTL = {
    "/api/routes": 3600,
    "/api/routetrips/": 3600,
    "/api/shapestops/": 3600,
    "/api/timetable/": 3600,
    "/api/timetableschedule/": 3600,
    "/api/stops/": 3600,
    "/api/stoproutes/": 3600,
}

# httpx loga cada request em INFO; no transporte stdio isso so polui o stderr.
logging.getLogger("httpx").setLevel(logging.WARNING)

_cache: dict[str, tuple[float, Any]] = {}
_client: httpx.AsyncClient | None = None


class OnibusAPIError(RuntimeError):
    """Erro tratado da API, com mensagem legivel em portugues."""


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=BASE,
            headers=_HEADERS,
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _ttl_for(path: str) -> int:
    for prefix, ttl in _CACHE_TTL.items():
        if path.startswith(prefix):
            return ttl
    return 0


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    expira, valor = hit
    if expira < time.time():
        _cache.pop(key, None)
        return None
    return valor


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    """Executa a chamada com 3 tentativas (timeout e 5xx) e backoff."""
    client = _get_client()
    ultimo_erro: str = ""
    for tentativa, espera in enumerate((0.5, 1.5, 0.0)):
        try:
            resp = await client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            ultimo_erro = "tempo limite excedido ao falar com onibus.info"
        except httpx.HTTPError as exc:
            ultimo_erro = f"falha de rede ao falar com onibus.info: {exc}"
        else:
            if resp.status_code >= 500:
                ultimo_erro = f"onibus.info respondeu HTTP {resp.status_code}"
            elif resp.status_code >= 400:
                raise OnibusAPIError(
                    f"onibus.info respondeu HTTP {resp.status_code} para {path}. "
                    "Confira se o identificador informado existe."
                )
            else:
                try:
                    return resp.json()
                except ValueError:
                    raise OnibusAPIError(
                        f"onibus.info devolveu uma resposta que nao e JSON para {path}."
                    ) from None
        if tentativa < 2:
            await asyncio.sleep(espera)
    raise OnibusAPIError(f"{ultimo_erro} (3 tentativas em {path}).")


async def get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    ttl = _ttl_for(path)
    chave = f"GET {path} {sorted((params or {}).items())}"
    if ttl:
        em_cache = _cache_get(chave)
        if em_cache is not None:
            return em_cache
    dados = await _request("GET", path, params=params)
    if ttl:
        _cache[chave] = (time.time() + ttl, dados)
    return dados


async def post_json(path: str, body: dict[str, Any]) -> Any:
    return await _request(
        "POST",
        path,
        json=body,
        headers={"content-type": "application/json;charset=UTF-8"},
    )


# --------------------------------------------------------------------------
# Helpers de data/hora (tudo em America/Sao_Paulo)
# --------------------------------------------------------------------------

_SO_HORA = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATA_BR = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?[ T]+(\d{1,2}):(\d{2})$")


def agora() -> datetime:
    return datetime.now(TZ)


def parse_horario(texto: str | None) -> datetime:
    """Converte texto livre em datetime local.

    Aceita: None/"agora", "08:30" (hoje; amanha se ja passou),
    "17/09/2026 08:30", "2026-09-17 08:30" e ISO 8601 (com ou sem fuso).
    """
    if texto is None:
        return agora()
    texto = texto.strip()
    if not texto or texto.lower() in {"agora", "now", "ja"}:
        return agora()

    m = _SO_HORA.match(texto)
    if m:
        base = agora()
        alvo = base.replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0
        )
        if alvo < base - timedelta(minutes=1):
            alvo += timedelta(days=1)
        return alvo

    m = _DATA_BR.match(texto)
    if m:
        dia, mes, ano, hora, minuto = m.groups()
        return datetime(
            int(ano) if ano else agora().year,
            int(mes),
            int(dia),
            int(hora),
            int(minuto),
            tzinfo=TZ,
        )

    iso = texto.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        raise OnibusAPIError(
            f"Nao entendi o horario {texto!r}. Use 'agora', '08:30', "
            "'17/09/2026 08:30' ou ISO 8601."
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def para_utc_iso(dt: datetime) -> str:
    """Formato que /api/directions espera: 2026-09-16T11:30:00.000Z (UTC)."""
    utc = dt.astimezone(ZoneInfo("UTC"))
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ms_para_local(ms: int | float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, TZ)


def hora_local(ms: int | float) -> str:
    return ms_para_local(ms).strftime("%H:%M")


def tipo_dia_de(dt: datetime) -> str:
    """Retorna o service_id usado pelo GTFS local: DU, SAB ou DOM."""
    return ("DU", "DU", "DU", "DU", "DU", "SAB", "DOM")[dt.weekday()]


_APELIDOS_TIPO_DIA = {
    "du": "DU", "util": "DU", "uteis": "DU", "diautil": "DU", "semana": "DU",
    "sab": "SAB", "sabado": "SAB", "sabados": "SAB",
    "dom": "DOM", "domingo": "DOM", "domingos": "DOM", "feriado": "DOM",
}


def normaliza_tipo_dia(valor: str | None, quando: datetime | None = None) -> str:
    """Traduz 'sabado', 'dia util', 'DOM'... no service_id do GTFS."""
    if not valor:
        return tipo_dia_de(quando or agora())
    chave = (
        valor.strip().lower()
        .replace("á", "a").replace("â", "a").replace("ã", "a")
        .replace("é", "e").replace("ê", "e")
        .replace("í", "i").replace("ó", "o").replace("õ", "o")
        .replace("ú", "u").replace("ç", "c")
        .replace(" ", "").replace("-", "").replace("_", "")
    )
    if chave in _APELIDOS_TIPO_DIA:
        return _APELIDOS_TIPO_DIA[chave]
    raise OnibusAPIError(
        f"Tipo de dia {valor!r} desconhecido. Use 'DU' (dias uteis), "
        "'SAB' (sabados) ou 'DOM' (domingos/feriados)."
    )
