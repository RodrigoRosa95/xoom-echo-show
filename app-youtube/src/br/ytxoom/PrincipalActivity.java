package br.ytxoom;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

/**
 * YouTube no Xoom: abre a página /yt do painel do notebook (busca pelo yt-dlp) num WebView.
 * Os links /yt/tocar viram o vídeo /yt/video.ts (H.264 remuxado no notebook) aberto direto no MX Player.
 */
public class PrincipalActivity extends Activity {
    static final String BASE = "http://192.168.4.135:8796";
    private WebView web;

    @Override
    protected void onCreate(Bundle salvo) {
        super.onCreate(salvo);
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(15, 15, 15));
        web.getSettings().setJavaScriptEnabled(true);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, String url) {
                Uri u = Uri.parse(url);
                if ("/yt/tocar".equals(u.getPath())) {
                    tocar(u);
                    return true;
                }
                return false;
            }

            @Override
            public void onReceivedError(WebView v, int codigo, String descricao, String url) {
                v.loadData("<html><body style='background:#0f0f0f;color:#fff;font:22px sans-serif;padding:40px'>"
                        + "Notebook fora do ar (" + descricao + ").<br><br><a style='color:#f44' href='" + BASE
                        + "/yt'>Tentar de novo</a></body></html>", "text/html; charset=utf-8", "UTF-8");
            }
        });
        setContentView(web);
        if (salvo != null) web.restoreState(salvo);
        else web.loadUrl(BASE + "/yt");
    }

    private void tocar(Uri u) {
        String q = u.getQueryParameter("q"), t = u.getQueryParameter("t"), titulo = u.getQueryParameter("titulo");
        String video = BASE + "/yt/video.ts?v=" + Uri.encode(u.getQueryParameter("v")) + "&q=" + (q == null ? "480" : q)
                + (t != null && !"0".equals(t) ? "&t=" + t : "");
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(Uri.parse(video), "video/mp2t");
        i.putExtra("title", titulo == null ? "YouTube" : titulo);
        i.setClassName("com.mxtech.videoplayer.ad", "com.mxtech.videoplayer.ad.ActivityScreen");
        try {
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            i.setComponent(null);  // sem MX Player: qualquer player de vídeo
            try {
                startActivity(i);
            } catch (ActivityNotFoundException e2) {
                Toast.makeText(this, "Nenhum player de vídeo instalado", Toast.LENGTH_LONG).show();
            }
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle estado) {
        super.onSaveInstanceState(estado);
        web.saveState(estado);
    }

    @Override
    public boolean onKeyDown(int tecla, KeyEvent ev) {
        if (tecla == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(tecla, ev);
    }
}
