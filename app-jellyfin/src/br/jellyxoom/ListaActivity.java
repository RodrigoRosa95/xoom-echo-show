package br.jellyxoom;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.AbsListView;
import android.widget.AdapterView;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Grade de itens. Sem extras = tela inicial (Continuar assistindo, Próximos episódios, bibliotecas e busca).
 * Com "caminho" = qualquer listagem da API ({Items:[...]} ou lista), paginada de 60 em 60.
 */
public class ListaActivity extends Activity {
    static final int POR_PAGINA = 60;

    String caminho, titulo;
    boolean paginar, raiz;
    final List<JSONObject> itens = new ArrayList<JSONObject>();
    int total = -1;
    boolean carregando, paisagem;
    GridView grade;
    TextView estado;
    Adaptador adaptador;
    float dp;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Api.carregar(this);
        testeAviso(getIntent());
        if (Api.token == null) {
            startActivity(new Intent(this, LoginActivity.class));
            finish();
            return;
        }
        dp = getResources().getDisplayMetrics().density;
        caminho = getIntent().getStringExtra("caminho");
        titulo = getIntent().getStringExtra("titulo");
        paginar = getIntent().getBooleanExtra("paginar", false);
        paisagem = getIntent().getBooleanExtra("paisagem", false);
        raiz = caminho == null;
        setTitle(raiz ? "Jellyfin" : titulo);

        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setBackgroundColor(Color.rgb(16, 16, 16));
        if (raiz) l.addView(barraDeBusca());
        FrameLayout f = new FrameLayout(this);
        grade = new GridView(this);
        grade.setVerticalSpacing((int) (10 * dp));
        grade.setHorizontalSpacing((int) (10 * dp));
        grade.setPadding((int) (10 * dp), (int) (10 * dp), (int) (10 * dp), (int) (10 * dp));
        grade.setClipToPadding(false);
        grade.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        ajustarColunas();
        adaptador = new Adaptador();
        grade.setAdapter(adaptador);
        f.addView(grade);
        estado = new TextView(this);
        estado.setTextSize(20);
        estado.setGravity(Gravity.CENTER);
        f.addView(estado, new FrameLayout.LayoutParams(-1, -1));
        l.addView(f, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(l);

        grade.setOnItemClickListener(new AdapterView.OnItemClickListener() {
            public void onItemClick(AdapterView<?> p, View v, int pos, long id) { abrir(itens.get(pos), false); }
        });
        grade.setOnItemLongClickListener(new AdapterView.OnItemLongClickListener() {
            public boolean onItemLongClick(AdapterView<?> p, View v, int pos, long id) {
                opcoes(itens.get(pos));
                return true;
            }
        });
        grade.setOnScrollListener(new AbsListView.OnScrollListener() {
            public void onScrollStateChanged(AbsListView v, int s) { }
            public void onScroll(AbsListView v, int primeiro, int visiveis, int totalVisivel) {
                if (paginar && !carregando && itens.size() < total && primeiro + visiveis >= totalVisivel - 12)
                    carregar(false);
            }
        });
        carregar(true);
    }

