"""YouTube no Xoom pelo notebook (22/10/2026): o Chrome 42 / apps do 4.4 não abrem mais o YouTube.

Página simples (HTML sem JavaScript moderno) em /yt: busca e "Em alta" pelo yt-dlp; miniaturas passam pelo
notebook (/yt/miniatura/<id>). Tocar abre o MX Player com /yt/video.ts?v=<id>&q=480: o yt-dlp acha o H.264
(avc1) + áudio m4a do YouTube e o ffmpeg só reempacota em MPEG-TS (sem recodificar; áudio vira AAC estéreo).
Se o vídeo não tiver avc1 na altura pedida, converte para H.264. ?t=<s> começa em t segundos.
"""
import html
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

YTDLP = '/home/rodrigo/.local/bin/yt-dlp'
JS = ['--js-runtimes', 'node:/opt/node/bin/node']
EM_ALTA = 'https://www.youtube.com/playlist?list=PL4fGSI1pDJn4Gs2meaJRo9O8PNYvhjHIg'  # Top 100 Music Videos Brazil
BASE = 'http://192.168.4.135:8796'
QUALIDADES = (360, 480, 720)
ID_OK = re.compile(r'^[\w-]{11}$')

_cache = {}          # chave → (validade, valor)
_trava = threading.Lock()


def _guardado(chave, segundos, gerar):
    agora = time.time()
    with _trava:
        if chave in _cache and _cache[chave][0] > agora:
            return _cache[chave][1]
    valor = gerar()
    with _trava:
        _cache[chave] = (agora + segundos, valor)
    return valor


def _lista(alvo, n):
    r = subprocess.run([YTDLP, *JS, '--flat-playlist', '-J', '--playlist-end', str(n), alvo],
                       capture_output=True, text=True, timeout=90)
    itens = []
    for e in (json.loads(r.stdout or '{}').get('entries') or []):
        if e.get('id') and ID_OK.match(e['id']) and e.get('duration'):  # sem duração = ao vivo/shorts estranhos
            itens.append({'id': e['id'], 'titulo': e.get('title') or '', 'canal': e.get('channel') or e.get('uploader') or '',
                          'duracao': int(e.get('duration') or 0), 'views': e.get('view_count')})
    return itens


def buscar(termo, n=20):
    return _guardado(('busca', termo.lower(), n), 1800, lambda: _lista(f'ytsearch{n}:{termo}', n))


def em_alta(n=30):
    return _guardado(('alta', n), 3600, lambda: _lista(EM_ALTA, n))


def miniatura(vid):
    def baixar():
        for nome in ('mqdefault.jpg', 'hqdefault.jpg', 'default.jpg'):
            try:
                with urllib.request.urlopen(f'https://i.ytimg.com/vi/{vid}/{nome}', timeout=15) as r:
                    return r.read()
            except Exception:
                continue
        return b''
    return _guardado(('mini', vid), 86400, baixar)


def _urls(vid, altura):
    """(urls, recodificar?) — prefere avc1 + m4a na altura pedida; senão o melhor até essa altura (recodifica)."""
    def achar():
        url = f'https://www.youtube.com/watch?v={vid}'
        for fmt, recod in ((f'bv*[height<={altura}][vcodec^=avc1]+ba[ext=m4a]/b[height<={altura}][vcodec^=avc1]', False),
                           (f'bv*[height<={altura}]+ba/b[height<={altura}]/b', True)):
            r = subprocess.run([YTDLP, *JS, '-f', fmt, '-g', url], capture_output=True, text=True, timeout=60)
            urls = [u for u in r.stdout.split('\n') if u.startswith('http')]
            if urls:
                return urls, recod
        return [], False
    return _guardado(('urls', vid, altura), 3 * 3600, achar)  # links do YouTube valem ~6 h


def transmitir(vid, altura=480, inicio=0):
    """Gerador MPEG-TS; o ffmpeg morre quando o player desconecta."""
    urls, recod = _urls(vid, altura)
    if not urls:
        return
    entradas = []
    for u in urls:
        entradas += (['-ss', str(inicio)] if inicio else []) + ['-reconnect', '1', '-reconnect_streamed', '1',
                                                                 '-reconnect_delay_max', '5', '-i', u]
    mapa = ['-map', '0:v:0', '-map', '1:a:0'] if len(urls) > 1 else ['-map', '0:v:0', '-map', '0:a:0?']
    if recod:
        video = ['-vf', f'scale=-2:{altura},format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast',
                 '-profile:v', 'main', '-level', '3.1', '-b:v', '1500k' if altura <= 480 else '2500k', '-g', '60']
    else:
        video = ['-c:v', 'copy']
    proc = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', *entradas, *mapa, *video,
                             '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-f', 'mpegts', 'pipe:1'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            bloco = proc.stdout.read(65536)
            if not bloco:
                break
            yield bloco
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


# ---------- página (HTML 4 + CSS simples: o Chrome 42 do Xoom renderiza liso) ----------

