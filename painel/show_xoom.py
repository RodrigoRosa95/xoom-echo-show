"""Tela "Echo Show" do Xoom (22/10/2026): relógio, tempo (weather.forecast_casa do HA), sensores do Echo Dot.

/show = página (show.html), /show/dados = JSON com o tempo e os sensores (guardado 5 min).
/show/voz = último estado do assistente de voz (ouvindo / o que entendeu / resposta), que o xoom-assistente
grava em /tmp/xoom-assistente-voz.json; a página consulta a cada segundo para mostrar a faixa de voz.
"""
import json
import os
import threading
import time
import urllib.request

HA = 'http://192.168.4.30'
ARQ_VOZ = '/tmp/xoom-assistente-voz.json'
CONDICOES = {  # condição do met.no → (texto, símbolo que a fonte do Android 4.4 tem)
    'clear-night': ('Céu limpo', '☾'), 'cloudy': ('Nublado', '☁'), 'fog': ('Neblina', '≋'),
    'hail': ('Granizo', '☂'), 'lightning': ('Trovoadas', '⚡'), 'lightning-rainy': ('Chuva e trovoadas', '⚡'),
    'partlycloudy': ('Parcialmente nublado', '⛅'), 'pouring': ('Chuva forte', '☂'), 'rainy': ('Chuva', '☂'),
    'snowy': ('Neve', '❄'), 'snowy-rainy': ('Chuva e neve', '❄'), 'sunny': ('Ensolarado', '☀'),
    'windy': ('Ventania', '༄'), 'windy-variant': ('Vento e nuvens', '༄'), 'exceptional': ('Tempo severo', '⚠'),
}
_cache = {'quando': 0, 'dados': None}
_trava = threading.Lock()


def _token():
    for linha in open(os.path.expanduser('~/.env'), encoding='utf-8'):
        if linha.startswith('HA_TOKEN='):
            return linha.split('=', 1)[1].strip().strip('"\'')


def _ha(caminho, corpo=None):
    req = urllib.request.Request(HA + caminho, json.dumps(corpo).encode() if corpo is not None else None,
                                 {'Authorization': 'Bearer ' + _token(), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _condicao(c):
    texto, simbolo = CONDICOES.get(c, (c or '—', '·'))
    return {'texto': texto, 'simbolo': simbolo}


def _montar():
    agora = _ha('/api/states/weather.forecast_casa')
    a = agora['attributes']
    prev = _ha('/api/services/weather/get_forecasts?return_response',
               {'entity_id': 'weather.forecast_casa', 'type': 'daily'})['service_response']['weather.forecast_casa']['forecast']
    dias = []
    for d in prev[:5]:
        dias.append({'data': d['datetime'][:10], 'max': round(d['temperature']), 'min': round(d.get('templow', d['temperature'])),
                     'chuva': d.get('precipitation') or 0, **_condicao(d.get('condition'))})
    sensores = {}
    for ent, chave in (('sensor.echo_dot_de_rodrigo_temperatura', 'temp_quarto'),
                       ('sensor.echo_dot_de_rodrigo_next_alarm', 'alarme'),
                       ('sensor.echo_dot_de_rodrigo_next_reminder', 'lembrete')):
        try:
            sensores[chave] = _ha(f'/api/states/{ent}')['state']
        except Exception:
            sensores[chave] = None
    return {'agora': {'temp': round(a.get('temperature', 0)), 'umidade': a.get('humidity'),
                      'vento': round(a.get('wind_speed') or 0), **_condicao(agora['state'])},
            'dias': dias, **sensores}


def dados():
    with _trava:
        if _cache['dados'] and time.time() - _cache['quando'] < 300:
            return _cache['dados']
    try:
        d = _montar()
    except Exception as e:
        return _cache['dados'] or {'erro': str(e)[:200]}
    with _trava:
        _cache.update(quando=time.time(), dados=d)
    return d


def voz():
    try:
        with open(ARQ_VOZ, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'estado': 'parado'}
