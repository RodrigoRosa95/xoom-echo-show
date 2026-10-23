#!/usr/bin/env python3
"""Painel de controle do tablet Xoom pelo ADB root (Flask, porta 8796, só rede local/Tailscale).

Página: http://<ip-do-notebook>:8796/
- tela ao vivo (screencap) com toque/arrasto direto na imagem
- mídia (play/pause, próximo, anterior, avançar, voltar, parar), volume, brilho
- energia (acordar, apagar, reiniciar), navegação (home, voltar, recentes, menu)
- relógio (abrir/fechar), webcam (abre o app de câmera IP instalado), abrir qualquer app, abrir link,
  digitar texto, informações (bateria, Wi-Fi, app em primeiro plano) e terminal root.
Roda no pm2 como "xoom-painel". Usa o adb-server do notebook (5037), o mesmo do Home Assistant.
"""
import io
import ipaddress
import os
import re
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse

from flask import Flask, Response, abort, jsonify, redirect, request, send_file
from PIL import Image

import camera_ip
import show_xoom
import top100
import youtube_xoom

PORTA = 8796
XOOM = '192.168.4.72:5555'
PASTA = os.path.dirname(os.path.abspath(__file__))
XOOM_SH = os.path.join(PASTA, 'xoom.sh')
REDES_PERMITIDAS = [ipaddress.ip_network(r) for r in ('127.0.0.0/8', '192.168.0.0/16', '100.64.0.0/10')]

# Botões simples → keyevent do Android
TECLAS = {
    'power': 26, 'home': 3, 'voltar': 4, 'recentes': 187, 'menu': 82,
    'vol_mais': 24, 'vol_menos': 25, 'mudo': 164,
    'play_pause': 85, 'play': 126, 'pause': 127, 'parar': 86,
    'proximo': 87, 'anterior': 88, 'retroceder': 89, 'avancar': 90,
    'cima': 19, 'baixo': 20, 'esquerda': 21, 'direita': 22, 'ok': 23,
    'enter': 66, 'apagar': 67, 'espaco': 62,
}
# Apps de webcam conhecidos (pacote, activity que já começa a transmitir)
WEBCAMS = [
    ('com.pas.webcam', 'com.pas.webcam/.Rolling'),
    ('com.dev47apps.droidcam', 'com.dev47apps.droidcam/.DroidCam'),
    ('com.dev47apps.droidcamx', 'com.dev47apps.droidcamx/.DroidCamX'),
]
# Log do buscar_dublado (um arquivo por dia, AAAA-MM-DD.log) mostrado ao vivo em /logs
LOGS_DUBLADO = os.path.expanduser('~/buscar-dublado/logs')
URL_LOGS = 'http://192.168.4.135:8796/logs'
URL_TOP100 = 'http://192.168.4.135:8796/top100.ts'
# Webcam da TV box (DroidCam, MJPEG, 1 cliente por vez) aberta no VLC do Xoom
WEBCAM_TVBOX = 'http://192.168.4.121:4747/video'
# Câmera IP Yoosee (RTSP HEVC) convertida no notebook e aberta no MX Player do Xoom
URL_CAMERA_IP = 'http://192.168.4.135:8796/camera.ts'

app = Flask(__name__)


@app.before_request
def so_rede_local():
    try:
        ip = ipaddress.ip_address(request.remote_addr)
    except ValueError:
        abort(403)
    if not any(ip in r for r in REDES_PERMITIDAS):
        abort(403)


