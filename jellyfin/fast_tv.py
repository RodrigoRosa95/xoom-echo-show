#!/usr/bin/env python3
"""Canais gratuitos e legais para o Live TV do Jellyfin:

  /pluto.m3u     Pluto TV Brasil (~180 canais), pela API oficial. O link de cada canal leva um token de sessão
                 que expira: a lista aponta para /pluto/<id>.m3u8 aqui, que redireciona (302) para o link com o
                 token da vez — o ffmpeg do Jellyfin segue o redirecionamento.
  /pluto.xml     guia do Pluto Brasil (i.mjh.nz, ids = ids dos canais do Pluto), em cache.
  /abertos.m3u   canais das listas do iptv-org (BR, idioma português, Portugal) e do Free-TV/IPTV (Brasil e
                 Portugal) que são abertos/gratuitos e com link da própria emissora ou de plataforma FAST: fora os
                 pagos (NAO_LIVRES), os de outro idioma (OUTRO_IDIOMA), os links por IP solto (retransmissão) e os
                 do Pluto (já vêm acima). Só entra o que respondeu no último teste (a cada TESTE_HORAS).
  /plex.m3u      Plex TV ao vivo (grátis, lineup Brasil) pela API oficial com usuário anônimo: canais em português
                 + música/natureza (PLEX_SEM_IDIOMA). /plex/<id>.m3u8 redireciona com o token da vez.
  /plex.xml      guia da Plex (grid oficial, em português), em cache.

Samsung TV Plus não tem versão Brasil; LG Channels Brasil só entrega os links pelo app da TV (a API web devolve
só a grade) — boa parte dos canais dele é do Pluto ou está no iptv-org com o link oficial.
Uso: fast_tv.py (servidor, pm2 "fast-tv"). Log: fast-tv.log.
"""
import concurrent.futures
import gzip
import http.server
import json
import os
import re
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PORTA = 8097
AQUI = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(AQUI, 'fast-tv.log')
ESTADO = os.path.join(AQUI, 'fast-tv.json')        # clientID fixo do Pluto + última lista de abertos testada
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'

PLUTO_BOOT = 'https://boot.pluto.tv/v4/start'
PLUTO_GUIA = 'https://i.mjh.nz/PlutoTV/br.xml.gz'
PLUTO_CANAIS_HORAS = 6
GUIA_HORAS = 3
IPTV_ORG = 'https://iptv-org.github.io/iptv/countries/br.m3u'
# 13/10 (pedido do usuário: mais canais grátis de várias fontes): listas lidas para os abertos, (url, grupos aceitos)
FONTES_ABERTOS = [(IPTV_ORG, None),
                  ('https://iptv-org.github.io/iptv/languages/por.m3u', None),
                  ('https://iptv-org.github.io/iptv/countries/pt.m3u', None),
                  ('https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8', {'Brazil', 'Portugal'})]
OUTRO_IDIOMA = re.compile(r'^(Mana (Church|Cirkev|Eglise|Iglesia|Tserkov)|TV Mana (English|Espanhol|Russkiy)|XITE|'
                          r'Trailers UK|EbS|Trace Urban)', re.I)
PLEX_LOGIN = 'https://clients.plex.tv/api/v2/users/anonymous'
PLEX_EPG = 'https://epg.provider.plex.tv'
PLEX_CAB = {'Accept': 'application/json', 'X-Plex-Product': 'Plex Web', 'X-Plex-Version': '4.141.0',
            'X-Plex-Platform': 'Chrome', 'X-Plex-Client-Identifier': 'fast-tv-rodrigo'}
PLEX_SEM_IDIOMA = {'Alternative', 'Classic Rock', 'Easy Listening', 'Euro Hits', 'Flashback 70s', 'Hip-Hop/R&B',
                   'Metal.Rocks', 'Nature Moments', "Nothin' But 90s", 'Pop Adult', 'Qwest TV', 'Remember the ‘80s',
                   'Smooth Jazz', 'Stingray Greatest Hits', 'Stingray Holidayscapes', 'Stingray Hot Country',
                   'Stingray Soul Storm', 'Stingray Naturescape', 'The Spa', "Today's K-Pop", 'WildEarth', 'Y2K',
                   'ZenLIFE by Stingray', 'Stingray Karaoke'}
