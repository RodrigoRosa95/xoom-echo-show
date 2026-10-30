package br.jellyxoom;

import android.app.Activity;
import android.app.ProgressDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Parcelable;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Prepara e toca um item no MX Player:
 *  - SEMPRE em 480p (pedido do usuário 23/10/2026): DIRETO só se o arquivo já for H.264 até 854x480 (nível 4.1, 8 bits;
 *    áudio AAC/MP3/AC3/MP2); senão pede ao Jellyfin H.264 480p + AAC estéreo em HLS (teto configurável no menu);
 *  - se o áudio em português não for a faixa padrão do arquivo, pede só o remux (vídeo copiado) com essa faixa;
 *  - sem áudio em português, liga a legenda PT-BR (texto vai como .srt externo; imagem é queimada na conversão);
 *  - sem áudio NEM legenda em português, espera até 3 min o legenda_na_hora.py do notebook criar a legenda (ele
 *    começa quando o app avisa /Sessions/Playing) antes de abrir o MX, que só lê legendas ao abrir (23/10/2026);
 *  - ao voltar do MX grava a posição no Jellyfin e, se o episódio terminou, emenda o próximo;
 *  - canal ao vivo (30/10/2026): o Jellyfin abre o stream e devolve sempre conversão HLS em 480p; ao voltar, fecha.
 */
public class TocarActivity extends Activity {
    static final String[] MX = {"com.mxtech.videoplayer.pro", "com.mxtech.videoplayer.ad"};
    static final long TICKS_MS = 10000L;

