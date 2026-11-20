#!/usr/bin/env bash
# Saati 60 dakikadan fazla geçtiği hâlde kuyrukta duran gönderileri listeler.
# Çıktı boşsa sorun yok. GH_TOKEN, REPO ve TZ=Europe/Istanbul ortamda olmalı.
set -u
sinir=$(date -d '-60 min' +%Y-%m-%d_%H%M)
gh api "repos/$REPO/contents/kuyruk" --jq '.[] | select(.type=="dir") | .name' 2>/dev/null \
  | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}_' \
  | awk -v s="$sinir" 'substr($0,1,15) < s' || true
