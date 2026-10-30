'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { criarJellyflix } = require('../alexa-jellyflix');

const CFG = { urlPublica: 'https://x/api/jellyflix', segredo: 's', jellyfin: 'http://jf', jellyfinKey: 'k' };
const ID = (n) => String(n).padStart(32, '0');
const APARELHO = 'aparelho-123456789012';
const tk = (n) => `${APARELHO.slice(-12)}|${n}|${ID(n)}`;

function montar(tamanho = 5) {
  const j = criarJellyflix(CFG, { arquivoFilas: null });
  j.filas.set(APARELHO, { ids: Array.from({ length: tamanho }, (_, i) => ID(i)), nomes: Array.from({ length: tamanho }, (_, i) => `M${i}`), pos: 0 });
  const evento = (type, extra = {}) => j.tratar({ request: { type, ...extra }, context: { System: { device: { deviceId: APARELHO } } } });
  return { j, evento };
}
const falhou = (n, extra = {}) => ({ token: tk(n), error: { type: 'MEDIA_ERROR_UNKNOWN' }, ...extra });
const play = (r) => (r.response.directives || []).find((d) => d.type === 'AudioPlayer.Play');

test('faixa que falha passa para a próxima, substituindo a fila', async () => {
  const { evento } = montar();
  const d = play(await evento('AudioPlayer.PlaybackFailed', falhou(1)));
  assert.equal(d.playBehavior, 'REPLACE_ALL');
  assert.equal(d.audioItem.stream.token, tk(2));
  assert.equal(d.audioItem.stream.expectedPreviousToken, undefined);
});

test('falha da faixa enfileirada não corta a que ainda toca', async () => {
  const { evento } = montar();
  const d = play(await evento('AudioPlayer.PlaybackFailed', falhou(2, { currentPlaybackState: { token: tk(1), playerActivity: 'PLAYING' } })));
  assert.equal(d.playBehavior, 'ENQUEUE');
  assert.equal(d.audioItem.stream.token, tk(3));
  assert.equal(d.audioItem.stream.expectedPreviousToken, tk(1));
});

test('última faixa com falha encerra sem diretiva', async () => {
  const { evento } = montar(3);
  assert.equal(play(await evento('AudioPlayer.PlaybackFailed', falhou(2))), undefined);
});

test('3 falhas seguidas param a fila; faixa que toca zera a contagem', async () => {
  const { evento } = montar(10);
  assert.ok(play(await evento('AudioPlayer.PlaybackFailed', falhou(0))));
  assert.ok(play(await evento('AudioPlayer.PlaybackFailed', falhou(1))));
  await evento('AudioPlayer.PlaybackStarted', { token: tk(2) });
  assert.ok(play(await evento('AudioPlayer.PlaybackFailed', falhou(2))));
  assert.ok(play(await evento('AudioPlayer.PlaybackFailed', falhou(3))));
  assert.equal(play(await evento('AudioPlayer.PlaybackFailed', falhou(4))), undefined);
});

test('estado da fila acompanha os eventos do Echo', async () => {
  const { j, evento } = montar(2);
  const f = j.filas.get(APARELHO);
  await evento('AudioPlayer.PlaybackStarted', { token: tk(0) });
  assert.equal(f.estado, 'tocando');
  await evento('AudioPlayer.PlaybackStopped', { token: tk(0) });
  assert.equal(f.estado, 'pausado');
  await evento('AudioPlayer.PlaybackStarted', { token: tk(1) });
  await evento('AudioPlayer.PlaybackFinished', { token: tk(1) });
  assert.equal(f.estado, 'parado');
});

test('posição da faixa para a letra: início desconta o offset e a pausa guarda onde parou', async () => {
  const { j, evento } = montar(2);
  const f = j.filas.get(APARELHO);
  const antes = Date.now();
  await evento('AudioPlayer.PlaybackStarted', { token: tk(0), offsetInMilliseconds: 5000 });
  assert.ok(f.inicioMs >= antes - 5000 && f.inicioMs <= Date.now() - 5000);
  await evento('AudioPlayer.PlaybackStopped', { token: tk(0), offsetInMilliseconds: 7000 });
  assert.equal(f.inicioMs, 0);
  assert.equal(f.parouMs, 7000);
});