NUM_PLEX = 4000
PLEX_GUIA_DIAS = 2
TESTE_HORAS = 6
NUM_PLUTO = 2000                                    # tvg-chno = base + número do canal
NUM_ABERTOS = 5000                                  # 14/10: era 3000, mas o Pluto já passa de 3500 (2000 + nº até 1555)

# Canais pagos (ou de streaming pago) que aparecem na lista do iptv-org: não entram.
NAO_LIVRES = re.compile(r'^(' + '|'.join(re.escape(n) for n in (
    'A&E', 'AMC', 'Adult Swim', 'AXN', 'Band Sports', 'BandSports', 'BandNews', 'Cartoon', 'Combate',
    'Comedy Central Brasil', 'Discovery', 'ESPN', 'GloboNews', 'Globoplay', 'GNT', 'Gloob', 'History', 'Lifetime',
    'Nickelodeon', 'NickOnline', 'Sony', 'SporTV', 'Studio Universal', 'Telecine', 'TLC', 'TNT', 'Music Box',
    'Travel Box', 'Box Kids', 'Chef TV', 'Canal Off', 'Arte 1', 'Futura', 'Canal Futura', 'Fish TV', 'MCM Pop',
    'Premiere', 'HBO', 'Warner', 'Universal TV', 'Megapix', 'Viva', 'Multishow', 'Canal Brasil', 'Rede Globo',
    'TV Globo', 'TV Series', 'Series Classic', 'TV Ra Tim Bum', 'Primer TV', 'Woohoo', 'TCM', 'Paramount',
    'Space', 'Star', 'Cinemax', 'Disney', 'FX', 'Fox', 'STZ TV', 'TV Bahia', 'CNN Portugal', 'TVI Ficcao',
    'TVI Ficção', 'TVI Reality', 'V+ TVI')) + r')(?!\w)', re.I)
IP_SOLTO = re.compile(r'^https?://\d+\.\d+\.\d+\.\d+')

trava = threading.Lock()
estado = {}
plex = {'canais': [], 'canais_quando': 0, 'variantes': {}}
pluto = {'sessao': None, 'quando': 0, 'validade': 0, 'canais': [], 'canais_quando': 0}
cache = {}                                           # caminho → (hora, bytes)


def log(msg):
    linha = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}'
    print(linha, flush=True)
    with open(LOG, 'a') as f:
        f.write(linha + '\n')


def salvar_estado():
    tmp = ESTADO + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(estado, f, ensure_ascii=False)
    os.replace(tmp, ESTADO)


def baixar(url, timeout=20, cab=None):
    r = urllib.request.Request(url, headers=dict({'User-Agent': UA}, **(cab or {})))
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        dados = resp.read()
    return gzip.decompress(dados) if dados[:2] == b'\x1f\x8b' else dados


# ---------- Pluto TV ----------

def sessao_pluto():
    """Sessão do Pluto (token + parâmetros do stitcher), renovada na metade da validade informada pela API."""
    with trava:
        s = pluto['sessao']
        if s and time.time() - pluto['quando'] < pluto['validade'] / 2:
            return s
    q = urllib.parse.urlencode({'appName': 'web', 'appVersion': '9.10.0', 'deviceVersion': '140.0.0',
                                'deviceModel': 'web', 'deviceMake': 'chrome', 'deviceType': 'web',
                                'clientID': estado['pluto_cliente'], 'clientModelNumber': '1.0.0',
                                'serverSideAds': 'false'})
    s = json.loads(baixar(f'{PLUTO_BOOT}?{q}'))
    with trava:
        pluto.update(sessao=s, quando=time.time(), validade=max(600, int(s.get('refreshInSec') or 3600)))
    return s


