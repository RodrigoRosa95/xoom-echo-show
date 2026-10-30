// Skill "jellyflix" da Alexa (13/10/2026): mídia por voz — Jellyseerr, Sonarr, Radarr, Jellyfin, qBittorrent.
//   "Alexa, pede pro jellyflix baixar Duna"            → busca no Jellyseerr, confirma título e ano, faz o pedido
//   "…o episódio novo de It's Always Sunny já chegou?"  → Sonarr: no Jellyfin? baixando (quanto falta)? quando sai?
//   "…o que estreia esta semana?"                       → calendário do Sonarr + lançamentos do Radarr
//   "…Ozark já tem dublado?"                            → temporadas dubladas x inglês; oferece buscar dublado agora
//   "…como estão os downloads?"                         → fila do Sonarr/Radarr
//   "…coloca Friends na TV" / "pausa a TV" / "volta trinta segundos" / "liga a legenda em português"
//   "…o que eu estava assistindo?"                      → itens em andamento; oferece colocar na TV
//   "…toca o top cinquenta" / "…toca Zé Neto"           → música do Jellyfin tocando na própria Echo (AudioPlayer)
//   "…me indica um filme dublado"                       → sorteia um filme dublado não visto
//   "…pausa os downloads" / "retoma os downloads"        → qBittorrent (+ arquivo PAUSAR-DOWNLOADS do qbit_top5)
//   "…está tudo funcionando?"                           → serviços, túnel, espaço nos discos, Claude
// Serviço próprio (pm2 "alexa-jellyflix", porta JELLYFLIX_PORTA), exposto pelo túnel da Cloudflare em
// https://SEU-DOMINIO/api/jellyflix (SÓ esse caminho). Assinatura da Amazon conferida em alexa-verificacao.js.
'use strict';
const path = require('path');
const os = require('os');
require('dotenv').config({ path: path.join(__dirname, '.env'), quiet: true });
const crypto = require('crypto');
const fs = require('fs');
const { execFile, spawn } = require('child_process');
const { verificarRequisicaoAlexa, responder } = require('./alexa-verificacao');

// ---------- configuração ----------
const env = (k, padrao = '') => String(process.env[k] || padrao).trim();
const CFG = {
  porta: Number(env('JELLYFLIX_PORTA', '3011')),
  skillId: env('ALEXA_SKILL_ID_JELLYFLIX'),
  urlPublica: env('JELLYFLIX_URL_PUBLICA', 'https://seu-dominio.exemplo/api/jellyflix').replace(/\/+$/, ''),
  segredo: env('JELLYFLIX_SEGREDO'),
  jellyfin: env('JELLYFLIX_JELLYFIN_URL', 'http://127.0.0.1:8096').replace(/\/+$/, ''),
  jellyfinKey: env('JELLYFIN_API_KEY'),
  sonarr: env('SONARR_URL', 'http://127.0.0.1:8989').replace(/\/+$/, ''),
  sonarrKey: env('SONARR_API_KEY'),
  radarr: env('RADARR_URL', 'http://127.0.0.1:7878').replace(/\/+$/, ''),
  radarrKey: env('RADARR_API_KEY'),
  jellyseerr: env('JELLYSEERR_URL', 'http://127.0.0.1:5055').replace(/\/+$/, ''),
  jellyseerrKey: env('JELLYSEERR_API_KEY'),
  qbit: env('JELLYFLIX_QBIT_URL', 'http://127.0.0.1:8080').replace(/\/+$/, ''),
  // TV: sessão do Jellyfin cujo nome do aparelho contém isto (app Android TV aberto; senão o receptor DLNA da TV)
  tv: env('JELLYFLIX_TV', 'Smart TV'),
  // pessoas da casa no Jellyfin (para "o que eu estava assistindo" e sugestões quando a TV não diz quem é)
  usuariosCasa: env('JELLYFLIX_USUARIOS', '').split(',').map(s => s.trim()).filter(Boolean),
  bibliotecaTop: env('JELLYFLIX_BIBLIOTECA_TOP', 'Top 50 Brasil'),
  pausaArquivo: env('JELLYFLIX_PAUSA', `${os.homedir()}/buscar-dublado/PAUSAR-DOWNLOADS`),
  buscarDublado: `${os.homedir()}/buscar-dublado/buscar_dublado.py`,
  claudeFora: `${os.homedir()}/buscar-dublado/claude-fora.json`,
  discos: [['disco do sistema', '/']],   // [nome falado, ponto de montagem]; acrescente os seus HDs
  sonarrDb: '/var/lib/sonarr/sonarr.db',
  // monitor_4tb.py congela (SIGSTOP) Sonarr/Radarr enquanto alguém assiste algo do HD de 4 TB: a Alexa só espera ~8 s
  congelados: '/run/monitor-4tb-congelados.json'
};
const SCORE_DUBLADO = 1000;   // perfil "Dual PT-BR": dublado pontua ≥ 1000 (ver servarr-dual-ptbr)
const log = (m) => console.log(`[${new Date().toLocaleString('sv-SE', { timeZone: 'America/Sao_Paulo' })}] ${m}`);