def adb(*args, timeout=20):
    """Roda adb no Xoom (reconecta uma vez se cair). → (código, saída)."""
    for tentativa in range(2):
        try:
            r = subprocess.run(['adb', '-s', XOOM, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, 'tempo esgotado'
        saida = (r.stdout + r.stderr).strip()
        if tentativa == 0 and ('not found' in saida or 'offline' in saida or 'closed' in saida):
            subprocess.run(['adb', 'connect', XOOM], capture_output=True, timeout=15)
            continue
        return r.returncode, saida
    return 1, saida


def sh(comando, timeout=20):
    return adb('shell', comando, timeout=timeout)


def pacotes_instalados():
    _, s = sh('pm list packages')
    return {l[8:].strip() for l in s.splitlines() if l.startswith('package:')}


@app.route('/')
def pagina():
    return send_file(os.path.join(PASTA, 'painel.html'))


@app.route('/logs')
def pagina_logs():
    return send_file(os.path.join(PASTA, 'logs.html'))


@app.route('/api/log-dublado')
def log_dublado():
    """Pedaço novo do log mais recente a partir de pos (pos<0 = só o final). Trocou o arquivo → recomeça."""
    nomes = sorted(n for n in os.listdir(LOGS_DUBLADO) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.log', n))
    if not nomes:
        return jsonify(arquivo='', pos=0, texto='')
    nome = nomes[-1]
    caminho = os.path.join(LOGS_DUBLADO, nome)
    tamanho = os.path.getsize(caminho)
    pos = request.args.get('pos', -1, type=int)
    if request.args.get('arquivo') != nome or pos < 0 or pos > tamanho:
        pos = max(0, tamanho - 30000)
    with open(caminho, 'rb') as f:
        f.seek(pos)
        dados = f.read(200000)
    return jsonify(arquivo=nome, pos=pos + len(dados), texto=dados.decode('utf-8', 'replace'),
                   rodando=subprocess.run(['pgrep', '-f', r'^\S*python3?\S* \S*buscar_dublado\.py'],
                                          capture_output=True).returncode == 0)


@app.route('/top100.ts')
def top100_ts():
    """Top 100 YouTube convertido pelo ffmpeg (MPEG-TS contínuo); ?inicio=N começa no N-ésimo da playlist."""
    inicio = max(1, request.args.get('inicio', 1, type=int))
    return Response(top100.transmitir(inicio), mimetype='video/mp2t', direct_passthrough=True)


@app.route('/camera.ts')
def camera_ts():
    """Câmera IP Yoosee ao vivo, convertida para H.264 720p (MPEG-TS contínuo)."""
    return Response(camera_ip.transmitir(), mimetype='video/mp2t', direct_passthrough=True)


@app.route('/camera.mjpg')
def camera_mjpg():
    """Câmera IP em MJPEG (Home Assistant e navegadores)."""
    return Response(camera_ip.mjpeg(), mimetype='multipart/x-mixed-replace;boundary=ffmpeg', direct_passthrough=True)


@app.route('/camera.jpg')
def camera_jpg():
    jpg = camera_ip.foto()
    if not jpg:
        abort(504)
    return Response(jpg, mimetype='image/jpeg')


@app.route('/show')
def show_pagina():
    """Tela no estilo Echo Show (app Xoom Show)."""
    return send_file(os.path.join(PASTA, 'show.html'))


@app.route('/show/dados')
def show_dados():
    return jsonify(show_xoom.dados())


@app.route('/show/voz')
def show_voz():
    return jsonify(show_xoom.voz())


@app.route('/yt')
def youtube_pagina():
    """YouTube para o Chrome 42 do Xoom (busca/Em alta pelo yt-dlp; tocar abre o MX Player)."""
    n = min(60, max(10, request.args.get('n', 20, type=int)))
    return Response(youtube_xoom.pagina(request.args.get('q', ''), n, request.args.get('tocando', ''),
                                        request.args.get('falhou', '')), mimetype='text/html')


@app.route('/yt/miniatura/<vid>')
def youtube_miniatura(vid):
    if not youtube_xoom.ID_OK.match(vid):
        abort(404)
    jpg = youtube_xoom.miniatura(vid)
    if not jpg:
        abort(404)
    return Response(jpg, mimetype='image/jpeg', headers={'Cache-Control': 'max-age=86400'})


@app.route('/yt/tocar')
def youtube_tocar():
    """Botão Assistir: abre o MX Player no Xoom pelo ADB e volta para a lista."""
    vid = request.args.get('v', '')
    if not youtube_xoom.ID_OK.match(vid):
        abort(404)
    altura = request.args.get('q', 480, type=int)
    altura = altura if altura in youtube_xoom.QUALIDADES else 480
    _, p = sh('dumpsys power | grep mScreenOn')
    if 'mScreenOn=false' in p:
        sh('input keyevent 26')
    top100.parar()
    titulo = request.args.get('titulo', '')
    cod, _ = sh(youtube_xoom.comando_mx(vid, titulo, altura, max(0, request.args.get('t', 0, type=int))))
    # volta para a mesma lista com o aviso "tocando no Xoom" (ou de erro, se o ADB falhou)
    volta = urllib.parse.urlsplit(request.referrer or '/yt')
    args = [(k, v) for k, v in urllib.parse.parse_qsl(volta.query) if k not in ('tocando', 'falhou')]
    args.append(('tocando' if cod == 0 else 'falhou', f'{titulo} ({altura}p)'))
    return redirect(f'/yt?{urllib.parse.urlencode(args)}')


@app.route('/yt/video.ts')
def youtube_video():
    vid = request.args.get('v', '')
    if not youtube_xoom.ID_OK.match(vid):
        abort(404)
    altura = request.args.get('q', 480, type=int)
    altura = altura if altura in youtube_xoom.QUALIDADES else 480
    inicio = max(0, request.args.get('t', 0, type=int))
    return Response(youtube_xoom.transmitir(vid, altura, inicio), mimetype='video/mp2t', direct_passthrough=True)


@app.route('/api/top100')
def top100_estado():
    return jsonify(top100.estado())


@app.route('/api/estado')
def estado():
    cod, _ = adb('get-state', timeout=8)
    if cod != 0:
        return jsonify(conectado=False)
    _, bat = sh('dumpsys battery')
    _, power = sh('dumpsys power | grep mScreenOn')
    _, foco = sh('dumpsys window windows | grep mCurrentFocus')
    _, wifi = sh('dumpsys wifi | grep "mWifiInfo"')
    _, brilho = sh('settings get system screen_brightness')
    nivel = re.search(r'level: (\d+)', bat)
    carregando = re.search(r'(AC|USB) powered: true', bat)
    app_atual = re.search(r'\{\S+ \S+ ([^/\s}]+)', foco)
    ssid = re.search(r'SSID: ([^,]+)', wifi)
    sinal = re.search(r'RSSI: (-?\d+)', wifi)
    return jsonify(
        conectado=True,
        bateria=int(nivel.group(1)) if nivel else None,
        carregando=bool(carregando),
        tela_ligada='mScreenOn=true' in power,
        app=app_atual.group(1) if app_atual else '',
        wifi=ssid.group(1).strip() if ssid else '',
        sinal=int(sinal.group(1)) if sinal else None,
        brilho=int(brilho) if brilho.isdigit() else None,
    )


# Apps em que a tela ao vivo pausa: o print disputa CPU e Wi-Fi com o vídeo e o Tegra 2 engasga
PLAYERS = ('com.mxtech.videoplayer', 'org.videolan.vlc')
_tela = {'jpeg': None, 'foco': (0.0, '')}
_captura = threading.Lock()  # duas capturas juntas gravavam o mesmo arquivo no tablet (HTTP 500)


def app_em_foco():
    """Pacote em primeiro plano (consulta no máximo a cada 10 s)."""
    agora = time.time()
    if agora - _tela['foco'][0] > 10:
        _, f = sh('dumpsys window windows | grep -m1 mCurrentFocus')
        m = re.search(r'\{\S+ \S+ ([^/\s}]+)', f)
        _tela['foco'] = (agora, m.group(1) if m else '')
    return _tela['foco'][1]


@app.route('/api/tela.png')
def tela():
    forcar = request.args.get('forcar') == '1'
    if not forcar and _tela['jpeg'] and (top100.estado()['tocando'] or app_em_foco().startswith(PLAYERS)):
        return Response(_tela['jpeg'], mimetype='image/jpeg',
                        headers={'Cache-Control': 'no-store', 'X-Tela': 'pausada'})
    # captura CRUA (0,9 s no Xoom; o PNG levava 4–5 s de CPU) e o JPEG é feito aqui no notebook.
    # Android 4.4 não tem `adb exec-out`: grava no tablet e puxa
    with _captura:
        sh('screencap /data/local/tmp/painel.raw', timeout=20)
        with tempfile.NamedTemporaryFile(suffix='.raw', delete=False) as f:
            destino = f.name
        cod, s = adb('pull', '/data/local/tmp/painel.raw', destino, timeout=30)
        if cod != 0:
            os.unlink(destino)
            return Response(s, status=503)
        dados = open(destino, 'rb').read()
        os.unlink(destino)
    largura, altura = struct.unpack('<II', dados[:8])
    img = Image.frombuffer('RGBA', (largura, altura), dados[12:12 + largura * altura * 4], 'raw', 'RGBA', 0, 1)
    saida = io.BytesIO()
    img.convert('RGB').save(saida, 'JPEG', quality=75)
    _tela['jpeg'] = saida.getvalue()
    return Response(_tela['jpeg'], mimetype='image/jpeg', headers={'Cache-Control': 'no-store', 'X-Tela': 'ao-vivo'})


@app.route('/api/tecla/<nome>', methods=['POST'])
def tecla(nome):
    if nome not in TECLAS:
        abort(404)
    cod, s = sh(f'input keyevent {TECLAS[nome]}')
    return jsonify(ok=cod == 0, saida=s)


def teclado_no_vnc():
    """Depois que o RealVNC conecta, abre o teclado dele (a tecla Menu alterna o teclado: só manda se estiver fechado)."""
    for _ in range(20):                                   # até ~20 s esperando a tela do VNC
        time.sleep(1)
        _, foco = sh('dumpsys window windows | grep mCurrentFocus')
        if 'realvnc' in foco and 'DesktopActivity' in foco:
            break
    else:
        return
    time.sleep(3)                                         # a conexão termina de abrir depois da tela
    _, ime = sh('dumpsys input_method | grep mInputShown')
    if 'mInputShown=true' not in ime:
        sh('input keyevent 82')


@app.route('/api/acao/<nome>', methods=['POST'])
def acao(nome):
    d = request.get_json(silent=True) or {}
    if nome == 'acordar':
        _, p = sh('dumpsys power | grep mScreenOn')
        if 'mScreenOn=false' in p:
            sh('input keyevent 26')
        cod, s = sh('input keyevent 82')
    elif nome == 'apagar_tela':
        _, p = sh('dumpsys power | grep mScreenOn')
        cod, s = sh('input keyevent 26') if 'mScreenOn=true' in p else (0, 'já apagada')
    elif nome in ('relogio_abrir', 'relogio_fechar'):
        r = subprocess.run([XOOM_SH, nome.replace('_', '-')], capture_output=True, text=True, timeout=90)
        cod, s = r.returncode, (r.stdout + r.stderr).strip()
    elif nome == 'webcam':
        instalados = pacotes_instalados()
        alvo = next((a for p, a in WEBCAMS if p in instalados), None)
        if not alvo:
            return jsonify(ok=False, saida='Nenhum app de webcam instalado (IP Webcam ou DroidCam).')
        cod, s = sh(f'am start -n {alvo}')
    elif nome == 'webcam_parar':
        for p, _ in WEBCAMS:
            sh(f'am force-stop {p}')
        cod, s = 0, 'webcam parada'
    elif nome == 'webcam_tvbox':
        _, p = sh('dumpsys power | grep mScreenOn')
        if 'mScreenOn=false' in p:
            sh('input keyevent 26')
        cod, s = sh('am start -a android.intent.action.VIEW -n org.videolan.vlc/.gui.video.VideoPlayerActivity'
                    f' -d {WEBCAM_TVBOX} -t video/mjpeg')
    elif nome == 'camera_ip':
        _, p = sh('dumpsys power | grep mScreenOn')
        if 'mScreenOn=false' in p:
            sh('input keyevent 26')
        top100.parar()
        cod, s = sh('am start -a android.intent.action.VIEW -n com.mxtech.videoplayer.ad/.ActivityScreen'
                    f" -d '{URL_CAMERA_IP}' -t video/mp2t --es title 'Câmera IP'")
    elif nome == 'camera_ip_parar':
        camera_ip.parar()
        sh('am force-stop com.mxtech.videoplayer.ad')
        cod, s = 0, 'câmera IP parada'
    elif nome == 'logs_dublado':
        _, p = sh('dumpsys power | grep mScreenOn')
        if 'mScreenOn=false' in p:
            sh('input keyevent 26')
        cod, s = sh(f"am start -a android.intent.action.VIEW -n com.android.chrome/com.google.android.apps.chrome.Main -d '{URL_LOGS}'")
    elif nome == 'top100':
        inicio = max(1, int(d.get('inicio') or 1))
        _, p = sh('dumpsys power | grep mScreenOn')
        if 'mScreenOn=false' in p:
            sh('input keyevent 26')
        camera_ip.parar()  # os dois usam o MX e o ffmpeg; a câmera já para o Top 100 do mesmo jeito
        cod, s = sh('am start -a android.intent.action.VIEW -n com.mxtech.videoplayer.ad/.ActivityScreen'
                    f" -d '{URL_TOP100}?inicio={inicio}' -t video/mp2t --es title 'Top 100 YouTube'")
    elif nome == 'top100_parar':
        top100.parar()
        sh('am force-stop com.mxtech.videoplayer.ad')
        cod, s = 0, 'Top 100 parado'
    elif nome == 'whatsapp_vnc':
        # entrada "Whatsapp" (192.168.4.135:5902, senha salva) da agenda do RealVNC Viewer do Xoom
        _, p = sh('dumpsys power | grep mScreenOn')
        if 'mScreenOn=false' in p:
            sh('input keyevent 26')
        # a tela de conexão do RealVNC não é exportada: só root consegue abrir direto
        cod, s = sh('su -c "am start -a com.realvnc.android.viewer.CONNECT'
                    ' -d content://com.realvnc.viewer.android.data.AddressBook/entries/2'
                    ' -t vnd.android.cursor.item/vnd.realvnc.entry"')
        if 'Error' in s or 'Exception' in s:
            cod = 1
        else:
            threading.Thread(target=teclado_no_vnc, daemon=True).start()
    elif nome == 'youtube_parar':
        sh('am force-stop com.mxtech.videoplayer.ad')  # o ffmpeg do /yt/video.ts morre quando o MX desconecta
        cod, s = 0, 'YouTube parado'
    elif nome == 'toque':
        cod, s = sh(f"input tap {int(d['x'])} {int(d['y'])}")
    elif nome == 'arrastar':
        cod, s = sh(f"input swipe {int(d['x1'])} {int(d['y1'])} {int(d['x2'])} {int(d['y2'])} {int(d.get('ms', 300))}")
    elif nome == 'texto':
        # input text não aceita espaço literal: vira %s; aspas e símbolos de shell são escapados
        texto = re.sub(r'([\\"\'`$&|;<>()*?!#~])', r'\\\1', str(d.get('texto', ''))).replace(' ', '%s')
        cod, s = sh(f'input text "{texto}"')
    elif nome == 'brilho':
        valor = max(0, min(255, int(d.get('valor', 128))))
        sh('settings put system screen_brightness_mode 0')
        cod, s = sh(f'settings put system screen_brightness {valor}')
    elif nome == 'volume':
        # stream 3 = mídia; o comando `media volume` existe no 4.4
        valor = max(0, min(15, int(d.get('valor', 7))))
        # esta ROM não tem `media volume`: lê o volume atual do alto-falante e aperta as teclas a diferença
        _, saida = sh('dumpsys audio | grep -A3 STREAM_MUSIC')
        m = re.search(r'Current:.*?\b2: (\d+)', saida)
        atual = int(m.group(1)) if m else 0
        tecla = 24 if m and valor > atual else 25
        vezes = abs(valor - atual) if m else 15
        cod, s = sh(f'for i in $(seq 1 {vezes}); do input keyevent {tecla}; done') if vezes else (0, '')
        if not m:  # não deu para ler: zera e sobe até o valor
            cod, s = sh(f'for i in $(seq 1 {valor}); do input keyevent 24; done')
        s = f'volume {atual} → {valor}'
    elif nome == 'abrir_app':
        pacote = re.sub(r'[^\w.]', '', str(d.get('pacote', '')))
        cod, s = sh(f'monkey -p {pacote} -c android.intent.category.LAUNCHER 1')
    elif nome == 'fechar_app':
        _, foco = sh('dumpsys window windows | grep mCurrentFocus')
        m = re.search(r'\{\S+ \S+ ([^/\s}]+)', foco)
        if not m:
            return jsonify(ok=False, saida='nenhum app em primeiro plano')
        cod, s = sh(f'am force-stop {m.group(1)}')
        s = f'fechado: {m.group(1)} {s}'
    elif nome == 'abrir_link':
        url = str(d.get('url', '')).replace("'", '')
        cod, s = sh(f"am start -a android.intent.action.VIEW -d '{url}'")
    elif nome == 'girar':
        _, atual = sh('settings get system user_rotation')
        prox = (int(atual) + 1) % 4 if atual.isdigit() else 0
        sh('settings put system accelerometer_rotation 0')
        cod, s = sh(f'settings put system user_rotation {prox}')
    elif nome == 'reiniciar':
        cod, s = adb('reboot', timeout=15)
    elif nome == 'terminal':
        cod, s = sh(str(d.get('comando', '')), timeout=60)
    else:
        abort(404)
    return jsonify(ok=cod == 0, saida=s[-4000:])


@app.route('/api/apps')
def apps():
    _, s = sh('pm list packages -3')
    terceiros = sorted(l[8:].strip() for l in s.splitlines() if l.startswith('package:'))
    sistema = [p for p in ('com.android.deskclock', 'com.android.settings', 'com.android.browser',
                           'com.android.gallery3d', 'com.android.camera2', 'com.android.music')
               if p in pacotes_instalados()]
    return jsonify(apps=sistema + terceiros)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORTA, threaded=True)