    View barraDeBusca() {
        LinearLayout barra = new LinearLayout(this);
        barra.setPadding((int) (10 * dp), (int) (6 * dp), (int) (10 * dp), 0);
        final EditText busca = new EditText(this);
        busca.setHint("Buscar filme, série ou episódio…");
        busca.setSingleLine(true);
        busca.setTextSize(18);
        busca.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        busca.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            public boolean onEditorAction(TextView v, int acao, KeyEvent e) {
                String t = busca.getText().toString().trim();
                if (t.isEmpty()) return false;
                abrirLista("Busca: " + t, "/Items?userId=" + Api.userId + "&searchTerm=" + Api.enc(t)
                        + "&IncludeItemTypes=Movie,Series,Episode&Recursive=true&Limit=100&" + Api.CAMPOS, false, null);
                return true;
            }
        });
        barra.addView(busca, new LinearLayout.LayoutParams(-1, -2));
        return barra;
    }

    void ajustarColunas() {
        grade.setColumnWidth((int) ((paisagem ? 250 : 150) * dp));
        grade.setNumColumns(GridView.AUTO_FIT);
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        carregar(true);  // volta do player: progresso e "visto" mudaram
    }

    void carregar(final boolean doZero) {
        if (carregando) return;
        carregando = true;
        if (doZero && itens.isEmpty()) estado.setText("Carregando…");
        final int inicio = doZero ? 0 : itens.size();
        new Thread() {
            public void run() {
                final List<JSONObject> novos = new ArrayList<JSONObject>();
                int tot = -1;
                String erro = null;
                try {
                    if (raiz) {
                        novos.addAll(inicial());
                    } else {
                        String c = caminho;
                        if (paginar) c += (c.contains("?") ? "&" : "?") + "StartIndex=" + inicio + "&Limit=" + POR_PAGINA;
                        JSONObject r = Api.get(c);
                        JSONArray a = r.optJSONArray("Items");
                        for (int i = 0; a != null && i < a.length(); i++) {
                            JSONObject o = a.getJSONObject(i);
                            // só as fontes gratuitas da fast-tv (Pluto, Plex, abertos), as únicas com número de canal
                            if ("TvChannel".equals(o.optString("Type")) && o.optString("ChannelNumber").isEmpty()) continue;
                            novos.add(o);
                        }
                        tot = r.optInt("TotalRecordCount", novos.size() + inicio);
                    }
                } catch (Api.NaoAutorizado e) {
                    erro = "login";
                } catch (Exception e) {
                    erro = e.getMessage();
                }
                final int fTot = tot;
                final String fErro = erro;
                runOnUiThread(new Runnable() {
                    public void run() {
                        carregando = false;
                        if ("login".equals(fErro)) { sair(); return; }
                        if (fErro != null) {
                            estado.setText(itens.isEmpty() ? "Erro: " + fErro + "\n(toque em ⟳ no menu)" : "");
                            return;
                        }
                        if (doZero) itens.clear();
                        itens.addAll(novos);
                        total = paginar ? fTot : itens.size();
                        if (!raiz && doZero && !getIntent().hasExtra("paisagem")) {
                            int ep = 0;
                            for (JSONObject o : itens) if ("Episode".equals(o.optString("Type"))) ep++;
                            boolean p = ep * 2 > itens.size();
                            if (p != paisagem) { paisagem = p; ajustarColunas(); }
                        }
                        estado.setText(itens.isEmpty() ? "Nada aqui." : "");
                        adaptador.notifyDataSetChanged();
                    }
                });
            }
        }.start();
    }

    /** Tela inicial: atalhos + bibliotecas, como cartões (campo "_caminho" = abre outra lista). */
    List<JSONObject> inicial() throws Exception {
        List<JSONObject> l = new ArrayList<JSONObject>();
        String u = "userId=" + Api.userId;
        l.add(atalho("Continuar assistindo", "/UserItems/Resume?" + u + "&Limit=60&MediaTypes=Video&" + Api.CAMPOS, true));
        l.add(atalho("Próximos episódios", "/Shows/NextUp?" + u + "&Limit=60&" + Api.CAMPOS, true));
        l.add(atalho("Adicionados recentemente", "/Items/Latest?" + u + "&Limit=60&IncludeItemTypes=Movie,Episode&GroupItems=false&" + Api.CAMPOS, false));
        JSONArray v = Api.get("/UserViews?" + u).optJSONArray("Items");
        for (int i = 0; v != null && i < v.length(); i++) l.add(v.getJSONObject(i));
        return l;
    }

    JSONObject atalho(String nome, String c, boolean paisagem) throws Exception {
        return new JSONObject().put("Name", nome).put("_caminho", c).put("_paisagem", paisagem);
    }

    void abrirLista(String t, String c, boolean pag, Boolean pais) {
        Intent i = new Intent(this, ListaActivity.class).putExtra("titulo", t).putExtra("caminho", c).putExtra("paginar", pag);
        if (pais != null) i.putExtra("paisagem", pais.booleanValue());
        startActivity(i);
    }

    void abrir(JSONObject o, boolean doInicio) {
        String id = o.optString("Id"), tipo = o.optString("Type"), nome = o.optString("Name");
        String u = "userId=" + Api.userId;
        if (o.has("_caminho")) {
            abrirLista(nome, o.optString("_caminho"), false, o.optBoolean("_paisagem"));
        } else if ("livetv".equals(o.optString("CollectionType"))) {
            abrirLista(nome, "/LiveTv/Channels?" + u + "&Limit=500&AddCurrentProgram=true&EnableUserData=false"
                    + "&EnableImageTypes=Primary&ImageTypeLimit=1", false, true);
        } else if ("CollectionFolder".equals(tipo) || "UserView".equals(tipo)) {
            String ct = o.optString("CollectionType");
            String filtro = "tvshows".equals(ct) ? "&IncludeItemTypes=Series&Recursive=true"
                    : "movies".equals(ct) ? "&IncludeItemTypes=Movie&Recursive=true" : "";
            abrirLista(nome, "/Items?" + u + "&ParentId=" + id + filtro + "&SortBy=SortName&" + Api.CAMPOS, true, false);
        } else if ("Series".equals(tipo)) {
            abrirLista(nome, "/Shows/" + id + "/Seasons?" + u + "&" + Api.CAMPOS, false, false);
        } else if ("Season".equals(tipo)) {
            abrirLista(o.optString("SeriesName") + " — " + nome, "/Shows/" + o.optString("SeriesId")
                    + "/Episodes?" + u + "&seasonId=" + id + "&" + Api.CAMPOS, false, true);
        } else if (o.optBoolean("IsFolder")) {
            abrirLista(nome, "/Items?" + u + "&ParentId=" + id + "&SortBy=SortName&" + Api.CAMPOS, true, null);
        } else {
            startActivity(new Intent(this, TocarActivity.class).putExtra("id", id).putExtra("doInicio", doInicio));
        }
    }

    void opcoes(final JSONObject o) {
        if (o.has("_caminho") || o.optString("Id").isEmpty() || "TvChannel".equals(o.optString("Type"))) return;
        final boolean pasta = o.optBoolean("IsFolder");
        final boolean visto = o.optJSONObject("UserData") != null && o.optJSONObject("UserData").optBoolean("Played");
        final List<String> ops = new ArrayList<String>();
        if (!pasta) ops.add("Tocar do início");
        final String tipo = o.optString("Type");
        if ("Episode".equals(tipo) || "Movie".equals(tipo)) ops.add("Traduzir legenda com IA");
        ops.add(visto ? "Marcar como não visto" : "Marcar como visto");
        new AlertDialog.Builder(this).setTitle(o.optString("Name"))
                .setItems(ops.toArray(new String[0]), new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int qual) {
                        String op = ops.get(qual);
                        if (op.equals("Tocar do início")) abrir(o, true);
                        else if (op.equals("Traduzir legenda com IA")) traduzirIa(o);
                        else marcar(o.optString("Id"), !visto);
                    }
                }).show();
    }

    /** Pede a tradução ao notebook, mostra o andamento e, quando fica pronta, toca de onde parou com a legenda nova. */
    void traduzirIa(final JSONObject o) {
        final String id = o.optString("Id");
        final ProgressDialog d = new ProgressDialog(this);
        d.setTitle("Traduzir legenda com IA");
        d.setMessage("Pedindo ao notebook…");
        d.setCancelable(false);
        final boolean[] escondido = {false};
        d.setButton(DialogInterface.BUTTON_NEGATIVE, "Esconder (continua traduzindo)", new DialogInterface.OnClickListener() {
            public void onClick(DialogInterface x, int w) { escondido[0] = true; }
        });
        d.show();
        new Thread() {
            public void run() {
                String fim;
                boolean ok = false;
                try {
                    String r = Api.pedirUrl("POST", Api.urlIa(), new JSONObject().put("itemId", id).toString());
                    mostrar(d, new JSONObject(r).optString("mensagem"));
                    long limite = System.currentTimeMillis() + 40 * 60000L;
                    JSONObject a = new JSONObject();
                    while (System.currentTimeMillis() < limite) {
                        Thread.sleep(3000);
                        try {
                            a = new JSONObject(Api.pedirUrl("GET", Api.urlIa() + "?itemId=" + Api.enc(id), null));
                        } catch (java.io.IOException semNovidade) {
                            continue;
                        }
                        if (a.optBoolean("fim")) break;
                        mostrar(d, a.optString("mensagem"));
                    }
                    ok = a.optBoolean("ok");
                    fim = a.optBoolean("fim") ? a.optString("mensagem") : "Ainda traduzindo — tente tocar daqui a pouco.";
                } catch (Exception e) {
                    fim = "Não consegui pedir a tradução: " + e.getMessage();
                }
                final String fFim = fim;
                final boolean fOk = ok;
                if (fOk) avisoSonoro();   // pedido do usuário 23/10/2026: avisa em volume máximo, mesmo escondido
                runOnUiThread(new Runnable() {
                    public void run() {
                        d.dismiss();
                        if (isFinishing()) return;
                        if (fOk && !escondido[0]) {
                            Toast.makeText(ListaActivity.this, fFim, Toast.LENGTH_LONG).show();
                            abrir(o, false);
                        } else {
                            new AlertDialog.Builder(ListaActivity.this).setTitle(o.optString("Name")).setMessage(fFim)
                                    .setPositiveButton("OK", null).show();
                        }
                    }
                });
            }
        }.start();
    }

    /** Teste do aviso da tradução: am start -n br.jellyxoom/.ListaActivity --ez teste_aviso true */
    void testeAviso(Intent i) {
        if (i != null && i.getBooleanExtra("teste_aviso", false)) {
            i.removeExtra("teste_aviso");
            new Thread() { public void run() { avisoSonoro(); } }.start();
        }
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        testeAviso(i);
    }

    /** Legenda traduzida pronta: volume de mídia no máximo, a frase "A legenda por I.A. requisitada está pronta."
        (res/raw/aviso_legenda.wav, voz Piper pt-BR faber, a mesma do assistente do Xoom) e o volume de antes de volta. */
    void avisoSonoro() {
        android.util.Log.i("jellyxoom", "aviso sonoro da tradução");
        android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
        int antes = am.getStreamVolume(android.media.AudioManager.STREAM_MUSIC);
        am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC,
                am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC), 0);
        try {
            android.media.MediaPlayer mp = android.media.MediaPlayer.create(this,
                    getResources().getIdentifier("aviso_legenda", "raw", getPackageName()));   // sem R.java neste build
            if (mp != null) {
                mp.start();
                Thread.sleep(mp.getDuration() + 400);
                mp.release();
            }
        } catch (Exception ignorado) {
        }
        am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, antes, 0);
        android.util.Log.i("jellyxoom", "aviso sonoro tocado (volume de volta a " + antes + ")");
    }

    void mostrar(final ProgressDialog d, final String texto) {
        runOnUiThread(new Runnable() { public void run() { if (d.isShowing()) d.setMessage(texto); } });
    }

    void marcar(final String id, final boolean visto) {
        new Thread() {
            public void run() {
                try {
                    String c = "/UserPlayedItems/" + id + "?userId=" + Api.userId;
                    if (visto) Api.post(c, null); else Api.delete(c);
                    runOnUiThread(new Runnable() { public void run() { carregar(true); } });
                } catch (final Exception e) {
                    runOnUiThread(new Runnable() {
                        public void run() { Toast.makeText(ListaActivity.this, "Falhou: " + e.getMessage(), Toast.LENGTH_LONG).show(); }
                    });
                }
            }
        }.start();
    }

    @Override
    public boolean onCreateOptionsMenu(Menu m) {
        m.add(0, 1, 0, "⟳ Atualizar").setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS);
        m.add(0, 2, 0, "Qualidade da conversão");
        m.add(0, 3, 0, "Sair (trocar usuário)");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem mi) {
        if (mi.getItemId() == 1) carregar(true);
        else if (mi.getItemId() == 2) qualidade();
        else if (mi.getItemId() == 3) sair();
        return true;
    }

    void qualidade() {
        final int[] valores = {1000000, 1500000, 2000000, 3000000};
        String[] nomes = {"1 Mbps", "1,5 Mbps (padrão)", "2 Mbps", "3 Mbps"};
        int atual = 1;
        for (int i = 0; i < valores.length; i++) if (valores[i] == Api.bitrateConversao) atual = i;
        new AlertDialog.Builder(this).setTitle("Conversão para 480p (H.264)")
                .setSingleChoiceItems(nomes, atual, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int q) {
                        Api.bitrateConversao = valores[q];
                        Api.prefs(ListaActivity.this).edit().putInt("bitrate480", valores[q]).apply();
                        d.dismiss();
                    }
                }).show();
    }

    void sair() {
        Api.sair(this);
        startActivity(new Intent(this, LoginActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK | Intent.FLAG_ACTIVITY_NEW_TASK));
        finish();
    }

    static String minutos(long ticks) {
        long s = ticks / 10000000L;
        return s >= 3600 ? String.format("%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60) : String.format("%d:%02d", s / 60, s % 60);
    }

    class Adaptador extends BaseAdapter {
        public int getCount() { return itens.size(); }
        public Object getItem(int p) { return itens.get(p); }
        public long getItemId(int p) { return p; }

        public View getView(int p, View v, ViewGroup pai) {
            Cartao c = v == null ? new Cartao() : (Cartao) v.getTag();
            c.mostrar(itens.get(p));
            return c.raiz;
        }
    }

    class Cartao {
        LinearLayout raiz;
        FrameLayout moldura;
        ImageView img;
        TextView sobre, nome, info;
        ProgressBar barra;

        Cartao() {
            raiz = new LinearLayout(ListaActivity.this);
            raiz.setOrientation(LinearLayout.VERTICAL);
            raiz.setTag(this);
            moldura = new FrameLayout(ListaActivity.this);
            moldura.setBackgroundColor(Color.rgb(40, 40, 46));
            img = new ImageView(ListaActivity.this);
            img.setScaleType(ImageView.ScaleType.CENTER_CROP);
            moldura.addView(img, new FrameLayout.LayoutParams(-1, -1));
            sobre = new TextView(ListaActivity.this);
            sobre.setTextSize(18);
            sobre.setTypeface(Typeface.DEFAULT_BOLD);
            sobre.setGravity(Gravity.CENTER);
            sobre.setPadding(8, 8, 8, 8);
            moldura.addView(sobre, new FrameLayout.LayoutParams(-1, -1));
            barra = new ProgressBar(ListaActivity.this, null, android.R.attr.progressBarStyleHorizontal);
            barra.setMax(1000);
            moldura.addView(barra, new FrameLayout.LayoutParams(-1, (int) (6 * dp), Gravity.BOTTOM));
            raiz.addView(moldura);
            nome = new TextView(ListaActivity.this);
            nome.setTextSize(15);
            nome.setTextColor(Color.WHITE);
            nome.setMaxLines(2);
            nome.setEllipsize(TextUtils.TruncateAt.END);
            raiz.addView(nome);
            info = new TextView(ListaActivity.this);
            info.setTextSize(13);
            info.setTextColor(Color.GRAY);
            info.setSingleLine(true);
            raiz.addView(info);
        }

        void mostrar(JSONObject o) {
            int largura = grade.getColumnWidth() > 0 ? grade.getColumnWidth() : (int) ((paisagem ? 250 : 150) * dp);
            boolean pais = paisagem || o.has("_caminho") || "CollectionFolder".equals(o.optString("Type"))
                    || "UserView".equals(o.optString("Type"));
            moldura.setLayoutParams(new LinearLayout.LayoutParams(-1, pais ? largura * 9 / 16 : largura * 3 / 2));
            String tipo = o.optString("Type"), id = o.optString("Id");
            img.setScaleType("TvChannel".equals(tipo) ? ImageView.ScaleType.FIT_CENTER : ImageView.ScaleType.CENTER_CROP);  // logo inteira
            JSONObject tags = o.optJSONObject("ImageTags");
            String url = null;
            int alt = pais ? 200 : 260;
            if (o.has("_caminho")) url = null;
            else if (pais && tags != null && tags.has("Thumb")) url = Api.imagem(id, "Thumb", alt);
            else if (tags != null && tags.has("Primary")) url = Api.imagem(id, "Primary", alt);
            else if (o.has("ParentThumbItemId") && pais) url = Api.imagem(o.optString("ParentThumbItemId"), "Thumb", alt);
            else if (o.has("SeriesPrimaryImageTag")) url = Api.imagem(o.optString("SeriesId"), "Primary", alt);
            if (url != null) Imagens.carregar(img, url); else { img.setTag(null); img.setImageDrawable(null); }
            sobre.setText(url == null ? o.optString("Name") : "");

            String n = o.optString("Name");
            if ("Episode".equals(tipo)) {
                String se = (o.has("ParentIndexNumber") ? "T" + o.optInt("ParentIndexNumber") : "")
                        + (o.has("IndexNumber") ? "E" + o.optInt("IndexNumber") : "");
                n = (paisagem && caminho != null && caminho.contains("seasonId=") ? "" : o.optString("SeriesName") + "\n")
                        + (se.isEmpty() ? "" : se + " · ") + n;
            }
            nome.setText(o.has("_caminho") ? "" : n);

            JSONObject ud = o.optJSONObject("UserData");
            long pos = ud == null ? 0 : ud.optLong("PlaybackPositionTicks");
            long dur = o.optLong("RunTimeTicks");
            String inf = "";
            if (ud != null && ud.optBoolean("Played")) inf = "✓ visto";
            else if (pos > 0) inf = "▶ parou em " + minutos(pos) + (dur > 0 ? " de " + minutos(dur) : "");
            else if (ud != null && ud.has("UnplayedItemCount") && ud.optInt("UnplayedItemCount") > 0)
                inf = ud.optInt("UnplayedItemCount") + " não vistos";
            else if (o.has("ProductionYear") && !"Episode".equals(tipo)) inf = String.valueOf(o.optInt("ProductionYear"));
            else if (dur > 0) inf = minutos(dur);
            if ("TvChannel".equals(tipo) && o.optJSONObject("CurrentProgram") != null) inf = o.optJSONObject("CurrentProgram").optString("Name");
            info.setText(inf);
            info.setVisibility(o.has("_caminho") ? View.GONE : View.VISIBLE);
            barra.setVisibility(pos > 0 && dur > 0 ? View.VISIBLE : View.GONE);
            if (dur > 0) barra.setProgress((int) (pos * 1000 / dur));
        }
    }
}
