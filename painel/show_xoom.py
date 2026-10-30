"""Tela "Echo Show" do Xoom (22/10/2026): relógio, tempo (weather.forecast_casa do HA), sensores do Echo Dot.

/show = página (show.html), /show/dados = JSON com o tempo e os sensores (guardado 5 min).
/show/voz = último estado do assistente de voz (ouvindo / o que entendeu / resposta), que o xoom-assistente
grava em /tmp/xoom-assistente-voz.json; a página consulta a cada segundo para mostrar a faixa de voz.
Luzes (22/10/2026): as lâmpadas são da Alexa (não estão no HA), então /show/luz manda ao Echo Dot o comando de texto
"liga a <nome>" / "desliga a <nome>" (alexa_devices.send_text_command), como se alguém falasse. A lista fica em
luzes.json ([{"nome": "luz da sala"}, ...]; o nome é o mesmo do app Alexa). A Alexa não informa se está acesa.
Quadro (22/10/2026): /show/quadro = cartões que a tela vai trocando com transição suave no espaço livre da direita.
Primeiro tipo: próximas estreias do Sonarr (episódios, 21 dias) e do Radarr (filmes, 90 dias), buscadas numa thread
a cada 15 min (o Sonarr/Radarr podem levar minutos para responder com o HD de 4 TB ocupado; a tela nunca espera).
Capas por /show/capa/<sonarr|radarr>/<id> (pôster pequeno do próprio Sonarr/Radarr, guardado em capas/).
"""
import json
import socket
import os
import re
import threading
import time
import urllib.parse
import urllib.request

HA = 'http://192.168.4.30'
ARQ_VOZ = '/tmp/xoom-assistente-voz.json'
ARQ_AGENDA = '/tmp/xoom-agenda.json'
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


def agenda():
    """Próximos compromissos do Google Agenda (o xoom-assistente grava /tmp/xoom-agenda.json a cada 5 min)."""
    try:
        with open(ARQ_AGENDA, encoding='utf-8') as f:
            d = json.load(f)
        # a tela mostra os 5 próximos se revezando ('todos' tem até 50; arquivo antigo só tem 'eventos')
        return {'configurado': d.get('configurado', False), 'eventos': (d.get('todos') or d.get('eventos') or [])[:5]}
    except (OSError, ValueError):
        return {'configurado': False, 'eventos': []}


def dados():
    return dict(_dados(), agenda=agenda())


def _dados():
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


ARQ_LUZES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'luzes.json')
ECHO_NOTIFY = 'notify.echo_dot_de_rodrigo_speak'   # só para achar o device_id do Echo Dot no HA
_echo_id = []


