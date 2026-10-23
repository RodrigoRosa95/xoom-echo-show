"""Comandos de voz do tablet Xoom (usados pela voz do próprio Xoom e pelos Echo Dot via Home Assistant).

responder(texto) → frase para falar. Primeiro os comandos do tablet (câmera, YouTube, Top 100, WhatsApp,
volume, parar…), depois o Assist do Home Assistant (casa, horas…), e por último o Claude para perguntas gerais
(só quando permitir_ia=True: na voz do Xoom sim; pelos Echo não, a Alexa já responde essas).
"""
import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.expanduser('~/xoom-painel'))
import show_xoom      # noqa: E402  (tempo do HA)
import youtube_xoom   # noqa: E402  (busca do YouTube)

PAINEL = 'http://127.0.0.1:8796'
HA = 'http://192.168.4.30'
CLAUDE = os.path.expanduser('~/.local/bin/claude')
# o assistente.py troca por uma função que manda o volume (0–10) para o app Xoom Show conectado;
# devolve False se o app não estiver conectado (aí cai no painel, que usa as teclas de volume)
MUDAR_VOLUME = None
NUMEROS = {'zero': 0, 'um': 1, 'uma': 1, 'dois': 2, 'duas': 2, 'tres': 3, 'quatro': 4, 'cinco': 5, 'seis': 6,
           'sete': 7, 'oito': 8, 'nove': 9, 'dez': 10, 'maximo': 10, 'minimo': 1}


def normalizar(texto):
    t = unicodedata.normalize('NFD', texto.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s%]', ' ', t)).strip()


def acao(nome, dados=None):
    req = urllib.request.Request(f'{PAINEL}/api/acao/{nome}', json.dumps(dados or {}).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r).get('ok')


def terminal(comando):
    return acao('terminal', {'comando': comando})


