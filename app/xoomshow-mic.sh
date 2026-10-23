#!/system/bin/sh
# Microfone do headset USB para o app Xoom Show (22/10/2026). Roda como root pelo VozService.
# 1) o Android 4.4 manda a SAÍDA de som para o headset USB: desconecta essa saída (o som fica no alto-falante);
# 2) grava do microfone USB pelo arecord do Debian (chroot) e escreve PCM 16 bits mono 16 kHz no stdout.
# Sem headset USB: sai com código 2 (o app usa o microfone interno).
N=$(grep -m1 'USB-Audio' /proc/asound/cards | sed 's/^ *\([0-9]*\).*/\1/')
[ -n "$N" ] || exit 2
# um arecord que sobrou (o app reconectou) segura o microfone: fecha antes
busybox pkill -x arecord 2>/dev/null; busybox sleep 0.3
# saída USB conectada no audio policy (bit 0x4000)? → setDeviceConnectionState(OUT_USB_DEVICE, indisponível,
# "card=N;device=0"); o endereço vai como CString em palavras de 32 bits
tirar_saida_usb() {
  if dumpsys media.audio_policy | grep -a 'Output devices' | grep -q '0000[4-7c-fC-F]'; then
    service call media.audio_policy 1 i32 16384 i32 0 i32 1685217635 i32 1681600573 i32 1667855973 i32 3161445 >/dev/null
  fi
}
tirar_saida_usb
# ao religar o headset o Android conecta a saída USB alguns segundos depois: confere por 1 min
( for i in 1 2 3 4 5 6 7 8 9 10 11 12; do busybox sleep 5; tirar_saida_usb; done ) </dev/null >/dev/null 2>&1 &
exec sh /data/local/debian-chroot.sh arecord -q -D plughw:$N,0 -f S16_LE -r 16000 -c 1 -t raw 2>/dev/null
