"""Câmera IP Yoosee (192.168.4.42) no Xoom: RTSP HEVC 1080p convertido pelo ffmpeg para H.264 720p 15 fps.

O Tegra 2 não decodifica HEVC; aqui sai MPEG-TS contínuo (H.264 Main 3.1 + AAC mono do microfone da câmera).
A câmera só aceita RTSP por UDP. Uma transmissão por vez: abrir de novo encerra o ffmpeg anterior.
"""
import os
import subprocess
import threading
import urllib.parse

CAMERA = '192.168.4.42:554/onvif1'

_trava = threading.Lock()
_atual = {'proc': None}


def _senha():
    for linha in open(os.path.expanduser('~/.env'), encoding='utf-8'):
        if linha.startswith('CAMERA_YOOSEE_SENHA='):
            return linha.split('=', 1)[1].strip().strip('"\'')


def _url():
    return f"rtsp://admin:{urllib.parse.quote(_senha(), safe='')}@{CAMERA}"


def _comando():
    return ['ffmpeg', '-nostdin', '-v', 'error', '-rtsp_transport', 'udp', '-i', _url(),
            '-map', '0:v:0', '-map', '0:a:0?', '-vf', 'scale=1280:720,fps=15,format=yuv420p',
            '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-profile:v', 'main', '-level', '3.1',
            '-b:v', '1500k', '-maxrate', '2000k', '-bufsize', '3000k', '-g', '30',
            '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '16000', '-f', 'mpegts', 'pipe:1']


def parar():
    with _trava:
        p, _atual['proc'] = _atual['proc'], None
    if p and p.poll() is None:
        p.kill()


def transmitir():
    """Gerador de bytes MPEG-TS ao vivo; o ffmpeg morre quando o player desconecta."""
    parar()
    proc = subprocess.Popen(_comando(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with _trava:
        _atual['proc'] = proc
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
        with _trava:
            if _atual['proc'] is proc:
                _atual['proc'] = None


# Home Assistant (câmera MJPEG): vídeo em /camera.mjpg e foto em /camera.jpg, independentes da transmissão do Xoom
def mjpeg():
    """Gerador multipart MJPEG 1280x720 5 fps (boundary "ffmpeg"); o ffmpeg morre quando o cliente desconecta."""
    proc = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-rtsp_transport', 'udp', '-i', _url(),
                             '-map', '0:v:0', '-vf', 'scale=1280:720,fps=5', '-q:v', '6', '-f', 'mpjpeg', 'pipe:1'],
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


def foto():
    """Um quadro JPEG 1280x720 (ou None se a câmera não responder em 15 s)."""
    try:
        r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-rtsp_transport', 'udp', '-i', _url(),
                            '-map', '0:v:0', '-frames:v', '1', '-vf', 'scale=1280:720', '-q:v', '4',
                            '-f', 'image2', '-c:v', 'mjpeg', 'pipe:1'], capture_output=True, timeout=15)
        return r.stdout or None
    except subprocess.TimeoutExpired:
        return None
