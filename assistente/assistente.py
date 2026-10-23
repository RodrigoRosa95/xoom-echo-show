#!/usr/bin/env python3
"""Assistente de voz do tablet Xoom ("Echo Show" caseiro), 22/10/2026. Roda no PM2 como "xoom-assistente".

1) Voz do próprio Xoom: o app Xoom Show manda o microfone (PCM 16 bits mono) por TCP na porta 8797.
   openWakeWord ("hey jarvis") → grava até a pessoa parar de falar → Whisper (pt) → comandos.responder()
   → Piper (voz pt_BR faber) → WAV de volta para o app tocar. Enquanto o tablet fala, o microfone é ignorado.
2) Echo Dot: escuta no Home Assistant os eventos de voz da Alexa (event.*_voice_event); se a frase fala do
   "tablet"/"xoom", executa o comando aqui e responde no próprio Echo (notify.*_speak).
Nos dois casos grava o estado em /tmp/xoom-assistente-voz.json, que a tela /show mostra na faixa azul.

Protocolo do TCP: cliente → "XOOM <taxa>\n" e depois áudio cru; servidor → b"W" + tamanho (4 bytes, big endian)
+ WAV para tocar.
"""
import asyncio
import io
import json
import os
import re
import struct
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)
import comandos  # noqa: E402

PORTA = 8797
TAXA = 16000
LIMIAR_ATIVACAO = 0.4   # 0,5 perdia pedidos reais (0,49 com barulho na sala, 22/10)
ARQ_VOZ = '/tmp/xoom-assistente-voz.json'
VOZ_PIPER = os.path.join(PASTA, 'modelos/pt_BR-faber-medium.onnx')
ECHOS = {'event.echo_dot_de_rodrigo_voice_event': 'notify.echo_dot_de_rodrigo_speak',
         'event.tv_voice_event': 'notify.tv_speak'}
FALA_TABLET = re.compile(r'\b(tablet|xoom|zoom)\b', re.I)
# repete no Xoom (tela + voz) o que os Echo respondem (pedido do usuário 22/10/2026)
DUPLICAR_ECHO = True

executor = ThreadPoolExecutor(2)
_modelos = {}
_sessoes = set()
_loop = None


def tocar_no_xoom(wav):
    """Chamado de outra thread: manda um WAV para os apps Xoom Show conectados (e silencia o microfone)."""
    quadro = b'W' + struct.pack('>I', len(wav)) + wav
    fim = time.time() + duracao_wav(wav) + 0.6
    for sessao in list(_sessoes):
        sessao.mudo_ate = max(sessao.mudo_ate, fim)
        _loop.call_soon_threadsafe(sessao.escritor.write, quadro)


def repetir_echo(pergunta, resposta):
    estado('respondeu', pergunta, resposta)
    if _sessoes:
        tocar_no_xoom(falar_wav(resposta[:400]))


def mudar_volume(nivel):
    """Chamado pelos comandos (em outra thread): manda b'V' para os apps Xoom Show conectados."""
    if not _sessoes:
        return False
    quadro = b'V' + struct.pack('>I', 1) + bytes([nivel])
    for sessao in list(_sessoes):
        _loop.call_soon_threadsafe(sessao.escritor.write, quadro)
    return True


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def estado(nome, texto='', resposta=''):
    tmp = ARQ_VOZ + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'estado': nome, 'texto': texto, 'resposta': resposta, 'quando': time.time()}, f, ensure_ascii=False)
    os.replace(tmp, ARQ_VOZ)


# ---------- modelos (carregados uma vez) ----------

def whisper():
    if 'whisper' not in _modelos:
        from faster_whisper import WhisperModel
        _modelos['whisper'] = WhisperModel('small', device='cpu', compute_type='int8',
                                           download_root=os.path.join(PASTA, 'modelos/whisper'), cpu_threads=4)
    return _modelos['whisper']


def piper():
    if 'piper' not in _modelos:
        from piper import PiperVoice
        _modelos['piper'] = PiperVoice.load(VOZ_PIPER)
    return _modelos['piper']


def novo_detector():
    import openwakeword
    from openwakeword.model import Model
    d = os.path.join(os.path.dirname(openwakeword.__file__), 'resources/models')
    return Model(wakeword_model_paths=[os.path.join(d, 'hey_jarvis_v0.1.onnx')])


def transcrever(audio16):
    seg, _ = whisper().transcribe(audio16.astype(np.float32) / 32768, language='pt', beam_size=1,
                                  vad_filter=True, initial_prompt='Comandos para o tablet: YouTube, câmera, Top 100, '
                                  'WhatsApp, volume, previsão do tempo.')
    return ' '.join(s.text for s in seg).strip()


def falar_wav(texto):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        piper().synthesize_wav(texto, w)
    return buf.getvalue()


def bipe():
    """Bipe curto de "estou ouvindo" (duas notas subindo)."""
    t = np.arange(int(TAXA * 0.09)) / TAXA
    nota = lambda f: (np.sin(2 * np.pi * f * t) * np.hanning(len(t)) * 9000).astype(np.int16)  # noqa: E731
    dados = np.concatenate([nota(880), nota(1320)])
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(TAXA); w.writeframes(dados.tobytes())
    return buf.getvalue()