def canais_pluto():
    with trava:
        if pluto['canais'] and time.time() - pluto['canais_quando'] < PLUTO_CANAIS_HORAS * 3600:
            return pluto['canais']
    s = sessao_pluto()
    url = s['servers']['channels'] + '/v2/guide/channels?channelIds=&offset=0&limit=1000&sort=number:asc'
    dados = json.loads(baixar(url, cab={'Authorization': 'Bearer ' + s['sessionToken']}))['data']
    canais = []
    for c in dados:
        logo = next((i['url'] for i in c.get('images', []) if i.get('type') == 'colorLogoPNG'), '')
        canais.append({'id': c['id'], 'nome': c['name'], 'numero': c.get('number') or 0, 'logo': logo,
                       'grupo': c.get('category') or 'Pluto TV'})
    with trava:
        pluto.update(canais=canais, canais_quando=time.time())
    log(f'📺 Pluto TV: {len(canais)} canais')
    return canais


def link_pluto(canal_id):
    s = sessao_pluto()
    return (f"{s['servers']['stitcher']}/v2/stitch/hls/channel/{canal_id}/master.m3u8?"
            f"{s['stitcherParams']}&jwt={s['sessionToken']}&masterJWTPassthrough=true")


def lista_pluto():
    base = f'http://127.0.0.1:{PORTA}'
    linhas = ['#EXTM3U']
    for c in canais_pluto():
        linhas.append(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-chno="{NUM_PLUTO + c["numero"]}" tvg-name="{c["nome"]}" '
                      f'tvg-logo="{c["logo"]}" group-title="Pluto TV",{c["nome"]}')
        linhas.append(f'{base}/pluto/{c["id"]}.m3u8')
    return ('\n'.join(linhas) + '\n').encode()


def guia_pluto():
    with trava:
        item = cache.get('pluto.xml')
    if item and time.time() - item[0] < GUIA_HORAS * 3600:
        return item[1]
    try:
        dados = baixar(PLUTO_GUIA, timeout=60)
    except OSError as e:
        if item:                                     # i.mjh.nz fora: segue com o guia anterior
            log(f'⚠️ guia do Pluto: {e} — mantendo o anterior')
            return item[1]
        raise
    dados = numerar_episodios(dados)
    with trava:
        cache['pluto.xml'] = (time.time(), dados)
    return dados


def numerar_episodios(dados):
    """O guia do i.mjh.nz traz o episódio só como onscreen "S06E06", que o Jellyfin não lê (o programa fica sem
    temporada/episódio e a gravação de série sai sem SxxEyy). Troca pelo xmltv_ns, que começa do zero."""
    return re.sub(rb'<episode-num system="onscreen">S(\d+)E(\d+)</episode-num>',
                  lambda m: b'<episode-num system="xmltv_ns">%d.%d.</episode-num>' % (int(m[1]) - 1, int(m[2]) - 1),
                  dados)


# ---------- Plex (TV ao vivo grátis) ----------

def token_plex(novo=False):
    with trava:
        if estado.get('plex_token') and not novo:
            return estado['plex_token']
    r = urllib.request.Request(PLEX_LOGIN, data=b'', method='POST', headers=dict({'User-Agent': UA}, **PLEX_CAB))
    with urllib.request.urlopen(r, timeout=20) as resp:
        tok = json.load(resp)['authToken']
    with trava:
        estado['plex_token'] = tok
    salvar_estado()
    return tok


def api_plex(caminho):
    for tentativa in (0, 1):
        try:
            return json.loads(baixar(PLEX_EPG + caminho, timeout=30,
                                     cab=dict(PLEX_CAB, **{'X-Plex-Token': token_plex(novo=tentativa == 1)})))
        except urllib.error.HTTPError as e:
            if e.code != 401 or tentativa:
                raise


def canais_plex():
    with trava:
        if plex['canais'] and time.time() - plex['canais_quando'] < PLUTO_CANAIS_HORAS * 3600:
            return plex['canais']
    canais = []
    for c in api_plex('/lineups/plex/channels')['MediaContainer'].get('Channel', []):
        if c.get('language') != 'pt' and c['title'] not in PLEX_SEM_IDIOMA:
            continue
        midia = next((m for m in c.get('Media', []) if not m.get('drm')), None)
        if not midia:
            continue
        canais.append({'id': c['gridKey'], 'nome': c['title'], 'logo': c.get('thumb', ''),
                       'parte': midia['Part'][0]['key'], 'grupo': 'Plex' if c.get('language') == 'pt' else 'Plex Música'})
    canais.sort(key=lambda c: (c['grupo'], c['nome'].lower()))
    with trava:
        plex.update(canais=canais, canais_quando=time.time())
    log(f'📺 Plex: {len(canais)} canais')
    return canais


