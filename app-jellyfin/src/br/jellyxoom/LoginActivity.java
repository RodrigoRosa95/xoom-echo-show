package br.jellyxoom;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

public class LoginActivity extends Activity {
    EditText servidor, usuario, senha;
    TextView aviso;
    Button entrar;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Api.carregar(this);
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        int p = (int) (32 * getResources().getDisplayMetrics().density);
        l.setPadding(p * 4, p, p * 4, p);
        servidor = campo(l, "Servidor", Api.servidor, InputType.TYPE_TEXT_VARIATION_URI);
        usuario = campo(l, "Usuário", Api.USUARIO_PADRAO, InputType.TYPE_CLASS_TEXT);
        senha = campo(l, "Senha", "", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        entrar = new Button(this);
        entrar.setText("Entrar");
        entrar.setTextSize(20);
        l.addView(entrar);
        aviso = new TextView(this);
        aviso.setTextSize(18);
        l.addView(aviso);
        setContentView(l);
        senha.requestFocus();
        entrar.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { entrar(); }
        });
    }

    EditText campo(LinearLayout l, String rotulo, String valor, int tipo) {
        TextView t = new TextView(this);
        t.setText(rotulo);
        t.setTextSize(18);
        l.addView(t);
        EditText e = new EditText(this);
        e.setText(valor);
        e.setTextSize(20);
        e.setSingleLine(true);
        e.setInputType(tipo);
        l.addView(e);
        return e;
    }

    void entrar() {
        final String srv = servidor.getText().toString().trim().replaceAll("/+$", "");
        final String usu = usuario.getText().toString().trim();
        final String sen = senha.getText().toString();
        entrar.setEnabled(false);
        aviso.setText("Entrando…");
        new Thread() {
            public void run() {
                String erro = null;
                try {
                    Api.servidor = srv;
                    Api.token = null;
                    JSONObject r = Api.post("/Users/AuthenticateByName",
                            new JSONObject().put("Username", usu).put("Pw", sen));
                    Api.salvarLogin(LoginActivity.this, srv, r.getString("AccessToken"),
                            r.getJSONObject("User").getString("Id"));
                } catch (Api.NaoAutorizado e) {
                    erro = "Usuário ou senha errados.";
                } catch (Exception e) {
                    erro = "Não consegui falar com o servidor: " + e.getMessage();
                }
                final String fErro = erro;
                runOnUiThread(new Runnable() {
                    public void run() {
                        entrar.setEnabled(true);
                        if (fErro != null) { aviso.setText(fErro); return; }
                        startActivity(new Intent(LoginActivity.this, ListaActivity.class)
                                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK | Intent.FLAG_ACTIVITY_NEW_TASK));
                        finish();
                    }
                });
            }
        }.start();
    }
}