def luzes():
    try:
        with open(ARQ_LUZES, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return [{'nome': 'luz da sala'}, {'nome': 'luz da rua'}]


def _echo_device_id():
    if not _echo_id:
        req = urllib.request.Request(HA + '/api/template',
                                     json.dumps({'template': "{{ device_id('%s') }}" % ECHO_NOTIFY}).encode(),
                                     {'Authorization': 'Bearer ' + _token(), 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as r:
            _echo_id.append(r.read().decode().strip())
    return _echo_id[0]


def luz(nome, ligar):
    """Pede ao Echo Dot para ligar/desligar a lâmpada pelo nome dela na Alexa."""
    if nome.lower() not in {l['nome'].lower() for l in luzes()}:
        raise ValueError('lâmpada desconhecida')
    texto = ('liga a ' if ligar else 'desliga a ') + nome.lower()
    _ha('/api/services/alexa_devices/send_text_command',
        {'device_id': _echo_device_id(), 'text_command': texto})
    return texto


TV_MAC = '74c17ee0e0f8'  # TV LG da sala (192.168.4.160)
TV_ENTIDADE = 'media_player.lg_webos_tv_ut8050psa_2'


def tv(ligar):
    """Liga a TV LG por Wake-on-LAN (direto daqui, não depende do HA) ou desliga pelo HA."""
    if not ligar:
        _ha('/api/services/media_player/turn_off', {'entity_id': TV_ENTIDADE})
        return 'desligar'
    pacote = b'\xff' * 6 + bytes.fromhex(TV_MAC) * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for destino in ('192.168.4.255', '255.255.255.255'):
        s.sendto(pacote, (destino, 9))
    s.close()
    return 'ligar'


TV_ENTRADAS = ['Live TV', 'HDMI 1', 'HDMI 2']
TV_APPS = ['Jellyfin', 'YouTube']
TV_BOTOES = {'UP', 'DOWN', 'LEFT', 'RIGHT', 'ENTER', 'BACK', 'HOME'}
TV_SERVICOS = {'mais': 'volume_up', 'menos': 'volume_down'}


def tv_estado():
    e = _ha('/api/states/' + TV_ENTIDADE)
    a = e.get('attributes', {})
    vol = a.get('volume_level')
    lista = a.get('source_list') or []
    return {'estado': e.get('state'), 'entrada': a.get('source'),
            'volume': round(vol * 100) if vol is not None else None, 'mudo': a.get('is_volume_muted'),
            'apps': [x for x in TV_APPS if not lista or x in lista],
            'entradas': [x for x in TV_ENTRADAS if not lista or x in lista] + [x for x in lista if x.startswith('HDMI') and x not in TV_ENTRADAS]}


def tv_controle(acao, valor=None):
    """Volume, mudo e entrada da TV pelo HA (integração webostv)."""
    alvo = {'entity_id': TV_ENTIDADE}
    if acao in TV_SERVICOS:
        _ha('/api/services/media_player/' + TV_SERVICOS[acao], alvo)
    elif acao == 'mudo':
        mudo = bool(_ha('/api/states/' + TV_ENTIDADE).get('attributes', {}).get('is_volume_muted'))
        _ha('/api/services/media_player/volume_mute', dict(alvo, is_volume_muted=not mudo))
    elif acao == 'botao' and valor in TV_BOTOES:
        _ha('/api/services/webostv/button', dict(alvo, button=valor))
    elif acao == 'entrada' and valor:
        _ha('/api/services/media_player/select_source', dict(alvo, source=str(valor)))
    else:
        raise ValueError('ação desconhecida')
    return acao


def spotify_top50():
    """Ícone do Spotify no Show (23/10/2026): o Echo Dot toca o Top 50 Brasil do Jellyfin pela skill "meu cinema"
    (alexa-jellyflix.js, MusicaTopIntent). A frase tem de ser igual ao exemplo da skill: "tocar" cai na busca."""
    texto = 'pede pro meu cinema toca o top cinquenta'
    _ha('/api/services/alexa_devices/send_text_command', {'device_id': _echo_device_id(), 'text_command': texto})
    return texto


# controles da música no Echo (23/10/2026; tela própria em 30/10): comando de texto para a Alexa, como se fosse falado —
# pausa/continua/próxima/anterior vão para a skill que está tocando (alexa-jellyflix); o media_play do HA deu HTTP 500
ECHO_CONTROLES = {'pausa': 'pausa', 'play': 'continua', 'proxima': 'próxima', 'anterior': 'anterior',
                  'inicio': 'começa de novo', 'parar': 'para'}
ARQ_FILAS = os.path.expanduser('~/.alexa-jellyflix-filas.json')
ECHO_MEDIA = 'media_player.echo_dot_de_rodrigo'
_echo_vol = {'nivel': None, 'quando': 0}      # último volume pedido: o HA demora a mostrar, e os toques vêm em sequência
_bibliotecas = {'nomes': [], 'quando': 0}


def echo_controle(acao):
    texto = ECHO_CONTROLES[acao]
    _ha('/api/services/alexa_devices/send_text_command', {'device_id': _echo_device_id(), 'text_command': texto})
    return texto


def _echo_nivel():
    """Volume do Echo de 0 a 10, ou None se o HA não souber (ele fica "unavailable" alguns minutos de vez em quando)."""
    if time.time() - _echo_vol['quando'] < 20:
        return _echo_vol['nivel']
    v = _ha('/api/states/' + ECHO_MEDIA).get('attributes', {}).get('volume_level')
    return None if v is None else int(round(v * 10))


def echo_volume(delta):
    atual = _echo_nivel()
    if atual is None:
        raise RuntimeError('volume do Echo indisponível no HA')
    nivel = max(0, min(10, atual + delta))
    texto = f'volume {nivel}'
    _ha('/api/services/alexa_devices/send_text_command', {'device_id': _echo_device_id(), 'text_command': texto})
    _echo_vol.update(nivel=nivel, quando=time.time())
    return texto


def echo_bibliotecas():
    if time.time() - _bibliotecas['quando'] > 300 or not _bibliotecas['nomes']:
        _bibliotecas['nomes'] = sorted(v['Name'] for v in _jf('/Library/VirtualFolders') if v.get('CollectionType') == 'music')
        _bibliotecas['quando'] = time.time()
    return _bibliotecas['nomes']


def echo_biblioteca(nome):
    if nome not in echo_bibliotecas():
        raise ValueError('biblioteca desconhecida')
    if nome.lower() == BIBLIOTECA_TOP.lower():
        return spotify_top50()
    texto = 'pede pro meu cinema toca ' + nome.lower()
    _ha('/api/services/alexa_devices/send_text_command', {'device_id': _echo_device_id(), 'text_command': texto})
    return texto


def echo_estado(leve=False):
    """O que a skill está tocando (fila em ~/.alexa-jellyflix-filas.json). leve=True (tela inicial) não consulta o HA."""
    try:
        filas = json.load(open(ARQ_FILAS, encoding='utf-8'))
    except (OSError, ValueError):
        filas = {}
    f = max(filas.values(), key=lambda x: x.get('quando', 0), default=None)
    e = {'estado': 'parado', 'faixa': '', 'artista': '', 'pos': 0, 'total': 0, 'rotulo': '', 'proximas': [], 'id': '', 'ms': None}
    if f and f.get('ids'):
        pos = max(0, min(f.get('pos', 0), len(f['ids']) - 1))
        faixa, _, artista = f['nomes'][pos].partition(' — ')
        e.update(estado=f.get('estado', 'parado'), faixa=faixa, artista=artista, pos=pos + 1, total=len(f['ids']), id=f['ids'][pos],
                 rotulo=re.sub(r'^(a pasta|o|a) ', '', f.get('rotulo', '')), proximas=f['nomes'][pos + 1:pos + 5])
    if leve:
        return e
    try:
        e['volume'] = _echo_nivel()
    except Exception:
        e['volume'] = None
    try:
        e['bibliotecas'] = echo_bibliotecas()
    except Exception:
        e['bibliotecas'] = []
    e['ajuste'] = _letra_ajuste()
    if f:   # por último: a consulta ao HA acima pode demorar e a tela conta o tempo a partir desta resposta
        e['ms'] = _echo_ms(f)
    return e


# letra em tempo real na tela de música (30/10/2026): 1º a letra do próprio Jellyfin (embutida na faixa, é a mesma
# gravação), depois LRCLIB. Fica em letras/<id>.json; "nada" é tentado de novo depois de 7 dias.
DIR_LETRAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'letras')
ARQ_AJUSTE = os.path.join(DIR_LETRAS, 'ajuste.json')
LETRA_NADA_DIAS = 7
LRCLIB_FOLGA_S = 5      # duração diferente disso é outra gravação: a letra vira texto, sem marcar a linha
AJUSTE_MAX = 10000


def _echo_ms(f):
    """Posição estimada da faixa em ms; None se a skill ainda não informou (fila de antes da letra)."""
    if f.get('estado') == 'tocando' and f.get('inicioMs'):
        return max(0, int(time.time() * 1000) - f['inicioMs'])
    if f.get('estado') == 'pausado':
        return f.get('parouMs')
    return None


def _letra_ajuste():
    try:
        return json.load(open(ARQ_AJUSTE, encoding='utf-8'))['ms']
    except (OSError, ValueError, KeyError):
        return 0


def _gravar_json(arq, dados):
    os.makedirs(DIR_LETRAS, exist_ok=True)
    tmp = f'{arq}.{threading.get_ident()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as s:
        json.dump(dados, s, ensure_ascii=False)
    os.replace(tmp, arq)


def echo_ajuste(delta):
    """Soma ao ajuste de sincronia, que vale para todas as faixas (a demora é a do Echo, não a de cada música)."""
    ms = max(-AJUSTE_MAX, min(AJUSTE_MAX, _letra_ajuste() + int(delta)))
    _gravar_json(ARQ_AJUSTE, {'ms': ms})
    return ms


def _letra_jellyfin(item):
    try:
        linhas = _jf(f'/Audio/{item}/Lyrics').get('Lyrics') or []
    except Exception:       # 404 = faixa sem letra
        return None
    if any(x.get('Start') for x in linhas):
        return {'tipo': 'sinc', 'fonte': 'jellyfin', 'linhas': [{'t': x['Start'] // 10000, 'x': x['Text'].strip()}
                                                                for x in linhas if x.get('Start') is not None and (x.get('Text') or '').strip()]}
    texto = [{'x': x['Text'].strip()} for x in linhas if (x.get('Text') or '').strip()]
    return {'tipo': 'texto', 'fonte': 'jellyfin', 'linhas': texto} if texto else None


def _lrc(texto):
    linhas = []
    for bruta in texto.splitlines():
        marcas = re.findall(r'\[(\d+):(\d+(?:\.\d+)?)\]', bruta)
        frase = re.sub(r'\[[^\]]*\]', '', bruta).strip()
        if frase:
            linhas += [{'t': int((int(m) * 60 + float(s)) * 1000), 'x': frase} for m, s in marcas]
    return sorted(linhas, key=lambda x: x['t'])


def _lrclib(nome, artista):
    """Resultados da busca, ou None se o site não responder (ele dá 503 com frequência, por isso as 3 tentativas)."""
    url = 'https://lrclib.net/api/search?' + urllib.parse.urlencode({'track_name': nome, 'artist_name': artista})
    for tentativa in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'xoom-show (github.com/RodrigoRosa95/xoom-echo-show)'})
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.load(r)
        except Exception:
            time.sleep(1 + tentativa)
    return None


def _letra_lrclib(item):
    """Letra do LRCLIB; None se não deu para consultar (não guarda, tenta de novo depois)."""
    i = _jf(f'/Items?Ids={item}')['Items'][0]
    artista = (i.get('Artists') or [''])[0].split(';')[0].strip()
    duracao = (i.get('RunTimeTicks') or 0) / 1e7
    nome = i.get('Name', '')
    limpo = re.sub(r'\s*[-(\[].*$', '', nome).strip()
    achou_texto, falhou = None, False
    for busca in dict.fromkeys(x for x in (nome, limpo) if x):
        res = _lrclib(busca, artista)
        if res is None:
            falhou = True
            continue
        sinc = [x for x in res if x.get('syncedLyrics') and abs((x.get('duration') or 0) - duracao) <= LRCLIB_FOLGA_S]
        if sinc:
            melhor = min(sinc, key=lambda x: abs(x['duration'] - duracao))
            return {'tipo': 'sinc', 'fonte': 'lrclib', 'linhas': _lrc(melhor['syncedLyrics'])}
        for x in res:
            if not achou_texto and (x.get('plainLyrics') or '').strip():
                achou_texto = [{'x': l.strip()} for l in x['plainLyrics'].splitlines() if l.strip()]
    if achou_texto:
        return {'tipo': 'texto', 'fonte': 'lrclib', 'linhas': achou_texto}
    return None if falhou else {'tipo': 'nada', 'linhas': []}


def echo_letra(item):
    arq = os.path.join(DIR_LETRAS, item + '.json')
    try:
        d = json.load(open(arq, encoding='utf-8'))
        if d['tipo'] != 'nada' or time.time() - os.path.getmtime(arq) < LETRA_NADA_DIAS * 86400:
            return d
    except (OSError, ValueError, KeyError):
        pass
    d = _letra_jellyfin(item) or _letra_lrclib(item)
    if d is None:
        return {'tipo': 'nada', 'linhas': [], 'tentar': True}
    _gravar_json(arq, d)
    return d


# Top 50 tocando no próprio Xoom (23/10/2026): o ícone do Spotify toca a biblioteca "Top 50 Brasil" do Jellyfin num
# <audio> da própria tela do Show (sem sair dela). A chave do Jellyfin fica aqui: a página só vê /show/musica/<id>.mp3
JELLYFIN = 'http://127.0.0.1:8096'
BIBLIOTECA_TOP = 'Top 50 Brasil'
_jf_usuario = []


def _jf_chave():
    for linha in open(os.path.expanduser('~/.env'), encoding='utf-8'):
        if linha.startswith('JELLYFIN_API_KEY='):
            return linha.split('=', 1)[1].strip().strip('"\'')


def _jf(caminho):
    req = urllib.request.Request(JELLYFIN + caminho, headers={'X-Emby-Token': _jf_chave()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _jf_admin():
    if not _jf_usuario:
        _jf_usuario.append(next(u['Id'] for u in _jf('/Users') if (u.get('Policy') or {}).get('IsAdministrator')))
    return _jf_usuario[0]


def musica_lista():
    """Faixas do Top 50 Brasil na ordem da parada (igual ao MusicaTopIntent do alexa-jellyflix)."""
    lib = next(v for v in _jf('/Library/VirtualFolders') if v['Name'].strip().lower() == BIBLIOTECA_TOP.lower())
    r = _jf(f"/Items?ParentId={lib['ItemId']}&Recursive=true&IncludeItemTypes=Audio&SortBy=IndexNumber,SortName&Limit=300")
    return [{'id': i['Id'], 'nome': i['Name'], 'artista': (i.get('Artists') or [''])[0]} for i in r.get('Items') or []]


def musica_abrir(item_id, faixa_bytes=None):
    """Stream do Jellyfin convertido para MP3 (o WebView do Android 4.4 toca MP3 sem susto)."""
    u = (f'{JELLYFIN}/Audio/{item_id}/universal?UserId={_jf_admin()}&DeviceId=xoom-show&Container=mp3'
         '&TranscodingContainer=mp3&TranscodingProtocol=http&AudioCodec=mp3&MaxStreamingBitrate=192000')
    cab = {'X-Emby-Token': _jf_chave()}
    if faixa_bytes:
        cab['Range'] = faixa_bytes
    return urllib.request.urlopen(urllib.request.Request(u, headers=cab), timeout=60)


def voz():
    try:
        with open(ARQ_VOZ, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'estado': 'parado'}


# ---------- quadro de cartões (estreias do Sonarr/Radarr) ----------
import datetime as _dt
import re as _re

ARR = {'sonarr': ('http://127.0.0.1:8989', '/var/lib/sonarr/config.xml'),
       'radarr': ('http://127.0.0.1:7878', '/var/lib/radarr/config.xml')}
PASTA_CAPAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'capas')
_quadro = {'cartoes': [], 'quando': 0}
SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']


def _arr(app, caminho, timeout=600):
    base, cfg = ARR[app]
    chave = _re.search(r'<ApiKey>([^<]+)', open(cfg).read()).group(1)
    req = urllib.request.Request(base + caminho, headers={'X-Api-Key': chave})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _quando_txt(d, com_hora):
    """'hoje 22:00', 'amanhã', 'sáb 25/10' (hora local)."""
    hoje = _dt.date.today()
    dia = d.date()
    if dia == hoje:
        txt = 'hoje'
    elif dia == hoje + _dt.timedelta(days=1):
        txt = 'amanhã'
    elif (dia - hoje).days < 7:
        txt = SEMANA[dia.weekday()]
    else:
        txt = f'{SEMANA[dia.weekday()]} {dia.day:02}/{dia.month:02}'
    return f'{txt} {d.hour:02}:{d.minute:02}' if com_hora else txt


def _estreias():
    cartoes = []
    hoje = _dt.date.today()
    agora = _dt.datetime.now().astimezone()
    try:
        eps = json.loads(_arr('sonarr', f'/api/v3/calendar?start={hoje}&end={hoje + _dt.timedelta(days=21)}'
                                        '&includeSeries=true&unmonitored=false'))
        vistos = set()
        for e in sorted(eps, key=lambda e: e.get('airDateUtc') or ''):
            if not e.get('airDateUtc') or e.get('hasFile'):
                continue
            d = _dt.datetime.fromisoformat(e['airDateUtc'].replace('Z', '+00:00')).astimezone()
            if d < agora - _dt.timedelta(hours=3):
                continue
            s = e.get('series') or {}
            chave = s.get('id')                  # só o próximo episódio de cada série
            if chave in vistos:
                continue
            vistos.add(chave)
            cartoes.append({'grupo': 'Estreia · série', 'titulo': s.get('title', '?'),
                            'linha': f"T{e['seasonNumber']} E{e['episodeNumber']}" + (f" · {e['title']}" if e.get('title') and e['title'] != 'TBA' else ''),
                            'quando': _quando_txt(d, True), 'ordem': d.isoformat(),
                            'capa': f"/show/capa/sonarr/{s.get('id')}" if s.get('id') else ''})
    except Exception as ex:
        print('quadro: sonarr', ex, flush=True)
    try:
        filmes = json.loads(_arr('radarr', f'/api/v3/calendar?start={hoje}&end={hoje + _dt.timedelta(days=90)}'
                                           '&unmonitored=false'))
        nomes = {'digitalRelease': 'digital', 'physicalRelease': 'Blu-ray', 'inCinemas': 'cinema'}
        for m in filmes:
            if m.get('hasFile'):
                continue
            datas = []
            for campo, nome in nomes.items():
                if m.get(campo):
                    d = _dt.datetime.fromisoformat(m[campo].replace('Z', '+00:00')).astimezone()
                    if d.date() >= hoje:
                        datas.append((d, nome))
            if not datas:
                continue
            d, nome = min(datas)
            cartoes.append({'grupo': 'Estreia · filme', 'titulo': m.get('title', '?'),
                            'linha': f"{m.get('year', '')} · estreia {nome}".strip(' ·'),
                            'quando': _quando_txt(d, False), 'ordem': d.isoformat(),
                            'capa': f"/show/capa/radarr/{m['id']}"})
    except Exception as ex:
        print('quadro: radarr', ex, flush=True)
    cartoes.sort(key=lambda c: c['ordem'])
    return cartoes[:20]


def _atualizar_quadro():
    while True:
        c = _estreias()
        if c or not _quadro['cartoes']:
            _quadro.update(cartoes=c, quando=time.time())
        time.sleep(900)


threading.Thread(target=_atualizar_quadro, daemon=True).start()


def quadro():
    return {'cartoes': _quadro['cartoes'], 'quando': _quadro['quando']}


def capa(app, ident):
    """Pôster pequeno do Sonarr/Radarr (guardado em capas/ por 7 dias). None se não houver."""
    if app not in ARR or not str(ident).isdigit():
        return None
    os.makedirs(PASTA_CAPAS, exist_ok=True)
    arq = os.path.join(PASTA_CAPAS, f'{app}-{ident}.jpg')
    if os.path.exists(arq) and time.time() - os.path.getmtime(arq) < 7 * 86400:
        return arq
    try:
        dados = _arr(app, f'/api/v3/mediacover/{ident}/poster-250.jpg', timeout=60)
    except Exception:
        return arq if os.path.exists(arq) else None
    with open(arq, 'wb') as f:
        f.write(dados)
    return arq
