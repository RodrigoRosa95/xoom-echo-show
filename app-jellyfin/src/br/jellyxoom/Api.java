package br.jellyxoom;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/** Acesso à API REST do Jellyfin (HttpURLConnection + org.json, nada de bibliotecas). */
public class Api {
    public static final String SERVIDOR_PADRAO = "http://192.168.4.135:8096";
    public static final String USUARIO_PADRAO = "jellyfin";

    public static String servidor, token, userId, deviceId;
    /** Teto da conversão (bits/s) quando o arquivo não pode tocar direto. */
    public static int bitrateConversao;

    public static class NaoAutorizado extends IOException {
        NaoAutorizado() { super("sessão expirada"); }
    }

    public static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("jellyxoom", Context.MODE_PRIVATE);
    }

    public static void carregar(Context c) {
        SharedPreferences p = prefs(c);
        servidor = p.getString("servidor", SERVIDOR_PADRAO);
        token = p.getString("token", null);
        userId = p.getString("userId", null);
        bitrateConversao = p.getInt("bitrate480", 1500000);   // conversão sempre em 480p desde 23/10/2026
        deviceId = Settings.Secure.getString(c.getContentResolver(), Settings.Secure.ANDROID_ID);
        if (deviceId == null) deviceId = "xoom";
        deviceId = "jellyxoom-" + deviceId;
    }

    public static void salvarLogin(Context c, String srv, String tok, String uid) {
        prefs(c).edit().putString("servidor", srv).putString("token", tok).putString("userId", uid).apply();
        servidor = srv; token = tok; userId = uid;
    }

    public static void sair(Context c) {
        prefs(c).edit().remove("token").remove("userId").apply();
        token = null; userId = null;
    }

    static String autorizacao() {
        return "MediaBrowser Client=\"Jellyfin Xoom\", Device=\"Xoom\", DeviceId=\"" + deviceId
                + "\", Version=\"1.0\"" + (token != null ? ", Token=\"" + token + "\"" : "");
    }

    public static String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return s; }
    }

    /** Endereço com a chave na própria URL (para o MX Player e imagens, que não mandam cabeçalho). */
    public static String comChave(String caminhoOuUrl) {
        String u = caminhoOuUrl.startsWith("http") ? caminhoOuUrl : servidor + caminhoOuUrl;
        if (u.toLowerCase().contains("api_key=")) return u;
        return u + (u.contains("?") ? "&" : "?") + "api_key=" + token;
    }

    public static String imagem(String itemId, String tipo, int altura) {
        return servidor + "/Items/" + itemId + "/Images/" + tipo + "?maxHeight=" + altura + "&quality=70";
    }

    static String pedir(String metodo, String caminho, String corpo) throws IOException {
        return pedirUrl(metodo, servidor + caminho, corpo);
    }

    /** "Traduzir legenda com IA": legenda_na_hora.py no mesmo notebook do Jellyfin, porta 8793 (23/10/2026). */
    public static String urlIa() {
        return servidor.replaceFirst(":\\d+/?$", "") + ":8793/traduzir-ia";
    }

    static String pedirUrl(String metodo, String url, String corpo) throws IOException {
        String caminho = url;
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(10000);
        c.setReadTimeout(30000);
        c.setRequestMethod(metodo);
        c.setRequestProperty("X-Emby-Authorization", autorizacao());
        c.setRequestProperty("Authorization", autorizacao());
        c.setRequestProperty("Accept", "application/json");
        if (corpo != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            OutputStream o = c.getOutputStream();
            o.write(corpo.getBytes("UTF-8"));
            o.close();
        }
        int cod = c.getResponseCode();
        if (cod == 401) throw new NaoAutorizado();
        InputStream in = cod >= 400 ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        if (in != null) {
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            in.close();
        }
        String txt = b.toString("UTF-8");
        if (cod >= 400) throw new IOException("HTTP " + cod + " em " + caminho + ": " + txt);
        return txt;
    }

    public static JSONObject get(String caminho) throws Exception {
        String t = pedir("GET", caminho, null).trim();
        if (t.startsWith("[")) return new JSONObject().put("Items", new JSONArray(t));
        return t.isEmpty() ? new JSONObject() : new JSONObject(t);
    }

    public static JSONObject post(String caminho, JSONObject corpo) throws Exception {
        String t = pedir("POST", caminho, corpo == null ? "" : corpo.toString()).trim();
        return t.isEmpty() ? new JSONObject() : new JSONObject(t);
    }

    public static void delete(String caminho) throws Exception {
        pedir("DELETE", caminho, null);
    }

    /** Campos pedidos em toda listagem (só o necessário para os cartões). */
    public static final String CAMPOS = "Fields=PrimaryImageAspectRatio&EnableImageTypes=Primary,Thumb"
            + "&ImageTypeLimit=1&EnableUserData=true";
}
