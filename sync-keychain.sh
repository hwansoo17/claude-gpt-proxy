#!/bin/bash
# macOS Keychain → credential 파일 동기화
# crontab -e 에서 등록: */30 * * * * /path/to/sync-keychain.sh

CRED_FILE="$HOME/.claude/.credentials.json"
KEYCHAIN_SERVICE="Claude Code-credentials"

TOKEN_JSON=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$TOKEN_JSON" ]; then
    echo "[sync] Keychain 읽기 실패"
    exit 1
fi

echo "$TOKEN_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
with open('$CRED_FILE', 'w') as f:
    json.dump(data, f, indent=2)
print('[sync] credential 파일 갱신 완료')
"
