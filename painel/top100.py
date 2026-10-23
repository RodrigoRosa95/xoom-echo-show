"""Top 100 YouTube no Xoom: a biblioteca do Jellyfin vira uma transmissão contínua convertida pelo ffmpeg.

Ordem = a da playlist (nome de ordenação 001, 002… gravado pelo ordem_youtube.py). Cada vídeo é convertido
para 480p (H.264 Main 3.0 30 fps + AAC estéreo): o 720p deixava o Tegra 2 no limite e dava engasgos e emendado no mesmo MPEG-TS,
com o relógio somado (-output_ts_offset) para o player ver uma linha do tempo só.
Uma transmissão por vez: abrir de novo encerra o ffmpeg anterior.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.request

JELLYFIN = 'http://127.0.0.1:8096'
BIBLIOTECA = 'b2072d0c40dafa96bf9d5db5bfde92a3'  # "Top 100 YouTube"
FONTE = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
SEGUNDOS_LEGENDA = 6

_trava = threading.Lock()
_atual = {'proc': None, 'geracao': 0, 'posicao': None, 'titulo': None}


def _chave():
    for linha in open(os.path.expanduser('~/.env'), encoding='utf-8'):
        if linha.startswith('JELLYFIN_API_KEY='):
            return linha.split('=', 1)[1].strip().strip('"\'')


def lista():
    """[(posição, título, caminho, duração em s)] na ordem da playlist."""
    req = urllib.request.Request(
        f'{JELLYFIN}/Items?ParentId={BIBLIOTECA}&Recursive=true&IncludeItemTypes=MusicVideo,Video'
        '&SortBy=SortName&SortOrder=Ascending&Fields=Path,SortName',
        headers={'X-Emby-Token': _chave()})
    with urllib.request.urlopen(req, timeout=30) as r:
        itens = json.load(r)['Items']
    saida = []
    for n, i in enumerate(itens, 1):
        if i.get('Path') and os.path.exists(i['Path']):
            saida.append((n, re.sub(r'\s*\[[\w-]{11}\]$', '', i.get('Name', '')), i['Path'], (i.get('RunTimeTicks') or 0) / 1e7))
    return saida


def _duracao(caminho):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', caminho],
                       capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _comando(caminho, offset, texto_arq):
    filtro = ('scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2,'
              'fps=30,format=yuv420p,'
              f"drawtext=fontfile={FONTE}:textfile={texto_arq}:fontsize=20:fontcolor=white:"
              f"box=1:boxcolor=black@0.55:boxborderw=10:x=26:y=h-72:enable='lt(t,{SEGUNDOS_LEGENDA})'")
    return ['ffmpeg', '-nostdin', '-v', 'error', '-i', caminho,
            '-map', '0:v:0', '-map', '0:a:0?', '-vf', filtro,
            '-c:v', 'libx264', '-preset', 'veryfast', '-profile:v', 'main', '-level', '3.0',
            '-b:v', '1200k', '-maxrate', '1500k', '-bufsize', '3000k', '-g', '60',
            '-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-ar', '44100',
            '-output_ts_offset', f'{offset:.3f}', '-muxdelay', '0', '-f', 'mpegts', 'pipe:1']


def parar():
    with _trava:
        _atual['geracao'] += 1
        p = _atual['proc']
        _atual['proc'] = None
        _atual['posicao'] = _atual['titulo'] = None
    if p and p.poll() is None:
        p.kill()


def estado():
    return {'tocando': _atual['posicao'] is not None, 'posicao': _atual['posicao'], 'titulo': _atual['titulo']}


def transmitir(inicio=1):
    """Gerador de bytes MPEG-TS a partir da posição `inicio` (1 = primeiro da playlist)."""
    parar()
    with _trava:
        geracao = _atual['geracao']
    videos = [v for v in lista() if v[0] >= inicio]
    offset = 0.0
    texto = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8')
    texto.close()
    try:
        for pos, titulo, caminho, dur in videos:
            with open(texto.name, 'w', encoding='utf-8') as f:
                f.write(f'#{pos}  {titulo[:52] + ("…" if len(titulo) > 52 else "")}'.replace('%', '%%'))
            proc = subprocess.Popen(_comando(caminho, offset, texto.name), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            with _trava:
                if _atual['geracao'] != geracao:  # outra transmissão começou
                    proc.kill()
                    return
                _atual.update(proc=proc, posicao=pos, titulo=titulo)
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
            offset += dur or _duracao(caminho)
            if _atual['geracao'] != geracao:
                return
    finally:
        os.unlink(texto.name)
        with _trava:
            if _atual['geracao'] == geracao:
                _atual.update(proc=None, posicao=None, titulo=None)
