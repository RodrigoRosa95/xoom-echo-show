#!/usr/bin/env python3
"""Assistente de voz do tablet Xoom ("Echo Show" caseiro), 22/10/2026. Roda no PM2 como "xoom-assistente".

1) Voz do próprio Xoom: o app Xoom Show manda o microfone (PCM 16 bits mono) por TCP na porta 8797.
   openWakeWord ("hey jarvis") → grava até a pessoa parar de falar → Whisper (pt: Groq large-v3 desde 28/10,
   faster-whisper small local de reserva) → comandos.responder()
   → Piper (voz pt_BR faber) → WAV de volta para o app tocar. Enquanto o tablet fala, o microfone é ignorado.
2) Echo Dot: escuta no Home Assistant os eventos de voz da Alexa (event.*_voice_event); se a frase fala do
   "tablet"/"xoom", executa o comando aqui e responde no próprio Echo (notify.*_speak).
3) Skill "meu tablet" da Alexa (~/alexa-tablet.js): POST http://127.0.0.1:8798/comando {"texto": …} → {"resposta": …}.
Desde 22/10/2026 (FALAR_NO_ECHO) toda a fala sai no Echo Dot e o Xoom só mostra o texto; sem o HA, volta o Piper.
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
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)
import agenda  # noqa: E402
import comandos  # noqa: E402

PORTA = 8797
TAXA = 16000
LIMIAR_ATIVACAO = 0.4   # 0,5 perdia pedidos reais (0,49 com barulho na sala, 22/10)
ARQ_VOZ = '/tmp/xoom-assistente-voz.json'
GROQ_MODELO = 'whisper-large-v3'   # 28/10: ~0,45 s contra ~2,8 s do small local no notebook, acerto igual ou melhor
GROQ_LIMITE = 2.5                   # passou disso (ou sem internet/chave) → Whisper local
PROMPT_WHISPER = 'Comandos para o tablet: YouTube, câmera, Top 100, WhatsApp, volume, previsão do tempo.'
ARQ_TEMPOS = os.path.join(PASTA, 'tempos.jsonl')     # um pedido por linha (resumo: python tempos.py)
VOZ_PIPER = os.path.join(PASTA, 'modelos/pt_BR-faber-medium.onnx')
ECHOS = {'event.echo_dot_de_rodrigo_voice_event': 'notify.echo_dot_de_rodrigo_speak',
         'event.tv_voice_event': 'notify.tv_speak'}
FALA_TABLET = re.compile(r'\b(tablet|xoom|zoom)\b', re.I)
# "pede pro meu tablet …" é da skill (alexa-tablet.js): o histórico de voz também traz a frase, não executar 2 vezes
FALA_SKILL = re.compile(r'\bmeu tablet\b', re.I)
# repete no Xoom (tela + voz) o que os Echo respondem (pedido do usuário 22/10/2026)
DUPLICAR_ECHO = True
# todo o áudio do assistente no Echo Dot, o Xoom só mostra o texto (pedido do usuário 22/10/2026)
FALAR_NO_ECHO = True
ECHO_PRINCIPAL = 'notify.echo_dot_de_rodrigo_speak'
PORTA_SKILL = 8798
# depois do plim do troféu, uma voz (Piper, no Xoom) diz a frase abaixo — opcional (pedido do usuário 23/10/2026)
FALAR_TROFEU = True
FRASE_TROFEU = 'Você desbloqueou uma conquista no Play 2.'
ESPERA_SKILL = 6        # a Alexa desiste em ~8 s; o que demorar mais é falado depois pelo notify

executor = ThreadPoolExecutor(2)
_modelos = {}
_sessoes = set()
_loop = None
_falas_proprias = {}    # texto → quando; o que o próprio assistente mandou o Echo falar não é repetido na tela


def tocar_no_xoom(wav):
    """Chamado de outra thread: manda um WAV para os apps Xoom Show conectados (e silencia o microfone)."""
    quadro = b'W' + struct.pack('>I', len(wav)) + wav
    fim = time.time() + duracao_wav(wav) + 0.6
    for sessao in list(_sessoes):
        sessao.mudo_ate = max(sessao.mudo_ate, fim)
        _loop.call_soon_threadsafe(sessao.escritor.write, quadro)


def falar_no_echo(texto, alvo=ECHO_PRINCIPAL):
    """Chamado de outra thread. False se o HA não aceitou (aí quem chamou fala no Xoom)."""
    agora = time.time()
    for t, q in list(_falas_proprias.items()):
        if agora - q > 120:
            _falas_proprias.pop(t, None)
    _falas_proprias[texto] = agora
    try:
        comandos.show_xoom._ha('/api/services/notify/send_message', {'entity_id': alvo, 'message': texto})
        return True
    except Exception as e:
        log('o Echo não falou:', repr(e))
        return False


def repetir_echo(pergunta, resposta):
    estado('respondeu', pergunta, resposta)
    if _sessoes and not FALAR_NO_ECHO:
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
    tmp = f'{ARQ_VOZ}.{os.getpid()}.{threading.get_ident()}.tmp'   # skill, Echo e voz do Xoom gravam ao mesmo tempo
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


def transcrever_groq(audio16):
    """Whisper large-v3 da Groq (só o trecho falado depois do "hey jarvis" sai de casa)."""
    import urllib.request
    import uuid
    chave = next((l.split('=', 1)[1].strip().strip('"\'') for l in open(os.path.expanduser('~/.env'), encoding='utf-8')
                  if l.startswith('GROQ_API_KEY=')), '')
    if not chave:
        raise RuntimeError('sem GROQ_API_KEY no ~/.env')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(TAXA); w.writeframes(audio16.astype(np.int16).tobytes())
    b = uuid.uuid4().hex
    corpo = b''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                     for k, v in (('model', GROQ_MODELO), ('language', 'pt'), ('temperature', '0'),
                                  ('prompt', PROMPT_WHISPER), ('response_format', 'json')))
    corpo += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="fala.wav"\r\n'
              'Content-Type: audio/wav\r\n\r\n').encode() + buf.getvalue() + f'\r\n--{b}--\r\n'.encode()
    req = urllib.request.Request('https://api.groq.com/openai/v1/audio/transcriptions', corpo, {
        'Authorization': 'Bearer ' + chave, 'Content-Type': f'multipart/form-data; boundary={b}',
        'User-Agent': 'xoom-assistente'})
    with urllib.request.urlopen(req, timeout=GROQ_LIMITE) as r:
        return str(json.load(r).get('text') or '').strip()


def transcrever(audio16):
    """→ (texto, motor). Groq primeiro; erro, demora ou sem internet → faster-whisper small local."""
    try:
        return transcrever_groq(audio16), 'groq'
    except Exception as e:
        log('groq falhou, usando o Whisper local:', repr(e)[:120])
    seg, _ = whisper().transcribe(audio16.astype(np.float32) / 32768, language='pt', beam_size=1,
                                  vad_filter=True, initial_prompt=PROMPT_WHISPER)
    return ' '.join(s.text for s in seg).strip(), 'local'


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


def medir_tempos(fala, transcricao, resposta, voz, texto, motor='local'):
    """Tempo de cada etapa depois que a pessoa para de falar: log, tempos.jsonl (resumo: python tempos.py)
    e sensor.xoom_tempo_resposta no HA. "voz" = gerar o Piper ou entregar o pedido ao Echo (a fala do Echo
    começa uns segundos depois e não dá para medir daqui)."""
    rota = comandos.ROTA
    total = transcricao + resposta + voz
    log(f'tempos [{rota}]: whisper ({motor}) {transcricao:.1f}s | resposta {resposta:.1f}s | voz {voz:.1f}s | '
        f'total {total:.1f}s (fala de {fala:.1f}s)')
    try:
        with open(ARQ_TEMPOS, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'quando': time.time(), 'rota': rota, 'fala': round(fala, 2),
                                'whisper': round(transcricao, 2), 'motor': motor, 'resposta': round(resposta, 2),
                                'voz': round(voz, 2), 'total': round(total, 2), 'texto': texto},
                               ensure_ascii=False) + '\n')
        comandos.show_xoom._ha('/api/states/sensor.xoom_tempo_resposta', {'state': round(total, 1), 'attributes': {
            'unit_of_measurement': 's', 'friendly_name': 'Xoom: tempo de resposta', 'icon': 'mdi:timer-outline',
            'rota': rota, 'motor': motor, 'whisper': round(transcricao, 1), 'resposta': round(resposta, 1), 'voz': round(voz, 1),
            'texto': texto}})
    except Exception as e:
        log('tempos:', repr(e))


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
            if not FALAR_NO_ECHO:
                await self.enviar_wav(bipe())
            fala = await self.gravar_comando()
            self.limpar_detector()
            if fala is None:
                estado('parado')
                continue
            estado('pensando')
            t0 = time.time()                                       # fim da fala (0,9 s de silêncio)
            texto, motor = await loop.run_in_executor(executor, transcrever, fala)
            t1 = time.time()
            log('entendi:', texto)
            if not texto:
                estado('respondeu', '', 'Não ouvi nada.')
                continue
            estado('pensando', texto)
            resposta = await loop.run_in_executor(executor, processar, texto, True) or 'Não sei fazer isso ainda.'
            t2 = time.time()
            log('resposta:', resposta)
            estado('respondeu', texto, resposta)
            await self.falar(resposta)
            loop.run_in_executor(executor, medir_tempos, len(fala) / TAXA, t1 - t0, t2 - t1, time.time() - t2, texto, motor)
            self.limpar_detector()
            self.ignorar_ate = self.mudo_ate + 2

    async def falar(self, texto):
        loop = asyncio.get_running_loop()
        if FALAR_NO_ECHO and await loop.run_in_executor(executor, falar_no_echo, texto):
            self.mudo_ate = max(self.mudo_ate, time.time() + 3)   # o Echo demora uns segundos para começar
            return
        await self.enviar_wav(await loop.run_in_executor(executor, falar_wav, texto))

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
                        if DUPLICAR_ECHO and aviso and aviso not in _falas_proprias:
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
                                  if t and FALA_TABLET.search(t) and not FALA_SKILL.search(t)), '')
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
                    _falas_proprias[resposta] = time.time()
                    await ws.send(json.dumps({'id': int(time.time() * 1000) % 2**31, 'type': 'call_service',
                                              'domain': 'notify', 'service': 'send_message',
                                              'target': {'entity_id': ECHOS[ent]}, 'service_data': {'message': resposta}}))
        except Exception as e:
            log('HA desconectado:', repr(e))
            await asyncio.sleep(15)


# ---------- 3) skill "meu tablet" da Alexa ----------

async def falar_quando_terminar(texto, tarefa):
    resposta = await tarefa or 'Não sei fazer isso no tablet ainda.'
    log('skill (atrasada):', resposta)
    estado('respondeu', texto, resposta)
    await asyncio.get_running_loop().run_in_executor(executor, falar_no_echo, resposta)


async def pedido_da_skill(leitor, escritor):
    """HTTP mínimo só para o alexa-tablet.js (127.0.0.1): POST /comando {"texto"} → {"resposta", "depois"}."""
    loop = asyncio.get_running_loop()
    try:
        cab = await asyncio.wait_for(leitor.readuntil(b'\r\n\r\n'), 5)
        m = re.search(rb'(?i)content-length:\s*(\d+)', cab)
        corpo = json.loads(await leitor.readexactly(int(m.group(1))) if m else b'{}')
        if cab.startswith(b'POST /teste-trofeu'):   # teste do anúncio (não grava nada no histórico da box)
            ok = await anunciar_trofeu({'titulo': corpo.get('titulo', 'Teste de conquista'), 'pontos': 5,
                                        'descricao': 'Teste do aviso', 'jogo': 'PS2 Henbox', 'medalha': ''})
            saida = json.dumps({'tocou_no_xoom': ok}).encode()
            escritor.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n'
                           b'Connection: close\r\n\r\n' % len(saida) + saida)
            await escritor.drain()
            return
        texto = str(corpo.get('texto') or '').strip()
        log('skill:', texto)
        estado('pensando', texto)
        tarefa = loop.run_in_executor(executor, processar, texto, False)
        try:
            resposta, depois = await asyncio.wait_for(asyncio.shield(tarefa), ESPERA_SKILL), False
            resposta = resposta or 'Não sei fazer isso no tablet ainda.'
            estado('respondeu', texto, resposta)
        except asyncio.TimeoutError:
            resposta, depois = None, True
            asyncio.ensure_future(falar_quando_terminar(texto, tarefa))
        log('skill →', resposta or '(responde depois)')
        saida = json.dumps({'resposta': resposta, 'depois': depois}, ensure_ascii=False).encode()
        escritor.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n'
                       b'Content-Length: %d\r\nConnection: close\r\n\r\n' % len(saida) + saida)
        await escritor.drain()
    except Exception as e:
        log('erro no pedido da skill:', repr(e))
    finally:
        escritor.close()


# ---------- 🏆 conquistas do PS2 (23/10/2026): pop-up estilo PS4 no Show + plim ----------
URL_DESBLOQUEIOS = 'http://192.168.4.79:8780/conquistas/api/desbloqueios'   # vigia do videos.py na TV box (Wi-Fi, era .121 no cabo até 28/10)
SOM_TROFEU = os.path.expanduser('~/ps2-monitor/navegador/sons/trofeu.wav')  # plim próprio (não é o da Sony)
ARQ_TROFEU = '/tmp/xoom-trofeu.json'                                          # a tela /show lê por /show/trofeu


def buscar_desbloqueios(desde):
    import urllib.request
    with urllib.request.urlopen(f'{URL_DESBLOQUEIOS}?desde={desde}', timeout=8) as r:
        return json.loads(r.read())


async def anunciar_trofeu(d):
    """Cartão no Show + plim + (opcional) a frase falada. Também usado pelo teste: POST /teste-trofeu na 8798."""
    loop = asyncio.get_running_loop()
    seq = int(time.time() * 1000)   # único mesmo depois de reiniciar (o Show compara com o último)
    log('🏆 conquista no PS2:', d.get('titulo'), '·', d.get('jogo'))
    tmp = ARQ_TROFEU + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'seq': seq, 'mostrado_em': time.time(), **d}, f, ensure_ascii=False)
    os.replace(tmp, ARQ_TROFEU)
    if _sessoes and os.path.exists(SOM_TROFEU):
        plim = open(SOM_TROFEU, 'rb').read()
        tocar_no_xoom(plim)
        if FALAR_TROFEU:
            await asyncio.sleep(duracao_wav(plim) + 0.2)
            if 'trofeu' not in _modelos:
                _modelos['trofeu'] = await loop.run_in_executor(executor, falar_wav, FRASE_TROFEU)
            tocar_no_xoom(_modelos['trofeu'])
    return bool(_sessoes)


async def vigiar_trofeus():
    loop = asyncio.get_running_loop()
    desde = time.time()                  # só o que for desbloqueado daqui para a frente
    falhas = 0                           # box fora: espera mais entre tentativas e loga só a 1ª falha e a volta
    while True:
        try:
            novos = await loop.run_in_executor(executor, buscar_desbloqueios, desde)
            if falhas:
                log(f'troféus: TV box respondendo de novo (depois de {falhas} falhas)')
            falhas = 0
            for d in novos:
                desde = max(desde, d.get('quando') or 0)
                await anunciar_trofeu(d)
                await asyncio.sleep(7)       # um cartão por vez
        except Exception as e:
            falhas += 1
            if falhas == 1 and 'Connection refused' not in str(e):
                log('troféus: TV box sem responder, tentando de novo a cada minuto:', repr(e)[:120])
        await asyncio.sleep(3 if falhas < 5 else 60)


async def atualizar_agenda():
    """Próximos compromissos do Google Agenda para a tela do Show, a cada 5 min."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(executor, agenda.atualizar)
        except Exception as e:
            log('agenda:', repr(e))
        await asyncio.sleep(300)


