// Skill "meu tablet" da Alexa (22/10/2026): intent → frase que o xoom-assistente (comandos.py) entende.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { criarTablet } = require('../alexa-tablet');

function skill(resposta = {}) {
  const enviados = [];
  const t = criarTablet({ skillId: 'x', enviar: async (texto) => { enviados.push(texto); return { resposta: 'ok', ...resposta }; } });
  return { t, enviados };
}
const pedido = (name, slots = {}, nova = true) => ({ session: { new: nova }, request: { type: 'IntentRequest', intent: { name, slots } } });

test('intents viram frases para o comandos.py', async () => {
  const { t, enviados } = skill();
  await t.tratar(pedido('AbrirIntent', { pedido: { value: 'o WhatsApp' } }));
  await t.tratar(pedido('TocarIntent', { pedido: { value: 'Coldplay' } }));
  await t.tratar(pedido('TocarIntent', { pedido: { value: 'o top 100' } }));
  await t.tratar(pedido('VolumeIntent', { numero: { value: '5' } }));
  await t.tratar(pedido('AMAZON.StopIntent'));
  assert.deepEqual(enviados, ['abre o WhatsApp', 'toca Coldplay no youtube', 'toca o top 100', 'volume 5', 'para tudo']);
});

test('Stop dentro da conversa só se despede; demora vira "Já estou fazendo"', async () => {
  const { t, enviados } = skill({ resposta: null, depois: true });
  assert.equal((await t.tratar(pedido('AMAZON.StopIntent', {}, false))).response.outputSpeech.text, 'Até mais.');
  assert.equal(enviados.length, 0);
  assert.equal((await t.tratar(pedido('MostrarIntent', { pedido: { value: 'a câmera' } }))).response.outputSpeech.text, 'Já estou fazendo.');
});

test('assistente fora do ar responde com aviso', async () => {
  const t = criarTablet({ skillId: 'x', enviar: async () => { throw new Error('ECONNREFUSED'); } });
  assert.match((await t.tratar(pedido('AbrirIntent', { pedido: { value: 'o YouTube' } }))).response.outputSpeech.text, /não respondeu/);
});
