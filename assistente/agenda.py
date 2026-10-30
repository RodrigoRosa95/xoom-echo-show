"""Próximos compromissos do Google Agenda para a tela do Xoom Show (22/10/2026).

O Xoom não precisa logar no Google: o notebook lê o "Endereço secreto no formato iCal" de cada agenda
(Google Agenda na web → Configurações → a agenda → Integrar agenda), guardado em ~/.env como
GOOGLE_AGENDA_ICS=<url>[,<url>...]. A cada 5 min grava /tmp/xoom-agenda.json com os próximos eventos
(14 dias, eventos que se repetem incluídos); a tela /show e o comando de voz leem esse arquivo.
"""
import datetime
import json
import os
import urllib.request

import icalendar
import recurring_ical_events

ARQ = '/tmp/xoom-agenda.json'
DIAS = 14
# Criados pelo grupo "Agenda" do WhatsApp (agenda_whats.py): o iCal secreto do Google demora a mostrar evento novo,
# então os lembretes também leem esta lista. ENVIADOS guarda quais lembretes já saíram.
ARQ_BOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agenda-bot.json')
ENVIADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lembretes-enviados.json')
HORA_RESUMO = 8          # resumo do dia às 8h; os com hora também avisam 1 h antes
FUSO = datetime.datetime.now().astimezone().tzinfo


def _urls():
    for linha in open(os.path.expanduser('~/.env'), encoding='utf-8'):
        if linha.startswith('GOOGLE_AGENDA_ICS='):
            return [u.strip() for u in linha.split('=', 1)[1].strip().strip('"\'').split(',') if u.strip()]
    return []


def _local(v):
    """date (dia inteiro) ou datetime → datetime local."""
    if isinstance(v, datetime.datetime):
        return (v if v.tzinfo else v.replace(tzinfo=FUSO)).astimezone(FUSO)
    return datetime.datetime(v.year, v.month, v.day, tzinfo=FUSO)


def proximos(n=3):
    agora = datetime.datetime.now(FUSO)
    eventos = []
    for url in _urls():
        with urllib.request.urlopen(url, timeout=30) as r:
            cal = icalendar.Calendar.from_ical(r.read())
        for ev in recurring_ical_events.of(cal).between(agora - datetime.timedelta(days=1),
                                                         agora + datetime.timedelta(days=DIAS)):
            ini = ev.get('DTSTART').dt
            fim = ev.get('DTEND').dt if ev.get('DTEND') else ini
            dia_todo = not isinstance(ini, datetime.datetime)
            if _local(fim) <= agora and not (dia_todo and _local(ini).date() == agora.date()):
                continue   # já terminou (o de dia inteiro de hoje continua aparecendo)
            eventos.append({'titulo': str(ev.get('SUMMARY') or '(sem título)'), 'inicio': _local(ini).isoformat(),
                            'dia_todo': dia_todo, 'local': str(ev.get('LOCATION') or '')})
    eventos.sort(key=lambda e: e['inicio'])
    return eventos[:n]


def atualizar():
    """Grava o arquivo da tela; sem URL configurada grava configurado=false (a tela não mostra o quadro)."""
    if not _urls():
        dados = {'configurado': False, 'eventos': []}
    else:
        try:
            todos = proximos(50)
            # os criados pelo grupo do WhatsApp entram na tela na hora (o iCal do Google demora a mostrá-los)
            agora = datetime.datetime.now(FUSO)
            vistos = {(e['titulo'].strip().lower(), e['inicio'][:16]) for e in todos}
            for e in _ler(ARQ_BOT, []):
                ini = datetime.datetime.fromisoformat(e['inicio'])
                fim = ini + datetime.timedelta(days=1 if e['dia_todo'] else 0, hours=0 if e['dia_todo'] else 1)
                if fim > agora and (e['titulo'].strip().lower(), e['inicio'][:16]) not in vistos:
                    todos.append({k: e[k] for k in ('titulo', 'inicio', 'dia_todo', 'local')})
            todos.sort(key=lambda e: e['inicio'])
            dados = {'configurado': True, 'eventos': todos[:3], 'todos': todos}
        except Exception as e:   # sem internet / URL errada: mantém o que já tinha
            try:
                dados = dict(json.load(open(ARQ)), erro=str(e)[:200])
            except (OSError, ValueError):
                dados = {'configurado': True, 'eventos': [], 'erro': str(e)[:200]}
    dados['atualizado'] = datetime.datetime.now(FUSO).isoformat()
    tmp = ARQ + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, ARQ)
    return dados


