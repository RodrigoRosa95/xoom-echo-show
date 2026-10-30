// Skill "meu tablet" da Alexa (22/10/2026): comandos para o tablet Xoom ("Echo Show" caseiro) pelo Echo Dot.
//   "Alexa, pede pro meu tablet abrir o WhatsApp"      → AbrirIntent  → "abre o whatsapp"
//   "Alexa, pede pro meu tablet tocar Coldplay"         → TocarIntent  → "toca coldplay no youtube"
//   "Alexa, pede pro meu tablet abrir a câmera" / "ver a garagem" → CameraIntent ("mostrar a câmera" a Alexa intercepta)
//   "Alexa, pede pro meu tablet fechar o YouTube" / "pausar" / "continuar" / "volume cinco" / "tela inicial"
// Resolve o problema do histórico de voz da Alexa (voice_command vazio no HA): aqui a Amazon manda a frase.
// Quem executa é o xoom-assistente (POST 127.0.0.1:8798/comando, mesmo comandos.py da voz do Xoom); a resposta
// sai na voz da própria Alexa e o texto aparece na tela do Show. Não tem porta própria: o alexa-jellyflix.js
// recebe em /api/jellyflix (o túnel só libera esse caminho) e repassa quando o applicationId é o desta skill.
'use strict';
const { verificarRequisicaoAlexa, responder } = require('./alexa-verificacao');

const log = (m) => console.log(`[${new Date().toLocaleString('sv-SE', { timeZone: 'America/Sao_Paulo' })}] [tablet] ${m}`);
const AJUDA = 'Diga, por exemplo: abrir o WhatsApp, tocar Coldplay, abrir a câmera, fechar o YouTube ou volume cinco.';

// intent → como a frase vira texto para o comandos.py (que entende português solto)
const FRASES = {
  AbrirIntent: (p) => `abre ${p}`,
  MostrarIntent: (p) => `mostra ${p}`,
  FecharIntent: (p) => `fecha ${p}`,
  // "toca X" sem dizer onde = YouTube (o Top 100 o comandos.py reconhece sozinho)
  TocarIntent: (p) => (/youtube|\btop\b|\bcem\b|100/i.test(p) ? `toca ${p}` : `toca ${p} no youtube`),
  VolumeIntent: (_, n) => `volume ${n}`,
  AumentarVolumeIntent: () => 'aumenta o volume',
  AbaixarVolumeIntent: () => 'abaixa o volume',
  TelaInicialIntent: () => 'tela inicial',
  // "mostrar a câmera" a Alexa toma para as câmeras dela e nem chama a skill: "abrir a câmera" / "ver a garagem"
  CameraIntent: () => 'abre a camera',
  PararTudoIntent: () => 'para tudo',
  AgendaIntent: () => 'qual meu próximo compromisso',
  'AMAZON.PauseIntent': () => 'pausa',
  'AMAZON.ResumeIntent': () => 'continua',
  'AMAZON.NavigateHomeIntent': () => 'tela inicial'
};

function criarTablet(deps = {}) {
  const cfg = {
    skillId: String(process.env.ALEXA_SKILL_ID_TABLET || '').trim(),
    assistente: 'http://127.0.0.1:8798/comando',
    ...deps
  };

  async function enviar(texto) {
    if (cfg.enviar) return cfg.enviar(texto);
    const r = await fetch(cfg.assistente, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto }), signal: AbortSignal.timeout(7000)
    });
    return r.json();
  }

  async function executar(texto) {
    log(`→ ${texto}`);
    let r;
    try {
      r = await enviar(texto);
    } catch (e) {
      log(`❌ assistente: ${e.message}`);
      return responder('O tablet não respondeu. O assistente no notebook pode estar desligado.');
    }
    return responder(r.depois ? 'Já estou fazendo.' : (r.resposta || 'Pronto.'));
  }

  async function tratar(corpo) {
    const req = corpo.request || {};
    if (req.type === 'LaunchRequest') {
      return responder('Tablet pronto. O que você quer?', { encerrar: false, pergunta: AJUDA });
    }
    if (req.type === 'SessionEndedRequest') return responder('', { encerrar: true });
    if (req.type !== 'IntentRequest') return responder('Não entendi.');
    const intent = req.intent || {};
    const slots = intent.slots || {};
    const pedido = String((slots.pedido || {}).value || '').trim();
    const numero = (slots.numero || {}).value;
    switch (intent.name) {
      case 'AMAZON.HelpIntent': return responder(AJUDA, { encerrar: false, pergunta: 'O que você quer?' });
      case 'AMAZON.StopIntent':
        // "pede pro meu tablet parar" chega como Stop numa sessão nova; dentro da conversa é só tchau
        return (corpo.session || {}).new ? executar('para tudo') : responder('Até mais.');
      case 'AMAZON.CancelIntent': return responder('Até mais.');
      case 'AMAZON.FallbackIntent': return responder(`Não entendi. ${AJUDA}`, { encerrar: false, pergunta: 'O que você quer?' });
    }
    const frase = FRASES[intent.name];
    if (!frase) return responder(`Não entendi. ${AJUDA}`, { encerrar: false, pergunta: 'O que você quer?' });
    if (intent.name === 'VolumeIntent' && (numero === undefined || numero === '?')) return responder('Qual volume, de zero a dez?', { encerrar: false, pergunta: 'Qual volume?' });
    if (/^(Abrir|Mostrar|Fechar|Tocar)Intent$/.test(intent.name) && !pedido) {
      return responder('O quê?', { encerrar: false, pergunta: AJUDA });
    }
    return executar(frase(pedido, numero));
  }

  async function rota(req, res) {
    let corpo;
    try {
      corpo = await (cfg.verificar || verificarRequisicaoAlexa)(req.headers, req.rawBody);
    } catch (e) {
      log(`🚫 Pedido recusado: ${e.message}`);
      return res.status(400).json({ error: 'pedido inválido' });
    }
    const sist = (corpo.context || {}).System || {};
    const appId = ((corpo.session || {}).application || {}).applicationId || (sist.application || {}).applicationId;
    if (!cfg.skillId || appId !== cfg.skillId) {
      log(`🚫 Skill não autorizada: ${appId}`);
      return res.status(403).json({ error: 'skill não autorizada' });
    }
    try {
      res.json(await tratar(corpo));
    } catch (e) {
      log(`❌ Erro: ${e && e.stack ? e.stack : e}`);
      res.json(responder('Tive um problema agora. Tente de novo.'));
    }
  }

  // applicationId sem verificar, só para escolher qual skill trata o pedido (cada uma verifica a assinatura)
  function ehDestaSkill(req) {
    const c = req.body || {};
    const appId = ((c.session || {}).application || {}).applicationId || (((c.context || {}).System || {}).application || {}).applicationId;
    return Boolean(cfg.skillId) && appId === cfg.skillId;
  }

  return { tratar, rota, ehDestaSkill };
}

module.exports = { criarTablet, FRASES };