def _ha_assist(texto):
    token = show_xoom._token()
    req = urllib.request.Request(f'{HA}/api/conversation/process',
                                 json.dumps({'text': texto, 'language': 'pt'}).encode(),
                                 {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.load(r)['response']
    fala = (resp.get('speech', {}).get('plain', {}) or {}).get('speech', '')
    entendeu = resp.get('response_type') != 'error' and not re.search(r'desculp|nao entendi|não entendi|não sei', fala.lower())
    return entendeu, fala


def _claude(pergunta):
    prompt = ('Você é a assistente de voz de um tablet numa casa em Guaíba (RS). Responda em português do Brasil, '
              'em no máximo 2 frases curtas e naturais, para ser lida em voz alta (sem markdown, sem listas, '
              f'sem emojis). Data/hora agora: {datetime.datetime.now():%d/%m/%Y %H:%M}. Pergunta: {pergunta}')
    try:
        r = subprocess.run([CLAUDE, '-p', '--model', 'haiku', prompt], capture_output=True, text=True, timeout=60)
        resp = re.sub(r'[*#_`]', '', r.stdout).strip()
        return resp or 'Não consegui pensar numa resposta agora.'
    except subprocess.TimeoutExpired:
        return 'Demorei demais para pensar. Pergunta de novo?'


def _tempo(t):
    d = show_xoom.dados()
    if 'agora' not in d:
        return 'Não consegui ver a previsão do tempo agora.'
    a, dias = d['agora'], d['dias']
    i = 1 if 'amanha' in t else 0
    dia = dias[i] if len(dias) > i else None
    if i == 1 and dia:
        frase = f"Amanhã: {dia['texto'].lower()}, máxima de {dia['max']} e mínima de {dia['min']} graus."
    else:
        frase = f"Agora está {a['temp']} graus, {a['texto'].lower()}."
        if dia:
            frase += f" Hoje a máxima é {dia['max']} e a mínima {dia['min']}."
    if re.search(r'chov|chuva', t) and dia:
        frase += ' Deve chover.' if dia.get('chuva', 0) >= 1 else ' Não deve chover.'
    return frase


def _youtube(t):
    m = re.search(r'(?:toca|tocar|toque|coloca|colocar|coloque|bota|botar|abre|abrir|procura|procurar|busca|buscar|'
                  r'poe|por|quero ver|quero ouvir)\s+(.+?)\s+(?:no|na)\s+(?:youtube|yt)', t) or \
        re.search(r'(?:youtube|yt)\s+(?:de\s+|o\s+|a\s+)?(.+)', t)
    termo = (m.group(1) if m else '').strip()
    termo = re.sub(r'^(a |o |as |os |um |uma |musica |video |clipe )+', '', termo)
    termo = re.sub(r'\s+(para|pra) (mim|nos|a gente)$', '', termo)
    if not termo:
        acao('terminal', {'comando': 'monkey -p br.ytxoom -c android.intent.category.LAUNCHER 1'})
        return 'Abrindo o YouTube.'
    itens = youtube_xoom.buscar(termo, 5)
    if not itens:
        return f'Não achei {termo} no YouTube.'
    v = itens[0]
    q = urllib.parse.urlencode({'v': v['id'], 'q': 480, 't': 0, 'titulo': v['titulo'][:80]})
    urllib.request.urlopen(f'{PAINEL}/yt/tocar?{q}', timeout=90).read()
    return f"Tocando {v['titulo'][:70]}."


def responder(texto, permitir_ia=True):
    """Executa o comando e devolve a frase de resposta (ou None se não for nada que o tablet saiba fazer)."""
    t = normalizar(texto)
    t = re.sub(r'\b(no|na|do|da|pro|para o|pra) (tablet|xoom|zoom|tela)\b', '', t).strip()
    t = re.sub(r'^(hey |ei )?(jarvis|alexa)\b', '', t).strip()
    if not t:
        return None
    # ---- comandos do tablet ----
    # fechar vem antes de abrir: "fecha o whatsapp" contém "whatsapp" e abriria de novo
    # ("para" só no começo: "toca X para mim no youtube" não é fechar)
    fechar = re.search(r'\b(fecha|fechar|feche|fechando|desliga|desligar|tira|tirar|sai|sair|encerra|encerrar)\b', t) \
        or re.search(r'^(para|parar|pare|parando)\b', t)
    if fechar:
        if 'whatsapp' in t or 'zap' in t:
            terminal('am force-stop com.realvnc.viewer.android')
            terminal('am start -n br.xoomshow/.ShowActivity')
            return 'Fechei o WhatsApp.'
        if re.search(r'\b(jellyfin|jelly|filme|filmes|serie|series)\b', t):
            terminal('am force-stop br.jellyxoom')
            terminal('am force-stop com.mxtech.videoplayer.ad')
            terminal('am start -n br.xoomshow/.ShowActivity')
            return 'Fechei o Jellyfin.'
        if re.search(r'\b(youtube|yt|video)\b', t):
            acao('youtube_parar')
            terminal('am start -n br.xoomshow/.ShowActivity')
            return 'Fechei o YouTube.'
        if re.search(r'\btop ?(100|cem)\b|\bmusica\b', t):
            acao('top100_parar')
            terminal('am start -n br.xoomshow/.ShowActivity')
            return 'Parei o Top 100.'
    if re.search(r'\b(camera|cameras|garagem|portao)\b', t):
        if re.search(r'\b(fecha|fechar|feche|fechando|para|parar|pare|desliga|desligar|tira|tirar|sai|sair)\b', t):
            acao('camera_ip_parar')
            terminal('am start -n br.xoomshow/.ShowActivity')
            return 'Fechei a câmera.'
        acao('camera_ip')
        return 'Mostrando a câmera.'
    if re.search(r'\b(youtube|yt)\b', t):
        return _youtube(t)
    if re.search(r'\btop ?(100|cem)\b|mais tocadas|parada de sucesso', t):
        acao('top100')
        return 'Tocando o Top 100.'
    if re.search(r'\b(jellyfin|jelly ?fin|jelly|geli ?fin)\b', t) or re.search(r'\b(abre|abrir|abra) (os )?(filmes|series)\b', t):
        acao('abrir_app', {'pacote': 'br.jellyxoom'})
        return 'Abrindo o Jellyfin.'
    if 'whatsapp' in t or 'zap' in t:
        acao('whatsapp_vnc')
        return 'Abrindo o WhatsApp.'
    if re.search(r'\b(pausa|pausar|pause)\b', t):
        terminal('input keyevent 127')
        return 'Pausado.'
    if re.search(r'\b(continua|continuar|despausa|retoma|play)\b', t):
        terminal('input keyevent 126')
        return 'Continuando.'
    # "para" também é preposição: só vale no começo da frase ou seguido do que está tocando
    if (re.search(r'^(para|parar|pare|parando|chega|fecha|fechar|fechando|sai|sair)\b', t) or
            re.search(r'\b(para|pare|parar|fecha|desliga) (o |a )?(video|musica|tudo|camera)\b', t)) \
            and not re.search(r'\b(tv|televisao|luz|luzes)\b', t):
        acao('top100_parar')
        acao('youtube_parar')
        acao('camera_ip_parar')
        terminal('am start -n br.xoomshow/.ShowActivity')
        return 'Parei.'
    if 'volume' in t or re.search(r'\b(mais alto|mais baixo|aumenta o som|abaixa o som)\b', t):
        n = re.search(r'\b(\d{1,3})\b', t)
        palavra = next((v for k, v in NUMEROS.items() if re.search(rf'\b{k}\b', t)), None)
        if n or palavra is not None:
            v = int(n.group(1)) if n else palavra
            v = min(10, v // 10 if v > 10 else v)            # "volume 50" = 50%
            if not (MUDAR_VOLUME and MUDAR_VOLUME(v)):
                acao('volume', {'valor': round(v * 1.5)})   # 0–10 → 0–15 do Android
            return f'Volume {v}.'
        sobe = re.search(r'\b(aumenta|aumentar|sobe|subir|mais alto)\b', t)
        for _ in range(3):
            terminal(f'input keyevent {24 if sobe else 25}')
        return 'Aumentei o volume.' if sobe else 'Abaixei o volume.'
    if re.search(r'\b(tela inicial|relogio|inicio|show|tela principal)\b', t):
        terminal('am start -n br.xoomshow/.ShowActivity')
        return 'Pronto.'
    if re.search(r'\b(tempo|previsao|temperatura|chover|chuva|calor|frio)\b', t):
        return _tempo(t)
    if re.search(r'\b(que horas|horas sao|hora e)\b', t):
        a = datetime.datetime.now()
        return f'São {a.hour} horas e {a.minute} minutos.' if a.minute else f'São {a.hour} horas em ponto.'
    if re.search(r'\b(que dia|data de hoje|dia e hoje|dia hoje)\b', t):
        dias = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
        meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
                 'outubro', 'novembro', 'dezembro']
        a = datetime.date.today()
        return f'Hoje é {dias[a.weekday()]}, {a.day} de {meses[a.month - 1]}.'
    # ---- casa (Assist do Home Assistant) ----
    try:
        entendeu, fala = _ha_assist(texto)
        if entendeu and fala:
            return fala
    except Exception:
        pass
    # ---- pergunta geral ----
    return _claude(texto) if permitir_ia else None