def falar_proximo():
    """Frase para o assistente de voz."""
    try:
        d = json.load(open(ARQ))
    except (OSError, ValueError):
        d = atualizar()
    if not d.get('configurado'):
        return 'A agenda ainda não está ligada.'
    if not d['eventos']:
        return 'Você não tem compromissos nos próximos dias.'
    e = d['eventos'][0]
    ini = datetime.datetime.fromisoformat(e['inicio'])
    hoje = datetime.datetime.now(FUSO).date()
    dias = (ini.date() - hoje).days
    quando = 'hoje' if dias <= 0 else 'amanhã' if dias == 1 else \
        ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'][ini.weekday()]
    hora = '' if e['dia_todo'] else (f' às {ini.hour} horas' + (f' e {ini.minute}' if ini.minute else ''))
    return f"Seu próximo compromisso é {e['titulo']}, {quando}{hora}."


# ---------- lembretes no Echo Dot / Xoom Show (22/10/2026) ----------
SEMANA = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']


def _hora_falada(ini):
    return f'{ini.hour} horas' + (f' e {ini.minute}' if ini.minute else '')


def _ler(arq, padrao):
    try:
        return json.load(open(arq, encoding='utf-8'))
    except (OSError, ValueError):
        return padrao


def _gravar(arq, dados):
    tmp = arq + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, arq)


def eventos_para_lembrar():
    """iCal (via /tmp/xoom-agenda.json) + criados pelo bot, sem repetir o mesmo título no mesmo início."""
    vistos, saida = set(), []
    for e in _ler(ARQ, {}).get('todos', []) + _ler(ARQ_BOT, []):
        chave = (e['titulo'].strip().lower(), e['inicio'][:16])
        if chave not in vistos:
            vistos.add(chave)
            saida.append(e)
    return saida


def lembretes_devidos(agora=None):
    """Frases a falar agora. Cada lembrete sai uma vez só (chave em lembretes-enviados.json)."""
    agora = agora or datetime.datetime.now(FUSO)
    enviados = _ler(ENVIADOS, {})
    frases = []
    eventos = eventos_para_lembrar()
    hoje = [e for e in eventos if datetime.datetime.fromisoformat(e['inicio']).date() == agora.date()]
    chave_resumo = 'resumo:' + agora.date().isoformat()
    if hoje and agora.hour == HORA_RESUMO and chave_resumo not in enviados:
        hoje.sort(key=lambda e: e['inicio'])
        itens = [e['titulo'] if e['dia_todo'] else f"{e['titulo']} às {_hora_falada(datetime.datetime.fromisoformat(e['inicio']))}"
                 for e in hoje]
        frases.append(('Bom dia! Seus compromissos de hoje: ' if len(itens) > 1 else 'Bom dia! Hoje você tem ')
                       + ', '.join(itens[:-1]) + (' e ' if len(itens) > 1 else '') + itens[-1] + '.')
        enviados[chave_resumo] = agora.isoformat()
    for e in eventos:
        if e['dia_todo']:
            continue
        ini = datetime.datetime.fromisoformat(e['inicio'])
        falta = (ini - agora).total_seconds() / 60
        chave = f"1h:{e['titulo']}:{e['inicio'][:16]}"
        if 0 < falta <= 60 and chave not in enviados:
            quando = 'daqui a uma hora' if falta > 50 else f'daqui a {round(falta)} minutos'
            frases.append(f"Lembrete: {e['titulo']} {quando}, às {_hora_falada(ini)}.")
            enviados[chave] = agora.isoformat()
    if frases:
        limite = (agora - datetime.timedelta(days=30)).isoformat()
        _gravar(ENVIADOS, {k: v for k, v in enviados.items() if v >= limite})
    return frases


def registrar_do_bot(ev):
    """Evento criado pelo grupo do WhatsApp: {id, titulo, inicio (iso local), dia_todo, local}."""
    agora = datetime.datetime.now(FUSO) - datetime.timedelta(days=1)
    lista = [e for e in _ler(ARQ_BOT, []) if datetime.datetime.fromisoformat(e['inicio']) > agora and e.get('id') != ev.get('id')]
    _gravar(ARQ_BOT, lista + [ev])


def apagar_do_bot(ev_id):
    _gravar(ARQ_BOT, [e for e in _ler(ARQ_BOT, []) if e.get('id') != ev_id])