async def lembretes_agenda():
    """Resumo do dia às 8h e aviso 1 h antes de cada compromisso: o Echo Dot fala e o Show mostra."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            for frase in await loop.run_in_executor(executor, agenda.lembretes_devidos):
                log('lembrete:', frase)
                await loop.run_in_executor(executor, falar_no_echo, frase)
                await loop.run_in_executor(executor, repetir_echo, '📅 Agenda', frase)
        except Exception as e:
            log('lembretes:', repr(e))
        await asyncio.sleep(60)


async def main():
    global _loop
    estado('parado')
    loop = _loop = asyncio.get_running_loop()
    comandos.MUDAR_VOLUME = mudar_volume
    await loop.run_in_executor(executor, whisper)           # carrega antes da 1ª frase
    await loop.run_in_executor(executor, piper)
    servidor = await asyncio.start_server(atender, '0.0.0.0', PORTA)
    skill = await asyncio.start_server(pedido_da_skill, '127.0.0.1', PORTA_SKILL)
    log('assistente pronto na porta', PORTA, '(skill na', PORTA_SKILL, ')')
    async with servidor, skill:
        await asyncio.gather(servidor.serve_forever(), skill.serve_forever(), ouvir_echos(), atualizar_agenda(),
                             lembretes_agenda(), vigiar_trofeus())


if __name__ == '__main__':
    asyncio.run(main())