def link_plex(grid):
    c = next((c for c in canais_plex() if c['id'] == grid), None)
    if not c:
        raise ValueError('canal da Plex não encontrado')
    q = urllib.parse.urlencode({'X-Plex-Token': token_plex(), 'X-Plex-Client-Identifier': PLEX_CAB['X-Plex-Client-Identifier'],
                                'X-Plex-Product': 'Plex Web', 'X-Plex-Platform': 'Chrome'})
    return f"{PLEX_EPG}{c['parte']}?{q}"


def playlist_plex(grid):
    """Playlist de mídia do canal, limpa (14/10): a Plex/Xumo põe no lugar de alguns pedaços um link de rastreamento
    de anúncio (hls-beacons…/beacon?url=<pedaço de verdade>) sem extensão .ts — o ffmpeg do Jellyfin recusa
    ("Invalid data", gravação do Z Nation morria em 4 s/65 s). Troca pelo pedaço real e deixa os links absolutos.
    A variante (com o id da sessão de reprodução) fica guardada 30 min; se falhar, abre outra."""
    for tentativa in (0, 1):
        with trava:
            v = plex['variantes'].get(grid)
        if tentativa or not v or time.time() - v[0] > 1800:
            mestre = link_plex(grid)
            corpo = baixar(mestre, timeout=20).decode('utf-8', 'replace')
            url = next((urllib.parse.urljoin(mestre, l.strip()) for l in corpo.splitlines() if l.strip() and not l.startswith('#')), None)
            if not url:
                raise ValueError('playlist mestre da Plex sem variante')
            v = (time.time(), url)
            with trava:
                plex['variantes'][grid] = v
        try:
            corpo = baixar(v[1], timeout=20).decode('utf-8', 'replace')
            break
        except urllib.error.HTTPError:
            if tentativa:
                raise
    linhas = []
    for l in corpo.splitlines():
        l = l.strip()
        if l and not l.startswith('#'):
            l = urllib.parse.urljoin(v[1], l)
            if '/beacon?' in l:
                real = urllib.parse.parse_qs(urllib.parse.urlsplit(l).query).get('url')
                l = real[0] if real else l
        linhas.append(l)
    return ('\n'.join(linhas) + '\n').encode()


def lista_plex():
    base = f'http://127.0.0.1:{PORTA}'
    linhas = ['#EXTM3U']
    for i, c in enumerate(canais_plex(), 1):
        linhas.append(f'#EXTINF:-1 tvg-id="plex-{c["id"]}" tvg-chno="{NUM_PLEX + i}" tvg-name="{c["nome"]}" '
                      f'tvg-logo="{c["logo"]}" group-title="{c["grupo"]}",{c["nome"]}')
        linhas.append(f'{base}/plex/{c["id"]}.m3u8')
    return ('\n'.join(linhas) + '\n').encode()


