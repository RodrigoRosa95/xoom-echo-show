#!/usr/bin/env python3
"""Resumo dos tempos do "hey jarvis" (tempos.jsonl, gravado pelo assistente.py).

Uso: python tempos.py [dias]   (padrão: 7)
Mediana e pior caso de cada etapa por rota e da transcrição por motor (groq / local) (local = comandos.py, ha = Assist do HA, groq/claude = pergunta geral).
"""
import json
import os
import statistics
import sys
import time

ARQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tempos.jsonl')
dias = float(sys.argv[1]) if len(sys.argv) > 1 else 7
desde = time.time() - dias * 86400
linhas = []
if os.path.exists(ARQ):
    with open(ARQ, encoding='utf-8') as f:
        linhas = [d for d in map(json.loads, filter(str.strip, f)) if d['quando'] >= desde]
if not linhas:
    sys.exit(f'Nenhum pedido medido nos últimos {dias:g} dias.')

print(f'Últimos {dias:g} dias: {len(linhas)} pedidos (mediana / pior, em segundos)\n')
print(f'{"rota":<8}{"n":>4}  {"whisper":>12}{"resposta":>12}{"voz":>12}{"total":>12}')
for rota in ('local', 'ha', 'groq', 'claude'):
    g = [d for d in linhas if d['rota'] == rota]
    if g:
        col = lambda k: f'{statistics.median(d[k] for d in g):5.1f} /{max(d[k] for d in g):5.1f}'  # noqa: E731
        print(f'{rota:<8}{len(g):>4}  {col("whisper"):>12}{col("resposta"):>12}{col("voz"):>12}{col("total"):>12}')
motores = {}
for d in linhas:
    m = motores.setdefault(d.get('motor', 'local'), [])
    m.append(d['whisper'])
print('\nTranscrição: ' + ' | '.join(f'{k} {len(v)}x, mediana {statistics.median(v):.2f}s' for k, v in motores.items()))
lento = max(linhas, key=lambda d: d['total'])
print(f'\nMais lento: {lento["total"]:.1f}s [{lento["rota"]}] "{lento["texto"]}" '
      f'em {time.strftime("%d/%m %H:%M", time.localtime(lento["quando"]))}')