// ---------- utilitários ----------
function normalizar(t) {
  return String(t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}
const DIAS = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado'];
function quando(dataIso) {
  const fuso = { timeZone: 'America/Sao_Paulo' };
  const dia = (d) => new Date(d).toLocaleDateString('sv-SE', fuso);
  const d = new Date(dataIso);
  const hora = d.toLocaleTimeString('pt-BR', { ...fuso, hour: '2-digit', minute: '2-digit' });
  if (dia(d) === dia(Date.now())) return { dia: 'hoje', hora };
  if (dia(d) === dia(Date.now() + 86400000)) return { dia: 'amanhã', hora };
  const semana = Number(new Date(`${dia(d)}T12:00:00`).getDay());
  return { dia: DIAS[semana], hora };
}
function lista(itens) {
  const v = itens.filter(Boolean);
  return v.length <= 1 ? (v[0] || '') : `${v.slice(0, -1).join(', ')} e ${v[v.length - 1]}`;
}
function minutos(timeleft) {   // "01:23:45" → texto
  const m = String(timeleft || '').match(/(?:(\d+)\.)?(\d+):(\d+):(\d+)/);
  if (!m) return '';
  const total = (Number(m[1] || 0) * 24 + Number(m[2])) * 60 + Number(m[3]);
  if (total < 1) return 'menos de um minuto';
  if (total < 60) return `${total} minuto${total === 1 ? '' : 's'}`;
  const h = Math.floor(total / 60);
  return `${h} hora${h === 1 ? '' : 's'}${total % 60 ? ` e ${total % 60} minutos` : ''}`;
}

const MAX_FALHAS_SEGUIDAS = 3;

function criarJellyflix(cfg = CFG, deps = {}) {
  const buscar = deps.fetch || globalThis.fetch;
  const executar = deps.execFile || execFile;
  const disparar = deps.spawn || spawn;
  // deviceId → { ids, nomes, pos }  (música na Echo); em disco para sobreviver a reinícios do pm2
  const arqFilas = deps.arquivoFilas === undefined ? path.join(__dirname, '.alexa-jellyflix-filas.json') : deps.arquivoFilas;
  let filas;
  try { filas = new Map(Object.entries(JSON.parse(fs.readFileSync(arqFilas, 'utf8')))); } catch { filas = new Map(); }
  const salvarFilas = () => { if (arqFilas) try { fs.writeFileSync(arqFilas, JSON.stringify(Object.fromEntries(filas))); } catch { /* só memória */ } };

  async function api(base, caminho, { metodo = 'GET', corpo, headers = {}, tempo = 15000, texto = false } = {}) {
    const r = await buscar(base + caminho, {
      method: metodo, headers: { ...(corpo !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: corpo !== undefined ? JSON.stringify(corpo) : undefined, signal: AbortSignal.timeout(tempo)
    });
    if (!r.ok) throw new Error(`${base}${caminho.split('?')[0]}: HTTP ${r.status}`);
    if (texto) return r.text();
    const t = await r.text();
    return t ? JSON.parse(t) : null;
  }
  const jf = (c, o = {}) => api(cfg.jellyfin, c, { ...o, headers: { 'X-Emby-Token': cfg.jellyfinKey, ...(o.headers || {}) } });
  const sonarr = (c, o = {}) => api(cfg.sonarr, c, { tempo: 6000, ...o, headers: { 'X-Api-Key': cfg.sonarrKey } });
  const radarr = (c, o = {}) => api(cfg.radarr, c, { tempo: 6000, ...o, headers: { 'X-Api-Key': cfg.radarrKey } });
  const seerr = (c, o = {}) => api(cfg.jellyseerr, c, { ...o, headers: { 'X-Api-Key': cfg.jellyseerrKey } });

  // Sonarr/Radarr congelados pelo monitor do 4 TB → resposta imediata em vez de estourar o tempo da Alexa
  function congelado(...nomes) {
    try {
      const c = Object.values(JSON.parse(fs.readFileSync(cfg.congelados, 'utf8')));
      return nomes.filter(n => c.includes(n));
    } catch { return []; }
  }
  function respostaCongelado(nomes) {
    return responder(`${nomes.length > 1 ? `O ${nomes.join(' e o ')} estão pausados` : `O ${nomes[0]} está pausado`} enquanto alguém assiste algo guardado no HD de 4 tera. `
      + 'Quando a reprodução parar, ele volta em uns dois minutos; pergunte de novo depois.', { encerrar: true });
  }

  // ---------- usuários e TV ----------
  let _usuarios = null;
  async function usuarios() {
    if (!_usuarios) _usuarios = await jf('/Users');
    return _usuarios;
  }
  async function usuariosDaCasa() {
    const todos = await usuarios();
    const casa = todos.filter(u => cfg.usuariosCasa.some(n => normalizar(n) === normalizar(u.Name)));
    return casa.length ? casa : todos.filter(u => u.Policy && u.Policy.IsAdministrator);
  }
  async function sessaoTv() {
    const sessoes = (await jf('/Sessions')).filter(s => s.SupportsRemoteControl && normalizar(s.DeviceName).includes(normalizar(cfg.tv)));
    sessoes.sort((a, b) => (/android tv/i.test(b.Client) - /android tv/i.test(a.Client))
      || (Date.parse(b.LastActivityDate || 0) - Date.parse(a.LastActivityDate || 0)));
    return sessoes[0] || null;
  }
  const usuarioValido = (id) => Boolean(id) && !/^0+$/.test(String(id).replace(/-/g, ''));   // DLNA vem com id zerado
  async function usuarioParaTv(sessao) {
    if (sessao && usuarioValido(sessao.UserId)) return sessao.UserId;
    const casa = await usuariosDaCasa();
    return casa[0] && casa[0].Id;
  }

  // ---------- busca de séries/filmes ----------
  let _series = { quando: 0, lista: [] };
  async function seriesSonarr() {
    if (Date.now() - _series.quando > 10 * 60000) _series = { quando: Date.now(), lista: await sonarr('/api/v3/series') };
    return _series.lista;
  }
  // Nome falado (pode ser o nome em português) → série do Sonarr
  async function acharSerie(nome) {
    const alvo = normalizar(nome);
    if (!alvo) return null;
    const series = await seriesSonarr();
    const titulos = (s) => [s.title, ...(s.alternateTitles || []).map(a => a.title)].map(normalizar);
    let s = series.find(x => titulos(x).includes(alvo)) || series.find(x => titulos(x).some(t => t.includes(alvo) || (alvo.includes(t) && t.length > 3)));
    if (s) return s;
    // nome em português: o Jellyfin (metadados PT-BR) e o Jellyseerr (TMDB pt-BR) sabem o id
    try {
      const jfr = await jf(`/Items?SearchTerm=${encodeURIComponent(nome)}&IncludeItemTypes=Series&Recursive=true&Fields=ProviderIds&Limit=5`);
      for (const it of jfr.Items || []) {
        const tvdb = Number((it.ProviderIds || {}).Tvdb); const tmdb = Number((it.ProviderIds || {}).Tmdb);
        s = series.find(x => (tvdb && x.tvdbId === tvdb) || (tmdb && x.tmdbId === tmdb));
        if (s) return s;
      }
    } catch { /* segue para o Jellyseerr */ }
    try {
      const r = await seerr(`/api/v1/search?query=${encodeURIComponent(nome)}&language=pt-BR&page=1`);
      for (const it of (r.results || []).filter(x => x.mediaType === 'tv')) {
        s = series.find(x => x.tmdbId === it.id);
        if (s) return s;
      }
    } catch { /* não achou */ }
    return null;
  }
  async function acharFilme(nome) {
    const alvo = normalizar(nome);
    const filmes = await radarr('/api/v3/movie');
    return filmes.find(f => [f.title, f.originalTitle].map(normalizar).includes(alvo))
      || filmes.find(f => normalizar(f.title).includes(alvo) && alvo.length > 3) || null;
  }
  function arquivoDublado(f) {
    return f && ((f.customFormatScore || 0) >= SCORE_DUBLADO || (f.languages || []).some(l => /portug/i.test(l.name || '')));
  }

  // ---------- 1. pedir ----------
  async function pedir(titulo) {
    let tipoPedido = null;
    const m = String(titulo).match(/^(?:o\s+)?filme\s+(.+)$|^(?:a\s+)?s[eé]rie\s+(.+)$/i);
    if (m) { tipoPedido = m[1] ? 'movie' : 'tv'; titulo = (m[1] || m[2]).trim(); }
    const r = await seerr(`/api/v1/search?query=${encodeURIComponent(titulo)}&language=pt-BR&page=1`);
    const res = (r.results || []).filter(x => (x.mediaType === 'movie' || x.mediaType === 'tv') && (!tipoPedido || x.mediaType === tipoPedido));
    if (!res.length) return responder(`Não achei ${titulo}. Tente falar o nome de outro jeito.`, { encerrar: true });
    const alvo = normalizar(titulo);
    // vários com o mesmo nome ("Click": série de 2000 e filme de 2006) → o mais conhecido (votos no TMDB)
    const iguais = res.filter(x => [x.title, x.name, x.originalTitle, x.originalName].some(t => t && normalizar(t) === alvo))
      .sort((a, b) => (b.voteCount || 0) - (a.voteCount || 0));
    const it = iguais[0] || res[0];
    const nome = it.title || it.name;
    const outro = iguais.find(x => x.mediaType !== it.mediaType);
    const ano = String(it.releaseDate || it.firstAirDate || '').slice(0, 4);
    const tipo = it.mediaType === 'movie' ? 'filme' : 'série';
    const status = (it.mediaInfo || {}).status;
    if (status === 5) return responder(`${nome} já está no Jellyfin.`, { encerrar: true });
    if (status === 2 || status === 3) return responder(`${nome} já foi pedido e está a caminho.`, { encerrar: true });
    const p = { acao: 'pedir', id: it.id, tipo: it.mediaType, nome };
    const aviso = outro ? ` Também existe ${outro.mediaType === 'movie' ? 'um filme' : 'uma série'} com esse nome; se for ${outro.mediaType === 'movie' ? 'ele' : 'ela'}, diga não e peça "${outro.mediaType === 'movie' ? 'o filme' : 'a série'} ${nome}".` : '';
    return responder(`Achei ${nome}, ${tipo}${ano ? ` de ${ano}` : ''}.${aviso} Quer que eu peça?`,
      { encerrar: false, sessao: { pendente: p }, pergunta: `Peço ${nome}? Diga sim ou não.` });
  }
  async function confirmarPedido(p) {
    await seerr('/api/v1/request', { metodo: 'POST', corpo: { mediaType: p.tipo, mediaId: p.id, ...(p.tipo === 'tv' ? { seasons: 'all' } : {}) } });
    log(`🎬 Pedido feito: ${p.nome} (${p.tipo} ${p.id})`);
    return responder(`Pedido feito! Aviso aqui quando ${p.nome} chegar.`, { encerrar: true });
  }

  // ---------- 2. já chegou? ----------
  async function chegou(titulo) {
    const s = await acharSerie(titulo);
    if (!s) {
      const f = await acharFilme(titulo).catch(() => null);
      if (f) {
        if (f.hasFile) return responder(`Sim, ${f.title} já está no Jellyfin${arquivoDublado(f.movieFile) ? ', dublado' : ''}.`, { encerrar: true });
        return responder(`${f.title} ainda não chegou. ${f.digitalRelease ? `O lançamento digital é em ${new Date(f.digitalRelease).toLocaleDateString('pt-BR')}.` : ''}`.trim(), { encerrar: true });
      }
      return responder(`Não achei ${titulo} no Sonarr nem no Radarr. Se quiser, diga: baixar ${titulo}.`, { encerrar: true });
    }
    const eps = (await sonarr(`/api/v3/episode?seriesId=${s.id}`)).filter(e => e.seasonNumber > 0 && e.airDateUtc);
    const agora = Date.now();
    const exibidos = eps.filter(e => Date.parse(e.airDateUtc) <= agora).sort((a, b) => Date.parse(b.airDateUtc) - Date.parse(a.airDateUtc));
    const proximo = eps.filter(e => Date.parse(e.airDateUtc) > agora).sort((a, b) => Date.parse(a.airDateUtc) - Date.parse(b.airDateUtc))[0];
    const fraseProx = proximo ? ` O próximo sai ${quando(proximo.airDateUtc).dia} às ${quando(proximo.airDateUtc).hora}.` : '';
    const ult = exibidos[0];
    if (!ult) return responder(`${s.title} ainda não estreou.${fraseProx}`, { encerrar: true });
    const nomeEp = `o episódio ${ult.episodeNumber} da temporada ${ult.seasonNumber}`;
    if (ult.hasFile) {
      let dub = '';
      try { dub = arquivoDublado(await sonarr(`/api/v3/episodefile/${ult.episodeFileId}`)) ? ', dublado' : ', em inglês por enquanto'; } catch { /* sem detalhe */ }
      return responder(`Sim! ${nomeEp.charAt(0).toUpperCase() + nomeEp.slice(1)} de ${s.title} já está no Jellyfin${dub}.${fraseProx}`, { encerrar: true });
    }
    const fila = (await sonarr('/api/v3/queue?pageSize=1000')).records || [];
    const q = fila.find(r => r.episodeId === ult.id);
    if (q) {
      const pct = q.size ? Math.round(100 * (q.size - q.sizeleft) / q.size) : 0;
      const falta = minutos(q.timeleft);
      return responder(`Ainda não. ${nomeEp} de ${s.title} está baixando, ${pct} por cento${falta && pct < 100 ? `, faltam ${falta}` : ''}.${fraseProx}`, { encerrar: true });
    }
    return responder(`Ainda não. ${nomeEp} de ${s.title} saiu ${quando(ult.airDateUtc).dia === 'hoje' ? 'hoje' : `em ${new Date(ult.airDateUtc).toLocaleDateString('pt-BR')}`} e o sistema está procurando.${fraseProx}`, { encerrar: true });
  }

  // ---------- 3. estreias da semana ----------
  async function estreias() {
    const ini = new Date(); const fim = new Date(Date.now() + 7 * 86400000);
    const q = `start=${ini.toISOString()}&end=${fim.toISOString()}`;
    const [eps, filmes] = await Promise.all([
      sonarr(`/api/v3/calendar?${q}&includeSeries=true`).catch(() => []),
      radarr(`/api/v3/calendar?${q}`).catch(() => [])
    ]);
    const porSerie = new Map();
    for (const e of eps.filter(x => x.monitored && x.airDateUtc)) {
      const nome = (e.series && e.series.title) || 'série';
      if (!porSerie.has(nome)) porSerie.set(nome, e);
    }
    const porDia = new Map();   // "amanhã: Slow Horses e Merry Berry Love; quinta: …"
    for (const e of [...porSerie.values()].sort((a, b) => Date.parse(a.airDateUtc) - Date.parse(b.airDateUtc)).slice(0, 10)) {
      const d = quando(e.airDateUtc).dia;
      porDia.set(d, [...(porDia.get(d) || []), e.series.title]);
    }
    const series = [...porDia.entries()].map(([d, nomes]) => `${d}: ${lista(nomes)}`);
    const fl = filmes.map(f => {
      const d = [['digitalRelease', 'em digital'], ['inCinemas', 'nos cinemas']].find(([k]) => f[k] && Date.parse(f[k]) >= ini && Date.parse(f[k]) <= fim);
      return d ? `${f.title} ${d[1]} ${quando(f[d[0]]).dia}` : null;
    }).filter(Boolean).slice(0, 5);
    if (!series.length && !fl.length) return responder('Nada estreia esta semana entre as séries e filmes que você acompanha.', { encerrar: true });
    return responder([series.length ? `Séries esta semana. ${series.join('; ')}.` : '', fl.length ? `Filmes: ${lista(fl)}.` : ''].filter(Boolean).join(' '), { encerrar: true });
  }

  // ---------- 4. dublado? ----------
  async function dublado(titulo) {
    const s = await acharSerie(titulo);
    if (!s) {
      const f = await acharFilme(titulo).catch(() => null);
      if (!f) return responder(`Não achei ${titulo} no Sonarr nem no Radarr.`, { encerrar: true });
      if (!f.hasFile) return responder(`${f.title} ainda não foi baixado.`, { encerrar: true });
      if (arquivoDublado(f.movieFile)) return responder(`Sim, ${f.title} está dublado.`, { encerrar: true });
      return responder(`${f.title} ainda está em inglês. Quer que eu procure o dublado agora?`,
        { encerrar: false, sessao: { pendente: { acao: 'buscar-dublado', tipo: 'filme', titulo: f.title } }, pergunta: 'Procuro o dublado agora?' });
    }
    const arquivos = await sonarr(`/api/v3/episodefile?seriesId=${s.id}`);
    const porTemp = new Map();
    for (const f of arquivos) {
      const t = porTemp.get(f.seasonNumber) || { dub: 0, total: 0 };
      t.total += 1; if (arquivoDublado(f)) t.dub += 1;
      porTemp.set(f.seasonNumber, t);
    }
    const temps = (s.seasons || []).filter(x => x.seasonNumber > 0).map(x => x.seasonNumber);
    const dubladas = [], parciais = [], ingles = [], semArq = [];
    for (const n of temps) {
      const t = porTemp.get(n);
      if (!t) semArq.push(n); else if (t.dub === t.total) dubladas.push(n); else if (t.dub) parciais.push(n); else ingles.push(n);
    }
    const frase = (rot, ns) => ns.length ? `${ns.length === 1 ? `Temporada ${ns[0]}` : `Temporadas ${lista(ns.map(String))}`} ${rot}.` : '';
    const partes = [frase('dublada' + (dubladas.length > 1 ? 's' : ''), dubladas), frase('em parte dublada' + (parciais.length > 1 ? 's' : ''), parciais),
      frase('em inglês', ingles), frase('ainda sem arquivos', semArq)].filter(Boolean);
    if (!ingles.length && !parciais.length) {
      return responder(`${s.title}: ${partes.join(' ') || 'nenhum episódio baixado ainda.'}`, { encerrar: true });
    }
    return responder(`${s.title}: ${partes.join(' ')} Quer que eu procure dublado agora?`,
      { encerrar: false, sessao: { pendente: { acao: 'buscar-dublado', tipo: 'serie', titulo: s.title } }, pergunta: 'Procuro o dublado agora?' });
  }
  function confirmarBuscaDublado(p) {
    let whatsapp = '';
    try { whatsapp = (require('child_process').execSync('crontab -l', { encoding: 'utf8' }).match(/BUSCA_DUBLADO_WHATSAPP=(\d+)/) || [])[1] || ''; } catch { /* sem resumo */ }
    const saida = fs.openSync(`${os.homedir()}/buscar-dublado/logs/alexa-busca.log`, 'a');
    const filho = disparar('flock', ['-n', '/tmp/buscar-dublado.lock', 'env', `BUSCA_DUBLADO_WHATSAPP=${whatsapp}`,
      `PATH=${os.homedir()}/.local/bin:/usr/bin:/bin`, 'LANG=pt_BR.UTF-8', '/usr/bin/python3', cfg.buscarDublado,
      p.tipo === 'filme' ? '--filme' : '--serie', p.titulo, '--sem-titulos'], { detached: true, stdio: ['ignore', saida, saida] });
    filho.unref();
    log(`🔎 Busca de dublado disparada: ${p.titulo}`);
    return responder(`Busca iniciada para ${p.titulo}. O resultado chega no WhatsApp. Se outra busca estiver rodando, esta fica para a próxima.`, { encerrar: true });
  }

  // ---------- 5. downloads ----------
  async function downloads() {
    const [s, r] = await Promise.all([sonarr('/api/v3/queue?pageSize=1000&includeSeries=true&includeEpisode=true').catch(() => ({ records: [] })),
      radarr('/api/v3/queue?pageSize=1000&includeMovie=true').catch(() => ({ records: [] }))]);
    const vistos = new Set();
    const itens = [...(s.records || []), ...(r.records || [])].filter(q => {
      if (vistos.has(q.downloadId)) return false; vistos.add(q.downloadId);
      return q.status === 'downloading' && q.size;
    }).map(q => ({
      nome: q.movie ? q.movie.title : q.series ? `${q.series.title}${q.episode ? ` episódio ${q.episode.episodeNumber}` : ''}` : q.title,
      pct: Math.round(100 * (q.size - q.sizeleft) / q.size), falta: minutos(q.timeleft)
    })).sort((a, b) => b.pct - a.pct);
    const pausado = fs.existsSync(cfg.pausaArquivo);
    if (!itens.length) return responder(pausado ? 'Os downloads estão pausados.' : 'Nada baixando agora.', { encerrar: true });
    const top = itens.slice(0, 3).map(i => `${i.nome} em ${i.pct} por cento${i.falta && i.pct < 100 ? `, faltam ${i.falta}` : ''}`);
    return responder(`${itens.length} download${itens.length > 1 ? 's' : ''} ativo${itens.length > 1 ? 's' : ''}${pausado ? ', mas pausados por você' : ''}. ${lista(top)}.`, { encerrar: true });
  }

  // ---------- 6/7/9. TV ----------
  async function semTv() {
    return responder(`Não achei a TV. Abra o app do Jellyfin na ${cfg.tv} e tente de novo.`, { encerrar: true });
  }
  async function tocarNaTv(itemId, nome, posicao = 0, sessao = null) {
    sessao = sessao || await sessaoTv();
    if (!sessao) return semTv();
    // TV DLNA desligada ou travada segura o comando por até 100 s, e a Alexa só espera uns 8 s
    const envio = jf(`/Sessions/${sessao.Id}/Playing?playCommand=PlayNow&itemIds=${itemId}${posicao ? `&startPositionTicks=${posicao}` : ''}`, { metodo: 'POST', texto: true, tempo: 110000 })
      .then(() => 'ok', (e) => { log(`⚠️ TV não respondeu (${sessao.Client}): ${e.message}`); return 'erro'; });
    const r = await Promise.race([envio, new Promise(ok => setTimeout(() => ok('demorando'), 5000))]);
    if (r === 'erro') return responder('A TV não respondeu. Veja se ela está ligada e com o Jellyfin aberto.', { encerrar: true });
    log(`📺 ${r === 'ok' ? 'Tocando' : 'Mandado (TV demorando)'} na TV (${sessao.Client}): ${nome}`);
    if (r === 'demorando') return responder(`Mandei ${nome} para a TV. Ela está demorando para responder; se não começar, veja se está ligada.`, { encerrar: true });
    return responder(`Colocando ${nome} na TV.`, { encerrar: true });
  }
  async function colocarNaTv(titulo) {
    const sessao = await sessaoTv();
    if (!sessao) return semTv();
    const uid = await usuarioParaTv(sessao);
    const r = await jf(`/Users/${uid}/Items?SearchTerm=${encodeURIComponent(titulo)}&IncludeItemTypes=Movie,Series,Episode&Recursive=true&Limit=5`);
    const alvo = normalizar(titulo);
    const it = (r.Items || []).find(x => normalizar(x.Name) === alvo) || (r.Items || [])[0];
    if (!it) return responder(`Não achei ${titulo} no Jellyfin.`, { encerrar: true });
    if (it.Type === 'Series') {
      const prox = ((await jf(`/Shows/NextUp?SeriesId=${it.Id}&UserId=${uid}&Limit=1`)).Items || [])[0]
        || ((await jf(`/Shows/${it.Id}/Episodes?UserId=${uid}&Limit=1`)).Items || [])[0];
      if (!prox) return responder(`${it.Name} não tem episódios no Jellyfin.`, { encerrar: true });
      const pos = (prox.UserData && prox.UserData.PlaybackPositionTicks) || 0;
      return tocarNaTv(prox.Id, `${it.Name}, episódio ${prox.IndexNumber} da temporada ${prox.ParentIndexNumber}`, pos, sessao);
    }
    return tocarNaTv(it.Id, it.Name, (it.UserData && it.UserData.PlaybackPositionTicks) || 0, sessao);
  }
  async function continuar() {
    const sessao = await sessaoTv().catch(() => null);
    const uids = sessao && usuarioValido(sessao.UserId) ? [sessao.UserId] : (await usuariosDaCasa()).map(u => u.Id);
    const itens = [];
    for (const uid of uids) {
      for (const it of ((await jf(`/Users/${uid}/Items/Resume?Limit=3&MediaTypes=Video`)).Items || [])) itens.push({ ...it, uid });
    }
    itens.sort((a, b) => Date.parse((b.UserData || {}).LastPlayedDate || 0) - Date.parse((a.UserData || {}).LastPlayedDate || 0));
    if (!itens.length) return responder('Não há nada em andamento.', { encerrar: true });
    const nome = (it) => it.Type === 'Episode' ? `${it.SeriesName}, episódio ${it.IndexNumber}` : it.Name;
    const p = itens[0];
    const outros = itens.slice(1, 3).map(nome);
    return responder(`Você parou em ${nome(p)}${outros.length ? `. Também em andamento: ${lista(outros)}` : ''}. Quer que eu coloque ${nome(p)} na TV?`,
      { encerrar: false, sessao: { pendente: { acao: 'tv', itemId: p.Id, nome: nome(p), pos: (p.UserData || {}).PlaybackPositionTicks || 0 } }, pergunta: 'Coloco na TV?' });
  }
  async function controleTv(acao) {
    const sessao = await sessaoTv();
    if (!sessao) return semTv();
    if (!sessao.NowPlayingItem) return responder('A TV não está tocando nada agora.', { encerrar: true });
    if (acao === 'pausar' || acao === 'continuar') {
      await jf(`/Sessions/${sessao.Id}/Playing/${acao === 'pausar' ? 'Pause' : 'Unpause'}`, { metodo: 'POST', texto: true });
      return responder(acao === 'pausar' ? 'Pausado.' : 'Continuando.', { encerrar: true });
    }
    if (acao === 'voltar') {
      const pos = Math.max(0, ((sessao.PlayState || {}).PositionTicks || 0) - 30 * 10000000);
      await jf(`/Sessions/${sessao.Id}/Playing/Seek?seekPositionTicks=${pos}`, { metodo: 'POST', texto: true });
      return responder('Voltei trinta segundos.', { encerrar: true });
    }
    // legenda em português
    const uid = await usuarioParaTv(sessao);
    const item = await jf(`/Users/${uid}/Items/${sessao.NowPlayingItem.Id}?Fields=MediaStreams`);
    const leg = (item.MediaStreams || []).find(m => m.Type === 'Subtitle' && (/^(por|pob|pt|pt-br)$/i.test(m.Language || '') || /portugu/i.test(`${m.DisplayTitle} ${m.Title}`)));
    if (!leg) return responder('Esse vídeo ainda não tem legenda em português. O sistema gera uma em alguns minutos; depois, feche e abra o vídeo de novo.', { encerrar: true });
    await jf(`/Sessions/${sessao.Id}/Command`, { metodo: 'POST', corpo: { Name: 'SetSubtitleStreamIndex', Arguments: { Index: String(leg.Index) } }, texto: true });
    return responder('Legenda em português ligada.', { encerrar: true });
  }
  async function sugestao() {
    const sessao = await sessaoTv().catch(() => null);
    const uid = await usuarioParaTv(sessao);
    const r = await jf(`/Users/${uid}/Items?IncludeItemTypes=Movie&Recursive=true&IsPlayed=false&SortBy=Random&Limit=30&Fields=MediaStreams,Overview`);
    const dub = (r.Items || []).find(it => (it.MediaStreams || []).some(m => m.Type === 'Audio' && /^(por|pob|pt)/i.test(m.Language || '')));
    if (!dub) return responder('Não achei filme dublado que você ainda não viu.', { encerrar: true });
    const sinopse = String(dub.Overview || '').split(/(?<=[.!?])\s/)[0].slice(0, 220);
    return responder(`Que tal ${dub.Name}${dub.ProductionYear ? `, de ${dub.ProductionYear}` : ''}? ${sinopse} Quer que eu coloque na TV?`,
      { encerrar: false, sessao: { pendente: { acao: 'tv', itemId: dub.Id, nome: dub.Name, pos: 0 } }, pergunta: 'Coloco na TV?' });
  }

  // ---------- 8. música na Echo ----------
  function assinar(dados) {
    return crypto.createHmac('sha256', cfg.segredo).update(dados).digest('base64url').slice(0, 32);
  }
  function urlAudio(itemId) {
    const ate = Math.floor(Date.now() / 1000) + 12 * 3600;
    const dados = `${itemId}.${ate}`;
    return `${cfg.urlPublica}/audio/${dados}.${assinar(dados)}.mp3`;
  }
  function validarTokenAudio(tk) {
    const m = String(tk || '').match(/^([0-9a-f]{32})\.(\d+)\.([A-Za-z0-9_-]{32})(?:\.mp3)?$/);
    if (!m) return null;
    if (Number(m[2]) < Date.now() / 1000) return null;
    const esperado = assinar(`${m[1]}.${m[2]}`);
    if (!crypto.timingSafeEqual(Buffer.from(esperado), Buffer.from(m[3]))) return null;
    return m[1];
  }
  function diretivaPlay(deviceId, pos, comportamento = 'REPLACE_ALL', anterior = null, offset = 0) {
    const f = filas.get(deviceId);
    const token = `${deviceId.slice(-12)}|${pos}|${f.ids[pos]}`;
    return {
      type: 'AudioPlayer.Play', playBehavior: comportamento,
      audioItem: {
        stream: { url: urlAudio(f.ids[pos]), token, offsetInMilliseconds: offset, ...(anterior ? { expectedPreviousToken: anterior } : {}) },
        metadata: { title: f.nomes[pos], subtitle: 'Jellyfin' }
      }
    };
  }
  function soDiretivas(diretivas) {
    return { version: '1.0', response: { directives: diretivas, shouldEndSession: true } };
  }
  // resposta a evento da Echo (AudioPlayer.*, PlaybackController.*): só diretivas, sem shouldEndSession nem fala
  function paraEvento(r) {
    const d = (r && r.response && r.response.directives) || [];
    return { version: '1.0', response: d.length ? { directives: d } : {} };
  }
  async function idsMusica(busca) {
    const lib = (await jf('/Library/VirtualFolders')).find(v => normalizar(v.Name) === normalizar(cfg.bibliotecaTop));
    if (!busca) {
      if (!lib) return null;
      const r = await jf(`/Items?ParentId=${lib.ItemId}&Recursive=true&IncludeItemTypes=Audio&SortBy=IndexNumber,SortName&Limit=300`);
      return { itens: r.Items || [], rotulo: `o ${cfg.bibliotecaTop}` };
    }
    // nome de uma biblioteca de música ("toca trap" → biblioteca "Trap Brasil"), em ordem aleatória
    const bib = (await jf('/Library/VirtualFolders')).find(v => v.CollectionType === 'music' && normalizar(busca).length > 2
      && normalizar(v.Name).split(' ').includes(normalizar(busca).split(' ')[0]) && normalizar(v.Name).includes(normalizar(busca)));
    if (bib && normalizar(bib.Name) === normalizar(cfg.bibliotecaTop)) return idsMusica('');   // top 50 na ordem da parada
    if (bib) {
      const m = await jf(`/Items?ParentId=${bib.ItemId}&Recursive=true&IncludeItemTypes=Audio&SortBy=Random&Limit=300`);
      if ((m.Items || []).length) return { itens: m.Items, rotulo: `a pasta ${bib.Name}` };
    }
    const r = await jf(`/Items?SearchTerm=${encodeURIComponent(busca)}&IncludeItemTypes=Audio,MusicArtist,MusicAlbum&Recursive=true&Limit=10`);
    const res = r.Items || [];
    const artista = res.find(x => x.Type === 'MusicArtist');
    if (artista) {
      const m = await jf(`/Items?ArtistIds=${artista.Id}&IncludeItemTypes=Audio&Recursive=true&SortBy=Random&Limit=100`);
      if ((m.Items || []).length) return { itens: m.Items, rotulo: `músicas de ${artista.Name}` };
    }
    const album = res.find(x => x.Type === 'MusicAlbum');
    if (album) {
      const m = await jf(`/Items?ParentId=${album.Id}&IncludeItemTypes=Audio&Recursive=true&SortBy=IndexNumber`);
      if ((m.Items || []).length) return { itens: m.Items, rotulo: `o álbum ${album.Name}` };
    }
    const musicas = res.filter(x => x.Type === 'Audio');
    if (musicas.length) return { itens: musicas, rotulo: musicas[0].Name };
    return null;
  }
  async function tocarMusica(busca, deviceId) {
    const achado = await idsMusica(busca);
    if (!achado || !achado.itens.length) return responder(busca ? `Não achei ${busca} na biblioteca de música.` : 'A biblioteca de música está vazia.', { encerrar: true });
    filas.set(deviceId, { ids: achado.itens.map(i => i.Id), nomes: achado.itens.map(i => `${i.Name}${i.Artists && i.Artists[0] ? ` — ${i.Artists[0]}` : ''}`), pos: 0, rotulo: achado.rotulo, estado: 'tocando', quando: Date.now() });
    salvarFilas();
    const r = responder(`Tocando ${achado.rotulo}.`, { encerrar: true });
    r.response.directives = [diretivaPlay(deviceId, 0)];
    log(`🎵 Música na Echo: ${achado.rotulo} (${achado.itens.length} faixas)`);
    return r;
  }
  function posDoToken(token) {
    const p = String(token || '').split('|');
    return p.length === 3 ? Number(p[1]) : -1;
  }
  // estado da fila para a tela de música do Xoom (lê .alexa-jellyflix-filas.json); quando = aparelho mais recente
  function marcarEstado(f, estado) {
    f.estado = estado;
    f.quando = Date.now();
    salvarFilas();
  }
  // faixa que falha não derruba a fila: segue para a próxima; várias seguidas = problema geral (túnel, Jellyfin), desiste
  function aposFalha(deviceId, req) {
    const f = filas.get(deviceId);
    const pos = posDoToken(req.token) + 1;
    if (!f || pos <= 0 || pos >= f.ids.length) return soDiretivas([]);
    f.falhas = (f.falhas || 0) + 1;
    salvarFilas();
    if (f.falhas >= MAX_FALHAS_SEGUIDAS) {
      log(`⚠️ ${f.falhas} faixas seguidas falharam: parando a fila`);
      marcarEstado(f, 'parado');
      return soDiretivas([]);
    }
    // a faixa que falhou era a enfileirada e a anterior ainda toca: enfileira a seguinte sem cortar
    const atual = req.currentPlaybackState || {};
    if (atual.playerActivity === 'PLAYING' && atual.token && atual.token !== req.token) return soDiretivas([diretivaPlay(deviceId, pos, 'ENQUEUE', atual.token)]);
    return soDiretivas([diretivaPlay(deviceId, pos)]);
  }
  function eventoAudio(corpo, deviceId) {
    const req = corpo.request;
    const f = filas.get(deviceId);
    log(`🎵 ${req.type.replace('AudioPlayer.', '')} faixa ${posDoToken(req.token)}${req.offsetInMilliseconds ? ` em ${Math.round(req.offsetInMilliseconds / 1000)} s` : ''}${f ? '' : ' (sem fila: skill reiniciada?)'}`);
    if (req.type === 'AudioPlayer.PlaybackNearlyFinished' && f) {
      const pos = posDoToken(req.token) + 1;
      if (pos > 0 && pos < f.ids.length) return soDiretivas([diretivaPlay(deviceId, pos, 'ENQUEUE', req.token)]);
    }
    // inicioMs/parouMs: posição da faixa para a letra em tempo real no Xoom (a Echo só informa a posição nos eventos)
    if (req.type === 'AudioPlayer.PlaybackStarted' && f) { f.pos = Math.max(0, posDoToken(req.token)); f.falhas = 0; f.inicioMs = Date.now() - (req.offsetInMilliseconds || 0); marcarEstado(f, 'tocando'); }
    if (req.type === 'AudioPlayer.PlaybackStopped' && f) { f.inicioMs = 0; f.parouMs = req.offsetInMilliseconds || 0; marcarEstado(f, 'pausado'); }
    if (req.type === 'AudioPlayer.PlaybackFinished' && f && posDoToken(req.token) >= f.ids.length - 1) marcarEstado(f, 'parado');
    if (req.type === 'AudioPlayer.PlaybackFailed') {
      log(`⚠️ Echo não tocou: ${JSON.stringify(req.error || {}).slice(0, 200)}`);
      return aposFalha(deviceId, req);
    }
    return soDiretivas([]);
  }
  function pularMusica(deviceId, delta, corpo) {
    const f = filas.get(deviceId);
    if (!f) return null;
    const atual = posDoToken(((corpo.context || {}).AudioPlayer || {}).token);
    const pos = Math.min(Math.max((atual >= 0 ? atual : f.pos) + delta, 0), f.ids.length - 1);
    f.pos = pos;
    return soDiretivas([diretivaPlay(deviceId, pos)]);
  }
  function retomarMusica(deviceId, corpo) {
    const ap = (corpo.context || {}).AudioPlayer || {};
    const f = filas.get(deviceId);
    const pos = posDoToken(ap.token);
    if (!f || pos < 0) return null;
    return soDiretivas([diretivaPlay(deviceId, pos, 'REPLACE_ALL', null, ap.offsetInMilliseconds || 0)]);
  }
  function musicaTocando(corpo) {
    const ap = (corpo.context || {}).AudioPlayer || {};
    return ap.playerActivity === 'PLAYING' || ap.playerActivity === 'PAUSED' || ap.playerActivity === 'STOPPED' && Boolean(ap.token);
  }

  // ---------- 13. pausar downloads ----------
  async function qbitSessao() {
    let user = '', senha = '';
    const Database = require('better-sqlite3');
    const db = new Database(cfg.sonarrDb, { readonly: true, fileMustExist: true });
    try {
      const s = JSON.parse(db.prepare("select Settings from DownloadClients where Implementation='QBittorrent' limit 1").get().Settings);
      user = s.username || ''; senha = s.password || '';
    } finally { db.close(); }
    const r = await buscar(`${cfg.qbit}/api/v2/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', Referer: cfg.qbit },
      body: new URLSearchParams({ username: user, password: senha }), signal: AbortSignal.timeout(15000) });
    const cookie = (r.headers.get('set-cookie') || '').split(';')[0];
    if (!r.ok || !cookie) throw new Error('login do qBittorrent falhou');
    return (c, corpo) => buscar(`${cfg.qbit}${c}`, { method: corpo ? 'POST' : 'GET', headers: { Cookie: cookie, Referer: cfg.qbit, ...(corpo ? { 'Content-Type': 'application/x-www-form-urlencoded' } : {}) },
      body: corpo ? new URLSearchParams(corpo) : undefined, signal: AbortSignal.timeout(30000) });
  }
  async function pausarDownloads(pausar) {
    const qb = await qbitSessao();
    if (pausar) {
      const ativos = await (await qb('/api/v2/torrents/info?filter=downloading')).json();
      const hashes = ativos.map(t => t.hash);
      fs.writeFileSync(cfg.pausaArquivo, `Pausado pela Alexa em ${new Date().toISOString()}\n${hashes.join('\n')}\n`);
      if (hashes.length) await qb('/api/v2/torrents/pause', { hashes: hashes.join('|') });
      log(`⏸️ Downloads pausados pela Alexa (${hashes.length})`);
      return responder(hashes.length ? `Pausei ${hashes.length} download${hashes.length > 1 ? 's' : ''}. Para voltar, diga: retoma os downloads.` : 'Não havia nada baixando, mas deixei pausado.', { encerrar: true });
    }
    let hashes = [];
    try { hashes = fs.readFileSync(cfg.pausaArquivo, 'utf8').split('\n').slice(1).filter(h => /^[0-9a-f]{40}$/.test(h)); fs.unlinkSync(cfg.pausaArquivo); } catch { /* não estava pausado */ }
    if (hashes.length) await qb('/api/v2/torrents/resume', { hashes: hashes.join('|') });
    log(`▶️ Downloads retomados pela Alexa (${hashes.length})`);
    return responder('Downloads retomados. O controle automático da fila volta em até dois minutos.', { encerrar: true });
  }

  // ---------- 14. status ----------
  async function status() {
    const teste = async (nome, fn) => { try { await fn(); return null; } catch { return nome; } };
    const ativo = (servico) => new Promise((ok, falha) => executar('systemctl', ['is-active', servico], { timeout: 5000 }, (e, out) => (String(out).trim() === 'active' ? ok() : falha(new Error(servico)))));
    const fora = (await Promise.all([
      teste('Sonarr', () => sonarr('/api/v3/system/status', { tempo: 5000 })),
      teste('Radarr', () => radarr('/api/v3/system/status', { tempo: 5000 })),
      teste('Jellyfin', () => jf('/System/Info/Public', { tempo: 5000 })),
      teste('Jellyseerr', () => seerr('/api/v1/status', { tempo: 5000 })),
      teste('qBittorrent', () => api(cfg.qbit, '/', { tempo: 5000, texto: true })),
      teste('túnel da Cloudflare', () => ativo('cloudflared'))
    ])).filter(Boolean);
    const espaco = cfg.discos.map(([nome, p]) => {
      try { const s = fs.statfsSync(p); return { nome, gb: Math.round(s.bavail * s.bsize / 1e9) }; } catch { return null; }
    }).filter(Boolean);
    const apertados = espaco.filter(d => d.gb < 30).map(d => `${d.nome} com ${d.gb} giga${d.gb === 1 ? '' : 's'} livre${d.gb === 1 ? '' : 's'}`);
    let claude = '';
    try { const c = JSON.parse(fs.readFileSync(cfg.claudeFora, 'utf8')); if (c.ate * 1000 > Date.now()) claude = ' O Claude está fora do ar; os scripts estão usando as reservas sem IA.'; } catch { /* Claude ok */ }
    const pausa = fs.existsSync(cfg.pausaArquivo) ? ' Os downloads estão pausados por você.' : '';
    const partes = [fora.length ? `Fora do ar: ${lista(fora)}.` : 'Todos os serviços estão no ar.',
      apertados.length ? `Pouco espaço: ${lista(apertados)}.` : 'Espaço nos discos ok.'];
    return responder(partes.join(' ') + pausa + claude, { encerrar: true });
  }

  // ---------- roteamento ----------
  const AJUDA = 'Você pode dizer: baixar Duna; o episódio novo de Friends já chegou; o que estreia esta semana; Ozark tem dublado; como estão os downloads; coloca Friends na TV; toca o top cinquenta; me indica um filme; ou: está tudo funcionando.';

  async function tratar(corpo) {
    const req = corpo.request || {};
    const sist = (corpo.context || {}).System || {};
    const deviceId = ((sist.device || {}).deviceId) || 'sem-aparelho';
    if (/^(AudioPlayer|PlaybackController)\./.test(req.type)) {
      if (req.type === 'PlaybackController.NextCommandIssued') return paraEvento(pularMusica(deviceId, 1, corpo));
      if (req.type === 'PlaybackController.PreviousCommandIssued') return paraEvento(pularMusica(deviceId, -1, corpo));
      if (req.type === 'PlaybackController.PauseCommandIssued') return paraEvento(soDiretivas([{ type: 'AudioPlayer.Stop' }]));
      if (req.type === 'PlaybackController.PlayCommandIssued') return paraEvento(retomarMusica(deviceId, corpo));
      return paraEvento(eventoAudio(corpo, deviceId));
    }
    if (req.type === 'LaunchRequest') return responder(`Meu cinema aberto. ${AJUDA}`, { encerrar: false, pergunta: 'O que você quer?' });
    if (req.type === 'SessionEndedRequest' || req.type === 'System.ExceptionEncountered') {
      if (req.error) log(`⚠️ ${req.type}: ${JSON.stringify(req.error).slice(0, 300)}${req.cause ? ` (causa: ${String(req.cause.requestId || '').slice(-12)})` : ''}`);
      return { version: '1.0', response: {} };
    }
    if (req.type !== 'IntentRequest') return responder('Não entendi.', { encerrar: true });

    const nome = req.intent.name;
    const slots = req.intent.slots || {};
    const slot = (k) => (slots[k] && slots[k].value ? String(slots[k].value).trim() : '');
    const pendente = ((corpo.session || {}).attributes || {}).pendente;
    log(`🗣️ ${nome}${Object.keys(slots).length ? ` ${JSON.stringify(Object.fromEntries(Object.keys(slots).map(k => [k, slot(k)])))}` : ''}`);

    const precisaArr = { ChegouIntent: ['Sonarr', 'Radarr'], EstreiasIntent: ['Sonarr', 'Radarr'], DubladoIntent: ['Sonarr', 'Radarr'], DownloadsIntent: ['Sonarr', 'Radarr'] };
    if (precisaArr[nome]) {
      const parados = congelado(...precisaArr[nome]);
      if (parados.length) return respostaCongelado(parados);
    }

    switch (nome) {
      case 'PedirIntent': return slot('titulo') ? pedir(slot('titulo')) : responder('Qual filme ou série?', { encerrar: false, pergunta: 'Diga: baixar e o nome.' });
      case 'ChegouIntent': return chegou(slot('titulo'));
      case 'EstreiasIntent': return estreias();
      case 'DubladoIntent': return dublado(slot('titulo'));
      case 'DownloadsIntent': return downloads();
      case 'TocarTvIntent': return colocarNaTv(slot('titulo'));
      case 'ContinuarAssistindoIntent': return continuar();
      case 'TvPausarIntent': return controleTv('pausar');
      case 'TvContinuarIntent': return controleTv('continuar');
      case 'TvVoltarIntent': return controleTv('voltar');
      case 'TvLegendaIntent': return controleTv('legenda');
      case 'MusicaIntent': return tocarMusica(slot('busca'), deviceId);
      case 'MusicaTopIntent': return tocarMusica('', deviceId);
      case 'SugestaoIntent': return sugestao();
      case 'PausarDownloadsIntent': return pausarDownloads(true);
      case 'RetomarDownloadsIntent': return pausarDownloads(false);
      case 'StatusIntent': return status();
      case 'AMAZON.YesIntent':
        if (!pendente) return responder('Não há nada para confirmar.', { encerrar: true });
        if (pendente.acao === 'pedir') return confirmarPedido(pendente);
        if (pendente.acao === 'buscar-dublado') return confirmarBuscaDublado(pendente);
        if (pendente.acao === 'tv') return tocarNaTv(pendente.itemId, pendente.nome, pendente.pos);
        return responder('Não há nada para confirmar.', { encerrar: true });
      case 'AMAZON.NoIntent': return responder('Tudo bem.', { encerrar: true });
      case 'AMAZON.PauseIntent':
        if (musicaTocando(corpo)) return soDiretivas([{ type: 'AudioPlayer.Stop' }]);
        return controleTv('pausar');
      case 'AMAZON.ResumeIntent':
        return retomarMusica(deviceId, corpo) || controleTv('continuar');
      case 'AMAZON.NextIntent': return pularMusica(deviceId, 1, corpo) || responder('Não há música tocando.', { encerrar: true });
      case 'AMAZON.PreviousIntent': return pularMusica(deviceId, -1, corpo) || responder('Não há música tocando.', { encerrar: true });
      case 'AMAZON.StartOverIntent': return pularMusica(deviceId, 0, corpo) || responder('Não há música tocando.', { encerrar: true });
      case 'AMAZON.LoopOnIntent': case 'AMAZON.LoopOffIntent': case 'AMAZON.ShuffleOnIntent': case 'AMAZON.ShuffleOffIntent':
      case 'AMAZON.RepeatIntent':
        return responder('Ainda não sei fazer isso com a música.', { encerrar: true });
      case 'AMAZON.CancelIntent': case 'AMAZON.StopIntent':
        return musicaTocando(corpo) ? soDiretivas([{ type: 'AudioPlayer.ClearQueue', clearBehavior: 'CLEAR_ALL' }, { type: 'AudioPlayer.Stop' }]) : responder('Até mais.', { encerrar: true });
      case 'AMAZON.HelpIntent': return responder(AJUDA, { encerrar: false, pergunta: 'O que você quer?' });
      default: return responder(`Não entendi. ${AJUDA}`, { encerrar: false, pergunta: 'O que você quer?' });
    }
  }

  async function rota(req, res) {
    let corpo;
    try {
      corpo = await (deps.verificar || verificarRequisicaoAlexa)(req.headers, req.rawBody);
    } catch (e) {
      log(`🚫 Pedido recusado: ${e.message}`);
      return res.status(400).json({ error: 'pedido inválido' });
    }
    const sist = (corpo.context || {}).System || {};
    const appId = ((corpo.session || {}).application || {}).applicationId || (sist.application || {}).applicationId;
    if (!cfg.skillId || appId !== cfg.skillId) {
      log(`🚫 Skill não autorizada: ${appId} (configure ALEXA_SKILL_ID_JELLYFLIX no .env)`);
      return res.status(403).json({ error: 'skill não autorizada' });
    }
    try {
      res.json(await tratar(corpo));
    } catch (e) {
      log(`❌ Erro: ${e && e.stack ? e.stack : e}`);
      const tipo = (corpo.request || {}).type || '';
      res.json(/^(AudioPlayer|PlaybackController)\./.test(tipo) ? paraEvento(null) : responder('Tive um problema agora. Tente de novo em instantes.', { encerrar: true }));
    }
  }

  // Áudio para a Echo: token assinado (12 h) → stream do Jellyfin convertido para MP3
  async function rotaAudio(req, res) {
    const itemId = validarTokenAudio(req.params.token);
    if (!itemId) return res.status(403).end();
    const u = `${cfg.jellyfin}/Audio/${itemId}/universal?UserId=${(await usuarios()).find(x => x.Policy && x.Policy.IsAdministrator).Id}`
      + '&DeviceId=alexa-jellyflix&Container=mp3&TranscodingContainer=mp3&TranscodingProtocol=http&AudioCodec=mp3&MaxStreamingBitrate=320000';
    const r = await buscar(u, { headers: { 'X-Emby-Token': cfg.jellyfinKey, ...(req.headers.range ? { Range: req.headers.range } : {}) } });
    res.status(r.status);
    for (const h of ['content-type', 'content-length', 'accept-ranges', 'content-range']) if (r.headers.get(h)) res.setHeader(h, r.headers.get(h));
    if (!r.body) return res.end();
    const { Readable } = require('stream');
    Readable.fromWeb(r.body).on('error', () => res.end()).pipe(res);
  }

  return { tratar, rota, rotaAudio, urlAudio, validarTokenAudio, acharSerie, filas, diretivaPlay };
}

if (require.main === module) {
  const express = require('express');
  const app = express();
  const j = criarJellyflix();
  // a skill "meu tablet" (alexa-tablet.js) usa o mesmo endereço: o túnel só libera /api/jellyflix
  const tablet = require('./alexa-tablet').criarTablet();
  app.post('/api/jellyflix', express.json({ limit: '1mb', verify: (req, res, buf) => { req.rawBody = Buffer.from(buf); } }),
    (req, res) => (tablet.ehDestaSkill(req) ? tablet.rota(req, res) : j.rota(req, res)));
  app.get('/api/jellyflix/audio/:token', (req, res) => j.rotaAudio(req, res).catch((e) => { log(`❌ Áudio: ${e.message}`); res.status(502).end(); }));
  app.listen(CFG.porta, '127.0.0.1', () => log(`🎬 Skill jellyflix ouvindo em 127.0.0.1:${CFG.porta}${CFG.skillId ? '' : ' (falta ALEXA_SKILL_ID_JELLYFLIX: tudo será recusado)'}`));
}

module.exports = { criarJellyflix, normalizar, minutos };
