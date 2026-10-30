package br.jellyxoom;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import android.widget.ImageView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Capas já pedidas no tamanho do cartão; cache em memória e fila LIFO (o que está na tela vem primeiro). */
public class Imagens {
    static final LruCache<String, Bitmap> cache = new LruCache<String, Bitmap>(12 * 1024 * 1024) {
        @Override
        protected int sizeOf(String k, Bitmap b) { return b.getByteCount(); }
    };
    static final ThreadPoolExecutor fila = new ThreadPoolExecutor(3, 3, 30, TimeUnit.SECONDS,
            new LinkedBlockingDeque<Runnable>() {
                @Override
                public boolean offer(Runnable r) { return offerFirst(r); }
            });
    static final Handler ui = new Handler(Looper.getMainLooper());

    public static void carregar(final ImageView iv, final String url) {
        iv.setTag(url);
        Bitmap b = cache.get(url);
        if (b != null) { iv.setImageBitmap(b); return; }
        iv.setImageDrawable(null);
        fila.execute(new Runnable() {
            public void run() {
                if (!url.equals(iv.getTag())) return;  // o cartão já foi reaproveitado
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(15000);
                    if (c.getResponseCode() != 200) return;
                    InputStream in = c.getInputStream();
                    BitmapFactory.Options o = new BitmapFactory.Options();
                    o.inPreferredConfig = Bitmap.Config.RGB_565;  // metade da memória; o Xoom tem 716 MB
                    final Bitmap bm = BitmapFactory.decodeStream(in, null, o);
                    in.close();
                    if (bm == null) return;
                    cache.put(url, bm);
                    ui.post(new Runnable() {
                        public void run() { if (url.equals(iv.getTag())) iv.setImageBitmap(bm); }
                    });
                } catch (Exception ignorado) {
                }
            }
        });
    }
}