def duracao_wav(wav):
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


def processar(texto, permitir_ia):
    try:
        return comandos.responder(texto, permitir_ia)
    except Exception as e:
        log('erro no comando:', repr(e))
        return 'Deu um erro ao fazer isso.'


# ---------- 1) voz do Xoom ----------

class Sessao:
    def __init__(self, leitor, escritor, taxa):
        self.leitor, self.escritor, self.taxa = leitor, escritor, taxa
        self.detector = novo_detector()
        self.mudo_ate = 0.0
        self.ignorar_ate = 0.0

    def diagnostico(self, pedaco, ponto):
        """A cada 30 s: nível médio do microfone e a maior pontuação do "hey jarvis" (só se passou de 0,15)."""
        agora = time.time()
        if not hasattr(self, '_diag'):
            self._diag = [agora, 0.0, 0.0, 0]
        d = self._diag
        d[1] = max(d[1], float(ponto)); d[2] += float(np.sqrt(np.mean(pedaco.astype(np.float32) ** 2))); d[3] += 1
        if agora - d[0] >= 30:
            if d[1] >= 0.15:
                log(f'mic: nível {d[2] / max(d[3], 1):.0f}, maior "hey jarvis" {d[1]:.2f}')
            self._diag = [agora, 0.0, 0.0, 0]

    def limpar_detector(self):
        """O openWakeWord guarda ~2 s de áudio: sem limpar, o mesmo "hey jarvis" dispara de novo."""
        silencio = np.zeros(1280, np.int16)
        for _ in range(30):
            self.detector.predict(silencio)
        self.detector.reset()

    async def enviar_wav(self, wav):
        self.escritor.write(b'W' + struct.pack('>I', len(wav)) + wav)
        await self.escritor.drain()
        self.mudo_ate = max(self.mudo_ate, time.time() + duracao_wav(wav) + 0.6)

    def para_16k(self, bloco):
        a = np.frombuffer(bloco, np.int16)
        if self.taxa == TAXA:
            return a
        n = int(len(a) * TAXA / self.taxa)
        return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.int16)

    async def ler_16k(self, amostras):
        """Lê `amostras` amostras já em 16 kHz (descarta o que chega enquanto o tablet fala)."""
        precisa = int(amostras * self.taxa / TAXA) * 2
        bloco = await self.leitor.readexactly(precisa)
        a = self.para_16k(bloco)
        return np.zeros_like(a) if time.time() < self.mudo_ate else a

    async def rodar(self):
        loop = asyncio.get_running_loop()
        while True:
            pedaco = await self.ler_16k(1280)                      # 80 ms
            pontos = self.detector.predict(pedaco)
            self.diagnostico(pedaco, max(pontos.values()))
            if max(pontos.values()) < LIMIAR_ATIVACAO or time.time() < self.ignorar_ate:
                continue
            log('ativado', pontos)
            estado('ouvindo')
            await self.enviar_wav(bipe())
            fala = await self.gravar_comando()
            self.limpar_detector()
            if fala is None:
                estado('parado')
                continue
            estado('pensando')
            texto = await loop.run_in_executor(executor, transcrever, fala)
            log('entendi:', texto)
            if not texto:
                estado('respondeu', '', 'Não ouvi nada.')
                continue
            estado('pensando', texto)
            resposta = await loop.run_in_executor(executor, processar, texto, True) or 'Não sei fazer isso ainda.'
            log('resposta:', resposta)
            estado('respondeu', texto, resposta)
            await self.enviar_wav(await loop.run_in_executor(executor, falar_wav, resposta))
            self.limpar_detector()
            self.ignorar_ate = self.mudo_ate + 2

    async def gravar_comando(self):
        """Grava depois do bipe até 0,9 s de silêncio (máx. 8 s). None se ninguém falou em 4 s."""
        await asyncio.sleep(0)
        self.mudo_ate = time.time() + 0.25                      # pula o próprio bipe
        partes, ruido, falou, silencio, inicio = [], None, False, 0.0, time.time()
        while time.time() - inicio < 8:
            p = await self.ler_16k(480)                          # 30 ms
            partes.append(p)
            rms = float(np.sqrt(np.mean(p.astype(np.float32) ** 2))) if len(p) else 0.0
            if ruido is None:
                ruido = rms
            elif not falou:
                ruido = ruido * 0.9 + rms * 0.1                 # nível do ambiente antes de começar a falar
            if rms > max(ruido * 3, 350):
                falou, silencio = True, 0.0
            elif falou:
                silencio += 0.03
                if silencio > 0.9:
                    break
            elif time.time() - inicio > 4:
                return None
        return np.concatenate(partes) if falou else None


