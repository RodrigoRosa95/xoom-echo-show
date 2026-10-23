package br.xoomshow;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.os.IBinder;
import android.os.PowerManager;

import java.io.DataInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Microfone sempre ligado para o assistente do notebook (xoom-assistente, porta 8797), mesmo com outro app
 * na frente. Manda "XOOM <taxa>\n" e depois o áudio cru (PCM 16 bits mono); recebe b'W' + tamanho + WAV e toca,
 * ou b'V' + tamanho 1 + nível (0–10) e muda o volume da mídia.
 * Com headset USB ligado usa o microfone dele (conversarUsb). Cai a conexão → tenta de novo a cada 5 s.
 */
public class VozService extends Service {
    static final String HOST = "192.168.4.135";
    static final int PORTA = 8797;
    private volatile boolean rodando;
    private PowerManager.WakeLock trava;

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        if (!rodando) {
            rodando = true;
            PendingIntent abrir = PendingIntent.getActivity(this, 0, new Intent(this, ShowActivity.class), 0);
            Notification n = new Notification.Builder(this).setContentTitle("Xoom Show")
                    .setContentText("Assistente de voz ouvindo (\"Hey Jarvis\")")
                    .setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentIntent(abrir).build();
            startForeground(1, n);
            trava = ((PowerManager) getSystemService(POWER_SERVICE))
                    .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "xoomshow:voz");
            trava.acquire();
            new Thread(new Runnable() { public void run() { laco(); } }, "voz").start();
        }
        return START_STICKY;
    }

    private void laco() {
        while (rodando) {
            try {
                conversar();
            } catch (Exception e) {
                // notebook fora / rede caiu: tenta de novo
            }
            try { Thread.sleep(5000); } catch (InterruptedException e) { return; }
        }
    }

    private AudioRecord abrirMicrofone(int[] taxaUsada) {
        for (int taxa : new int[]{16000, 44100, 48000, 22050, 8000}) {
            int min = AudioRecord.getMinBufferSize(taxa, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            if (min <= 0) continue;
            AudioRecord r = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, taxa,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, taxa));
            if (r.getState() == AudioRecord.STATE_INITIALIZED) {
                taxaUsada[0] = taxa;
                return r;
            }
            r.release();
        }
        return null;
    }

    /** Headset USB ligado? (o kernel cria uma placa ALSA "USB-Audio"; o Android 4.4 não grava dela) */
    static boolean temHeadsetUsb() {
        try {
            java.io.BufferedReader r = new java.io.BufferedReader(new java.io.FileReader("/proc/asound/cards"));
            try {
                for (String l; (l = r.readLine()) != null; ) if (l.contains("USB-Audio")) return true;
            } finally { r.close(); }
        } catch (Exception e) { /* sem placa */ }
        return false;
    }

    private void conversar() throws Exception {
        if (temHeadsetUsb()) conversarUsb();
        else conversarInterno();
    }

    /**
     * Microfone do headset USB: /data/local/xoomshow-mic.sh (root) tira o headset da saída do Android (o som fica
     * no alto-falante) e grava pelo arecord do Debian, já em 16 kHz mono. Tirou o headset → o arecord sai → reconecta.
     */
    private void conversarUsb() throws Exception {
        Process p = Runtime.getRuntime().exec(new String[]{"su", "-c", "sh /data/local/xoomshow-mic.sh"});
        final Socket s = new Socket();
        try {
            java.io.InputStream mic = p.getInputStream();
            s.connect(new InetSocketAddress(HOST, PORTA), 5000);
            s.setTcpNoDelay(true);
            OutputStream saida = s.getOutputStream();
            saida.write("XOOM 16000\n".getBytes("US-ASCII"));
            new Thread(new Runnable() { public void run() { receber(s); } }, "voz-rx").start();
            byte[] buf = new byte[1280];
            int n;
            while (rodando && (n = mic.read(buf)) > 0) saida.write(buf, 0, n);
        } finally {
            p.destroy();
            try { Runtime.getRuntime().exec(new String[]{"su", "-c", "busybox pkill -x arecord"}).waitFor(); }
            catch (Exception e) { /* ok */ }
            try { s.close(); } catch (Exception e) { /* ok */ }
        }
    }

    private void conversarInterno() throws Exception {
        int[] taxa = new int[1];
        AudioRecord mic = abrirMicrofone(taxa);
        if (mic == null) throw new IllegalStateException("sem microfone");
        final Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(HOST, PORTA), 5000);
            s.setTcpNoDelay(true);
            OutputStream saida = s.getOutputStream();
            saida.write(("XOOM " + taxa[0] + "\n").getBytes("US-ASCII"));
            new Thread(new Runnable() { public void run() { receber(s); } }, "voz-rx").start();
            mic.startRecording();
            byte[] buf = new byte[taxa[0] / 25 * 2];  // 40 ms
            int leituras = 0;
            while (rodando) {
                int n = mic.read(buf, 0, buf.length);
                if (n > 0) saida.write(buf, 0, n);
                else if (n < 0) throw new IllegalStateException("microfone falhou " + n);
                if (++leituras % 250 == 0 && temHeadsetUsb()) return;  // ligaram o headset: troca (a cada ~10 s)
            }
        } finally {
            try { mic.stop(); } catch (Exception e) { /* já parado */ }
            mic.release();
            try { s.close(); } catch (Exception e) { /* ok */ }
        }
    }

    private void receber(Socket s) {
        try {
            DataInputStream ent = new DataInputStream(s.getInputStream());
            while (true) {
                int tipo = ent.read();
                if (tipo < 0) break;
                int tam = ent.readInt();
                byte[] dados = new byte[tam];
                ent.readFully(dados);
                if (tipo == 'W') tocar(dados);
                else if (tipo == 'V' && tam == 1) volume(dados[0]);
            }
        } catch (Exception e) {
            // conexão caiu: o laço principal reconecta
        } finally {
            try { s.close(); } catch (Exception e) { /* ok */ }
        }
    }

    private void tocar(byte[] wav) {
        try {
            File f = File.createTempFile("fala", ".wav", getCacheDir());
            FileOutputStream o = new FileOutputStream(f);
            o.write(wav);
            o.close();
            final MediaPlayer mp = new MediaPlayer();
            mp.setAudioStreamType(AudioManager.STREAM_MUSIC);
            mp.setDataSource(f.getAbsolutePath());
            mp.prepare();
            final File arq = f;
            mp.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                public void onCompletion(MediaPlayer m) { m.release(); arq.delete(); }
            });
            mp.start();
        } catch (Exception e) {
            // não tocou: segue
        }
    }

    /** Volume da mídia de 0 a 10 (o `media volume` não existe nesta ROM). */
    private void volume(int nivel) {
        AudioManager am = (AudioManager) getSystemService(AUDIO_SERVICE);
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(Math.max(0, Math.min(10, nivel)) * max / 10f),
                AudioManager.FLAG_SHOW_UI);
    }

    @Override
    public void onDestroy() {
        rodando = false;
        if (trava != null && trava.isHeld()) trava.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent i) { return null; }
}