    ProgressDialog progresso;
    String itemId, mediaSourceId, playSessionId, metodo, serieId, tipo, liveStreamId;
    long duracao, inicioTicks;
    Integer audioIdx, legendaIdx;
    volatile boolean tocando, faltaLegenda, semLegenda;
    static final long ESPERA_LEGENDA_MS = 180000;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Api.carregar(this);
        progresso = new ProgressDialog(this);
        progresso.setMessage("Preparando…");
        progresso.setCancelable(true);
        progresso.setOnCancelListener(new DialogInterface.OnCancelListener() {
            public void onCancel(DialogInterface d) { finish(); }
        });
        preparar(getIntent().getStringExtra("id"), getIntent().getBooleanExtra("doInicio", false));
    }

    void preparar(final String id, final boolean doInicio) {
        progresso.show();
        new Thread() {
            public void run() {
                try {
                    Intent mx = montar(id, doInicio);
                    if (faltaLegenda) mx = esperarLegenda(id, doInicio, mx);
                    final Intent fMx = mx;
                    runOnUiThread(new Runnable() { public void run() { abrirMx(fMx); } });
                } catch (final Exception e) {
                    runOnUiThread(new Runnable() {
                        public void run() {
                            Toast.makeText(TocarActivity.this, "Não deu para tocar: " + e.getMessage(), Toast.LENGTH_LONG).show();
                            finish();
                        }
                    });
                }
            }
        }.start();
    }

    static boolean portugues(JSONObject s) {
        String l = s.optString("Language").toLowerCase();
        String t = (s.optString("DisplayTitle") + " " + s.optString("Title")).toLowerCase();
        return l.equals("por") || l.equals("pt") || l.equals("pob") || l.equals("ptb") || l.startsWith("pt-")
                || t.contains("portugu") || t.contains("dublado") || t.contains("brazil");
    }

    static final int LARGURA = 854, ALTURA = 480;

    /** Garante MaxWidth/MaxHeight de 480p na URL de conversão (o Jellyfin às vezes só limita pelo bitrate). */
    static String em480p(String u) {
        u = u.replaceAll("([?&])(MaxWidth|MaxHeight|Width|Height)=[^&]*", "$1").replaceAll("&{2,}", "&").replace("?&", "?");
        return u + (u.contains("?") ? "&" : "?") + "MaxWidth=" + LARGURA + "&MaxHeight=" + ALTURA;
    }

    static JSONObject perfil() throws Exception {
        JSONArray direto = new JSONArray().put(new JSONObject().put("Type", "Video")
                .put("Container", "mp4,m4v,mkv,mov,avi,ts,mpegts,3gp")
                .put("VideoCodec", "h264,mpeg4,mpeg2video").put("AudioCodec", "aac,mp3,ac3,mp2"));
        JSONArray conversao = new JSONArray().put(new JSONObject().put("Type", "Video").put("Container", "ts")
                .put("VideoCodec", "h264").put("AudioCodec", "aac").put("Protocol", "hls").put("Context", "Streaming")
                .put("MaxAudioChannels", "2").put("MinSegments", 1).put("BreakOnNonKeyFrames", true));
        JSONArray cond = new JSONArray()
                .put(cond("LessThanEqual", "Width", String.valueOf(LARGURA))).put(cond("LessThanEqual", "Height", String.valueOf(ALTURA)))
                .put(cond("LessThanEqual", "VideoLevel", "41")).put(cond("LessThanEqual", "VideoBitDepth", "8"))
                .put(cond("EqualsAny", "VideoProfile", "baseline|constrained baseline|main|high"));
        JSONArray codecs = new JSONArray().put(new JSONObject().put("Type", "Video").put("Codec", "h264").put("Conditions", cond));
        JSONArray legendas = new JSONArray();
        for (String f : new String[]{"srt", "subrip", "ass", "ssa", "vtt", "webvtt"})
            legendas.put(new JSONObject().put("Format", f).put("Method", "External"));
        for (String f : new String[]{"pgssub", "pgs", "dvdsub", "dvbsub", "vobsub"})
            legendas.put(new JSONObject().put("Format", f).put("Method", "Encode"));
        return new JSONObject().put("Name", "Xoom MX Player").put("MaxStaticBitrate", 40000000)
                .put("MaxStreamingBitrate", 40000000).put("DirectPlayProfiles", direto)
                .put("TranscodingProfiles", conversao).put("CodecProfiles", codecs).put("SubtitleProfiles", legendas)
                .put("ContainerProfiles", new JSONArray()).put("ResponseProfiles", new JSONArray());
    }

    static JSONObject cond(String c, String prop, String valor) throws Exception {
        return new JSONObject().put("Condition", c).put("Property", prop).put("Value", valor).put("IsRequired", false);
    }

    JSONObject playbackInfo(String id, int bitrate, boolean permitirDireto) throws Exception {
        String q = "/Items/" + id + "/PlaybackInfo?userId=" + Api.userId + "&MaxStreamingBitrate=" + bitrate
                + "&EnableDirectPlay=" + permitirDireto + "&EnableDirectStream=true&EnableTranscoding=true"
                + "&AutoOpenLiveStream=true&MediaSourceId=" + mediaSourceId
                + (audioIdx != null ? "&AudioStreamIndex=" + audioIdx : "")
                + (legendaIdx != null ? "&SubtitleStreamIndex=" + legendaIdx : "&SubtitleStreamIndex=-1");
        return Api.post(q, new JSONObject().put("DeviceProfile", perfil()));
    }

    Intent montar(String id, boolean doInicio) throws Exception {
        itemId = id;
        JSONObject item = Api.get("/Items/" + id + "?userId=" + Api.userId + "&Fields=MediaSources,MediaStreams");
        tipo = item.optString("Type");
        if ("TvChannel".equals(tipo)) return montarCanal(item);
        serieId = item.optString("SeriesId", null);
        duracao = item.optLong("RunTimeTicks");
        JSONObject ud = item.optJSONObject("UserData");
        long inicio = doInicio || ud == null ? 0 : ud.optLong("PlaybackPositionTicks");
        if (duracao > 0 && inicio > duracao * 95 / 100) inicio = 0;

        JSONArray fontes = item.optJSONArray("MediaSources");
        if (fontes == null || fontes.length() == 0) throw new Exception("item sem arquivo de vídeo");
        JSONObject fonte = fontes.getJSONObject(0);
        mediaSourceId = fonte.getString("Id");
        JSONArray streams = fonte.optJSONArray("MediaStreams");

        // faixa que o MX abre sozinho = a marcada como padrão, senão a primeira
        JSONObject audioMx = null, audioPt = null, legPt = null;
        for (int i = 0; streams != null && i < streams.length(); i++) {
            JSONObject s = streams.getJSONObject(i);
            if ("Audio".equals(s.optString("Type"))) {
                if (audioMx == null || (s.optBoolean("IsDefault") && !audioMx.optBoolean("IsDefault"))) audioMx = s;
                if (audioPt == null && portugues(s)) audioPt = s;
            } else if ("Subtitle".equals(s.optString("Type")) && portugues(s)) {
                if (legPt == null || (legPt.optBoolean("IsForced") && !s.optBoolean("IsForced"))) legPt = s;
            }
        }
        audioIdx = audioPt != null ? audioPt.getInt("Index") : (audioMx != null ? audioMx.getInt("Index") : null);
        legendaIdx = audioPt == null && legPt != null ? legPt.getInt("Index") : null;
        faltaLegenda = audioPt == null && legPt == null && ("Episode".equals(tipo) || "Movie".equals(tipo));
        boolean audioForaDoPadrao = audioPt != null && audioMx != null && audioPt.getInt("Index") != audioMx.getInt("Index");

        // 1ª tentativa: direto, sem teto (rede local); 2ª: conversão/remux com o teto escolhido
        JSONObject info = playbackInfo(id, 40000000, !audioForaDoPadrao);
        JSONObject ms = info.getJSONArray("MediaSources").getJSONObject(0);
        String url;
        if (ms.optBoolean("SupportsDirectPlay") && !audioForaDoPadrao) {
            metodo = "DirectPlay";
            url = Api.comChave("/Videos/" + id + "/stream?static=true&mediaSourceId=" + mediaSourceId
                    + "&deviceId=" + Api.enc(Api.deviceId));
        } else {
            info = playbackInfo(id, Api.bitrateConversao, false);
            ms = info.getJSONArray("MediaSources").getJSONObject(0);
            String tu = ms.optString("TranscodingUrl", null);
            if (tu == null) throw new Exception("o servidor não ofereceu conversão");
            metodo = "Transcode";
            url = Api.comChave(em480p(tu));
        }
        playSessionId = info.optString("PlaySessionId");

        String titulo = item.optString("Name");
        if ("Episode".equals(tipo))
            titulo = item.optString("SeriesName") + " T" + item.optInt("ParentIndexNumber") + "E" + item.optInt("IndexNumber") + " · " + titulo;

        Intent mx = new Intent(Intent.ACTION_VIEW);
        mx.setDataAndType(Uri.parse(url), "video/*");
        mx.putExtra("title", titulo);
        mx.putExtra("return_result", true);
        if (inicio > 0) mx.putExtra("position", (int) (inicio / TICKS_MS));
        // legenda de texto em PT-BR vai como arquivo externo (a linha do tempo é a do arquivo, também na conversão)
        if (legPt != null && legendaIdx != null && legPt.optBoolean("IsTextSubtitleStream", true)) {
            Uri sub = Uri.parse(Api.comChave("/Videos/" + id + "/" + mediaSourceId + "/Subtitles/" + legendaIdx + "/0/Stream.srt"));
            mx.putExtra("subs", new Parcelable[]{sub});
            mx.putExtra("subs.name", new String[]{"Português"});
            mx.putExtra("subs.enable", new Parcelable[]{sub});
        }
        inicioTicks = inicio;
        Api.post("/Sessions/Playing", estado(inicio));
        return mx;
    }

    /** Sem MediaSourceId: a fonte do canal só passa a existir quando o Jellyfin abre o stream (com o Id dele volta vazio). */
    Intent montarCanal(JSONObject item) throws Exception {
        JSONObject info = Api.post("/Items/" + itemId + "/PlaybackInfo?userId=" + Api.userId + "&MaxStreamingBitrate=" + Api.bitrateConversao
                + "&EnableDirectPlay=false&EnableDirectStream=false&EnableTranscoding=true&AutoOpenLiveStream=true&SubtitleStreamIndex=-1",
                new JSONObject().put("DeviceProfile", perfil()));
        JSONArray fontes = info.optJSONArray("MediaSources");
        if (fontes == null || fontes.length() == 0) throw new Exception("canal fora do ar");
        JSONObject ms = fontes.getJSONObject(0);
        String tu = ms.optString("TranscodingUrl", null);
        if (tu == null) throw new Exception("o servidor não ofereceu conversão");
        mediaSourceId = ms.getString("Id");
        liveStreamId = ms.optString("LiveStreamId", null);
        playSessionId = info.optString("PlaySessionId");
        metodo = "Transcode";
        Intent mx = new Intent(Intent.ACTION_VIEW);
        mx.setDataAndType(Uri.parse(Api.comChave(em480p(tu))), "video/*");
        mx.putExtra("title", item.optString("Name"));
        mx.putExtra("return_result", true);
        Api.post("/Sessions/Playing", estado(0));
        return mx;
    }

    /** Roda na thread de preparo: espera a legenda PT-BR aparecer no item (ou o botão / 3 min) e remonta o pedido. */
    Intent esperarLegenda(String id, boolean doInicio, Intent mx) throws Exception {
        semLegenda = false;
        final ProgressDialog[] espera = new ProgressDialog[1];
        runOnUiThread(new Runnable() {
            public void run() {
                progresso.dismiss();
                ProgressDialog d = new ProgressDialog(TocarActivity.this);
                d.setMessage("Sem dublado nem legenda em português.\nPreparando a legenda PT-BR no notebook… (até 3 min)");
                d.setCancelable(true);
                d.setOnCancelListener(new DialogInterface.OnCancelListener() {
                    public void onCancel(DialogInterface x) { semLegenda = true; finish(); }
                });
                d.setButton(DialogInterface.BUTTON_NEGATIVE, "Tocar sem legenda", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface x, int w) { semLegenda = true; }
                });
                d.show();
                espera[0] = d;
            }
        });
        long fim = System.currentTimeMillis() + ESPERA_LEGENDA_MS;
        try {
            while (!semLegenda && System.currentTimeMillis() < fim) {
                Thread.sleep(5000);
                try {
                    Api.post("/Sessions/Playing/Progress", estado(inicioTicks));   // mantém o "tocando" que dispara a legenda
                    JSONObject it = Api.get("/Items/" + id + "?userId=" + Api.userId + "&Fields=MediaSources,MediaStreams");
                    JSONArray st = it.getJSONArray("MediaSources").getJSONObject(0).optJSONArray("MediaStreams");
                    for (int i = 0; st != null && i < st.length(); i++) {
                        JSONObject x = st.getJSONObject(i);
                        if ("Subtitle".equals(x.optString("Type")) && portugues(x)) return montar(id, doInicio);
                    }
                } catch (Api.NaoAutorizado e) {
                    throw e;
                } catch (Exception tentaDeNovo) {
                }
            }
            return mx;
        } finally {
            runOnUiThread(new Runnable() {
                public void run() {
                    if (espera[0] != null) espera[0].dismiss();
                    if (!isFinishing()) progresso.show();
                }
            });
        }
    }

    JSONObject estado(long posicao) throws Exception {
        JSONObject o = new JSONObject().put("ItemId", itemId).put("MediaSourceId", mediaSourceId)
                .put("PlaySessionId", playSessionId).put("PlayMethod", metodo).put("PositionTicks", posicao)
                .put("CanSeek", liveStreamId == null).put("IsPaused", false);
        if (liveStreamId != null) o.put("LiveStreamId", liveStreamId);
        if (audioIdx != null) o.put("AudioStreamIndex", audioIdx);
        if (legendaIdx != null) o.put("SubtitleStreamIndex", legendaIdx);
        return o;
    }

    void abrirMx(Intent mx) {
        // confere antes de chamar: no 4.4 um startActivityForResult que falha (MX Pro ausente) ainda devolve
        // RESULT_CANCELED para o mesmo pedido, que chegava logo depois do MX gratuito abrir
        for (String p : MX) {
            try {
                getPackageManager().getPackageInfo(p, 0);
            } catch (Exception naoInstalado) {
                continue;
            }
            // explícito na ActivityScreen: pelo pacote o Android escolhe a WebDelegate (links http), que repassa
            // o vídeo e fecha na hora, devolvendo o resultado antes de tocar
            mx.setClassName(p, p + ".ActivityScreen");
            progresso.dismiss();
            startActivityForResult(mx, 1);
            tocando = true;
            manterSessao();
            return;
        }
        Toast.makeText(this, "MX Player não está instalado.", Toast.LENGTH_LONG).show();
        finish();
    }

    /** O Jellyfin encerra a conversão de quem some; um ping a cada 30 s mantém a sessão viva enquanto o MX toca. */
    void manterSessao() {
        new Thread() {
            public void run() {
                while (tocando) {
                    try {
                        Thread.sleep(30000);
                        if (tocando) Api.post("/Sessions/Playing/Ping?playSessionId=" + Api.enc(playSessionId), null);
                    } catch (Exception ignorado) {
                    }
                }
            }
        }.start();
    }

    @Override
    protected void onActivityResult(int req, int res, Intent dados) {
        tocando = false;
        long pos = -1;
        boolean terminou = false;
        if (dados != null) {
            terminou = "playback_completion".equals(dados.getStringExtra("end_by"));
            int ms = dados.getIntExtra("position", -1);
            if (ms >= 0) pos = ms * TICKS_MS;
        }
        if (terminou && duracao > 0) pos = duracao;
        final long fPos = pos;
        final boolean fTerminou = terminou;
        progresso.setMessage(terminou && "Episode".equals(tipo) ? "Procurando o próximo episódio…" : "Salvando…");
        progresso.show();
        new Thread() {
            public void run() {
                String proximo = null;
                try {
                    // sem posição do MX, devolve a de início: "Stopped" sem posição faz o Jellyfin marcar como visto
                    JSONObject e = estado(fPos >= 0 ? fPos : inicioTicks);
                    Api.post("/Sessions/Playing/Stopped", e);
                    if (liveStreamId != null) Api.post("/LiveStreams/Close?liveStreamId=" + Api.enc(liveStreamId), null);
                    if (fTerminou && "Episode".equals(tipo) && serieId != null) {
                        JSONArray a = Api.get("/Shows/" + serieId + "/Episodes?userId=" + Api.userId
                                + "&startItemId=" + itemId + "&Limit=2").optJSONArray("Items");
                        if (a != null && a.length() > 1) proximo = a.getJSONObject(1).getString("Id");
                    }
                } catch (Exception ignorado) {
                }
                final String fProx = proximo;
                runOnUiThread(new Runnable() {
                    public void run() {
                        if (fProx != null) {
                            Toast.makeText(TocarActivity.this, "Próximo episódio", Toast.LENGTH_SHORT).show();
                            preparar(fProx, true);
                        } else {
                            progresso.dismiss();
                            finish();
                        }
                    }
                });
            }
        }.start();
    }

    @Override
    protected void onDestroy() {
        tocando = false;
        if (progresso != null) progresso.dismiss();
        super.onDestroy();
    }
}