async def atender(leitor, escritor):
    quem = escritor.get_extra_info('peername')
    if not str(quem[0]).startswith(('192.168.', '127.', '100.')):   # só rede de casa / Tailscale
        escritor.close()
        return
    try:
        linha = (await asyncio.wait_for(leitor.readline(), 10)).decode().split()
        taxa = int(linha[1]) if len(linha) > 1 and linha[0] == 'XOOM' else TAXA
        log('xoom conectado', quem, taxa, 'Hz')
        sessao = Sessao(leitor, escritor, taxa)
        _sessoes.add(sessao)
        try:
            await sessao.rodar()
        finally:
            _sessoes.discard(sessao)
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
        pass
    except Exception as e:
        log('erro na sessão:', repr(e))
    finally:
        log('xoom desconectou', quem)
        escritor.close()


# ---------- 2) Echo Dot (eventos de voz da Alexa no Home Assistant) ----------

def aviso_para_echo(ev):
    """Texto de um notify.send_message / notify.alexa_media… / tts.speak mandado para um Echo, senão None."""
    if ev.get('domain') != 'notify':
        return None
    dados = ev.get('service_data') or {}
    alvo = dados.get('entity_id') or (dados.get('target') or {}).get('entity_id') or ''
    alvos = alvo if isinstance(alvo, list) else [alvo]
    if not any(a.startswith(('notify.echo', 'notify.tv_')) for a in alvos if isinstance(a, str)):
        return None
    texto = str(dados.get('message') or '').strip()
    return texto or None


async def ouvir_echos():
    loop = asyncio.get_running_loop()
    token = comandos.show_xoom._token()
    vistos = {}
    while True:
        try:
            async with websockets.connect('ws://192.168.4.30/api/websocket', max_size=None) as ws:
                await ws.recv()
                await ws.send(json.dumps({'type': 'auth', 'access_token': token}))
                await ws.recv()
                await ws.send(json.dumps({'id': 1, 'type': 'subscribe_events', 'event_type': 'state_changed'}))
                # o que o HA manda os Echo falarem não aparece no histórico da Alexa: pega na chamada do serviço
                await ws.send(json.dumps({'id': 2, 'type': 'subscribe_events', 'event_type': 'call_service'}))
                log('ouvindo os Echo no HA')
                async for msg in ws:
                    evento = json.loads(msg).get('event', {})
                    ev = evento.get('data', {})
                    if evento.get('event_type') == 'call_service':
                        aviso = aviso_para_echo(ev)
                        if DUPLICAR_ECHO and aviso:
                            log('HA mandou o echo falar:', aviso[:120])
                            loop.run_in_executor(executor, repetir_echo, '', aviso)
                        continue
                    ent = ev.get('entity_id')
                    if ent not in ECHOS or not ev.get('new_state'):
                        continue
                    attrs = ev['new_state'].get('attributes', {})
                    novo = vistos.get('dup:' + ent) != ev['new_state'].get('state')
                    vistos['dup:' + ent] = ev['new_state'].get('state')
                    fala_echo = (attrs.get('voice_reply') or '').strip()
                    if DUPLICAR_ECHO and novo and fala_echo:
                        pedido = (attrs.get('voice_command') or '').strip()
                        log('echo falou:', ent, repr(pedido), '→', fala_echo[:120])
                        loop.run_in_executor(executor, repetir_echo, pedido if pedido != 'Música e Áudio' else '', fala_echo)
                    # o histórico da Alexa nem sempre traz o que foi falado (vem vazio ou "Música e Áudio"):
                    # as Rotinas da Alexa respondem com "Alexa diz: … no tablet", que chega em voice_reply
                    texto = next((t.strip() for t in (attrs.get('voice_command'), attrs.get('voice_reply'))
                                  if t and FALA_TABLET.search(t)), '')
                    if not texto or vistos.get(ent) == ev['new_state'].get('state'):
                        continue
                    vistos[ent] = ev['new_state'].get('state')
                    log('echo:', ent, texto)
                    estado('pensando', texto)
                    resposta = await loop.run_in_executor(executor, processar, texto, False) \
                        or 'Não entendi esse comando.'   # sem a palavra tablet: não pode virar comando de novo
                    estado('respondeu', texto, resposta)
                    if texto == (attrs.get('voice_reply') or '').strip():
                        continue                        # veio de Rotina: a Alexa já falou a frase dela
                    await ws.send(json.dumps({'id': int(time.time() * 1000) % 2**31, 'type': 'call_service',
                                              'domain': 'notify', 'service': 'send_message',
                                              'target': {'entity_id': ECHOS[ent]}, 'service_data': {'message': resposta}}))
        except Exception as e:
            log('HA desconectado:', repr(e))
            await asyncio.sleep(15)


async def main():
    global _loop
    estado('parado')
    loop = _loop = asyncio.get_running_loop()
    comandos.MUDAR_VOLUME = mudar_volume
    await loop.run_in_executor(executor, whisper)           # carrega antes da 1ª frase
    await loop.run_in_executor(executor, piper)
    servidor = await asyncio.start_server(atender, '0.0.0.0', PORTA)
    log('assistente pronto na porta', PORTA)
    async with servidor:
        await asyncio.gather(servidor.serve_forever(), ouvir_echos())


if __name__ == '__main__':
    asyncio.run(main())
