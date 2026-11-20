#!/usr/bin/env bash
# Kullanım: oto/uyari.sh "Başlık" "Açıklama"
# Aynı başlıkla açık bir kayıt (issue) varsa yorum ekler, yoksa yenisini açar.
# Depo sahibine GitHub e-posta bildirimi gider. GH_TOKEN ve REPO ortamda olmalı.
set -u
baslik="$1"; govde="$2"
no=$(gh issue list -R "$REPO" --state open --limit 50 --json number,title \
      --jq ".[] | select(.title == \"$baslik\") | .number" 2>/dev/null | head -1)
if [ -n "$no" ]; then
  gh issue comment "$no" -R "$REPO" --body "$govde" >/dev/null && echo "Uyarı eklendi (#$no)"
else
  gh issue create -R "$REPO" --title "$baslik" --body "$govde" >/dev/null && echo "Uyarı açıldı"
fi
