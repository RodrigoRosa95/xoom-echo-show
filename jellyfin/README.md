# Jellyfin na integração

O Jellyfin (testado na versão 10.11) é o acervo por trás de tudo: a música que toca no Echo, a letra na tela, os vídeos do
app do Xoom e a TV ao vivo.

| Quem usa | Para quê | Como fala com o Jellyfin |
|---|---|---|
| Skill **meu cinema** (`alexa/`) | música no Echo, "colocar na TV", "o que eu estava assistindo" | chave de API, `JELLYFIN_API_KEY` |
| Painel (`painel/`) | bibliotecas e letras da tela de música, Top 100 | chave de API, `JELLYFIN_API_KEY` |
| App **Jellyfin Xoom** (`app-jellyfin/`) | navegar, tocar no MX Player, TV ao vivo | login de usuário no próprio app |

## Preparar o servidor

1. Instale o Jellyfin e crie um usuário para o tablet. O app sugere `jellyfin`, e a senha é digitada no tablet.
2. Em **Painel → Chaves de API**, crie uma chave e grave em `JELLYFIN_API_KEY`.
3. Ajuste o endereço em `app-jellyfin/src/br/jellyxoom/Api.java` (`SERVIDOR_PADRAO`), `painel/show_xoom.py` (`JELLYFIN`) e
   `JELLYFLIX_JELLYFIN_URL`.
4. Para a conversão de vídeo não pesar, ative a aceleração por hardware em **Painel → Reprodução**. O Xoom recebe tudo acima
   de 480p convertido.

## Música no Echo

Crie uma ou mais bibliotecas do tipo **Música**. Elas aparecem sozinhas na aba Bibliotecas da tela.

- `JELLYFLIX_BIBLIOTECA_TOP` define a biblioteca tocada em "toca o top cinquenta", na ordem de número da faixa.
- As demais tocam em ordem aleatória.
- O Echo recebe MP3 de até 320 kbps. O que não for MP3 é convertido pelo Jellyfin.

Este repositório não inclui música nem ferramenta para baixá-la. Use a sua própria coleção.

## Letra em tempo real

Coloque um arquivo `.lrc` com o mesmo nome do áudio, na mesma pasta, e atualize a biblioteca:

```text
Top 50 Brasil/
  01 - Minha Musica.mp3
  01 - Minha Musica.lrc
```

O painel consulta `GET /Audio/<id>/Lyrics` e, se não houver letra, procura no [LRCLIB](https://lrclib.net). Uma faixa com
letra sem tempo marcado aparece parada na tela. Se a letra sair do compasso em todas as músicas, use os botões de ±0,5 s.

## TV ao vivo

`fast_tv.py` monta listas só com fontes gratuitas: Pluto TV, Plex e canais abertos. Ele usa apenas a biblioteca padrão do
Python e serve na porta 8097:

```sh
pm2 start jellyfin/fast_tv.py --name fast-tv --interpreter python3
```

Em **Painel → TV ao vivo**, adicione:

| Sintonizador M3U | Guia XMLTV |
|---|---|
| `http://127.0.0.1:8097/pluto.m3u` | `http://127.0.0.1:8097/pluto.xml` |
| `http://127.0.0.1:8097/plex.m3u` | `http://127.0.0.1:8097/plex.xml` |
| `http://127.0.0.1:8097/abertos.m3u` | sem guia pronto: `/abertos.xml` só existe se você gerar esse arquivo |

O app do Xoom mostra apenas canais com número, ou seja, com `tvg-chno` na lista. A numeração vem do próprio `fast_tv.py`:
Pluto a partir de 2000, Plex de 4000 e abertos de 5000. Canais de outras listas sem esse campo ficam escondidos.

Para tocar um canal, o app chama `PlaybackInfo` **sem** `MediaSourceId`, porque a fonte do canal só existe depois que o
Jellyfin abre o stream. O canal chega como HLS em 480p, e o stream é fechado ao sair do MX Player.

## Legenda por IA (opcional)

O toque longo no app oferece **Traduzir legenda com IA**. Esse recurso depende de um serviço próprio no notebook, que não
faz parte deste repositório porque está ligado a outros sistemas. Sem ele, o resto do app funciona normalmente.

Para ligar o botão, escreva um serviço na porta 8793 com este contrato:

| Pedido | Resposta |
|---|---|
| `POST /traduzir-ia` com `{"itemId": "..."}` | `{"mensagem": "..."}` |
| `GET /traduzir-ia?itemId=...` | `{"mensagem": "...", "fim": true/false, "ok": true/false}` |

A legenda pronta deve ficar ao lado do vídeo como `<vídeo>.pt-BR.srt`, e o item precisa ser atualizado no Jellyfin.
