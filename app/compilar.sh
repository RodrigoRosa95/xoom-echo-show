#!/bin/bash
# Compila o APK "Xoom Show" (tela estilo Echo Show + microfone p/ o xoom-assistente do notebook) sem Gradle:
# javac + dx + aapt + apksigner (android-sdk-build-tools 29 e platform-23 do apt). Saída: XoomShow.apk
# Instalar: adb -s 192.168.4.72:5555 install -r XoomShow.apk
# A chave fica fora do projeto (~/.config/jellyfin-xoom/); sem ela as atualizações não instalam por cima.
set -e
cd "$(dirname "$0")"
SDK=/usr/lib/android-sdk
JAR=$SDK/platforms/android-23/android.jar
BT=$SDK/build-tools/29.0.3
OBRA=$(mktemp -d)
trap 'rm -rf "$OBRA"' EXIT
CHAVE=$HOME/.config/jellyfin-xoom/assinatura.jks
SENHA_ARQ=$HOME/.config/jellyfin-xoom/senha
if [ ! -f "$CHAVE" ]; then
    mkdir -p "$(dirname "$CHAVE")"; chmod 700 "$(dirname "$CHAVE")"
    head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$SENHA_ARQ"; chmod 600 "$SENHA_ARQ"
    keytool -genkeypair -keystore "$CHAVE" -alias jellyxoom -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass "$(cat "$SENHA_ARQ")" -keypass "$(cat "$SENHA_ARQ")" -dname "CN=Jellyfin Xoom" >/dev/null 2>&1
fi
mkdir -p "$OBRA/classes"
# sem lambdas: o dx não faz desugar
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath "$JAR" -Xlint:-options -d "$OBRA/classes" $(find src -name '*.java')
"$BT/dx" --dex --min-sdk-version=19 --output="$OBRA/classes.dex" "$OBRA/classes"
"$BT/aapt" package -f -M AndroidManifest.xml -S res -I "$JAR" -F "$OBRA/sem-codigo.apk"
(cd "$OBRA" && cp sem-codigo.apk sem-assinatura.apk && "$BT/aapt" add sem-assinatura.apk classes.dex >/dev/null)
"$BT/zipalign" -f 4 "$OBRA/sem-assinatura.apk" "$OBRA/alinhado.apk"
"$BT/apksigner" sign --ks "$CHAVE" --ks-pass "file:$SENHA_ARQ" --out XoomShow.apk "$OBRA/alinhado.apk"
rm -f XoomShow.apk.idsig
echo "XoomShow.apk ($(du -h XoomShow.apk | cut -f1))"
