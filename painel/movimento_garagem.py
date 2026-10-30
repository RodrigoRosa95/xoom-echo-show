#!/usr/bin/env python3
"""Movimento na câmera da garagem (Yoosee 192.168.4.42), 22/10/2026. Roda no PM2 como "movimento-garagem".

Lê o fluxo secundário da câmera (onvif2: HEVC 640x360, bem mais leve que o 1080p) com o ffmpeg a 3 quadros/s em
160x90 cinza e compara com um fundo que se adapta devagar. Movimento confirmado (2 quadros seguidos) →
  - Echo Dot fala "Movimento na garagem." (só com AVISO_NO_ECHO = True; desligado desde 23/10/2026) (notify do HA; o xoom-assistente já repete o texto na tela do Show);
  - binary_sensor.movimento_garagem fica "on" por 60 s no Home Assistant (para automações);
  - /tmp/xoom-movimento.json → a tela /show abre a câmera ao vivo por cima por 45 s;
  - foto em ~/xoom-painel/movimentos/ (guarda 7 dias).
Mudança de luz na cena inteira (infravermelho ligando, farol) só reinicia o fundo, sem aviso.
"""
import datetime
import glob
import json
import os
import subprocess
import threading
import time

import numpy as np

import camera_ip
import show_xoom

L, A = 160, 90
FPS = 3
LIMIAR_PIXEL = 24          # diferença de brilho (0–255) para contar o pixel como mudado
FRACAO_MIN = 0.012         # ~170 pixels de 14 400: uma pessoa longe já passa disso
FRACAO_LUZ = 0.45          # mais que isso de uma vez = mudança de luz, não movimento
QUADROS_SEGUIDOS = 2
INTERVALO_AVISO = 120      # s entre dois avisos falados
ECHO = 'notify.echo_dot_de_rodrigo_speak'
AVISO_NO_ECHO = False      # 23/10/2026: usuário pediu sem aviso de voz no Echo (tela do Show, sensor e foto continuam)
SENSOR = 'binary_sensor.movimento_garagem'
ARQ_SHOW = '/tmp/xoom-movimento.json'
PASTA_FOTOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'movimentos')

# relógio da câmera escrito no canto de cima à esquerda muda todo segundo
MASCARA = np.ones((A, L), bool)
MASCARA[:9, :70] = False


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def ha_estado(valor):
    try:
        show_xoom._ha(f'/api/states/{SENSOR}', {'state': valor, 'attributes': {
            'friendly_name': 'Movimento na garagem', 'device_class': 'motion'}})
    except Exception as e:
        log('HA (sensor):', repr(e))


def avisar(fracao):
    agora = datetime.datetime.now()
    log(f'MOVIMENTO ({fracao:.1%} da imagem)')
    with open(ARQ_SHOW + '.tmp', 'w') as f:
        json.dump({'quando': time.time(), 'fracao': round(fracao, 3)}, f)
    os.replace(ARQ_SHOW + '.tmp', ARQ_SHOW)
    ha_estado('on')
    if AVISO_NO_ECHO:
        try:
            show_xoom._ha('/api/services/notify/send_message', {'entity_id': ECHO, 'message': 'Movimento na garagem.'})
        except Exception as e:
            log('HA (Echo):', repr(e))
    jpg = camera_ip.foto()
    if jpg:
        os.makedirs(PASTA_FOTOS, exist_ok=True)
        with open(os.path.join(PASTA_FOTOS, f'{agora:%Y-%m-%d_%H-%M-%S}.jpg'), 'wb') as f:
            f.write(jpg)
    for velho in glob.glob(os.path.join(PASTA_FOTOS, '*.jpg')):
        if time.time() - os.path.getmtime(velho) > 7 * 86400:
            os.remove(velho)
    time.sleep(60)
    ha_estado('off')


def quadros():
    url = camera_ip._url().replace('/onvif1', '/onvif2')
    proc = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-rtsp_transport', 'udp', '-i', url,
                             '-map', '0:v:0', '-vf', f'fps={FPS},scale={L}:{A},format=gray', '-f', 'rawvideo',
                             'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            dados = proc.stdout.read(L * A)
            if len(dados) < L * A:
                return
            yield np.frombuffer(dados, np.uint8).reshape(A, L).astype(np.float32)
    finally:
        proc.kill()
        proc.wait()


def vigiar():
    fundo, seguidos, ultimo = None, 0, 0.0
    for q in quadros():
        # borra 3x3 (tira o chuvisco do sensor à noite)
        q = (q + np.roll(q, 1, 0) + np.roll(q, -1, 0) + np.roll(q, 1, 1) + np.roll(q, -1, 1)) / 5
        if fundo is None:
            fundo = q
            continue
        mudou = (np.abs(q - fundo) > LIMIAR_PIXEL) & MASCARA
        fracao = mudou.mean()
        if fracao > FRACAO_LUZ:
            fundo, seguidos = q, 0
            continue
        fundo = fundo * 0.93 + q * 0.07           # o fundo acompanha sombra/luz mudando devagar
        seguidos = seguidos + 1 if fracao >= FRACAO_MIN else 0
        if seguidos >= QUADROS_SEGUIDOS and time.time() - ultimo > INTERVALO_AVISO:
            ultimo = time.time()
            threading.Thread(target=avisar, args=(fracao,), daemon=True).start()


def main():
    log('vigiando a garagem')
    ha_estado('off')
    while True:
        try:
            vigiar()
            log('fluxo da câmera acabou; reconectando')
        except Exception as e:
            log('erro:', repr(e))
        time.sleep(10)


if __name__ == '__main__':
    main()
