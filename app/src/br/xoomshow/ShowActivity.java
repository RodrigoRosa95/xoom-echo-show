package br.xoomshow;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Xoom Show: tela no estilo Echo Show (página /show do painel do notebook) em tela cheia, sempre acesa,
 * e o serviço de voz (VozService) ligado. Os links /yt/tocar abrem o vídeo direto no MX Player.
 */
public class ShowActivity extends Activity {
    static final String BASE = "http://192.168.4.135:8796";
    private WebView web;
    private final Handler handler = new Handler();

    @Override
    protected void onCreate(Bundle salvo) {
        super.onCreate(salvo);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(5, 11, 22));
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
                // notebook fora do ar: tenta de novo em 30 s
                v.loadData("<html><body style='background:#050b16;color:#9ab;font:28px sans-serif;padding:60px'>"
                        + "Aguardando o notebook…</body></html>", "text/html; charset=utf-8", "UTF-8");
                handler.postDelayed(new Runnable() {
                    public void run() { web.loadUrl(BASE + "/show"); }
                }, 30000);
            }
        });
        setContentView(web);
        web.loadUrl(BASE + "/show");
        startService(new Intent(this, VozService.class));
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
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
            i.setComponent(null);
            try { startActivity(i); } catch (ActivityNotFoundException e2) { /* sem player */ }
        }
    }

    @Override
    public boolean onKeyDown(int tecla, KeyEvent ev) {
        if (tecla == KeyEvent.KEYCODE_BACK) {
            if (web.canGoBack()) web.goBack();
            else web.loadUrl(BASE + "/show");
            return true;  // Voltar nunca fecha a tela (como na Echo Show); sair pelo botão Início
        }
        return super.onKeyDown(tecla, ev);
    }
}