def _tempo(s):
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def _views(v):
    if not v:
        return ''
    for lim, suf in ((1e9, ' bi'), (1e6, ' mi'), (1e3, ' mil')):
        if v >= lim:
            return f'{v / lim:.1f}'.replace('.0', '').replace('.', ',') + suf + ' visualizações'
    return f'{v} visualizações'


def link_mx(vid, titulo, altura, inicio=0):
    """/yt/tocar: o notebook abre o MX Player do Xoom pelo ADB (o Chrome 42 ignora component= no intent://
    e mostraria o seletor de apps)."""
    return '/yt/tocar?' + urllib.parse.urlencode({'v': vid, 'q': altura, 't': inicio, 'titulo': titulo[:80]})


def comando_mx(vid, titulo, altura, inicio=0):
    """Comando `am start` (shell do Xoom) que abre o vídeo no MX Player."""
    url = f'{BASE}/yt/video.ts?v={vid}&q={altura}' + (f'&t={inicio}' if inicio else '')
    titulo = re.sub(r"[^\w .,!?&()-]", '', titulo, flags=re.UNICODE)[:80]
    return ('am start -a android.intent.action.VIEW -n com.mxtech.videoplayer.ad/.ActivityScreen'
            f" -d '{url}' -t video/mp2t --es title '{titulo}'")


CSS = '''body{margin:0;background:#0f0f0f;color:#f1f1f1;font-family:sans-serif}
.topo{background:#202020;padding:10px 14px;position:fixed;top:0;left:0;right:0;z-index:2}
.topo b{color:#f00;font-size:24px;vertical-align:middle;margin-right:10px}
.topo input[type=text]{width:55%;font-size:20px;padding:8px;border:1px solid #444;background:#121212;color:#fff}
.topo input[type=submit],.topo a.bt{font-size:20px;padding:8px 16px;background:#333;color:#fff;border:0;text-decoration:none}
.lista{padding:76px 10px 20px}
.item{overflow:hidden;margin:0 0 12px;background:#181818}
.item img{float:left;width:320px;height:180px;margin-right:14px;background:#333}
.item h3{margin:8px 8px 6px 0;font-size:20px;line-height:1.25}
.info{color:#aaa;font-size:16px;margin-bottom:10px}
.dur{display:inline-block;background:#000;color:#fff;padding:1px 6px;margin-right:8px}
.bts a{display:inline-block;font-size:19px;padding:9px 16px;margin:0 8px 6px 0;text-decoration:none;color:#fff;background:#c00}
.bts a.q{background:#333}
h2{font-size:20px;color:#ddd;margin:4px 4px 12px}
.aviso{background:#1b5e20;color:#fff;font-size:19px;padding:12px 14px;margin:0 0 12px}
.aviso.erro{background:#8b0000}
.mais{display:block;text-align:center;font-size:20px;padding:14px;background:#272727;color:#fff;text-decoration:none}'''


def pagina(termo='', n=20, tocando='', falhou=''):
    termo = termo.strip()
    try:
        itens = buscar(termo, n) if termo else em_alta(30)
        erro = ''
    except Exception as e:
        itens, erro = [], f'Erro ao falar com o YouTube: {html.escape(str(e))[:200]}'
    e = html.escape
    linhas = [f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
              f'<title>YouTube Xoom</title><style>{CSS}</style></head><body>',
              '<form class="topo" action="/yt" method="get"><b>&#9654; YouTube</b>'
              f'<input type="text" name="q" value="{e(termo)}" placeholder="Pesquisar"> '
              '<input type="submit" value="Buscar"> <a class="bt" href="/yt">Em alta</a></form><div class="lista">',
              f'<h2>{"Resultados para &ldquo;" + e(termo) + "&rdquo;" if termo else "Em alta no Brasil (músicas)"}</h2>']
    if tocando:
        linhas.append(f'<div class="aviso">&#9654; Tocando no Xoom: {e(tocando)}</div>')
    if falhou:
        linhas.append(f'<div class="aviso erro">Não consegui abrir no Xoom (ADB fora?): {e(falhou)}</div>')
    if erro:
        linhas.append(f'<p>{erro}</p>')
    elif not itens:
        linhas.append('<p>Nada encontrado.</p>')
    for i in itens:
        tocar = e(link_mx(i['id'], i['titulo'], 480))
        linhas.append(
            f'<div class="item"><a href="{tocar}"><img src="/yt/miniatura/{i["id"]}" alt=""></a>'
            f'<h3>{e(i["titulo"])}</h3><div class="info"><span class="dur">{_tempo(i["duracao"])}</span>'
            f'{e(i["canal"])}{" &middot; " + _views(i["views"]) if i["views"] else ""}</div><div class="bts">'
            f'<a href="{tocar}">&#9654; Assistir</a>'
            f'<a class="q" href="{e(link_mx(i["id"], i["titulo"], 720))}">720p</a>'
            f'<a class="q" href="{e(link_mx(i["id"], i["titulo"], 360))}">360p</a></div></div>')
    if termo and len(itens) >= n - 3 and n < 60:
        linhas.append(f'<a class="mais" href="/yt?q={urllib.parse.quote(termo)}&n={n + 20}">Mais resultados</a>')
    linhas.append('</div></body></html>')
    return ''.join(linhas)