def guia_plex():
    with trava:
        item = cache.get('plex.xml')
    if item and time.time() - item[0] < GUIA_HORAS * 3600:
        return item[1]
    from xml.sax.saxutils import escape, quoteattr
    hora = lambda t: time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(t))
    canais = canais_plex()
    saida = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="fast-tv plex">']
    for c in canais:
        saida.append(f'<channel id="plex-{c["id"]}"><display-name>{escape(c["nome"])}</display-name>'
                     f'<icon src={quoteattr(c["logo"])}/></channel>')

    def grade(c):
        progs = []
        for d in range(PLEX_GUIA_DIAS):
            dia = time.strftime('%Y-%m-%d', time.localtime(time.time() + d * 86400))
            try:
                progs += api_plex(f'/grid?channelGridKey={c["id"]}&date={dia}')['MediaContainer'].get('Metadata', [])
            except Exception as e:
                log(f'⚠️ guia Plex {c["nome"]} {dia}: {e}')
        return c, progs
    vistos = set()
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for c, progs in ex.map(grade, canais):
            for p in progs:
                for m in p.get('Media', []):
                    if not m.get('beginsAt') or (c['id'], m['beginsAt']) in vistos:
                        continue
                    vistos.add((c['id'], m['beginsAt']))
                    serie = p.get('grandparentTitle')
                    titulo = serie or p.get('title', '')
                    x = [f'<programme start="{hora(m["beginsAt"])}" stop="{hora(m["endsAt"])}" channel="plex-{c["id"]}">',
                         f'<title lang="pt">{escape(titulo)}</title>']
                    if serie and p.get('title'):
                        x.append(f'<sub-title lang="pt">{escape(p["title"])}</sub-title>')
                    if p.get('summary'):
                        x.append(f'<desc lang="pt">{escape(p["summary"])}</desc>')
                    x += [f'<category lang="pt">{escape(g["tag"])}</category>' for g in p.get('Genre', [])]
                    x.append('<category lang="en">Movie</category>' if p.get('type') == 'movie' else '')
                    if p.get('thumb'):
                        x.append(f'<icon src={quoteattr(p["thumb"])}/>')
                    if p.get('type') == 'episode' and p.get('index'):
                        x.append(f'<episode-num system="xmltv_ns">{max(0, (p.get("parentIndex") or 1) - 1)}.{p["index"] - 1}.</episode-num>')
                    if p.get('year'):
                        x.append(f'<date>{p["year"]}</date>')
                    if p.get('contentRating'):
                        x.append(f'<rating><value>{escape(p["contentRating"])}</value></rating>')
                    saida.append(''.join(x) + '</programme>')
    saida.append('</tv>')
    dados = '\n'.join(saida).encode()
    with trava:
        cache['plex.xml'] = (time.time(), dados)
    log(f'🗓️ guia Plex: {len(vistos)} programas em {len(canais)} canais')
    return dados


# ---------- canais abertos (iptv-org) ----------

def candidatos_abertos():
    canais = []
    for fonte, grupos in FONTES_ABERTOS:
        try:
            canais += candidatos_de(fonte, grupos)
        except OSError as e:
            if fonte == IPTV_ORG:
                raise
            log(f'⚠️ lista {fonte}: {e}')
    return canais


def candidatos_de(fonte, grupos):
    texto = baixar(fonte, timeout=60).decode('utf-8', 'replace')
    canais = []
    for bloco in texto.split('#EXTINF')[1:]:
        linhas = bloco.strip().splitlines()
        info = linhas[0]
        url = next((l.strip() for l in linhas[1:] if l.startswith('http')), None)
        if not url:
            continue
        nome = info.rsplit(',', 1)[-1].strip()
        nome_limpo = re.sub(r'\s*\((\d+[pi]|[^)]*)\)|\s*\[[^\]]*\]|\s*Ⓢ', '', nome).strip()
        attr = dict(re.findall(r'([\w-]+)="([^"]*)"', info))
        if ('pluto' in url or 'jmp2.uk' in url or 'bozztv' in url or 'Pluto TV' in nome or IP_SOLTO.match(url)
                or 'thomraider12' in url                 # links de terceiros "renovados" no GitHub, não da emissora
                or NAO_LIVRES.match(nome_limpo) or OUTRO_IDIOMA.match(nome_limpo)
                or (grupos and attr.get('group-title') not in grupos)):
            continue
        opcoes = {k: v for k, v in (re.findall(r'#EXTVLCOPT:http-(referrer|user-agent)=(.*)', bloco))}
        canais.append({'nome': nome_limpo, 'url': url, 'logo': attr.get('tvg-logo', ''),
                       'id': attr.get('tvg-id', ''), 'opcoes': opcoes})
    return canais


def no_ar(c):
    cab = {}
    if c['opcoes'].get('referrer'):
        cab['Referer'] = c['opcoes']['referrer']
    if c['opcoes'].get('user-agent'):
        cab['User-Agent'] = c['opcoes']['user-agent']
    try:
        corpo = baixar(c['url'], timeout=12, cab=cab)[:4096]
        return corpo.lstrip().startswith(b'#EXTM3U')
    except Exception:
        return False


