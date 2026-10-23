package br.xoomshow;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Liga o serviço de voz e abre a tela do Show quando o Xoom termina de iniciar. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent i) {
        c.startService(new Intent(c, VozService.class));
        Intent tela = new Intent(c, ShowActivity.class);
        tela.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        c.startActivity(tela);
    }
}
