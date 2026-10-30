// Verificação dos pedidos da Alexa exigida pela Amazon para skills com endpoint próprio: certificado
// echo-api.amazon.com baixado de s3.amazonaws.com/echo.api/, cadeia até uma raiz confiável, assinatura SHA-256 do
// corpo bruto e horário com no máximo 150 s de diferença.
'use strict';
const crypto = require('crypto');
const tls = require('tls');

const TOLERANCIA_MS = 150 * 1000;
const cacheCadeias = new Map();   // url → { pems, ate }

// ---------- verificação do pedido ----------

function urlCadeiaValida(endereco) {
  try {
    const u = new URL(endereco);
    return u.protocol === 'https:' && u.hostname.toLowerCase() === 's3.amazonaws.com'
      && u.pathname.startsWith('/echo.api/') && (!u.port || u.port === '443');
  } catch {
    return false;
  }
}

function separarPems(texto) {
  return String(texto || '').match(/-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g) || [];
}

async function baixarCadeia(endereco) {
  const guardada = cacheCadeias.get(endereco);
  if (guardada && guardada.ate > Date.now()) return guardada.pems;
  const resp = await fetch(endereco, { signal: AbortSignal.timeout(10000) });
  if (!resp.ok) throw new Error(`certificado: HTTP ${resp.status}`);
  const pems = separarPems(await resp.text());
  cacheCadeias.set(endereco, { pems, ate: Date.now() + 60 * 60 * 1000 });
  return pems;
}

// Raízes: as embutidas no Node + as do sistema (/etc/ssl/certs), caso a Amazon troque de autoridade certificadora
let _raizes = null;
function raizesConfiaveis() {
  if (_raizes) return _raizes;
  let sistema = [];
  try { sistema = separarPems(require('fs').readFileSync('/etc/ssl/certs/ca-certificates.crt', 'utf8')); } catch { /* sem bundle */ }
  _raizes = [...new Set([...tls.rootCertificates, ...sistema])];
  return _raizes;
}

// opcoes.raizes / opcoes.baixar / opcoes.agora existem para os testes (cadeia gerada localmente)
async function verificarRequisicaoAlexa(headers, corpoBruto, opcoes = {}) {
  const endereco = headers['signaturecertchainurl'];
  const assinatura = headers['signature-256'];
  if (!endereco || !assinatura) throw new Error('sem cabeçalhos de assinatura');
  if (!urlCadeiaValida(endereco)) throw new Error('URL do certificado inválida');
  if (!corpoBruto || !corpoBruto.length) throw new Error('corpo vazio');

  const pems = await (opcoes.baixar || baixarCadeia)(endereco);
  if (!pems.length) throw new Error('cadeia de certificados vazia');
  const cadeia = pems.map(p => new crypto.X509Certificate(p));
  const agora = opcoes.agora || Date.now();
  for (const c of cadeia) {
    if (agora < Date.parse(c.validFrom) || agora > Date.parse(c.validTo)) throw new Error('certificado fora da validade');
  }
  const folha = cadeia[0];
  if (!/(^|,\s*)DNS:echo-api\.amazon\.com(,|$)/.test(String(folha.subjectAltName || ''))) {
    throw new Error('certificado não é do echo-api.amazon.com');
  }
  for (let i = 0; i < cadeia.length - 1; i++) {
    if (!cadeia[i].checkIssued(cadeia[i + 1]) || !cadeia[i].verify(cadeia[i + 1].publicKey)) {
      throw new Error('cadeia de certificados quebrada');
    }
  }
  // A cadeia da Amazon sobe até "Amazon Root CA 1" e continua por certificados cruzados antigos (Starfield) que já
  // saíram das listas de raízes: vale o primeiro elo emitido por uma raiz confiável (ou que É a raiz, mesma chave).
  const raizes = (opcoes.raizes || raizesConfiaveis()).map(p => new crypto.X509Certificate(p));
  const chave = (c) => c.publicKey.export({ type: 'spki', format: 'der' });
  const confiavel = cadeia.some(c => raizes.some(r => (c.checkIssued(r) && c.verify(r.publicKey))
    || (c.subject === r.subject && chave(c).equals(chave(r)))));
  if (!confiavel) throw new Error('cadeia não chega a uma raiz confiável');

  if (!crypto.verify('sha256', corpoBruto, folha.publicKey, Buffer.from(String(assinatura), 'base64'))) {
    throw new Error('assinatura inválida');
  }
  const corpo = JSON.parse(corpoBruto.toString('utf8'));
  const quando = Date.parse(corpo && corpo.request && corpo.request.timestamp);
  if (!Number.isFinite(quando) || Math.abs(agora - quando) > TOLERANCIA_MS) throw new Error('pedido fora do horário');
  return corpo;
}


function responder(texto, { encerrar = true, sessao = {}, pergunta = null } = {}) {
  return {
    version: '1.0',
    sessionAttributes: sessao,
    response: {
      outputSpeech: { type: 'PlainText', text: texto },
      ...(pergunta ? { reprompt: { outputSpeech: { type: 'PlainText', text: pergunta } } } : {}),
      shouldEndSession: encerrar
    }
  };
}

module.exports = { verificarRequisicaoAlexa, urlCadeiaValida, responder };