def testar_abertos():
    try:
        candidatos = candidatos_abertos()
    except OSError as e:
        log(f'⚠️ lista do iptv-org: {e}')
        return
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        vivos = [c for c, ok in zip(candidatos, ex.map(no_ar, candidatos)) if ok]
    vistos, unicos = set(), []
    for c in vivos:                                  # mesmo canal (nome ou link) em mais de uma lista: fica o 1º
        if c['nome'].lower() not in vistos and c['url'] not in vistos:
            vistos.update((c['nome'].lower(), c['url']))
            unicos.append(c)
    with trava:
        estado['abertos'] = unicos
        estado['abertos_quando'] = time.time()
    salvar_estado()
    log(f'✅ abertos: {len(unicos)} no ar de {len(candidatos)} candidatos — ' +
        ', '.join(c['nome'] for c in unicos[:40]) + ('…' if len(unicos) > 40 else ''))


def lista_abertos():
    linhas = ['#EXTM3U']
    for i, c in enumerate(sorted(estado.get('abertos', []), key=lambda c: c['nome'].lower()), 1):
        linhas.append(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-chno="{NUM_ABERTOS + i}" tvg-name="{c["nome"]}" '
                      f'tvg-logo="{c["logo"]}" group-title="Abertos",{c["nome"]}')
        for k, v in c['opcoes'].items():
            linhas.append(f'#EXTVLCOPT:http-{k}={v}')
        linhas.append(c['url'])
    return ('\n'.join(linhas) + '\n').encode()


def ciclo_testes():
    while True:
        falta = TESTE_HORAS * 3600 - (time.time() - estado.get('abertos_quando', 0))
        if falta > 0:
            time.sleep(min(falta, 3600))
            continue
        testar_abertos()


# ---------- servidor ----------

class Servidor(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Pedido(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, corpo, tipo, codigo=200):
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(corpo)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        caminho = urllib.parse.urlparse(self.path).path
        try:
            m = re.match(r'^/pluto/([0-9a-f]{24})\.m3u8$', caminho)
            if m:
                self.send_response(302)
                self.send_header('Location', link_pluto(m.group(1)))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            m = re.match(r'^/plex/([0-9a-f]{24})\.m3u8$', caminho)
            if m:
                return self.responder(playlist_plex(m.group(1)), 'application/vnd.apple.mpegurl')
            if caminho == '/plex.m3u':
                return self.responder(lista_plex(), 'audio/x-mpegurl')
            if caminho == '/plex.xml':
                return self.responder(guia_plex(), 'application/xml')
            if caminho == '/pluto.m3u':
                return self.responder(lista_pluto(), 'audio/x-mpegurl')
            if caminho == '/pluto.xml':
                return self.responder(guia_pluto(), 'application/xml')
            if caminho == '/abertos.xml':                # arquivo opcional, gerado à parte
                with open(os.path.join(AQUI, 'abertos.xml'), 'rb') as f:
                    return self.responder(f.read(), 'application/xml')
            if caminho == '/abertos.m3u':
                return self.responder(lista_abertos(), 'audio/x-mpegurl')
            self.responder(b'nao encontrado', 'text/plain', 404)
        except Exception as e:
            log(f'⚠️ {self.path[:80]}: {e}')
            self.responder(str(e).encode()[:300], 'text/plain', 502)


def main():
    global estado
    try:
        with open(ESTADO) as f:
            estado = json.load(f)
    except (OSError, ValueError):
        estado = {}
    if not estado.get('pluto_cliente'):
        estado['pluto_cliente'] = str(uuid.uuid4())
        salvar_estado()
    try:
        canais_pluto()
    except Exception as e:
        log(f'⚠️ Pluto TV: {e}')
    threading.Thread(target=ciclo_testes, daemon=True).start()
    log(f'🚀 fast-tv na porta {PORTA}')
    Servidor(('127.0.0.1', PORTA), Pedido).serve_forever()


if __name__ == '__main__':
    main()
